"""Credential-free RankIC catalog and annual Parquet selection.

Catalog declarations describe local inputs, not proof of server authorization.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from rankic_engine import iso_date, normalize_code
from rankic_inputs import DataBundle, DataContractError, SourceFiles, _check_links, validate_origin

_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}')
_SHA256 = re.compile(r'[0-9a-f]{64}')


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DataContractError('catalog identifier must be non-secret, not a URL or free text')
    return value


def _coverage(value):
    if not isinstance(value, dict) or type(value.get('rows')) is not int or value['rows'] < 0:
        raise DataContractError('invalid coverage rows')
    first, last = value.get('first_date'), value.get('last_date')
    if value['rows'] == 0:
        if first is not None or last is not None:
            raise DataContractError('empty coverage must have null dates')
    elif iso_date(first) > iso_date(last):
        raise DataContractError('coverage dates are reversed')
    return dict(first_date=first, last_date=last, rows=value['rows'])


class Store:
    def __init__(self, root):
        self.root = Path(root).absolute()
        _check_links(self.root)
        if not self.root.is_dir():
            raise DataContractError('data root must be an existing directory')
        self.source_files = SourceFiles()
        catalog_path = self.path('catalog.json')
        if catalog_path.stat().st_size > 8 * 1024 * 1024:
            raise DataContractError('catalog exceeds size limit')
        self.catalog_fingerprint = self.source_files.verify(catalog_path)
        with catalog_path.open(encoding='utf-8-sig') as handle:
            self.catalog = json.load(handle)
        self.source_files.assert_unchanged()
        c = self.catalog
        if not isinstance(c, dict) or type(c.get('schema_version')) is not int or c['schema_version'] != 1 or c.get('dataset') != 'rankic':
            raise DataContractError('unsupported RankIC catalog schema')
        validate_origin(c.get('origin'), c.get('server_contract'))
        _identifier(c.get('snapshot_id'))
        _identifier(c.get('adjustment_id'))
        scope = c.get('scope', {})
        if scope.get('kind') != ('server_public_data' if c['origin'] == 'xyquant_server' else 'local_reference'):
            raise DataContractError('local reference scope is not server authorization')
        if not isinstance(scope.get('codes'), list) or not scope['codes']:
            raise DataContractError('explicit scope codes required')
        self.codes = sorted(normalize_code(code) for code in scope['codes'])
        if len(set(self.codes)) != len(self.codes):
            raise DataContractError('duplicate scope codes')
        self.scope_sha256 = hashlib.sha256(json.dumps(self.codes, separators=(',', ':')).encode()).hexdigest()
        self.calendar = c['calendar']
        self._descriptor(self.calendar)
        lower, upper = iso_date(self.calendar['complete_from']), iso_date(self.calendar['complete_through'])
        if not lower <= self.calendar['first_date'] <= self.calendar['last_date'] <= upper:
            raise DataContractError('calendar actual and complete coverage are inconsistent')
        self.market = c['market']
        if self.market.get('adjustment') != 'adjusted_close':
            raise DataContractError('market must use adjusted_close, not returns or eligible')
        self._annual(self.market['files'])
        self.price_coverage = _coverage(self.market['price_coverage'])
        self.listed_coverage = _coverage(self.market['listed_coverage'])
        through = iso_date(self.market['price_through'])
        if not self.price_coverage['rows'] or through > self.price_coverage['last_date']:
            raise DataContractError('price availability exceeds price coverage')
        for coverage in (self.price_coverage, self.listed_coverage):
            if coverage['rows'] and (coverage['first_date'] < min(f['first_date'] for f in self.market['files']) or
                                    coverage['last_date'] > max(f['last_date'] for f in self.market['files'])):
                raise DataContractError('role coverage exceeds annual market range')
        self.factors = c['factors']
        if not isinstance(self.factors, list) or not self.factors:
            raise DataContractError('catalog must contain factors')
        self.names, ids = {}, set()
        for factor in self.factors:
            ident = _identifier(factor['id'])
            if ident.casefold() in ids:
                raise DataContractError('duplicate factor identifier')
            ids.add(ident.casefold())
            labels = [ident, factor['name']]
            aliases = factor.get('aliases', [])
            if not isinstance(aliases, list):
                raise DataContractError('factor aliases must be a list')
            for label in labels + aliases:
                if not isinstance(label, str) or not label.strip() or len(label) > 200:
                    raise DataContractError('invalid factor name or alias')
                key = label.strip().casefold()
                if key in self.names and self.names[key] is not factor:
                    raise DataContractError('ambiguous factor alias in catalog')
                self.names[key] = factor
            self._annual(factor['files'])
        if 'provenance' in c:
            provenance = c['provenance']
            self._descriptor(provenance, panel=False)
            self.source_files.verify(self.path(provenance['path']), provenance['sha256'], provenance['bytes'])

    def path(self, relative):
        if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative or '\x00' in relative:
            raise DataContractError('invalid relative data path')
        parts = PurePosixPath(relative)
        if parts.is_absolute() or '..' in parts.parts or parts.as_posix() != relative:
            raise DataContractError('data path must stay inside its root')
        path = self.root.joinpath(*parts.parts)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise DataContractError('data path escapes its root')
        # Check existing parents too; unselected annual shards need not exist locally.
        for part in (path, *path.parents):
            if part.exists() or part.is_symlink():
                _check_links(part)
                break
        return path

    def _descriptor(self, value, panel=True):
        if not isinstance(value, dict):
            raise DataContractError('invalid file descriptor')
        path = self.path(value['path'])
        allowed = ('.pqt', '.parquet') if panel else ('.json',)
        if path.suffix.lower() not in allowed:
            raise DataContractError('unsupported data path extension')
        if type(value.get('bytes')) is not int or value['bytes'] <= 0:
            raise DataContractError('invalid file size')
        if not isinstance(value.get('sha256'), str) or not _SHA256.fullmatch(value['sha256']):
            raise DataContractError('invalid file hash')
        if panel:
            if type(value.get('rows')) is not int or value['rows'] <= 0:
                raise DataContractError('invalid file rows')
            if iso_date(value['first_date']) > iso_date(value['last_date']):
                raise DataContractError('invalid file date range')

    def _annual(self, files):
        if not isinstance(files, list) or not files:
            raise DataContractError('annual panel files required')
        years, paths = set(), set()
        for entry in files:
            self._descriptor(entry)
            year = entry.get('year')
            if type(year) is not int or not 1900 <= year <= 9999:
                raise DataContractError('invalid annual panel year')
            if year in years or entry['path'].casefold() in paths:
                raise DataContractError('duplicate annual year or overlapping files')
            if not all(entry[k].startswith(str(year) + '-') for k in ('first_date', 'last_date')):
                raise DataContractError('annual dates do not match year')
            years.add(year)
            paths.add(entry['path'].casefold())

    def resolve(self, requested):
        if not isinstance(requested, str) or requested.strip().casefold() not in self.names:
            raise DataContractError('factor not found in this catalog; do not substitute a sample')
        return self.names[requested.strip().casefold()]

    def inventory(self):
        return dict(origin=self.catalog['origin'], scope_kind=self.catalog['scope']['kind'],
                    scope_stock_count=len(self.codes), data_integrity_checked=False,
                    calendar=dict(first_date=self.calendar['first_date'], last_date=self.calendar['last_date'],
                                  complete_from=self.calendar['complete_from'], complete_through=self.calendar['complete_through']),
                    price_through=self.market['price_through'], price_coverage=self.price_coverage,
                    listed_coverage=self.listed_coverage,
                    factors=[dict(id=f['id'], name=f['name'], aliases=f.get('aliases', []),
                                  first_date=min(s['first_date'] for s in f['files']),
                                  last_date=max(s['last_date'] for s in f['files'])) for f in self.factors])

    def bundle(self, parameters):
        factor = self.resolve(parameters.factor_name)
        catalog_info = dict(schema_version=1, sha256=self.catalog_fingerprint['sha256'], factor_id=factor['id'],
                            scope_sha256=self.scope_sha256, price_coverage=self.price_coverage,
                            listed_coverage=self.listed_coverage, data_quality=self.catalog.get('data_quality', {}),
                            factor_input=self.catalog.get('factor_input'))
        common = dict(format='parquet', canonical=True)
        data = dict(origin=self.catalog['origin'], server_contract=self.catalog.get('server_contract'), snapshot_id=self.catalog['snapshot_id'],
                    adjustment_id=self.catalog['adjustment_id'], scope_kind=self.catalog['scope']['kind'],
                    authorized_codes=self.codes, factor_name=parameters.factor_name, catalog=catalog_info,
                    calendar=dict(self.calendar, **common),
                    factor=dict(files=factor['files'], **common),
                    price=dict(files=self.market['files'], adjustment='adjusted_close',
                               price_through=self.market['price_through'], presence_column='price_present', **common),
                    listed=dict(files=self.market['files'], presence_column='listed_present', **common))
        self.assert_unchanged()
        return DataBundle(data, parameters, self.root, source_files=self.source_files)

    def assert_unchanged(self):
        self.source_files.assert_unchanged()

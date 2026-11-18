"""Explicit local manifests; bounded, read-only source access. Never unpickle HDF5."""
from bisect import bisect_left
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile

import numpy as np
import pandas as pd

from rankic_engine import iso_date, normalize_code, period_pairs


class DataContractError(ValueError):
    pass


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_date(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None or any((value.hour, value.minute, value.second, value.microsecond)):
            raise DataContractError('canonical dates must be timezone-free calendar dates')
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return iso_date(value)


def _parquet_row_groups(parquet, date_column, selected_dates):
    """Conservatively prune by date bounds; missing/ambiguous statistics stay readable."""
    if selected_dates is None:
        return None
    dates = sorted(selected_dates)
    if not dates:
        return []
    indices = [i for i in range(len(parquet.schema)) if parquet.schema.column(i).path == date_column]
    if len(indices) != 1:
        return None
    groups = []
    for group in range(parquet.metadata.num_row_groups):
        statistics = parquet.metadata.row_group(group).column(indices[0]).statistics
        if statistics is not None and statistics.has_min_max:
            try:
                lower, upper = _canonical_date(statistics.min), _canonical_date(statistics.max)
            except (ValueError, TypeError, OverflowError, NotImplementedError):
                pass
            else:
                candidate = bisect_left(dates, lower)
                if lower <= upper and (candidate == len(dates) or dates[candidate] > upper):
                    continue
        groups.append(group)
    return groups


def validate_origin(origin, server_contract=None):
    if origin not in ('local_reference', 'xyquant_server'):
        raise DataContractError('origin must be local_reference or xyquant_server')
    if origin == 'xyquant_server' and server_contract != 'rankic-data-v1':
        # A hash or caller-provided verified flag is not server-contract acceptance.
        raise DataContractError('server RankIC data contract is not verified in this release; local reference only')


def _check_links(path):
    import stat
    for part in (path, *path.parents):
        info = part.lstat()
        if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise DataContractError('source path links are not permitted')


class SourceFiles:
    """Cache digests; recheck linked files and metadata-only changes by content."""
    def __init__(self):
        self.records = {}

    @staticmethod
    def _signature(path):
        info = path.stat()
        return (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino,
                info.st_dev, info.st_nlink)

    @staticmethod
    def _content_identity(signature):
        # Adding/removing a hard link changes ctime and link count, not contents.
        return signature[0], signature[1], signature[3], signature[4]

    def _checked_record(self, path):
        original, digest = self.records[path]
        current = self._signature(path)
        if self._content_identity(current) != self._content_identity(original):
            raise DataContractError('source snapshot changed during calculation')
        # Linked data can be written through another path. Rehash even when its
        # metadata is unchanged; ctime is a creation timestamp on some systems.
        if current != original or current[5] > 1:
            actual = _hash_file(path)
            after = self._signature(path)
            _check_links(path)
            if self._content_identity(current) != self._content_identity(after) or actual != digest:
                raise DataContractError('source snapshot changed during calculation')
            current = after
            self.records[path] = (current, digest)
        return current, digest

    def verify(self, path, expected_hash=None, expected_bytes=None):
        path = path.absolute()
        _check_links(path)
        before = self._signature(path)
        if path in self.records:
            before, digest = self._checked_record(path)
        else:
            digest = _hash_file(path)
            after = self._signature(path)
            _check_links(path)
            if self._content_identity(before) != self._content_identity(after):
                raise DataContractError('source snapshot changed during calculation')
            before = after
            self.records[path] = (before, digest)
        if expected_hash is not None and expected_hash != digest:
            raise DataContractError('source hash mismatch')
        if expected_bytes is not None and expected_bytes != before[0]:
            raise DataContractError('source size mismatch')
        return dict(sha256=digest, bytes=before[0])

    def assert_unchanged(self):
        for path in self.records:
            _check_links(path)
            self._checked_record(path)


def _canonical_schema(schema, role):
    import pyarrow as pa
    expected = {'date': pa.timestamp('ns')}
    if role != 'calendar':
        expected['stock_code'] = pa.string()
    if role in ('price', 'listed'):
        expected.update(adj_close=pa.float64(), listed=pa.int8(),
                        price_present=pa.bool_(), listed_present=pa.bool_())
    elif role == 'factor':
        expected['value'] = pa.float64()
    for name, dtype in expected.items():
        if name not in schema.names:
            raise DataContractError('canonical schema is missing required columns')
        actual = schema.field(name).type
        if pa.types.is_dictionary(actual):
            actual = actual.value_type
        if actual != dtype:
            raise DataContractError('canonical schema has an invalid column type')


class _Table:
    """Stream selected annual shards into an indexed, temporary date cache."""
    def __init__(self, paths, spec, role, selected_dates, cache_dir, stack, source_files):
        self.source_files = source_files
        value_key = {'factor': 'value', 'price': 'adj_close', 'listed': 'listed'}.get(role)
        keys = ['date'] + (['stock_code', value_key] if value_key else [])
        presence = spec.get('presence_column')
        presence_columns = [presence, 'listed_present' if role == 'price' else 'price_present'] if presence else []
        keys += presence_columns
        mapping = spec.get('columns', {})
        columns = [mapping.get(key, key) for key in keys]
        if len(set(columns)) != len(columns):
            raise DataContractError('columns mapping must be one-to-one')
        self.db = sqlite3.connect(str(cache_dir / (role + '.sqlite')))
        stack.callback(self.db.close)
        self.db.execute('CREATE TABLE data (date TEXT, stock_code TEXT, value REAL, PRIMARY KEY(date, stock_code))')
        self.db.execute('CREATE TABLE raw_keys (date TEXT, stock_code TEXT, PRIMARY KEY(date, stock_code))')
        all_dates, file_info = set(), []
        for path, entry in paths:
            if selected_dates is not None and 'year' in entry and not any(
                    d.startswith(str(entry['year']) + '-') for d in selected_dates):
                continue
            info = source_files.verify(path, entry.get('sha256'), entry.get('bytes'))
            if 'year' in entry:
                info.update({k: entry[k] for k in ('year', 'rows', 'first_date', 'last_date')})
            file_info.append(info)
            if spec['format'] == 'csv':
                batches = pd.read_csv(path, dtype={columns[0]: 'string', **({columns[1]: 'string'} if value_key else {})},
                                      chunksize=100000, encoding='utf-8-sig')
                stack.callback(batches.close)
            elif spec['format'] == 'parquet':
                import pyarrow.parquet as pq
                parquet = pq.ParquetFile(path)
                stack.callback(parquet.close)
                if not set(columns).issubset(parquet.schema_arrow.names):
                    raise DataContractError('missing required columns')
                if spec.get('canonical'):
                    _canonical_schema(parquet.schema_arrow, role)
                if 'rows' in entry and parquet.metadata.num_rows != entry['rows']:
                    raise DataContractError('source rows mismatch')
                row_groups = _parquet_row_groups(parquet, columns[0], selected_dates)
                batches = (batch.to_pandas() for batch in parquet.iter_batches(
                    batch_size=100000, columns=columns, row_groups=row_groups))
            else:
                raise DataContractError('unsupported table format')
            for frame in batches:
                if not set(columns).issubset(frame.columns):
                    raise DataContractError('missing required columns')
                frame = frame[columns].copy()
                frame.columns = keys
                frame['date'] = frame['date'].map(_canonical_date)
                if 'year' in entry and not frame['date'].str.startswith(str(entry['year']) + '-').all():
                    raise DataContractError('annual panel contains dates outside its declared year')
                if spec.get('canonical') and not frame['date'].between(entry['first_date'], entry['last_date']).all():
                    raise DataContractError('source dates exceed declared file coverage')
                if selected_dates is not None:
                    frame = frame[frame['date'].isin(selected_dates)]
                if value_key:
                    frame['stock_code'] = frame['stock_code'].map(normalize_code)
                if presence:
                    for column in presence_columns:
                        if not pd.api.types.is_bool_dtype(frame[column].dtype) or frame[column].isna().any():
                            raise DataContractError('presence flags must be non-null booleans')
                    if not frame[presence_columns].any(axis=1).all():
                        raise DataContractError('market rows must have at least one present source role')
                    flags = frame[presence]
                    if frame.loc[~flags, value_key].notna().any():
                        raise DataContractError('absent role keys cannot hide a present value')
                    try:
                        self.db.executemany('INSERT INTO raw_keys VALUES (?, ?)',
                                            zip(frame['date'], frame['stock_code']))
                    except sqlite3.IntegrityError as exc:
                        raise DataContractError('duplicate date/security keys') from exc
                    frame = frame[flags]
                if value_key:
                    values = pd.to_numeric(frame[value_key], errors='raise').astype(float)
                    records = ((d, c, float(v)) for d, c, v in zip(frame['date'], frame['stock_code'], values))
                else:
                    records = ((d, '', 0.0) for d in frame['date'])
                try:
                    self.db.executemany('INSERT INTO data VALUES (?, ?, ?)', records)
                except sqlite3.IntegrityError as exc:
                    raise DataContractError('duplicate date/security keys') from exc
                all_dates.update(frame['date'])
        self.db.commit()
        self.dates = sorted(all_dates)
        self.info = dict(format=spec['format'], files=file_info) if 'files' in spec else dict(format=spec['format'], **file_info[0])
        self.assert_unchanged()

    def at(self, day):
        rows = self.db.execute('SELECT stock_code, value FROM data WHERE date=? ORDER BY stock_code', (day,)).fetchall()
        return pd.Series([r[1] for r in rows], index=[r[0] for r in rows], dtype=float)

    def metadata(self):
        return dict(self.info)

    def assert_unchanged(self):
        self.source_files.assert_unchanged()


class _HDF:
    def __init__(self, path, spec, role, stack):
        if spec.get('date_encoding') != 'unix_seconds' or spec.get('timezone') not in ('UTC', 'UTC+08:00'):
            raise DataContractError('HDF5 requires explicit unix_seconds encoding and UTC/UTC+08:00 timezone')
        import h5py
        self.path, self.before = path, path.stat()
        self.handle = stack.enter_context(h5py.File(path, 'r'))
        raw = np.asarray(self.handle['DateTime'][:], dtype=float)
        if raw.ndim != 1 or not np.isfinite(raw).all():
            raise DataContractError('invalid HDF5 date axis')
        tz = timezone.utc if spec['timezone'] == 'UTC' else timezone(timedelta(hours=8))
        decoded = pd.to_datetime(raw, unit='s', utc=True).tz_convert(tz)
        if not (decoded == decoded.normalize()).all():
            raise DataContractError('HDF5 date axis is not midnight in declared timezone')
        self.dates = list(decoded.strftime('%Y-%m-%d'))
        if len(set(self.dates)) != len(self.dates):
            raise DataContractError('duplicate HDF5 date axis')
        self.rows = {day: i for i, day in enumerate(self.dates)}
        self.codes, self.columns, raw_codes = [], [], []
        if role != 'calendar':
            raw_codes = list(self.handle['ID'].asstr()[:])
            if len(set(raw_codes)) != len(raw_codes):
                raise DataContractError('duplicate HDF5 security axis')
            for column, label in enumerate(raw_codes):
                try:
                    code = normalize_code(label)
                except ValueError:
                    if spec.get('ignore_non_stock_labels') is not True:
                        raise
                    continue
                self.codes.append(code)
                self.columns.append(column)
            if len(set(self.codes)) != len(self.codes):
                raise DataContractError('duplicate HDF5 security axis')
            if self.handle['Data'].shape != (len(self.dates), len(raw_codes)) or self.handle['Data'].dtype.kind not in 'fiu':
                raise DataContractError('HDF5 Data must be a numeric date-by-security matrix')
        self.axis_hash = hashlib.sha256(json.dumps([self.dates, raw_codes], ensure_ascii=True).encode()).hexdigest()
        self.selected_hash = hashlib.sha256()
        self.read_count = 0
        self.info = {'format': 'hdf5', 'axes_sha256': self.axis_hash,
                     'bytes': self.before.st_size, 'mtime_ns': self.before.st_mtime_ns,
                     'date_encoding': 'unix_seconds', 'timezone': spec['timezone'],
                     'ignored_non_stock_columns': len(raw_codes) - len(self.codes)}
        if spec.get('sha256'):
            self.info['sha256'] = _hash_file(path)
            if self.info['sha256'] != spec['sha256']:
                raise DataContractError('source hash mismatch')

    def at(self, day):
        if day not in self.rows:
            return pd.Series(dtype=float)
        values = np.asarray(self.handle['Data'][self.rows[day], :], dtype='<f8')[self.columns]
        self.selected_hash.update(day.encode('ascii'))
        self.selected_hash.update(values.tobytes())
        self.read_count += 1
        return pd.Series(values, index=self.codes, dtype=float)

    def metadata(self):
        return dict(self.info, selected_rows_sha256=self.selected_hash.hexdigest(), selected_row_reads=self.read_count)

    def assert_unchanged(self):
        after = self.path.stat()
        if (after.st_size, after.st_mtime_ns) != (self.before.st_size, self.before.st_mtime_ns):
            raise DataContractError('source snapshot changed during calculation')


class DataBundle:
    def __init__(self, manifest, parameters, base_dir, *, source_files=None):
        self.stack = ExitStack()
        self.source_files = source_files if source_files is not None else SourceFiles()
        self.catalog_info = manifest.get('catalog')
        self.scope_kind = manifest.get('scope_kind', 'local_reference')
        try:
            self._load(manifest, parameters, Path(base_dir))
        except Exception:
            self.stack.close()
            raise

    def _load(self, data, p, base_dir):
        self.origin = data.get('origin')
        validate_origin(self.origin, data.get('server_contract'))
        if data.get('factor_name') != p.factor_name:
            raise DataContractError('factor identity does not match the request')
        for key in ('snapshot_id', 'adjustment_id'):
            value = data.get(key)
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}', value):
                raise DataContractError('snapshot_id and adjustment_id must be non-secret identifiers, not URLs or free text')
        if data.get('price', {}).get('adjustment') != 'adjusted_close':
            raise DataContractError('price must explicitly use adjusted_close, not returns')
        if p.pool == 'all_a' and 'listed' not in data:
            raise DataContractError('historical listed-at-T universe required')
        codes = data.get('authorized_codes')
        if not isinstance(codes, list) or not codes:
            raise DataContractError('explicit authorized_codes scope required')
        self.authorized_codes = set(normalize_code(c) for c in codes)
        calendar_spec = data['calendar']
        self.calendar_from = iso_date(calendar_spec['complete_from'])
        self.calendar_through = iso_date(calendar_spec['complete_through'])
        self.price_through = iso_date(data['price']['price_through'])
        if self.calendar_from > p.start or self.calendar_through < p.end:
            raise DataContractError('calendar does not cover the observation range')
        self.cache_dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='xyquant-rankic-')))
        self.readers = {}

        def load(role, selected_dates=None):
            spec = data[role]
            if spec.get('format') == 'hdf5':
                reader = _HDF((base_dir / spec['path']).resolve(), spec, role, self.stack)
            else:
                entries = spec.get('files', [spec])
                paths = [(base_dir / item['path'], item) for item in entries]
                reader = _Table(paths, spec, role, selected_dates, self.cache_dir, self.stack, self.source_files)
            self.readers[role] = reader
            return reader

        self.calendar = [day for day in load('calendar').dates if self.calendar_from <= day <= self.calendar_through]
        pairs = period_pairs(self.calendar, p.start, p.end, p.frequency, self.calendar_through)
        observations = {t for t, _ in pairs}
        price_dates = observations | {exit_date for _, exit_date in pairs if exit_date and exit_date <= self.price_through}
        load('factor', observations)
        load('price', price_dates)
        if p.pool == 'all_a':
            load('listed', observations)
        self.snapshot_id = data['snapshot_id']
        self.adjustment_id = data['adjustment_id']
        self.warnings = []
        for role, reader in self.readers.items():
            ignored = reader.metadata().get('ignored_non_stock_columns', 0)
            if ignored:
                self.warnings.append(f'Local HDF5 {role}: explicitly ignored {ignored} non-stock axis labels.')
        if self.origin == 'local_reference':
            self.warnings.append('LOCAL REFERENCE ONLY: not proof of server authorization, price semantics, historical-universe completeness or independent exchange-calendar verification.')

    def factor_at(self, day):
        return self.readers['factor'].at(day)

    def price_at(self, day):
        return self.readers['price'].at(day)

    def listed_at(self, day):
        values = self.readers['listed'].at(day)
        if values.empty or not values.dropna().isin([0.0, 1.0]).all():
            raise DataContractError('historical listed data missing or not binary')
        return values

    def metadata(self):
        result = dict(origin=self.origin, snapshot_id=self.snapshot_id, adjustment_id=self.adjustment_id,
                      calendar_from=self.calendar_from, calendar_through=self.calendar_through,
                      price_through=self.price_through, scope_kind=self.scope_kind,
                      scope_stock_count=len(self.authorized_codes),
                      scope_exchange_counts={exchange: sum(code.endswith('.' + exchange) for code in self.authorized_codes)
                                             for exchange in ('SH', 'SZ', 'BJ')},
                      sources={role: reader.metadata() for role, reader in self.readers.items()})
        if self.catalog_info is not None:
            result['catalog'] = self.catalog_info
        return result

    def assert_unchanged(self):
        self.source_files.assert_unchanged()
        for reader in self.readers.values():
            reader.assert_unchanged()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stack.close()

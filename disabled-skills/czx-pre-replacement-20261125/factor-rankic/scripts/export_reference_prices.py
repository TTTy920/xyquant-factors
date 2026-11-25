#!/usr/bin/env python3
"""Build a NEW local-reference price/calendar snapshot; never update HDF or server data."""
import argparse
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import shutil
import tempfile
import uuid

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from prepare_sample import _coverage, _same_role, _write_panel
from rankic_engine import iso_date, normalize_code
from rankic_inputs import DataContractError, _canonical_date, _canonical_schema, _check_links, _hash_file, _HDF
from rankic_store import Store


KEYS = ['date', 'stock_code']


def _read_panel(store, entry, role):
    """Fully validate an original shard before reusing any of its roles."""
    path = store.path(entry['path'])
    store.source_files.verify(path, entry['sha256'], entry['bytes'])
    with pq.ParquetFile(path) as panel:
        _canonical_schema(panel.schema_arrow, role)
        if panel.metadata.num_rows != entry['rows']:
            raise DataContractError('source rows mismatch')
        frame = panel.read().to_pandas()
    if frame['date'].isna().any():
        raise DataContractError('source dates must not be null')
    frame['date'] = pd.to_datetime(frame['date'].map(_canonical_date)).astype('datetime64[ns]')
    if _coverage(frame) != {k: entry[k] for k in ('rows', 'first_date', 'last_date')}:
        raise DataContractError('original panel coverage mismatch')
    if 'year' in entry and not (frame.date.dt.year == entry['year']).all():
        raise DataContractError('original panel dates outside declared year')
    if role != 'calendar':
        frame['stock_code'] = frame['stock_code'].map(normalize_code).astype('string')
    if frame.duplicated(['date'] if role == 'calendar' else KEYS).any():
        raise DataContractError('duplicate original date/security keys')
    if role in ('price', 'listed'):
        for flag, value in (('price_present', 'adj_close'), ('listed_present', 'listed')):
            if not pd.api.types.is_bool_dtype(frame[flag].dtype) or frame[flag].isna().any():
                raise DataContractError('presence flags must be non-null booleans')
            if frame.loc[~frame[flag], value].notna().any():
                raise DataContractError('absent role cannot hide a present value')
        if not frame[['price_present', 'listed_present']].any(axis=1).all():
            raise DataContractError('market rows must have a present role')
        if not frame.loc[frame.listed_present, 'listed'].dropna().isin([0, 1]).all():
            raise DataContractError('historical listed data must be binary or unknown')
    return frame


def _combined_coverage(parts):
    nonempty = [p for p in parts if p['rows']]
    return dict(rows=sum(p['rows'] for p in nonempty),
                first_date=min((p['first_date'] for p in nonempty), default=None),
                last_date=max((p['last_date'] for p in nonempty), default=None))


def _check_overlap(old_price, new_price):
    before = old_price.set_index(KEYS)['adj_close']
    after = new_price.set_index(KEYS)['adj_close']
    positions = after.index.get_indexer(before.index)
    if (positions < 0).any():
        raise DataContractError('original price keys are absent from the new snapshot')
    a = before.to_numpy(dtype='float64')
    b = after.iloc[positions].to_numpy(dtype='float64')
    if not ((a == b) | (np.isnan(a) & np.isnan(b))).all():
        raise DataContractError('original price overlap differs; refusing mixed adjustment snapshots')


def _price_year(reader, days):
    """Bound the HDF matrix read to one year, preserving the reader's validated axes."""
    positions = sorted(reader.rows[day] for day in days)
    dates = [reader.dates[row] for row in positions]
    matrix = np.asarray(reader.handle['Data'][positions, :], dtype='<f8')[:, reader.columns]
    return pd.DataFrame(dict(date=np.repeat(pd.to_datetime(dates).to_numpy(), len(reader.codes)),
                             stock_code=pd.array(reader.codes * len(dates), dtype='string'),
                             adj_close=matrix.reshape(-1)))


def export_reference_prices(source_path, data_root, destination, start, end, *,
                            date_encoding, timezone, excluded_labels=(), progress=None):
    """Only replace price/calendar in a new self-contained, explicitly local snapshot."""
    start, end = iso_date(start), iso_date(end)
    if start > end:
        raise DataContractError('requested range is reversed')
    source_path, destination = Path(source_path).absolute(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('destination exists; refusing overwrite')
    if not destination.parent.is_dir():
        raise DataContractError('destination parent must already exist')
    _check_links(destination.parent)
    store = Store(data_root)  # Reject server origin before reading HDF.
    if start > store.calendar['complete_from'] or end < store.calendar['complete_through']:
        raise DataContractError('requested range cannot shrink original calendar complete coverage')
    sources = store.source_files
    source_info = sources.verify(source_path)
    if not isinstance(excluded_labels, (list, tuple)) or any(not isinstance(s, str) for s in excluded_labels):
        raise DataContractError('excluded labels must be explicit strings')
    if len(set(excluded_labels)) != len(excluded_labels):
        raise DataContractError('duplicate excluded labels')
    for label in excluded_labels:
        try:
            normalize_code(label)
        except ValueError:
            continue
        raise DataContractError('a valid stock label cannot be excluded')
    original_calendar = _read_panel(store, store.calendar, 'calendar')
    old_by_year = {item['year']: item for item in store.market['files']}
    audit = dict(schema_version=1, origin='local_reference', requested=dict(start=start, end=end),
                 source=dict(filename=source_path.name, **source_info),
                 original_catalog_sha256=store.catalog_fingerprint['sha256'],
                 excluded_labels=list(excluded_labels), years=[],
                 independent_exchange_calendar_verified=False, server_to_report_verified=False)
    original_provenance = None
    if 'provenance' in store.catalog:
        entry = store.catalog['provenance']
        if entry['bytes'] > 8 * 1024 * 1024:
            raise DataContractError('original provenance exceeds size limit')
        original_provenance = json.loads(store.path(entry['path']).read_text(encoding='utf-8-sig'))
    with ExitStack() as stack:
        spec = dict(date_encoding=date_encoding, timezone=timezone,
                    ignore_non_stock_labels=bool(excluded_labels))
        reader = _HDF(source_path, spec, 'price', stack)
        kept = set(reader.columns)
        excluded = [label for i, label in enumerate(reader.handle['ID'].asstr()[:]) if i not in kept]
        if sorted(excluded) != sorted(excluded_labels):
            raise DataContractError('HDF excluded labels do not match the explicit allowlist')
        if set(reader.codes) != set(store.codes):
            raise DataContractError('HDF stock axis must match the original reference scope')
        if min(reader.dates) > start or max(reader.dates) < end:
            raise DataContractError('requested calendar range exceeds source axis coverage')
        days = sorted(day for day in reader.dates if start <= day <= end)
        if not days:
            raise DataContractError('requested range has no source dates')
        calendar = pd.DataFrame({'date': pd.to_datetime(days).astype('datetime64[ns]')})
        calendar_overlap = calendar[calendar.date.between(store.calendar['complete_from'],
                                                          store.calendar['complete_through'])]
        if len(calendar_overlap) != len(original_calendar) or not original_calendar.date.isin(calendar_overlap.date).all():
            raise DataContractError('original calendar differs within its complete coverage')
        _same_role(original_calendar, calendar_overlap)
        years = sorted({int(day[:4]) for day in days})
        if set(old_by_year) - set(years):
            raise DataContractError('requested range cannot drop original market years')
        audit['source'].update(reader.info)
        audit['calendar'] = dict(**_coverage(calendar), complete_from=start, complete_through=end)
        price_parts, listed_parts, old_price_parts = [], [], []
        with tempfile.TemporaryDirectory(prefix='.rankic-prices-', dir=destination.parent) as temporary:
            staging = Path(temporary) / 'data'
            staging.mkdir()
            calendar_entry = _write_panel(staging, 'calendar.pqt', calendar)
            calendar_entry.update(complete_from=start, complete_through=end)
            _same_role(calendar, pd.read_parquet(staging / 'calendar.pqt'))
            market_entries = []
            for year in years:
                price = _price_year(reader, [day for day in days if day.startswith(str(year) + '-')])
                listed = pd.DataFrame({'date': pd.Series(dtype='datetime64[ns]'),
                                       'stock_code': pd.Series(dtype='string'),
                                       'listed': pd.Series(dtype='Int8')})
                overlap_rows = 0
                if year in old_by_year:
                    old = _read_panel(store, old_by_year[year], 'price')
                    if not old.date.isin(calendar.date).all():
                        raise DataContractError('original market dates are absent from the new calendar')
                    old_price = old.loc[old.price_present, KEYS + ['adj_close']]
                    _check_overlap(old_price, price)
                    old_price_parts.append(_coverage(old_price))
                    overlap_rows = len(old_price)
                    listed = old.loc[old.listed_present, KEYS + ['listed']].copy()
                market = price.assign(price_present=True).merge(listed.assign(listed_present=True),
                                                                 on=KEYS, how='outer', validate='one_to_one')
                market['price_present'] = market.price_present.eq(True)
                market['listed_present'] = market.listed_present.eq(True)
                market['adj_close'] = market.adj_close.astype('float64')
                market['listed'] = market.listed.astype('Int8')
                market = market[KEYS + ['adj_close', 'listed', 'price_present', 'listed_present']]
                entry = _write_panel(staging, f'market/{year}.pqt', market, year)
                reread = pd.read_parquet(staging / entry['path'])
                _same_role(price, reread[reread.price_present], 'adj_close')
                _same_role(listed, reread[reread.listed_present], 'listed')
                pd.testing.assert_frame_equal(market.sort_values(KEYS).reset_index(drop=True),
                                              reread.assign(stock_code=reread.stock_code.astype('string')).sort_values(KEYS).reset_index(drop=True),
                                              check_exact=True)
                price_parts.append(_coverage(price))
                listed_parts.append(_coverage(listed))
                market_entries.append(entry)
                audit['years'].append(dict(year=year, price_rows=len(price), market_rows=len(market),
                                            listed_rows=len(listed), original_price_overlap_rows=overlap_rows,
                                            price_roundtrip_exact=True, listed_roundtrip_exact=True,
                                            presence_roundtrip_exact=True, file=entry))
                if progress:
                    progress(dict(year=year, price_rows=len(price), bytes=entry['bytes'], verified=True))
            if _combined_coverage(old_price_parts) != store.price_coverage:
                raise DataContractError('original price role coverage mismatch')
            if _combined_coverage(listed_parts) != store.listed_coverage:
                raise DataContractError('original listed role coverage mismatch')
            factor_fingerprints = []
            for factor in store.factors:
                for entry in factor['files']:
                    if not entry['path'].startswith('factors/'):
                        raise DataContractError('factor paths must stay under factors/')
                    _read_panel(store, entry, 'factor')
                    target = staging / entry['path']
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copyfile(store.path(entry['path']), target)
                    if target.stat().st_size != entry['bytes'] or _hash_file(target) != entry['sha256']:
                        raise DataContractError('copied factor file differs from original')
                    factor_fingerprints.append(dict(factor_id=factor['id'], **entry))
            audit.update(price_coverage=_combined_coverage(price_parts), listed_coverage=store.listed_coverage,
                         original_calendar_unchanged=True, original_prices_unchanged=True,
                         original_listed_unchanged=True, factor_files_unchanged=True,
                         factor_files=factor_fingerprints)
            reader.assert_unchanged()
            sources.assert_unchanged()
            if _hash_file(source_path) != source_info['sha256']:
                raise DataContractError('source HDF snapshot changed during export')
            sources.assert_unchanged()
            audit['source_files_unchanged'] = True
            provenance = dict(schema_version=1, origin='local_reference',
                              description='Expanded local HDF price/date-axis reference, not a server export.',
                              original_sample=original_provenance, price_extension=audit,
                              limitations=['Factor and listed coverage were NOT expanded.',
                                           'Calendar is the local price date axis, not independently exchange-verified.',
                                           'Adjusted-close semantics remain unverified; no server authorization claim.'])
            provenance_file = staging / 'provenance.json'
            provenance_file.write_text(json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
            catalog = copy.deepcopy(store.catalog)
            catalog.update(snapshot_id='local-price-' + uuid.uuid4().hex, calendar=calendar_entry)
            catalog['market'].update(files=market_entries, price_through=days[-1],
                                     price_coverage=audit['price_coverage'])
            catalog['provenance'] = dict(path='provenance.json', bytes=provenance_file.stat().st_size,
                                        sha256=_hash_file(provenance_file))
            (staging / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
            Store(staging).assert_unchanged()
            sources.assert_unchanged()
            # On Windows Path.rename refuses even an empty existing destination.
            if destination.exists() or destination.is_symlink():
                raise FileExistsError('destination appeared during export; refusing overwrite')
            staging.rename(destination)
    return audit


def main():
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-hdf5', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--date-encoding', required=True, choices=['unix_seconds'])
    parser.add_argument('--timezone', required=True, choices=['UTC', 'UTC+08:00'])
    parser.add_argument('--exclude-label', action='append', default=[])
    args = parser.parse_args()
    result = export_reference_prices(args.source_hdf5, args.data_root, args.output, args.start, args.end,
                                     date_encoding=args.date_encoding, timezone=args.timezone,
                                     excluded_labels=args.exclude_label,
                                     progress=lambda row: print(json.dumps(row), file=sys.stderr, flush=True))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()

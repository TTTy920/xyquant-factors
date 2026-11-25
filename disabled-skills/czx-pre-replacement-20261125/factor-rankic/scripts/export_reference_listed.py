#!/usr/bin/env python3
"""Build a NEW local-reference membership snapshot; keep prices/calendar/factors unchanged."""
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

from export_reference_prices import KEYS, _combined_coverage, _read_panel
from prepare_sample import _coverage, _same_role, _write_panel
from rankic_engine import iso_date
from rankic_inputs import DataContractError, _check_links, _hash_file, _HDF
from rankic_store import Store


def _listed_year(reader, days):
    positions = sorted(reader.rows[day] for day in days)
    dates = [reader.dates[row] for row in positions]
    values = np.asarray(reader.handle['Data'][positions, :], dtype='<f8')[:, reader.columns]
    if not (np.isnan(values) | (values == 0) | (values == 1)).all():
        raise DataContractError('historical listed source must be binary 0/1 or unknown')
    return pd.DataFrame(dict(date=np.repeat(pd.to_datetime(dates).to_numpy(), len(reader.codes)),
                             stock_code=pd.array(reader.codes * len(dates), dtype='string'),
                             listed=pd.array(values.reshape(-1), dtype='Int8')))


def _check_listed_overlap(original, expanded):
    positions = expanded.set_index(KEYS).index.get_indexer(original.set_index(KEYS).index)
    if (positions < 0).any():
        raise DataContractError('original listed keys are absent from the source overlap')
    try:
        _same_role(original, expanded.iloc[positions], 'listed')
    except AssertionError as error:
        raise DataContractError('original listed overlap differs; refusing replacement') from error


def _copy_original(store, staging, entry):
    source, target = store.path(entry['path']), staging / entry['path']
    store.source_files.verify(source, entry['sha256'], entry['bytes'])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if target.stat().st_size != entry['bytes'] or _hash_file(target) != entry['sha256']:
        raise DataContractError('copied original file differs')


def export_reference_listed(source_path, data_root, destination, start, end, *,
                            date_encoding, timezone, progress=None):
    """Fill the existing complete calendar from explicit HDF, without inventing keys or values."""
    start, end = iso_date(start), iso_date(end)
    if start > end:
        raise DataContractError('requested range is reversed')
    source_path, destination = Path(source_path).absolute(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('destination exists; refusing overwrite')
    if not destination.parent.is_dir():
        raise DataContractError('destination parent must already exist')
    _check_links(destination.parent)
    store = Store(data_root)  # The server-source rejection gate remains unchanged.
    if (start, end) != (store.calendar['complete_from'], store.calendar['complete_through']):
        raise DataContractError('listed range must match the existing complete calendar boundaries')
    sources = store.source_files
    source_info = sources.verify(source_path)
    calendar = _read_panel(store, store.calendar, 'calendar')
    calendar_days = sorted(calendar.date.dt.strftime('%Y-%m-%d').tolist())
    old_by_year = {entry['year']: entry for entry in store.market['files']}
    years = sorted({int(day[:4]) for day in calendar_days})
    if set(old_by_year) - set(years):
        raise DataContractError('original market years are outside the calendar')
    output_paths = [store.calendar['path'], 'catalog.json', 'provenance.json',
                    *(f'market/{year}.pqt' for year in years),
                    *(entry['path'] for factor in store.factors for entry in factor['files'])]
    if len({path.casefold() for path in output_paths}) != len(output_paths):
        raise DataContractError('output path collision; refusing to overwrite an original role')
    original_provenance = None
    if 'provenance' in store.catalog:
        entry = store.catalog['provenance']
        sources.verify(store.path(entry['path']), entry['sha256'], entry['bytes'])
        if entry['bytes'] > 8 * 1024 * 1024:
            raise DataContractError('original provenance exceeds size limit')
        original_provenance = json.loads(store.path(entry['path']).read_text(encoding='utf-8-sig'))
    audit = dict(schema_version=1, origin='local_reference', requested=dict(start=start, end=end),
                 source=dict(filename=source_path.name, **source_info),
                 original_catalog_sha256=store.catalog_fingerprint['sha256'], years=[],
                 independent_exchange_calendar_verified=False, server_to_report_verified=False)
    with ExitStack() as stack:
        reader = _HDF(source_path, dict(date_encoding=date_encoding, timezone=timezone,
                                        ignore_non_stock_labels=False), 'listed', stack)
        if not reader.codes:
            raise DataContractError('listed source must contain a stock axis')
        # This exporter reuses the already-complete calendar rather than extending it.
        # Matching every calendar day is sufficient; non-trading bounds need no HDF rows.
        days = sorted(day for day in reader.dates if start <= day <= end)
        if days != calendar_days:
            raise DataContractError('listed source date axis differs from the existing calendar')
        audit['source'].update(reader.info)
        audit['source'].update(stock_axis_count=len(reader.codes), source_first_date=min(reader.dates),
                               source_last_date=max(reader.dates))
        audit['calendar'] = dict(**_coverage(calendar), complete_from=start, complete_through=end)
        audit['scope_codes_absent_from_listed_axis'] = len(set(store.codes) - set(reader.codes))
        audit['listed_codes_outside_scope'] = len(set(reader.codes) - set(store.codes))
        price_parts, listed_parts, original_listed_parts = [], [], []
        with tempfile.TemporaryDirectory(prefix='.rankic-listed-', dir=destination.parent) as temporary:
            staging = Path(temporary) / 'data'
            staging.mkdir()
            _copy_original(store, staging, store.calendar)
            market_entries = []
            for year in years:
                listed = _listed_year(reader, [day for day in days if day.startswith(str(year) + '-')])
                price = pd.DataFrame({'date': pd.Series(dtype='datetime64[ns]'),
                                      'stock_code': pd.Series(dtype='string'),
                                      'adj_close': pd.Series(dtype='float64')})
                overlap_rows = 0
                if year in old_by_year:
                    old = _read_panel(store, old_by_year[year], 'listed')
                    if not old.date.isin(calendar.date).all():
                        raise DataContractError('original market dates are outside the calendar')
                    price = old.loc[old.price_present, KEYS + ['adj_close']].copy()
                    original_listed = old.loc[old.listed_present, KEYS + ['listed']].copy()
                    _check_listed_overlap(original_listed, listed)
                    original_listed_parts.append(_coverage(original_listed))
                    overlap_rows = len(original_listed)
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
                audit['years'].append(dict(year=year, dates=int(listed.date.nunique()),
                                            price_rows=len(price), listed_rows=len(listed), market_rows=len(market),
                                            listed_zero=int(listed.listed.eq(0).sum()),
                                            listed_one=int(listed.listed.eq(1).sum()),
                                            listed_unknown=int(listed.listed.isna().sum()),
                                            original_listed_overlap_rows=overlap_rows,
                                            price_roundtrip_exact=True, listed_roundtrip_exact=True,
                                            presence_roundtrip_exact=True, file=entry))
                if progress:
                    progress(dict(year=year, listed_rows=len(listed), bytes=entry['bytes'], verified=True))
            if _combined_coverage(price_parts) != store.price_coverage:
                raise DataContractError('original price role coverage mismatch')
            if _combined_coverage(original_listed_parts) != store.listed_coverage:
                raise DataContractError('original listed role coverage mismatch')
            factor_fingerprints = []
            for factor in store.factors:
                for entry in factor['files']:
                    if not entry['path'].startswith('factors/'):
                        raise DataContractError('factor paths must stay under factors/')
                    _read_panel(store, entry, 'factor')
                    _copy_original(store, staging, entry)
                    factor_fingerprints.append(dict(factor_id=factor['id'], **entry))
            audit.update(price_coverage=store.price_coverage, listed_coverage=_combined_coverage(listed_parts),
                         original_prices_unchanged=True, original_listed_overlap_exact=True,
                         calendar_file_unchanged=True, factor_files_unchanged=True,
                         factor_files=factor_fingerprints)
            reader.assert_unchanged()
            sources.assert_unchanged()
            if _hash_file(source_path) != source_info['sha256']:
                raise DataContractError('source HDF snapshot changed during export')
            sources.assert_unchanged()
            audit['source_files_unchanged'] = True
            provenance = dict(schema_version=1, origin='local_reference',
                              description='Expanded local historical listed reference, not a server export.',
                              original_snapshot=original_provenance, listed_extension=audit,
                              limitations=['Factor coverage was NOT expanded.',
                                           'Missing source keys and present-null listed values remain unknown.',
                                           'Calendar is the unchanged local price date axis, not independently exchange-verified.',
                                           'Adjusted-close semantics and full historical universe remain unverified; no server authorization claim.'])
            provenance_file = staging / 'provenance.json'
            provenance_file.write_text(json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
            catalog = copy.deepcopy(store.catalog)
            catalog['snapshot_id'] = 'local-market-' + uuid.uuid4().hex
            catalog['market'].update(files=market_entries, listed_coverage=audit['listed_coverage'])
            catalog['provenance'] = dict(path='provenance.json', bytes=provenance_file.stat().st_size,
                                        sha256=_hash_file(provenance_file))
            (staging / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
            Store(staging).assert_unchanged()
            sources.assert_unchanged()
            if destination.exists() or destination.is_symlink():
                raise FileExistsError('destination appeared during export; refusing overwrite')
            staging.rename(destination)  # Windows rename cannot replace an existing destination.
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
    args = parser.parse_args()
    result = export_reference_listed(args.source_hdf5, args.data_root, args.output, args.start, args.end,
                                     date_encoding=args.date_encoding, timezone=args.timezone,
                                     progress=lambda row: print(json.dumps(row), file=sys.stderr, flush=True))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()

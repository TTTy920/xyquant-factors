#!/usr/bin/env python3
"""One-way migration of explicit LOCAL reference Parquet samples, never server data."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import tempfile

import pandas as pd

from rankic_engine import FREQUENCIES, Parameters, calculate, normalize_code
from rankic_inputs import DataContractError, SourceFiles, _canonical_date, _hash_file
from rankic_store import Store, _identifier


def _coverage(frame):
    return dict(rows=len(frame), first_date=frame['date'].min().strftime('%Y-%m-%d') if len(frame) else None,
                last_date=frame['date'].max().strftime('%Y-%m-%d') if len(frame) else None)


def _write_panel(root, relative, frame, year=None):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = frame.sort_values(['date'] + (['stock_code'] if 'stock_code' in frame else [])).reset_index(drop=True)
    stored = ordered.copy()
    if 'stock_code' in stored:
        stored['stock_code'] = stored['stock_code'].astype('category')
    group_size = int(stored.groupby('date', observed=True).size().max())
    stored.to_parquet(path, index=False, compression='zstd', row_group_size=group_size)
    info = dict(path=relative, bytes=path.stat().st_size, sha256=_hash_file(path), **_coverage(ordered))
    if year is not None:
        info['year'] = int(year)
    return info


def _same_role(expected, actual, value=None):
    columns = ['date'] + (['stock_code', value] if value else [])
    def normalized(frame):
        result = frame[columns].copy()
        if value:
            result['stock_code'] = result['stock_code'].astype('string')
        return result.sort_values(columns[:2] if value else ['date']).reset_index(drop=True)
    pd.testing.assert_frame_equal(normalized(expected), normalized(actual), check_dtype=False,
                                  check_categorical=False, check_exact=True)


def normalize_sample(config_path, destination, factor_id, *, provenance_path=None, baseline_path=None):
    config_path, destination = Path(config_path).absolute(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('sample destination already exists; refusing overwrite')
    _identifier(factor_id)
    sources = SourceFiles()
    sources.verify(config_path)
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    data = config['data']
    if data.get('origin') != 'local_reference':
        raise DataContractError('only an explicitly local reference may be migrated; never relabel server data')
    if data.get('price', {}).get('adjustment') != 'adjusted_close':
        raise DataContractError('source price must explicitly declare adjusted_close; never relabel returns or raw prices')
    if baseline_path is None:
        raise DataContractError('four-frequency reference baseline required before sample publication')
    baseline_path = Path(baseline_path).absolute()
    sources.verify(baseline_path)
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    fields = ('status', 'summary', 'periods', 'return_range')
    if not isinstance(baseline, dict) or any(
            not isinstance(baseline.get(frequency), dict) or not set(fields).issubset(baseline[frequency])
            for frequency in FREQUENCIES):
        raise DataContractError('reference baseline must contain all four frequencies and result fields')
    frames, fingerprints = {}, {}
    for role in ('calendar', 'factor', 'price', 'listed'):
        spec = data[role]
        if spec.get('format') != 'parquet' or not spec.get('sha256'):
            raise DataContractError('sample migration requires hashed Parquet inputs, not HDF5 or guessed schemas')
        path = config_path.parent / spec['path']
        fingerprints[role] = sources.verify(path, spec['sha256'])
        columns = ['date'] + (['stock_code', {'factor': 'value', 'price': 'adj_close', 'listed': 'listed'}[role]] if role != 'calendar' else [])
        frame = pd.read_parquet(path, columns=columns)
        frame['date'] = pd.to_datetime(frame['date'].map(_canonical_date)).astype('datetime64[ns]')
        if role != 'calendar':
            frame['stock_code'] = frame['stock_code'].map(normalize_code).astype('string')
            value = columns[-1]
            if role == 'listed':
                if not frame[value].dropna().isin([0, 1]).all():
                    raise DataContractError('historical listed data must be binary or unknown')
                frame[value] = frame[value].astype('Int8')
            else:
                frame[value] = pd.to_numeric(frame[value], errors='raise').astype('float64')
        if frame.duplicated(['date'] + (['stock_code'] if role != 'calendar' else [])).any():
            raise DataContractError('duplicate sample date/security keys')
        frames[role] = frame
    original_provenance = None
    if provenance_path is not None:
        provenance_path = Path(provenance_path).absolute()
        sources.verify(provenance_path)
        original_provenance = json.loads(provenance_path.read_text(encoding='utf-8-sig'))
    price = frames['price'].assign(price_present=True)
    listed = frames['listed'].assign(listed_present=True)
    market = price.merge(listed, on=['date', 'stock_code'], how='outer', validate='one_to_one')
    market['price_present'] = market['price_present'].eq(True)
    market['listed_present'] = market['listed_present'].eq(True)
    market['adj_close'] = market['adj_close'].astype('float64')
    market['listed'] = market['listed'].astype('Int8')
    market = market[['date', 'stock_code', 'adj_close', 'listed', 'price_present', 'listed_present']]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.rankic-normalize-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'data'
        staging.mkdir()
        calendar = _write_panel(staging, 'calendar.pqt', frames['calendar'])
        calendar.update({k: data['calendar'][k] for k in ('complete_from', 'complete_through')})
        market_files = [_write_panel(staging, f'market/{year}.pqt', group, year)
                        for year, group in market.groupby(market['date'].dt.year)]
        factor_files = [_write_panel(staging, f'factors/{factor_id}/{year}.pqt', group, year)
                        for year, group in frames['factor'].groupby(frames['factor']['date'].dt.year)]
        catalog = dict(schema_version=1, dataset='rankic', origin='local_reference', snapshot_id=data['snapshot_id'],
                       adjustment_id=data['adjustment_id'], scope=dict(kind='local_reference', codes=data['authorized_codes']),
                       calendar=calendar, market=dict(files=market_files, adjustment='adjusted_close',
                       price_through=data['price']['price_through'], price_coverage=_coverage(frames['price']),
                       listed_coverage=_coverage(frames['listed'])),
                       factors=[dict(id=factor_id, name=data['factor_name'], aliases=[], files=factor_files)])
        # Compare complete role keys, all non-null values and missing positions after disk round-trip.
        reread = pd.concat([pd.read_parquet(staging / item['path']) for item in market_files], ignore_index=True)
        factor = pd.concat([pd.read_parquet(staging / item['path']) for item in factor_files], ignore_index=True)
        _same_role(frames['price'], reread[reread['price_present']], 'adj_close')
        _same_role(frames['listed'], reread[reread['listed_present']], 'listed')
        _same_role(frames['factor'], factor, 'value')
        _same_role(frames['calendar'], pd.read_parquet(staging / 'calendar.pqt'))
        audit = dict(roundtrip={role: True for role in frames}, old_file_fingerprints=fingerprints,
                     role_coverage={role: _coverage(frame) for role, frame in frames.items()},
                     source_files_unchanged=True, baseline_exact_match={})
        provenance = dict(schema_version=1, origin='local_reference',
                          description='Normalized local reference, not MCP-exported or verified identical to server factor.',
                          original_sample=original_provenance, normalization=audit,
                          limitations=['Not server authorization or server-to-report acceptance.',
                                       'Unknown historical membership is preserved, never filled as unlisted.',
                                       'Calendar and adjustment semantics remain local-reference-only.'])
        (staging / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        store = Store(staging)
        parameters = Parameters(**config['parameters'])
        for frequency in FREQUENCIES:
            p = replace(parameters, frequency=frequency)
            with store.bundle(p) as source:
                result = calculate(p, source.calendar, source, calendar_through=source.calendar_through,
                                   price_through=source.price_through)
                source.assert_unchanged()
            expected = {key: baseline[frequency][key] for key in fields}
            actual = {key: result[key] for key in fields}
            if actual != expected:
                raise DataContractError('sample migration differs from reference RankIC baseline')
            audit['baseline_exact_match'][frequency] = True
        sources.assert_unchanged()
        provenance_file = staging / 'provenance.json'
        provenance_file.write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        catalog['provenance'] = dict(path='provenance.json', bytes=provenance_file.stat().st_size,
                                     sha256=_hash_file(provenance_file))
        (staging / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        Store(staging).assert_unchanged()
        staging.rename(destination)
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-config', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--factor-id', required=True)
    parser.add_argument('--provenance', type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    args = parser.parse_args()
    audit = normalize_sample(args.source_config, args.destination, args.factor_id,
                             provenance_path=args.provenance, baseline_path=args.baseline)
    print(json.dumps(audit, ensure_ascii=True, allow_nan=False))


if __name__ == '__main__':
    main()

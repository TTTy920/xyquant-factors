"""Read-only source adapters and fail-closed data admission."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import h5py
import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from rankic_engine import Parameters, calculate
from rankic_inputs import DataBundle

CODES = ['000001.SZ', '000002.SZ', '600000.SH']
DATES = ['2025-01-03', '2025-01-10']


def write_hdf(path, dates=DATES, codes=CODES, values=None):
    timestamps = pd.DatetimeIndex(dates).tz_localize('Asia/Shanghai').asi8 / 1e9
    with h5py.File(path, 'w') as f:
        f.create_dataset('DateTime', data=timestamps)
        f.create_dataset('ID', data=np.array(codes, dtype=h5py.string_dtype('utf-8')))
        f.create_dataset('Data', data=values if values is not None else [[1, 2, 3], [4, 5, 6]])


def make_fixture(root, fmt='csv'):
    calendar = pd.DataFrame({'date': DATES})
    factor = pd.DataFrame({'date': [DATES[0]] * 3, 'stock_code': CODES, 'value': [1, 2, 3]})
    price = pd.DataFrame({'date': [DATES[0]] * 3 + [DATES[1]] * 3, 'stock_code': CODES * 2,
                          'adj_close': [10, 10, 10, 11, 12, 13]})
    listed = pd.DataFrame({'date': [DATES[0]] * 3, 'stock_code': CODES, 'listed': [1, 1, 1]})
    data = dict(origin='local_reference', snapshot_id='synthetic-fixture', authorized_codes=CODES,
                verified={}, factor_name='test', adjustment_id='same-adjusted-close-snapshot')
    for role, frame in [('calendar', calendar), ('factor', factor), ('price', price), ('listed', listed)]:
        path = root / f'{role}.{fmt}'
        if fmt == 'csv':
            frame.to_csv(path, index=False)
        else:
            frame.to_parquet(path, index=False)
        data[role] = dict(path=path.name, format=fmt)
    data['calendar'].update(complete_from='2025-01-01', complete_through='2025-01-12')
    data['price'].update(price_through=DATES[1], adjustment='adjusted_close')
    return dict(parameters=dict(factor_name='test', start=DATES[0], end=DATES[0], frequency='weekly',
                                pool='all_a', min_stocks=3), data=data)


class InputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_fixture(self.root)
        self.p = Parameters(**self.config['parameters'])

    def tearDown(self):
        self.tmp.cleanup()

    def bundle(self, config=None):
        return DataBundle((config or self.config)['data'], self.p, self.root)

    def test_csv_and_parquet_align_by_code(self):
        for fmt in ['csv', 'parquet']:
            config = make_fixture(self.root, fmt)
            with self.bundle(config) as source:
                result = calculate(self.p, source.calendar, source,
                                   calendar_through=source.calendar_through, price_through=source.price_through)
                self.assertAlmostEqual(result['periods'][0]['rank_ic'], 1)
                self.assertEqual(source.metadata()['origin'], 'local_reference')
                self.assertIn('sha256', source.metadata()['sources']['factor'])

    def test_parquet_skips_unneeded_date_row_groups(self):
        import pyarrow.parquet as pq
        original = pq.ParquetFile
        for date_type in ['string', 'date', 'datetime']:
            for statistics in [True, False]:
                config = make_fixture(self.root, 'parquet')
                path = self.root / config['data']['factor']['path']
                frame = pd.read_parquet(path).rename(columns={'date': 'as_of'})
                frame = pd.concat([frame.assign(as_of='2030-01-03'), frame,
                                   frame.assign(as_of='2010-01-03')], ignore_index=True)
                if date_type != 'string':
                    frame['as_of'] = pd.to_datetime(frame['as_of'])
                    if date_type == 'date':
                        frame['as_of'] = frame['as_of'].dt.date
                frame.to_parquet(path, index=False, row_group_size=3, write_statistics=statistics)
                config['data']['factor']['columns'] = {'date': 'as_of'}
                reads = []
                class ObservedParquet:
                    def __init__(self, filename):
                        self.delegate = original(filename)
                        self.observe = Path(filename) == path

                    def __getattr__(self, name):
                        return getattr(self.delegate, name)

                    def iter_batches(self, **kwargs):
                        for batch in self.delegate.iter_batches(**kwargs):
                            if self.observe:
                                reads.append(batch.num_rows)
                            yield batch
                with self.subTest(date_type=date_type, statistics=statistics):
                    with patch.object(pq, 'ParquetFile', ObservedParquet), self.bundle(config) as source:
                        self.assertEqual(source.factor_at(DATES[0]).tolist(), [1, 2, 3])
                        self.assertEqual(source.metadata()['sources']['factor']['sha256'],
                                         hashlib.sha256(path.read_bytes()).hexdigest())
                    self.assertEqual(sum(reads), 3 if statistics else 9)

    def test_hdf_timezone_sliced_rows_and_reordered_codes(self):
        path = self.root / 'source.hdf5'
        write_hdf(path, codes=CODES[::-1], values=[[3, 2, 1], [6, 5, 4]])
        self.config['data']['factor'] = dict(path=path.name, format='hdf5', date_encoding='unix_seconds', timezone='UTC+08:00')
        with self.bundle() as source:
            # Disallow entire-panel reads; only one selected row may be accessed.
            original = h5py.Dataset.__getitem__
            def bounded(dataset, key):
                if dataset.name == '/Data':
                    self.assertIsInstance(key, tuple)
                    self.assertIsInstance(key[0], (int, np.integer))
                return original(dataset, key)
            with patch.object(h5py.Dataset, '__getitem__', bounded):
                values = source.factor_at(DATES[0])
            self.assertEqual(values['000001.SZ'], 1)
            self.assertEqual(values['600000.SH'], 3)
            self.assertIn('selected_rows_sha256', source.metadata()['sources']['factor'])
        with h5py.File(path, 'r') as f:
            self.assertEqual(f['Data'][0, 0], 3)

    def test_hdf_duplicate_axes_and_unknown_encoding_rejected(self):
        for dates, codes in [(DATES * 2, CODES), (DATES, [CODES[0]] * 3)]:
            path = self.root / 'bad.hdf5'
            write_hdf(path, dates=dates, codes=codes, values=np.ones((len(dates), len(codes))))
            self.config['data']['factor'] = dict(path=path.name, format='hdf5', date_encoding='unix_seconds', timezone='UTC+08:00')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                self.bundle()
        self.config['data']['factor'].pop('date_encoding')
        with self.assertRaisesRegex(ValueError, 'encoding'):
            self.bundle()

    def test_duplicate_keys_are_errors_even_if_values_agree(self):
        path = self.root / 'factor.csv'
        frame = pd.read_csv(path)
        pd.concat([frame, frame]).to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.bundle()

    def test_price_definition_and_factor_identity_required(self):
        for field, value in [('adjustment_id', ''), ('factor_name', 'different')]:
            config = json.loads(json.dumps(self.config))
            config['data'][field] = value
            with self.assertRaises(ValueError):
                self.bundle(config)
        self.config['data']['price']['adjustment'] = 'daily_return'
        with self.assertRaisesRegex(ValueError, 'adjusted_close'):
            self.bundle()

    def test_server_contract_not_verified_is_blocked_before_read(self):
        self.config['data']['origin'] = 'xyquant_server'
        self.config['data']['factor']['path'] = 'does-not-exist.csv'
        with self.assertRaisesRegex(ValueError, 'verified'):
            self.bundle()

    def test_self_asserted_server_flags_cannot_enable_pending_integration(self):
        data = self.config['data']
        data.update(origin='xyquant_server', verification_reference='self-asserted',
                    verified=dict(authorization=True, adjusted_close=True, calendar=True, historical_universe=True))
        for role in ['calendar', 'factor', 'price', 'listed']:
            data[role]['sha256'] = hashlib.sha256((self.root / data[role]['path']).read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'verified'):
            self.bundle()

    def test_free_text_identifiers_cannot_publish_ticket_urls(self):
        for key in ['snapshot_id', 'adjustment_id']:
            config = json.loads(json.dumps(self.config))
            config['data'][key] = 'https://invalid.example/file?token=PRIVATE'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'identifier'):
                self.bundle(config)

    def test_hdf_nonstock_axis_labels_require_explicit_exclusion(self):
        path = self.root / 'mixed.hdf5'
        write_hdf(path, codes=CODES + ['T00018.SH'], values=np.array([[1, 2, 3, 99], [4, 5, 6, 99]]))
        spec = dict(path=path.name, format='hdf5', date_encoding='unix_seconds', timezone='UTC+08:00')
        self.config['data']['factor'] = spec
        with self.assertRaises(ValueError):
            self.bundle()
        spec['ignore_non_stock_labels'] = True
        with self.bundle() as source:
            self.assertEqual(len(source.factor_at(DATES[0])), 3)
            self.assertEqual(source.metadata()['sources']['factor']['ignored_non_stock_columns'], 1)
            self.assertTrue(any('non-stock' in item for item in source.warnings))

    def test_optional_source_hashes_are_checked(self):
        data = self.config['data']
        for role in ['calendar', 'factor', 'price', 'listed']:
            data[role]['sha256'] = hashlib.sha256((self.root / data[role]['path']).read_bytes()).hexdigest()
        with self.bundle() as source:
            self.assertEqual(source.metadata()['origin'], 'local_reference')
        data['factor']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.bundle()

    def test_missing_history_calendar_gaps_and_snapshot_mutation(self):
        config = json.loads(json.dumps(self.config))
        del config['data']['listed']
        with self.assertRaisesRegex(ValueError, 'historical'):
            self.bundle(config)
        config = json.loads(json.dumps(self.config))
        config['data']['calendar']['complete_from'] = '2025-01-04'
        with self.assertRaisesRegex(ValueError, 'calendar'):
            self.bundle(config)
        with self.bundle() as source:
            with (self.root / 'factor.csv').open('a') as handle:
                handle.write('\n')
            with self.assertRaisesRegex(ValueError, 'changed'):
                source.assert_unchanged()

    def test_bad_schema_and_nonbinary_history_rejected(self):
        (self.root / 'factor.csv').write_text('date,stock_code,not_value\n2025-01-03,000001.SZ,1\n')
        with self.assertRaisesRegex(ValueError, 'columns'):
            self.bundle()
        self.config = make_fixture(self.root)
        frame = pd.read_csv(self.root / 'listed.csv')
        frame.loc[0, 'listed'] = 2
        frame.to_csv(self.root / 'listed.csv', index=False)
        with self.bundle() as source, self.assertRaisesRegex(ValueError, 'listed'):
            source.listed_at(DATES[0])


if __name__ == '__main__':
    unittest.main()

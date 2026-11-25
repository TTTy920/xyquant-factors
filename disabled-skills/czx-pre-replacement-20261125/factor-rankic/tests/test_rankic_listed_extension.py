"""Lossless local HDF membership expansion on an unchanged price/calendar snapshot."""
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

from test_rankic_store import CODES, descriptor, make_catalog, save_catalog

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from rankic_inputs import DataContractError
from rankic_store import Store

if (SCRIPTS / 'export_reference_listed.py').exists():
    import export_reference_listed as exporter
else:
    exporter = None


class ListedExtensionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old, self.output = self.root / 'original', self.root / 'expanded'
        self.days = ['2020-01-02', '2024-02-29', '2025-01-03', '2025-01-10', '2026-10-30']
        self.catalog = make_catalog(self.old, self.days, '2020-01-01', '2026-10-31')
        self.source = self.root / 'listed.hdf5'
        self.axis_days = ['2019-12-31', *self.days, '2026-11-02']
        self.labels = [CODES[2], CODES[0], CODES[1]]
        self.values = np.array([[0., 1., np.nan], [1., 1., 1.], [0., 1., np.nan],
                                [1., 0., 1.], [np.nan, 1., 0.], [1., 1., 0.], [0., 0., 1.]])
        self.write_hdf()

    def tearDown(self):
        self.tmp.cleanup()

    def write_hdf(self):
        timestamps = pd.DatetimeIndex(self.axis_days).tz_localize('Asia/Shanghai').tz_convert('UTC').asi8 / 1e9
        with h5py.File(self.source, 'w') as handle:
            handle.create_dataset('DateTime', data=timestamps)
            handle.create_dataset('ID', data=self.labels, dtype=h5py.string_dtype('utf-8'))
            handle.create_dataset('Data', data=self.values)

    def run_export(self, **kwargs):
        self.assertIsNotNone(exporter, 'local HDF listed extension is not implemented')
        options = dict(date_encoding='unix_seconds', timezone='UTC+08:00')
        options.update(kwargs)
        return exporter.export_reference_listed(self.source, self.old, self.output,
                                                '2020-01-01', '2026-10-31', **options)

    def mutate_market(self, year, transform):
        index = next(i for i, e in enumerate(self.catalog['market']['files']) if e['year'] == year)
        entry = self.catalog['market']['files'][index]
        frame = transform(pd.read_parquet(self.old / entry['path']))
        self.catalog['market']['files'][index] = descriptor(self.old, entry['path'], frame, year)
        save_catalog(self.old, self.catalog)

    def test_expands_listed_without_changing_other_roles_or_original_files(self):
        original = {p.relative_to(self.old): p.read_bytes() for p in self.old.rglob('*') if p.is_file()}
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        audit = self.run_export()
        store = Store(self.output)
        self.assertEqual(store.calendar, self.catalog['calendar'])
        self.assertEqual(store.price_coverage, self.catalog['market']['price_coverage'])
        self.assertEqual(store.market['price_through'], self.catalog['market']['price_through'])
        self.assertEqual(store.catalog['scope'], self.catalog['scope'])
        self.assertEqual(store.catalog['factors'], self.catalog['factors'])
        self.assertEqual(store.catalog['adjustment_id'], self.catalog['adjustment_id'])
        self.assertNotEqual(store.catalog['snapshot_id'], self.catalog['snapshot_id'])
        self.assertEqual(store.listed_coverage, dict(rows=15, first_date=self.days[0], last_date=self.days[-1]))
        for relative, contents in original.items():
            self.assertEqual((self.old / relative).read_bytes(), contents)
            if str(relative).startswith(('calendar', 'factors')):
                self.assertEqual((self.output / relative).read_bytes(), contents)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)
        self.assertEqual(audit['source']['sha256'], source_hash)
        self.assertTrue(audit['original_prices_unchanged'])
        self.assertTrue(audit['original_listed_overlap_exact'])
        self.assertTrue(audit['calendar_file_unchanged'])
        self.assertTrue(audit['factor_files_unchanged'])
        self.assertTrue(all(y['listed_roundtrip_exact'] and y['price_roundtrip_exact'] for y in audit['years']))
        self.assertNotIn(str(self.source), (self.output / 'provenance.json').read_text())
        market = pd.read_parquet(self.output / 'market/2024.pqt').set_index('stock_code')
        self.assertTrue(market.listed_present.all())
        self.assertEqual(market.loc[CODES[2], 'listed'], 0)
        self.assertTrue(pd.isna(market.loc[CODES[1], 'listed']))

    def test_listed_only_keys_and_absent_codes_remain_distinct(self):
        self.labels[0] = '000003.SZ'
        def mutate(frame):
            frame.loc[frame.stock_code == CODES[2], ['listed', 'listed_present']] = [pd.NA, False]
            return frame
        self.mutate_market(2020, mutate)
        self.catalog['market']['listed_coverage']['rows'] = 2
        save_catalog(self.old, self.catalog)
        self.write_hdf()
        self.run_export()
        market = pd.read_parquet(self.output / 'market/2024.pqt').set_index('stock_code')
        self.assertFalse(market.loc[CODES[2], 'listed_present'])
        self.assertTrue(pd.isna(market.loc[CODES[2], 'listed']))
        self.assertFalse(market.loc['000003.SZ', 'price_present'])
        self.assertTrue(pd.isna(market.loc['000003.SZ', 'adj_close']))
        self.assertTrue(market.loc['000003.SZ', 'listed_present'])
        self.assertEqual(market.loc['000003.SZ', 'listed'], 0)

    def test_present_null_overlap_must_not_be_filled(self):
        def mutate(frame):
            frame.loc[frame.stock_code == CODES[1], 'listed'] = pd.NA
            return frame
        self.mutate_market(2020, mutate)
        self.values[1, 2] = np.nan
        self.write_hdf()
        self.run_export()
        market = pd.read_parquet(self.output / 'market/2020.pqt').set_index('stock_code')
        self.assertTrue(market.loc[CODES[1], 'listed_present'])
        self.assertTrue(pd.isna(market.loc[CODES[1], 'listed']))

    def test_conflicting_overlap_refuses_publication(self):
        self.values[1, 1] = 0
        self.write_hdf()
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_missing_original_listed_key_refuses_publication(self):
        self.labels[0] = '000003.SZ'
        self.write_hdf()
        with self.assertRaisesRegex(ValueError, 'original listed|overlap'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_nonbinary_and_infinite_source_values_refuse_publication(self):
        for value in (0.5, 2., -1., np.inf, -np.inf):
            with self.subTest(value=value):
                self.values[2, 0] = value
                self.write_hdf()
                with self.assertRaisesRegex(ValueError, 'binary|0/1'):
                    self.run_export()
                self.assertFalse(self.output.exists())

    def test_missing_or_extra_source_date_rejects_calendar_mismatch(self):
        for replacement in ('2024-03-01', '2024-02-28'):
            with self.subTest(replacement=replacement):
                self.axis_days[2] = replacement
                self.write_hdf()
                with self.assertRaisesRegex(ValueError, 'calendar|date'):
                    self.run_export()
                self.assertFalse(self.output.exists())

    def test_wrong_encoding_timezone_duplicate_or_invalid_axis_is_rejected(self):
        for options in (dict(date_encoding='guess'), dict(timezone='UTC')):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.run_export(**options)
        for labels in ([CODES[0], CODES[0], CODES[1]], [CODES[0], CODES[1], 'T00018.SH']):
            with self.subTest(labels=labels):
                self.labels = labels
                self.write_hdf()
                with self.assertRaises(ValueError):
                    self.run_export()
        self.assertFalse(self.output.exists())

    def test_existing_destination_is_not_overwritten(self):
        self.output.mkdir()
        marker = self.output / 'keep.txt'
        marker.write_text('keep')
        with self.assertRaises(FileExistsError):
            self.run_export()
        self.assertEqual(marker.read_text(), 'keep')

    def test_source_hash_or_invalid_presence_is_rejected(self):
        entry = self.catalog['market']['files'][0]
        path = self.old / entry['path']
        original = path.read_bytes()
        path.write_bytes(original + b'changed')
        with self.assertRaisesRegex(ValueError, 'hash|size'):
            self.run_export()
        path.write_bytes(original)
        def mutate(frame):
            frame.loc[0, 'price_present'] = False
            return frame
        self.mutate_market(2020, mutate)
        with self.assertRaisesRegex(ValueError, 'absent role'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_source_mutation_and_roundtrip_failure_do_not_publish(self):
        self.assertIsNotNone(exporter, 'local HDF listed extension is not implemented')
        with patch.object(exporter._HDF, 'assert_unchanged', side_effect=DataContractError('source changed')):
            with self.assertRaisesRegex(ValueError, 'source changed'):
                self.run_export()
        self.assertFalse(self.output.exists())
        with patch.object(exporter, '_same_role', side_effect=AssertionError('roundtrip differs')):
            with self.assertRaises((ValueError, AssertionError)):
                self.run_export()
        self.assertFalse(self.output.exists())

    def test_source_only_needs_existing_calendar_days_not_nontrading_boundaries(self):
        self.axis_days = self.days.copy()
        self.values = self.values[1:-1]
        self.write_hdf()
        self.run_export()
        self.assertEqual(Store(self.output).calendar, self.catalog['calendar'])

    def test_calendar_path_cannot_be_overwritten_by_new_market_shard(self):
        old_calendar = self.old / self.catalog['calendar']['path']
        entry = self.catalog['market']['files'][0]
        old_market = self.old / entry['path']
        moved = self.old / 'old-market-2020.pqt'
        old_market.rename(moved)
        entry['path'] = moved.name
        old_calendar.rename(old_market)
        self.catalog['calendar']['path'] = 'market/2020.pqt'
        save_catalog(self.old, self.catalog)
        with self.assertRaisesRegex(ValueError, 'path|collision|overwrite'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_server_origin_and_range_outside_calendar_are_rejected(self):
        self.catalog['origin'] = 'xyquant_server'
        save_catalog(self.old, self.catalog)
        with self.assertRaisesRegex(ValueError, 'server'):
            self.run_export()
        self.catalog['origin'] = 'local_reference'
        save_catalog(self.old, self.catalog)
        self.assertIsNotNone(exporter, 'local HDF listed extension is not implemented')
        with self.assertRaisesRegex(ValueError, 'calendar|range'):
            exporter.export_reference_listed(self.source, self.old, self.output, '2020-01-02', '2026-10-31',
                                             date_encoding='unix_seconds', timezone='UTC+08:00')
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()

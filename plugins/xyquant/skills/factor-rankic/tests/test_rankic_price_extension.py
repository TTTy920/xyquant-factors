"""Explicit local HDF price/calendar expansion, with lossless existing roles."""
import copy
import hashlib
import json
import os
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

if (SCRIPTS / 'export_reference_prices.py').exists():
    import export_reference_prices as exporter
else:
    exporter = None


class PriceExtensionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = self.root / 'original'
        self.catalog = make_catalog(self.old)
        self.source = self.root / 'prices.hdf5'
        self.output = self.root / 'expanded'
        self.dates = ['2019-12-31', '2020-01-02', '2024-02-29', '2025-01-03',
                      '2025-01-10', '2026-10-30', '2026-11-02']
        self.values = np.array([[9., 9., 9.], [5., np.nan, 0.], [np.inf, -1., 8.],
                                [10., 10., 10.], [11., 12., 13.], [20., 21., 22.],
                                [30., 31., 32.]])
        self.write_hdf()

    def tearDown(self):
        self.tmp.cleanup()

    def write_hdf(self, labels=None):
        labels = labels or [CODES[2], CODES[0], CODES[1], 'T00018.SH']
        timestamps = pd.DatetimeIndex(self.dates).tz_localize('Asia/Shanghai').tz_convert('UTC').asi8 / 1e9
        with h5py.File(self.source, 'w') as handle:
            handle.create_dataset('DateTime', data=timestamps)
            handle.create_dataset('ID', data=labels, dtype=h5py.string_dtype('utf-8'))
            handle.create_dataset('Data', data=np.column_stack([self.values[:, 2], self.values[:, 0],
                                                               self.values[:, 1], np.arange(len(self.dates))]))

    def run_export(self, **kwargs):
        self.assertIsNotNone(exporter, 'local HDF price extension is not implemented')
        options = dict(date_encoding='unix_seconds', timezone='UTC+08:00', excluded_labels=['T00018.SH'])
        options.update(kwargs)
        return exporter.export_reference_prices(self.source, self.old, self.output,
                                                 '2020-01-01', '2026-10-31', **options)

    def mutate_market(self, transform):
        entry = self.catalog['market']['files'][0]
        frame = transform(pd.read_parquet(self.old / entry['path']))
        self.catalog['market']['files'][0] = descriptor(self.old, entry['path'], frame, entry['year'])
        save_catalog(self.old, self.catalog)

    def test_boundary_years_values_and_original_roles_are_preserved(self):
        original = {p.relative_to(self.old): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.old.rglob('*') if p.is_file()}
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        audit = self.run_export()
        store = Store(self.output)
        self.assertEqual(store.catalog['scope'], self.catalog['scope'])
        self.assertEqual(store.catalog['factors'], self.catalog['factors'])
        self.assertNotEqual(store.catalog['snapshot_id'], self.catalog['snapshot_id'])
        self.assertEqual(store.catalog['adjustment_id'], self.catalog['adjustment_id'])
        self.assertEqual(store.market['price_through'], '2026-10-30')
        self.assertEqual(store.calendar['complete_through'], '2026-10-31')
        calendar = pd.read_parquet(self.output / 'calendar.pqt')
        self.assertEqual(calendar.date.dt.strftime('%Y-%m-%d').tolist(), self.dates[1:-1])
        self.assertEqual(store.price_coverage['rows'], 15)
        self.assertEqual(store.listed_coverage, self.catalog['market']['listed_coverage'])
        self.assertEqual([f['year'] for f in store.market['files']], [2020, 2024, 2025, 2026])
        market = pd.concat([pd.read_parquet(self.output / item['path']) for item in store.market['files']])
        for year in (2020, 2024, 2026):
            group = market[market.date.dt.year == year]
            self.assertTrue(group.price_present.all())
            self.assertFalse(group.listed_present.any())
            self.assertTrue(group.listed.isna().all())
        indexed = market.set_index(['date', 'stock_code'])
        self.assertTrue(pd.isna(indexed.loc[(pd.Timestamp('2020-01-02'), CODES[1]), 'adj_close']))
        self.assertTrue(np.isposinf(indexed.loc[(pd.Timestamp('2024-02-29'), CODES[0]), 'adj_close']))
        self.assertEqual(indexed.loc[(pd.Timestamp('2024-02-29'), CODES[1]), 'adj_close'], -1.)
        self.assertEqual(indexed.loc[(pd.Timestamp('2020-01-02'), CODES[2]), 'adj_close'], 0.)
        for relative, digest in original.items():
            self.assertEqual(hashlib.sha256((self.old / relative).read_bytes()).hexdigest(), digest)
        factor = self.catalog['factors'][0]['files'][0]['path']
        self.assertEqual((self.old / factor).read_bytes(), (self.output / factor).read_bytes())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)
        self.assertTrue(all(item['price_roundtrip_exact'] for item in audit['years']))
        self.assertEqual(audit['source']['sha256'], source_hash)
        self.assertEqual(audit['excluded_labels'], ['T00018.SH'])
        self.assertEqual(audit['calendar']['rows'], 5)
        self.assertTrue(audit['original_listed_unchanged'])
        self.assertTrue(audit['original_prices_unchanged'])
        self.assertTrue(audit['factor_files_unchanged'])
        self.assertFalse(audit['server_to_report_verified'])
        self.assertNotIn(str(self.source), (self.output / 'provenance.json').read_text())

    def test_present_null_and_listed_only_key_survive_outer_join(self):
        def mutate(frame):
            frame.loc[0, 'listed'] = pd.NA
            extra = frame.iloc[[0]].copy()
            extra['stock_code'] = '000003.SZ'
            extra['adj_close'] = np.nan
            extra['price_present'] = False
            extra['listed'] = pd.array([1], dtype='Int8')
            return pd.concat([frame, extra], ignore_index=True)
        self.mutate_market(mutate)
        self.catalog['market']['listed_coverage']['rows'] = 4
        save_catalog(self.old, self.catalog)
        self.run_export()
        market = pd.read_parquet(self.output / 'market/2025.pqt')
        self.assertEqual(int(market.listed_present.sum()), 4)
        row = market[market.stock_code == '000003.SZ'].iloc[0]
        self.assertTrue(row.listed_present)
        self.assertFalse(row.price_present)
        self.assertTrue(pd.isna(row.adj_close))
        self.assertEqual(row.listed, 1)
        self.assertTrue(pd.isna(market[(market.date == pd.Timestamp('2025-01-03')) &
                                       (market.stock_code == CODES[0])].iloc[0].listed))

    def test_destination_and_parent_input_are_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / 'keep.txt'
        marker.write_text('keep')
        with self.assertRaises(FileExistsError):
            self.run_export()
        self.assertEqual(marker.read_text(), 'keep')

    def test_conflicting_overlap_refuses_publication(self):
        self.values[3, 0] = 99.
        self.write_hdf()
        with self.assertRaisesRegex(ValueError, 'overlap|original price'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_missing_original_calendar_date_refuses_publication(self):
        self.dates[3] = '2025-01-02'
        self.write_hdf()
        with self.assertRaisesRegex(ValueError, 'calendar'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_scope_mismatch_refuses_publication(self):
        self.write_hdf(labels=[CODES[2], CODES[0], '000004.SZ', 'T00018.SH'])
        with self.assertRaisesRegex(ValueError, 'scope|stock axis'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_nonstock_exclusion_must_be_explicit_and_exact(self):
        for options in (dict(excluded_labels=[]), dict(excluded_labels=['T00018.SH', CODES[0]])):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_export(**options)
            self.assertFalse(self.output.exists())
        self.write_hdf(labels=[CODES[2], CODES[0], CODES[1], 'UNEXPECTED'])
        with self.assertRaisesRegex(ValueError, 'label|exclu'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_wrong_encoding_timezone_and_duplicate_dates_rejected(self):
        for options in (dict(date_encoding='guessed'), dict(timezone='UTC')):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_export(**options)
        self.dates[2] = self.dates[1]
        self.write_hdf()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_input_integrity_and_server_origin_cannot_be_bypassed(self):
        original = copy.deepcopy(self.catalog)
        self.catalog['market']['files'][0]['sha256'] = '0' * 64
        save_catalog(self.old, self.catalog)
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.run_export()
        self.catalog = original
        self.catalog['origin'] = 'xyquant_server'
        save_catalog(self.old, self.catalog)
        with self.assertRaisesRegex(ValueError, 'server'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_invalid_presence_and_duplicate_physical_keys_rejected(self):
        for kind in ('hidden', 'absent', 'duplicate'):
            self.catalog = make_catalog(self.old)
            def mutate(frame):
                if kind == 'hidden':
                    frame.loc[0, 'price_present'] = False
                elif kind == 'absent':
                    frame.loc[0, ['price_present', 'listed_present']] = False
                    frame.loc[0, ['adj_close', 'listed']] = [np.nan, pd.NA]
                else:
                    frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
                return frame
            self.mutate_market(mutate)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.run_export()
            self.assertFalse(self.output.exists())

    def test_source_mutation_during_export_prevents_publication(self):
        self.assertIsNotNone(exporter, 'local HDF price extension is not implemented')
        original = exporter._write_panel
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            info = self.source.stat()
            os.utime(self.source, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
            return result
        with patch.object(exporter, '_write_panel', side_effect=mutate), self.assertRaisesRegex(ValueError, 'snapshot|changed'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_roundtrip_failure_does_not_publish_partial_output(self):
        self.assertIsNotNone(exporter, 'local HDF price extension is not implemented')
        with patch.object(exporter, '_same_role', side_effect=AssertionError('roundtrip mismatch')):
            with self.assertRaises(AssertionError):
                self.run_export()
        self.assertFalse(self.output.exists())

    def test_added_date_inside_original_complete_calendar_is_rejected(self):
        self.dates.insert(4, '2025-01-06')
        self.values = np.insert(self.values, 4, [12., 13., 14.], axis=0)
        self.write_hdf()
        with self.assertRaisesRegex(ValueError, 'calendar'):
            self.run_export()
        self.assertFalse(self.output.exists())

    def test_original_complete_boundaries_cannot_be_shrunk(self):
        self.assertIsNotNone(exporter, 'local HDF price extension is not implemented')
        for i, (start, end) in enumerate((('2025-01-03', '2025-01-12'),
                                         ('2025-01-01', '2025-01-10'))):
            destination = self.root / f'shrunk-{i}'
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, 'calendar'):
                    exporter.export_reference_prices(self.source, self.old, destination, start, end,
                                                      date_encoding='unix_seconds', timezone='UTC+08:00',
                                                      excluded_labels=['T00018.SH'])
                self.assertFalse(destination.exists())

    def test_requested_dates_cannot_exceed_source_axis_coverage(self):
        self.assertIsNotNone(exporter, 'local HDF price extension is not implemented')
        with self.assertRaisesRegex(ValueError, 'coverage|range'):
            exporter.export_reference_prices(self.source, self.old, self.output, '2018-01-01', '2027-01-01',
                                              date_encoding='unix_seconds', timezone='UTC+08:00',
                                              excluded_labels=['T00018.SH'])
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()

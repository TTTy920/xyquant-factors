"""Lossless conversion of explicitly local legacy Parquet sample manifests."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

from test_rankic_inputs import make_fixture
from test_rankic_store import Store

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
if (SCRIPTS / 'prepare_sample.py').exists():
    from prepare_sample import normalize_sample
else:
    normalize_sample = None


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_fixture(self.root, 'parquet')
        for role in ('calendar', 'factor', 'price', 'listed'):
            spec = self.config['data'][role]
            spec['sha256'] = hashlib.sha256((self.root / spec['path']).read_bytes()).hexdigest()
        self.config_path = self.root / 'old-config.json'
        self.destination = self.root / 'canonical'
        from dataclasses import replace
        from rankic_engine import FREQUENCIES, Parameters, calculate
        from rankic_inputs import DataBundle
        baseline = {}
        p = Parameters(**self.config['parameters'])
        for frequency in FREQUENCIES:
            current = replace(p, frequency=frequency)
            with DataBundle(self.config['data'], current, self.root) as source:
                baseline[frequency] = calculate(current, source.calendar, source,
                                                calendar_through=source.calendar_through,
                                                price_through=source.price_through)
        self.baseline_path = self.root / 'reference.json'
        self.baseline_path.write_text(json.dumps(baseline, allow_nan=False), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def run_migration(self):
        self.assertIsNotNone(normalize_sample, 'lossless sample migration is not implemented')
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        return normalize_sample(self.config_path, self.destination, 'TestFactor', baseline_path=self.baseline_path)

    def test_roundtrip_all_role_keys_values_and_nulls_are_preserved(self):
        audit = self.run_migration()
        self.assertTrue(all(audit['roundtrip'].values()))
        store = Store(self.destination)
        self.assertEqual(store.inventory()['listed_coverage']['rows'], 3)
        market = pd.read_parquet(self.destination / 'market/2025.pqt')
        self.assertEqual(str(market['date'].dtype), 'datetime64[ns]')
        self.assertEqual(str(market['adj_close'].dtype), 'float64')
        self.assertEqual(str(market['listed'].dtype), 'Int8')
        self.assertEqual(market['listed_present'].sum(), 3)
        self.assertTrue(market.loc[~market['listed_present'], 'listed'].isna().all())
        for role in ('factor', 'price', 'listed', 'calendar'):
            self.assertEqual(hashlib.sha256((self.root / self.config['data'][role]['path']).read_bytes()).hexdigest(),
                             self.config['data'][role]['sha256'])

    def test_existing_destination_is_never_overwritten(self):
        self.destination.mkdir()
        sentinel = self.destination / 'sentinel.txt'
        sentinel.write_text('keep', encoding='utf-8')
        with self.assertRaises((ValueError, FileExistsError)):
            self.run_migration()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_source_hash_failure_publishes_nothing(self):
        self.config['data']['price']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.run_migration()
        self.assertFalse(self.destination.exists())

    def test_no_baseline_cannot_publish_a_converted_sample(self):
        self.baseline_path = None
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.run_migration()
        self.assertFalse(self.destination.exists())

    def test_incomplete_baseline_cannot_publish_a_converted_sample(self):
        baseline = json.loads(self.baseline_path.read_text())
        del baseline['quarterly']
        self.baseline_path.write_text(json.dumps(baseline), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.run_migration()
        self.assertFalse(self.destination.exists())

    def test_other_price_semantics_are_not_relabelled_adjusted_close(self):
        self.config['data']['price']['adjustment'] = 'daily_return'
        with self.assertRaisesRegex(ValueError, 'adjusted_close'):
            self.run_migration()
        self.assertFalse(self.destination.exists())

    def test_server_or_hdf_source_is_not_relabelled_as_a_local_sample(self):
        for kind in ('server', 'hdf'):
            if kind == 'server':
                self.config['data']['origin'] = 'xyquant_server'
            else:
                self.config['data']['origin'] = 'local_reference'
                self.config['data']['price']['format'] = 'hdf5'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.run_migration()
            self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()

"""Canonical catalog, annual panels and role-presence regression tests."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import pyarrow.parquet as pq

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from rankic_engine import Parameters, calculate

if (SCRIPTS / 'rankic_store.py').exists():
    from rankic_store import Store
else:
    Store = None

CODES = ['000001.SZ', '000002.SZ', '600000.SH']


def descriptor(root, relative, frame, year=None):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False, compression='zstd', row_group_size=3)
    dates = pd.to_datetime(frame['date']).dt.strftime('%Y-%m-%d')
    result = dict(path=relative, bytes=path.stat().st_size,
                  sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  rows=len(frame), first_date=dates.min(), last_date=dates.max())
    if year is not None:
        result['year'] = int(year)
    return result


def save_catalog(root, catalog):
    (root / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')


def make_catalog(root, dates=('2025-01-03', '2025-01-10'), complete_from='2025-01-01', complete_through='2025-01-12'):
    root.mkdir(parents=True, exist_ok=True)
    calendar = pd.DataFrame({'date': pd.to_datetime(list(dates))})
    market = pd.DataFrame({'date': pd.to_datetime([d for d in dates for _ in CODES]),
                           'stock_code': CODES * len(dates),
                           'adj_close': [10.0, 10.0, 10.0] + [11.0, 12.0, 13.0] * (len(dates)-1),
                           'listed': pd.array([1, 1, 1] + [None, None, None] * (len(dates)-1), dtype='Int8'),
                           'price_present': [True] * (3 * len(dates)),
                           'listed_present': [True, True, True] + [False, False, False] * (len(dates)-1)})
    factor = pd.DataFrame({'date': pd.to_datetime([dates[0]] * 3), 'stock_code': CODES,
                           'value': [1.0, 2.0, 3.0]})
    files = [descriptor(root, f'market/{year}.pqt', group, year)
             for year, group in market.groupby(market['date'].dt.year)]
    factor_files = [descriptor(root, f'factors/TestFactor/{year}.pqt', group, year)
                    for year, group in factor.groupby(factor['date'].dt.year)]
    catalog = dict(schema_version=1, dataset='rankic', origin='local_reference',
                   snapshot_id='synthetic-snapshot', adjustment_id='synthetic-adjustment',
                   scope=dict(kind='local_reference', codes=CODES),
                   calendar=descriptor(root, 'calendar.pqt', calendar),
                   market=dict(files=files, adjustment='adjusted_close', price_through=dates[-1],
                               price_coverage=dict(first_date=dates[0], last_date=dates[-1], rows=len(market)),
                               listed_coverage=dict(first_date=dates[0], last_date=dates[0], rows=3)),
                   factors=[dict(id='TestFactor', name='test', aliases=['测例'], files=factor_files)])
    catalog['calendar'].update(complete_from=complete_from, complete_through=complete_through)
    save_catalog(root, catalog)
    return catalog


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.catalog = make_catalog(self.root)
        self.p = Parameters('test', '2025-01-03', '2025-01-03', 'weekly', 'all_a', min_stocks=3)

    def tearDown(self):
        self.tmp.cleanup()

    def store(self):
        self.assertIsNotNone(Store, 'annual catalog store is not implemented')
        return Store(self.root)

    def change_market(self, transform):
        spec = self.catalog['market']['files'][0]
        frame = transform(pd.read_parquet(self.root / spec['path']))
        self.catalog['market']['files'][0] = descriptor(self.root, spec['path'], frame, spec['year'])
        save_catalog(self.root, self.catalog)

    def test_inventory_and_identity_resolution(self):
        store = self.store()
        inventory = store.inventory()
        self.assertEqual(inventory['scope_kind'], 'local_reference')
        self.assertEqual(inventory['scope_stock_count'], 3)
        self.assertFalse(inventory['data_integrity_checked'])
        self.assertEqual(inventory['listed_coverage']['last_date'], '2025-01-03')
        for name in ['test', 'TestFactor', 'testfactor', '测例']:
            self.assertEqual(store.resolve(name)['id'], 'TestFactor')
        with self.assertRaisesRegex(ValueError, 'factor'):
            store.resolve('not-a-factor')

    def test_run_preserves_calculation_and_non_secret_metadata(self):
        store = self.store()
        with store.bundle(self.p) as source:
            result = calculate(self.p, source.calendar, source,
                               calendar_through=source.calendar_through, price_through=source.price_through)
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['periods'][0]['rank_ic'], 1.0)
            self.assertEqual(source.metadata()['scope_kind'], 'local_reference')
            self.assertEqual(source.metadata()['catalog']['factor_id'], 'TestFactor')
            self.assertNotIn(str(self.root), json.dumps(source.metadata()))
            store.assert_unchanged()

    def test_presence_preserves_absent_keys_and_null_values(self):
        def mutate(frame):
            frame.loc[0, 'listed'] = pd.NA  # present key, unknown membership
            frame.loc[1, ['listed_present', 'listed']] = [False, pd.NA]  # absent key
            return frame
        self.change_market(mutate)
        with self.store().bundle(self.p) as source:
            listed = source.listed_at('2025-01-03')
            self.assertTrue(pd.isna(listed[CODES[0]]))
            self.assertNotIn(CODES[1], listed.index)
            self.assertEqual(listed[CODES[2]], 1.0)
            self.assertEqual(len(source.price_at('2025-01-10')), 3)
            with self.assertRaisesRegex(ValueError, 'listed'):
                source.listed_at('2025-01-10')

    def test_entire_listed_date_absent_is_not_manufactured_unknown_rows(self):
        self.change_market(lambda f: f.assign(listed=pd.array([None]*len(f), dtype='Int8'), listed_present=False))
        with self.store().bundle(self.p) as source:
            with self.assertRaisesRegex(ValueError, 'listed'):
                source.listed_at('2025-01-03')

    def test_present_all_null_listed_date_remains_unknown_not_absent(self):
        self.change_market(lambda f: f.assign(listed=pd.array([None]*len(f), dtype='Int8')))
        with self.store().bundle(self.p) as source:
            self.assertEqual(len(source.listed_at('2025-01-03')), 3)
            self.assertTrue(source.listed_at('2025-01-03').isna().all())

    def test_invalid_presence_and_hidden_value_rejected(self):
        for kind in ['nullable', 'nonboolean', 'hidden']:
            self.catalog = make_catalog(self.root)
            def mutate(frame):
                if kind == 'nullable':
                    frame['listed_present'] = pd.array(frame['listed_present'], dtype='boolean')
                    frame.loc[0, 'listed_present'] = pd.NA
                elif kind == 'nonboolean':
                    frame['price_present'] = frame['price_present'].astype('int8')
                else:
                    frame.loc[0, 'listed_present'] = False
                return frame
            self.change_market(mutate)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'presence|present|schema'):
                with self.store().bundle(self.p):
                    pass

    def test_cross_year_exit_reads_both_market_years(self):
        self.catalog = make_catalog(self.root, ('2025-12-31', '2026-01-02'), '2025-12-01', '2026-01-04')
        p = Parameters('test', '2025-12-31', '2025-12-31', 'daily', 'all_a', min_stocks=3)
        with self.store().bundle(p) as source:
            result = calculate(p, source.calendar, source,
                               calendar_through=source.calendar_through, price_through=source.price_through)
            self.assertEqual(result['periods'][0]['exit_date'], '2026-01-02')
            self.assertEqual(result['periods'][0]['rank_ic'], 1.0)
            self.assertEqual(len(source.metadata()['sources']['price']['files']), 2)

    def test_unselected_year_not_opened_or_hashed(self):
        extra = copy.deepcopy(self.catalog['market']['files'][0])
        extra.update(year=2030, path='market/2030.pqt', first_date='2030-01-03', last_date='2030-01-10')
        self.catalog['market']['files'].append(extra)
        save_catalog(self.root, self.catalog)
        with self.store().bundle(self.p) as source:
            self.assertEqual(len(source.metadata()['sources']['price']['files']), 1)

    def test_shared_market_hashed_once_and_snapshot_changes_detected(self):
        import rankic_inputs
        original = rankic_inputs._hash_file
        calls = []
        def observe(path):
            calls.append(Path(path).name)
            return original(path)
        with patch.object(rankic_inputs, '_hash_file', observe):
            store = self.store()
            with store.bundle(self.p) as source:
                self.assertEqual(calls.count('2025.pqt'), 2)  # market once + factor once
                with (self.root / 'catalog.json').open('a') as handle:
                    handle.write(' ')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    source.assert_unchanged()

    def test_source_change_detected_after_calculation(self):
        store = self.store()
        with store.bundle(self.p) as source:
            with (self.root / 'market/2025.pqt').open('ab') as handle:
                handle.write(b'X')
            with self.assertRaisesRegex(ValueError, 'changed'):
                source.assert_unchanged()

    def test_bad_hash_and_size_rejected(self):
        for field, value in [('sha256', '0'*64), ('bytes', 1), ('rows', 1)]:
            self.catalog = make_catalog(self.root)
            self.catalog['factors'][0]['files'][0][field] = value
            save_catalog(self.root, self.catalog)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'hash|size|rows'):
                with self.store().bundle(self.p):
                    pass

    def test_duplicate_keys_rejected(self):
        self.change_market(lambda frame: pd.concat([frame, frame.iloc[:1]], ignore_index=True))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            with self.store().bundle(self.p):
                pass

    def test_duplicate_year_and_alias_rejected(self):
        self.catalog['market']['files'].append(copy.deepcopy(self.catalog['market']['files'][0]))
        save_catalog(self.root, self.catalog)
        with self.assertRaisesRegex(ValueError, 'year|overlap'):
            self.store()
        self.catalog = make_catalog(self.root)
        other = copy.deepcopy(self.catalog['factors'][0])
        other.update(id='Other', name='different', aliases=['TEST'])
        self.catalog['factors'].append(other)
        save_catalog(self.root, self.catalog)
        with self.assertRaisesRegex(ValueError, 'ambiguous|alias'):
            self.store()

    def test_out_of_root_and_windows_path_tricks_rejected(self):
        for value in ['../outside.pqt', '/outside.pqt', 'C:/outside.pqt', 'market\\2025.pqt',
                      'market/../2025.pqt', 'market/2025.pqt:secret', 'https://invalid.example/data']:
            self.catalog = make_catalog(self.root)
            self.catalog['market']['files'][0]['path'] = value
            save_catalog(self.root, self.catalog)
            with self.subTest(path=value), self.assertRaisesRegex(ValueError, 'path'):
                self.store()

    def test_symlink_panel_rejected(self):
        target = self.root / 'target.pqt'
        source = self.root / 'market/2025.pqt'
        source.rename(target)
        try:
            source.symlink_to(target)
        except OSError:
            target.rename(source)
            self.skipTest('system does not permit synthetic symlinks')
        with self.assertRaisesRegex(ValueError, 'link|path'):
            with self.store().bundle(self.p):
                pass

    def test_server_gate_cannot_be_enabled_by_local_flags(self):
        self.catalog.update(origin='xyquant_server', verified={'authorization': True, 'adjusted_close': True})
        self.catalog['scope']['kind'] = 'server_authorized'
        save_catalog(self.root, self.catalog)
        (self.root / 'calendar.pqt').unlink()
        with self.assertRaisesRegex(ValueError, 'not verified'):
            self.store()

    def test_scope_changes_cannot_reuse_a_previous_task_cache(self):
        first = self.store()
        with first.bundle(self.p) as source:
            old_hash = source.metadata()['catalog']['scope_sha256']
            old_cache = source.cache_dir
            self.assertEqual(len(source.authorized_codes), 3)
        self.assertFalse(old_cache.exists())
        self.catalog['scope']['codes'] = CODES[:2]
        save_catalog(self.root, self.catalog)
        with self.store().bundle(self.p) as source:
            self.assertNotEqual(old_hash, source.metadata()['catalog']['scope_sha256'])
            result = calculate(self.p, source.calendar, source,
                               calendar_through=source.calendar_through, price_through=source.price_through)
            self.assertEqual(result['status'], 'no_result')
            self.assertEqual(result['periods'][0]['excluded_authorization'], 1)
            self.assertEqual(result['periods'][0]['n_valid'], 2)
        with self.assertRaisesRegex(ValueError, 'changed'):
            first.bundle(self.p)

    def test_absent_price_key_is_not_manufactured_by_listed_join(self):
        def mutate(frame):
            frame.loc[0, ['price_present', 'adj_close']] = [False, float('nan')]
            return frame
        self.change_market(mutate)
        with self.store().bundle(self.p) as source:
            self.assertNotIn(CODES[0], source.price_at('2025-01-03').index)
            self.assertIn(CODES[0], source.listed_at('2025-01-03').index)

    def test_decoded_dates_outside_declared_file_coverage_are_rejected(self):
        self.catalog['factors'][0]['files'][0].update(first_date='2025-01-04', last_date='2025-01-04')
        save_catalog(self.root, self.catalog)
        with self.assertRaisesRegex(ValueError, 'coverage'):
            with self.store().bundle(self.p):
                pass

    def test_market_row_with_neither_source_role_is_rejected(self):
        def mutate(frame):
            frame.loc[0, ['price_present', 'listed_present']] = [False, False]
            frame.loc[0, 'adj_close'] = float('nan')
            frame.loc[0, 'listed'] = pd.NA
            return frame
        self.change_market(mutate)
        with self.assertRaisesRegex(ValueError, 'present|presence'):
            with self.store().bundle(self.p):
                pass

    def test_declared_price_and_calendar_coverage_must_be_consistent(self):
        for change in ['price', 'calendar', 'scope']:
            self.catalog = make_catalog(self.root)
            if change == 'price':
                self.catalog['market']['price_through'] = '2026-01-01'
            elif change == 'calendar':
                self.catalog['calendar']['complete_through'] = '2025-01-01'
            else:
                self.catalog['scope']['kind'] = 'server_authorized'
            save_catalog(self.root, self.catalog)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store()


if __name__ == '__main__':
    unittest.main()

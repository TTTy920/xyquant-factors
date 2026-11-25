"""CLI/report acceptance tests; no network or real client data."""
import contextlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from openpyxl import load_workbook
import pandas as pd

from test_rankic_store import make_catalog, save_catalog

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/rankic.py'


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.catalog = make_catalog(self.root / 'data')
        self.config = dict(parameters=dict(factor_name='test', start='2025-01-03', end='2025-01-03',
                                           frequency='weekly', pool='all_a', min_stocks=3), data_root='data')

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, config=None):
        path = self.root / 'config.json'
        path.write_text(json.dumps(config or self.config), encoding='utf-8')
        process = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(CLI), 'run', '--config', str(path),
                                  '--project', str(self.root)], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertNotIn('Traceback', process.stderr)
        self.assertTrue(process.stdout.strip(), 'RankIC CLI must emit its JSON status; entry point is missing')
        return process, json.loads(process.stdout)

    def test_success_three_artifacts_numeric_workbook_and_recomputable_metadata(self):
        self.config['secret_token'] = 'MUST_NOT_LEAK_123456'
        process, payload = self.run_cli()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(payload['status'], 'success')
        folder = Path(payload['output_dir'])
        uuid.UUID(folder.name)
        self.assertEqual(folder.parent, self.root / 'results/rankic')
        self.assertEqual({p.name for p in folder.iterdir()}, {'report.md', 'rankic.xlsx', 'metadata.json'})
        metadata_text = (folder / 'metadata.json').read_text(encoding='utf-8')
        self.assertNotIn('MUST_NOT_LEAK', metadata_text)
        self.assertNotIn('NaN', metadata_text)
        self.assertNotIn('Infinity', metadata_text)
        metadata = json.loads(metadata_text)
        self.assertEqual(metadata['summary']['mean_rank_ic'], metadata['periods'][0]['rank_ic'])
        self.assertEqual(metadata['return_range'], ['2025-01-03', '2025-01-10'])
        self.assertIn('python', metadata['software'])
        report = (folder / 'report.md').read_text(encoding='utf-8')
        self.assertIn('2025-01-10', report)
        self.assertIn('LOCAL REFERENCE ONLY', report)
        workbook = load_workbook(folder / 'rankic.xlsx', data_only=True)
        self.assertEqual(workbook.sheetnames, ['Summary', 'Periods', 'Diagnostics'])
        values = dict(workbook['Summary'].values)
        self.assertIsInstance(values['mean_rank_ic'], (float, int))
        self.assertAlmostEqual(values['mean_rank_ic'], metadata['summary']['mean_rank_ic'])
        self.assertIsNone(values['icir'])
        workbook.close()
        formulas = load_workbook(folder / 'rankic.xlsx')
        self.assertTrue(formulas['Summary']['B2'].data_type == 'f')
        formulas.close()

    def test_no_result_has_null_statistics_and_exit_two(self):
        self.config['parameters']['min_stocks'] = 20
        process, payload = self.run_cli()
        self.assertEqual(process.returncode, 2)
        self.assertEqual(payload['status'], 'no_result')
        metadata = json.loads((Path(payload['output_dir']) / 'metadata.json').read_text())
        self.assertIsNone(metadata['summary']['mean_rank_ic'])
        workbook = load_workbook(Path(payload['output_dir']) / 'rankic.xlsx', data_only=True)
        self.assertIsNone(dict(workbook['Summary'].values)['mean_rank_ic'])
        workbook.close()

    def test_partial_success_keeps_incomplete_period_out_of_summary(self):
        self.config['parameters']['end'] = '2025-01-10'
        process, payload = self.run_cli()
        self.assertEqual(process.returncode, 0)
        self.assertEqual(payload['status'], 'partial_success')
        metadata = json.loads((Path(payload['output_dir']) / 'metadata.json').read_text())
        self.assertEqual(metadata['summary']['incomplete_periods'], 1)
        self.assertEqual(metadata['summary']['valid_periods'], 1)

    def test_bad_config_is_error_and_does_not_echo_private_values(self):
        self.config['parameters']['frequency'] = 'SECRET_BAD_VALUE'
        process, payload = self.run_cli()
        self.assertEqual(process.returncode, 2)
        self.assertEqual(payload['status'], 'error')
        self.assertNotIn('SECRET_BAD_VALUE', process.stdout + process.stderr)
        self.assertFalse((self.root / 'results').exists())

    def test_formula_injection_is_preserved_as_text(self):
        name = '=HYPERLINK("https://invalid.example","factor")'
        self.config['parameters']['factor_name'] = name
        self.catalog['factors'][0]['name'] = name
        save_catalog(self.root / 'data', self.catalog)
        _, payload = self.run_cli()
        workbook = load_workbook(Path(payload['output_dir']) / 'rankic.xlsx')
        for row in workbook['Summary']:
            if row[0].value == 'factor_name':
                self.assertEqual(row[1].data_type, 's')
                self.assertEqual(row[1].value, name)
                break
        else:
            self.fail('factor_name is missing')
        workbook.close()

    def test_invalid_excel_text_leaves_no_partial_success_report(self):
        name = 'factor\x01name'
        self.config['parameters']['factor_name'] = name
        self.catalog['factors'][0]['name'] = name
        save_catalog(self.root / 'data', self.catalog)
        process, payload = self.run_cli()
        self.assertEqual(process.returncode, 2)
        self.assertEqual(payload['status'], 'error')
        parent = self.root / 'results/rankic'
        self.assertEqual(list(parent.iterdir()) if parent.exists() else [], [])

    def test_failed_workbook_generation_or_publication_cleans_staging(self):
        from rankic_engine import Parameters, calculate
        from rankic_store import Store
        import rankic_report
        parameters = Parameters(**self.config['parameters'])
        with Store(self.root / 'data').bundle(parameters) as source:
            result = calculate(parameters, source.calendar, source,
                               calendar_through=source.calendar_through, price_through=source.price_through)
            metadata = source.metadata()
        original_workbook = rankic_report._workbook
        def fail_workbook(path, _):
            path.write_bytes(b'incomplete workbook')
            raise OSError('simulated disk failure')
        for failure in ['workbook', 'publish']:
            def checked_workbook(path, data):
                self.assertNotEqual(path.parent.name, data['task_id'])
                return original_workbook(path, data)
            context = (patch.object(rankic_report, '_workbook', fail_workbook) if failure == 'workbook' else
                       patch.object(Path, 'rename', side_effect=OSError('simulated publish failure')))
            with self.subTest(failure=failure), context:
                with patch.object(rankic_report, '_workbook', checked_workbook) if failure == 'publish' else contextlib.nullcontext():
                    with self.assertRaises(OSError):
                        rankic_report.write_reports(self.root, parameters, result, metadata)
            parent = self.root / 'results/rankic'
            self.assertEqual(list(parent.iterdir()) if parent.exists() else [], [])

    def test_excel_correlation_and_stock_counts_share_valid_status_filter(self):
        _, payload = self.run_cli()
        workbook = load_workbook(Path(payload['output_dir']) / 'rankic.xlsx')
        try:
            sheet = workbook['Periods']
            headers = {cell.value: cell.column_letter for cell in sheet[1]}
            self.assertIn('valid_rank_ic', headers)
            valid = headers['valid_rank_ic']
            raw, status = headers['rank_ic'], headers['status']
            self.assertEqual(sheet[f'{valid}2'].value,
                             f'=IF(AND({status}2="valid",ISNUMBER({raw}2)),{raw}2,"")')
            summary = dict(workbook['Summary'].values)
            for metric in ['valid_periods', 'mean_rank_ic', 'std_rank_ic', 'positive_fraction']:
                self.assertIn(f"'Periods'!{valid}2:{valid}2", summary[metric])
                self.assertNotIn(f"'Periods'!{raw}2:{raw}2", summary[metric])
        finally:
            workbook.close()
        cached = load_workbook(Path(payload['output_dir']) / 'rankic.xlsx', data_only=True)
        try:
            self.assertEqual(cached['Periods'][f'{valid}2'].value, payload['summary']['mean_rank_ic'])
        finally:
            cached.close()

    def test_inventory_does_not_create_reports_or_claim_data_verification(self):
        process = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(CLI), 'inventory',
                                  '--data-root', str(self.root / 'data')], capture_output=True,
                                 text=True, encoding='utf-8', timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload['factors'][0]['id'], 'TestFactor')
        self.assertFalse(payload['data_integrity_checked'])
        self.assertFalse((self.root / 'results').exists())

    def test_task_cannot_override_catalog_server_origin(self):
        self.catalog['origin'] = 'xyquant_server'
        save_catalog(self.root / 'data', self.catalog)
        self.config.update(origin='local_reference', verified={'adjusted_close': True})
        process, payload = self.run_cli()
        self.assertEqual(process.returncode, 2)
        self.assertIn('not verified', payload['message'])
        self.assertFalse((self.root / 'results').exists())

    def test_inline_legacy_manifest_is_not_a_second_customer_config_mode(self):
        config = dict(parameters=self.config['parameters'], data={'origin': 'local_reference'})
        process, payload = self.run_cli(config)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(payload['status'], 'error')
        self.assertFalse((self.root / 'results').exists())

    def test_reports_do_not_overwrite_previous_tasks(self):
        _, first = self.run_cli()
        _, second = self.run_cli()
        self.assertNotEqual(first['output_dir'], second['output_dir'])
        self.assertTrue((Path(first['output_dir']) / 'report.md').exists())


if __name__ == '__main__':
    unittest.main()

"""Client preparation is not authorization, a download, or a server backtest."""
import copy
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
workflow = importlib.import_module('rankic_workflow') if (SCRIPTS / 'rankic_workflow.py').exists() else None

COMPLETE = dict(factor_name='流通市值', start='2025-03-01', end='2025-03-31',
                frequency='weekly', pool='all_a')


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(workflow, 'client parameter preparation is not implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def prepare(self, parameters=None):
        return workflow.prepare(self.root, {'parameters': copy.deepcopy(COMPLETE if parameters is None else parameters)})

    def cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = workflow.main(arguments)
        return code, json.loads(output.getvalue())

    def test_name_only_asks_only_missing_fields_without_inventing_values(self):
        result = self.prepare({'factor_name': '  流通市值  '})
        self.assertEqual(result['status'], 'needs_parameters')
        self.assertEqual(result['parameters'], {'factor_name': '流通市值', 'min_stocks': 20})
        self.assertEqual(result['missing_parameters'], ['start', 'end', 'frequency', 'pool'])
        self.assertFalse(result['can_create_export'])
        self.assertFalse(result['can_run'])
        self.assertEqual(result['factor_resolution'], 'pending_catalog')

    def test_empty_parameters_can_be_prepared_but_nothing_is_assumed(self):
        result = self.prepare({})
        self.assertEqual(result['parameters'], {'min_stocks': 20})
        self.assertEqual(result['missing_parameters'], ['factor_name', 'start', 'end', 'frequency', 'pool'])

    def test_complete_parameters_default_to_twenty_and_still_require_data(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'parameters_complete')
        self.assertEqual(result['missing_parameters'], [])
        self.assertEqual(result['parameters']['min_stocks'], 20)
        self.assertFalse(result['can_create_export'])
        self.assertFalse(result['can_run'])
        result = workflow.preflight(self.root, result['request_id'])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(set(result['blockers']), {'rankic_data_required'})
        self.assertNotIn('data_root', result)

    def test_custom_codes_normalize_deduplicate_without_lowering_minimum(self):
        parameters = dict(COMPLETE, pool='custom', codes=['SZ000001', '000001.SZ', '600000', '000002'])
        result = self.prepare(parameters)
        self.assertEqual(result['parameters']['codes'], ['000001.SZ', '000002.SZ', '600000.SH'])
        self.assertEqual(result['parameters']['min_stocks'], 20)
        self.assertEqual(result['missing_parameters'], [])
        parameters['min_stocks'] = 3
        self.assertEqual(self.prepare(parameters)['parameters']['min_stocks'], 3)

    def test_custom_missing_codes_and_codes_without_pool_remain_questions(self):
        self.assertEqual(self.prepare(dict(COMPLETE, pool='custom'))['missing_parameters'], ['codes'])
        self.assertEqual(self.prepare(dict(COMPLETE, pool='custom', codes=[]))['missing_parameters'], ['codes'])
        parameters = dict(COMPLETE, codes=['000001.SZ'])
        parameters.pop('pool')
        result = self.prepare(parameters)
        self.assertEqual(result['missing_parameters'], ['pool'])
        self.assertNotIn('pool', result['parameters'])

    def test_invalid_known_parameters_fail_without_writing_tasks(self):
        cases = [dict(start='2025-02-30'), dict(start='2026-01-01'), dict(frequency='SECRET_FREQ'),
                 dict(min_stocks=True), dict(min_stocks=2), dict(pool='unknown'),
                 dict(codes=['000001.SZ']), dict(pool='custom', codes=['900001.SH']),
                 dict(pool='custom', codes=[1]), dict(factor_name=''), dict(factor_name='a\nname'),
                 dict(factor_name='https://invalid.example/?ticket=SECRET'), dict(start=None)]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(dict(COMPLETE, **changes))
        self.assertFalse((self.root / 'downloads').exists())

    def test_unknown_fields_and_caller_authorization_are_rejected(self):
        for extra in ['verified', 'download', 'ticket', 'data_root', 'capabilities', 'authorized_codes']:
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    workflow.prepare(self.root, {'parameters': COMPLETE, extra: 'SECRET_VALUE'})
                with self.assertRaises(ValueError):
                    self.prepare(dict(COMPLETE, **{extra: 'SECRET_VALUE'}))
        self.assertFalse((self.root / 'downloads').exists())

    def test_task_contains_only_whitelisted_non_authorizing_data(self):
        request = {'parameters': dict(COMPLETE)}
        original = copy.deepcopy(request)
        result = workflow.prepare(self.root, request)
        self.assertEqual(request, original)
        ident = str(uuid.UUID(result['request_id']))
        self.assertEqual(ident, result['request_id'])
        task = Path(result['task_file'])
        self.assertEqual(task, self.root / 'downloads/rankic-tasks' / ident / 'request.json')
        saved = json.loads(task.read_text(encoding='utf-8'))
        self.assertEqual(set(saved), {'schema_version', 'request_id', 'origin', 'parameters'})
        self.assertEqual(saved['origin'], 'xyquant_server')
        self.assertNotIn('verified', saved)
        self.assertNotIn('can_run', saved)
        self.assertFalse((self.root / 'results').exists())
        self.assertFalse((self.root / '.mcp.json').exists())

    def test_tasks_are_isolated_and_uuid_collision_does_not_overwrite(self):
        first = self.prepare()
        task = Path(first['task_file'])
        before = task.read_bytes()
        second = self.prepare({'factor_name': 'other'})
        self.assertNotEqual(first['request_id'], second['request_id'])
        with patch.object(workflow.uuid, 'uuid4', return_value=uuid.UUID(first['request_id'])):
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertEqual(task.read_bytes(), before)

    def test_preflight_is_read_only_and_never_downloads_or_connects(self):
        result = self.prepare()
        paths = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('socket.create_connection', side_effect=AssertionError('network forbidden')) as network, \
                patch('rankic_download.save', side_effect=AssertionError('download forbidden')) as download:
            checked = workflow.preflight(self.root, result['request_id'])
        network.assert_not_called()
        download.assert_not_called()
        self.assertFalse(checked['can_create_export'])
        self.assertFalse(checked['can_run'])
        self.assertEqual(paths, {p.relative_to(self.root).as_posix(): p.read_bytes()
                                 for p in self.root.rglob('*') if p.is_file()})

    def test_preflight_does_not_trust_ready_or_verified_in_task(self):
        for field in ['verified', 'status', 'can_run', 'download']:
            result = self.prepare()
            task = Path(result['task_file'])
            data = json.loads(task.read_text(encoding='utf-8'))
            data[field] = 'SECRET_VALUE'
            task.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                workflow.preflight(self.root, result['request_id'])

    def test_task_identity_origin_and_version_must_match(self):
        for field, value in [('request_id', str(uuid.uuid4())), ('origin', 'local_reference'),
                             ('schema_version', True), ('schema_version', 2)]:
            result = self.prepare()
            task = Path(result['task_file'])
            data = json.loads(task.read_text(encoding='utf-8'))
            data[field] = value
            task.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                workflow.preflight(self.root, result['request_id'])

    def test_preflight_rechecks_missing_parameters_not_saved_status(self):
        result = self.prepare({'factor_name': 'test'})
        checked = workflow.preflight(self.root, result['request_id'])
        self.assertIn('parameters_incomplete', checked['blockers'])
        self.assertEqual(checked['missing_parameters'], ['start', 'end', 'frequency', 'pool'])

    def test_invalid_project_and_task_paths_fail_without_creation(self):
        for project in [Path('relative-project'), self.root / 'missing', self.root / '..' / self.root.name]:
            with self.subTest(project=project), self.assertRaises(ValueError):
                workflow.prepare(project, {'parameters': COMPLETE})
        for ident in ['../escape', '', uuid.uuid4().hex, 'SECRET_JOB', str(uuid.uuid4())]:
            with self.subTest(ident=ident), self.assertRaises(ValueError):
                workflow.preflight(self.root, ident)
        self.assertFalse((self.root / 'downloads').exists())

    def test_wrong_directory_type_and_link_check_fail_closed(self):
        (self.root / 'downloads').write_text('keep', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual((self.root / 'downloads').read_text(), 'keep')
        with patch.object(workflow, '_check_links', side_effect=ValueError('link forbidden')):
            with self.assertRaises(ValueError):
                self.prepare()

    def test_write_failure_cleans_only_its_new_task(self):
        first = self.prepare()
        original = Path(first['task_file']).read_bytes()
        with patch.object(workflow.json, 'dump', side_effect=OSError('SECRET_WRITE_ERROR')):
            with self.assertRaises(ValueError):
                self.prepare()
        parent = self.root / 'downloads/rankic-tasks'
        self.assertEqual([p.name for p in parent.iterdir()], [first['request_id']])
        self.assertEqual(Path(first['task_file']).read_bytes(), original)

    def test_cli_prepare_preflight_exit_codes_and_input_unchanged(self):
        request = self.root / 'input.json'
        request.write_text(json.dumps({'parameters': COMPLETE}), encoding='utf-8')
        original = request.read_bytes()
        code, result = self.cli(['prepare', '--request', str(request), '--project', str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'parameters_complete')
        self.assertFalse(result['can_run'])
        code, checked = self.cli(['preflight', '--task', result['request_id'], '--project', str(self.root)])
        self.assertEqual(code, 2)
        self.assertEqual(checked['status'], 'blocked')
        self.assertEqual(request.read_bytes(), original)

    def test_cli_rejects_duplicate_nonfinite_oversized_json_without_echo(self):
        request = self.root / 'input.json'
        contents = ['{"parameters":{},"parameters":{}}', '{"parameters":{"min_stocks":NaN}}',
                    '{"parameters":{"min_stocks":Infinity}}', 'SECRET_MALFORMED',
                    json.dumps({'parameters': COMPLETE, 'ticket': 'SECRET_VALUE'}),
                    ' ' * (262144 + 1)]
        for content in contents:
            request.write_text(content, encoding='utf-8')
            code, result = self.cli(['prepare', '--request', str(request), '--project', str(self.root)])
            self.assertEqual(code, 2)
            self.assertEqual(result['status'], 'error')
            self.assertNotIn('SECRET', json.dumps(result))
        self.assertFalse((self.root / 'downloads').exists())

    def test_uri_factor_names_are_not_persisted_or_echoed(self):
        names = ['data:text/plain,ticket=SYNTHETIC_SECRET',
                 'file:/synthetic/export?ticket=SYNTHETIC_SECRET',
                 '//download.example.invalid/export?ticket=SYNTHETIC_SECRET',
                 'mailto:synthetic@example.invalid?token=SYNTHETIC_SECRET',
                 '\\\\synthetic\\export?ticket=SYNTHETIC_SECRET']
        request = self.root / 'input.json'
        for name in names:
            with self.subTest(name=name):
                request.write_text(json.dumps({'parameters': dict(COMPLETE, factor_name=name)}), encoding='utf-8')
                code, result = self.cli(['prepare', '--request', str(request), '--project', str(self.root)])
                self.assertEqual(code, 2)
                self.assertEqual(result['status'], 'error')
                self.assertNotIn('SYNTHETIC_SECRET', json.dumps(result))
                self.assertFalse((self.root / 'downloads').exists())

    def test_preflight_rejects_uri_name_in_previously_saved_task(self):
        result = self.prepare()
        task = Path(result['task_file'])
        document = json.loads(task.read_text(encoding='utf-8'))
        document['parameters']['factor_name'] = 'data:text/plain,ticket=SYNTHETIC_SECRET'
        task.write_text(json.dumps(document), encoding='utf-8')
        before = task.read_bytes()
        code, checked = self.cli(['preflight', '--task', result['request_id'], '--project', str(self.root)])
        self.assertEqual(code, 2)
        self.assertEqual(checked['status'], 'error')
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(checked))
        self.assertEqual(task.read_bytes(), before)

    def test_cli_parser_errors_are_json_without_argument_echo(self):
        code, result = self.cli(['prepare', '--SECRET_ARG', 'SECRET_VALUE'])
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('SECRET', json.dumps(result))


if __name__ == '__main__':
    unittest.main()

"""Real subprocess checks for the isolated, fixed-target trusted launcher."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_rankic_store import make_catalog

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'rankic_entry.py'
SCRIPTS = ROOT / 'scripts'
FAILURE = {'status': 'error', 'error_type': 'LauncherError',
           'message': 'Trusted isolated RankIC launcher could not start.'}
SECRET = 'PRIVATE_ARGUMENT_MUST_NOT_APPEAR'


class EntryTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ENTRY.is_file(), 'trusted isolated entry is not implemented')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'customer'
        self.project.mkdir()

    def environment(self, **extra):
        environment = dict(os.environ)
        for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'):
            environment.pop(name, None)
        environment.update(extra)
        return environment

    def invoke(self, arguments=(), *, entry=ENTRY, isolated=True, cwd=None, env=None, utf8=True):
        flags = (['-I'] if isolated else []) + (['-X', 'utf8'] if utf8 else [])
        return subprocess.run([sys.executable, *flags, str(entry), *arguments],
                              cwd=str(self.project if cwd is None else cwd),
                              env=self.environment() if env is None else env,
                              text=True, encoding='utf-8', capture_output=True, timeout=60)

    def assert_failure(self, result):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(json.loads(result.stdout), FAILURE)
        self.assertNotIn(SECRET, result.stdout + result.stderr)

    def catalog(self, *, min_stocks=3, config_parent=None):
        data = self.root / 'synthetic-data'
        make_catalog(data)
        config = (self.root if config_parent is None else config_parent) / 'request.json'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({'parameters': {
            'factor_name': '测例', 'start': '2025-01-03', 'end': '2025-01-03',
            'frequency': 'weekly', 'pool': 'all_a', 'min_stocks': min_stocks},
            'data_root': str(data)}, ensure_ascii=False), encoding='utf-8')
        return data, config

    def legacy(self, arguments):
        # The old sibling-import CLI is intentionally NOT claimed to support -I.
        return subprocess.run([sys.executable, '-E', '-s', '-B', '-X', 'utf8',
                               str(SCRIPTS / 'rankic.py'), *arguments],
                              cwd=str(ROOT), env=self.environment(), text=True,
                              encoding='utf-8', capture_output=True, timeout=60)

    def metadata(self, result, expected_code):
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        payload = json.loads(result.stdout)
        metadata = Path(payload['output_dir']) / 'metadata.json'
        return payload, json.loads(metadata.read_text(encoding='utf-8'))

    def copy_bundle(self, name='relocated'):
        destination = self.root / name
        destination.mkdir(parents=True)
        shutil.copyfile(ENTRY, destination / 'rankic_entry.py')
        shutil.copytree(SCRIPTS, destination / 'scripts',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        return destination

    def stub_bundle(self, body, *, target='rankic', name='stub'):
        destination = self.root / name
        (destination / 'scripts').mkdir(parents=True)
        shutil.copyfile(ENTRY, destination / 'rankic_entry.py')
        (destination / 'scripts' / (target + '.py')).write_text(body, encoding='utf-8')
        return destination

    def poison(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        markers = []
        for module in ('json', 'rankic_engine', 'sitecustomize', 'rankic', 'rankic_workflow'):
            marker = directory / (module + '.executed')
            (directory / (module + '.py')).write_text(
                f"open({str(marker)!r}, 'w').write('executed')\nraise RuntimeError({SECRET!r})\n",
                encoding='utf-8')
            markers.append(marker)
        return markers

    def directory_link(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                self.skipTest('system does not permit synthetic directory symlinks')
            # cmd.exe emits the native Windows code page, not Python's UTF-8 mode.
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True, timeout=15)
            if result.returncode:
                self.skipTest('system does not permit synthetic directory junctions')

    def test_isolated_inventory_matches_real_legacy_cli(self):
        data, _ = self.catalog()
        arguments = ['inventory', '--data-root', str(data)]
        actual = self.invoke(['rankic', *arguments])
        previous = self.legacy(arguments)
        self.assertEqual(previous.returncode, 0, previous.stdout + previous.stderr)
        self.assertEqual(actual.returncode, 0, actual.stdout + actual.stderr)
        self.assertEqual(actual.stderr, '')
        self.assertEqual(json.loads(actual.stdout), json.loads(previous.stdout))

    def test_isolated_run_preserves_success_results(self):
        _, config = self.catalog()
        arguments = ['run', '--config', str(config), '--project', str(self.project)]
        actual, metadata = self.metadata(self.invoke(['rankic', *arguments]), 0)
        previous, baseline = self.metadata(self.legacy(arguments), 0)
        self.assertEqual(actual['status'], 'success')
        self.assertEqual(actual['summary'], previous['summary'])
        for field in ('status', 'summary', 'periods', 'return_range'):
            with self.subTest(field=field):
                self.assertEqual(metadata[field], baseline[field])
        self.assertEqual(metadata['periods'][0]['rank_ic'], 1.0)
        self.assertEqual(metadata['return_range'], ['2025-01-03', '2025-01-10'])

    def test_native_stdout_handles_chinese_without_utf8_interpreter_flag(self):
        data, config = self.catalog()
        inventory = self.invoke(['rankic', 'inventory', '--data-root', str(data)], utf8=False)
        self.assertEqual(inventory.returncode, 0, inventory.stdout + inventory.stderr)
        self.assertEqual(inventory.stderr, '')
        self.assertEqual(json.loads(inventory.stdout)['factors'][0]['aliases'], ['测例'])
        project = self.root / '客户项目'
        project.mkdir()
        payload, metadata = self.metadata(self.invoke(
            ['rankic', 'run', '--config', str(config), '--project', str(project)], utf8=False), 0)
        self.assertIn('客户项目', payload['output_dir'])
        self.assertEqual(metadata['parameters']['factor_name'], '测例')
        self.assertEqual(metadata['status'], 'success')

    def test_isolated_run_preserves_no_result_and_exit_two(self):
        _, config = self.catalog(min_stocks=20)
        arguments = ['run', '--config', str(config), '--project', str(self.project)]
        actual, metadata = self.metadata(self.invoke(['rankic', *arguments]), 2)
        _, baseline = self.metadata(self.legacy(arguments), 2)
        self.assertEqual(actual['status'], 'no_result')
        for field in ('status', 'summary', 'periods', 'return_range'):
            with self.subTest(field=field):
                self.assertEqual(metadata[field], baseline[field])

    def test_business_validation_error_is_not_relabelled(self):
        arguments = ['inventory', '--data-root', str(self.root / 'missing-catalog')]
        actual = self.invoke(['rankic', *arguments])
        previous = self.legacy(arguments)
        self.assertEqual(previous.returncode, 2)
        self.assertEqual(actual.returncode, previous.returncode)
        self.assertEqual(actual.stderr, '')
        self.assertEqual(json.loads(actual.stdout), json.loads(previous.stdout))
        self.assertNotEqual(json.loads(actual.stdout), FAILURE)

    def test_without_isolation_refuses_even_help_without_echo(self):
        for arguments in (['rankic', SECRET], ['--help'], []):
            with self.subTest(arguments=arguments):
                self.assert_failure(self.invoke(arguments, isolated=False, cwd=ROOT))

    def test_non_isolated_refusal_does_not_import_polluted_pythonpath_modules(self):
        directory = self.root / 'untrusted-pythonpath'
        directory.mkdir()
        markers = []
        # Do not plant sitecustomize here: without -I, Python itself executes it
        # before any launcher can check flags. The isolated pollution test covers it.
        for module in ('json', 'pathlib', 'rankic_engine'):
            marker = directory / (module + '.executed')
            (directory / (module + '.py')).write_text(
                f"open({str(marker)!r}, 'w').write('executed')\nraise RuntimeError({SECRET!r})\n",
                encoding='utf-8')
            markers.append(marker)
        result = self.invoke(['rankic', SECRET], isolated=False, cwd=ROOT,
                             env=self.environment(PYTHONPATH=str(directory)))
        self.assert_failure(result)
        self.assertFalse(any(marker.exists() for marker in markers))

    def test_without_isolation_checks_before_standard_library_imports(self):
        marker = self.root / 'import.executed'
        bootstrap = f"""import sys
class BlockImports:
    def find_spec(self, *args, **kwargs):
        open({str(marker)!r}, 'w').write('import attempted')
        raise RuntimeError({SECRET!r})
sys.meta_path.insert(0, BlockImports())
sys.argv = [{str(ENTRY)!r}, 'rankic', {SECRET!r}]
with open({str(ENTRY)!r}, encoding='utf-8') as handle:
    source = handle.read()
exec(compile(source, {str(ENTRY)!r}, 'exec'), {{'__file__': {str(ENTRY)!r}, '__name__': '__main__'}})
"""
        result = subprocess.run([sys.executable, '-S', '-X', 'utf8', '-c', bootstrap],
                                cwd=str(ROOT), env=self.environment(), text=True,
                                encoding='utf-8', capture_output=True, timeout=30)
        self.assert_failure(result)
        self.assertFalse(marker.exists(), 'launcher imported before rejecting non-isolated Python')

    def test_unknown_targets_cannot_import_modules_or_echo_arguments(self):
        bundle = self.stub_bundle("raise RuntimeError('rankic must not be imported')\n")
        marker = bundle / 'unexpected-module.executed'
        (bundle / 'scripts' / 'unexpected_module.py').write_text(
            f"open({str(marker)!r}, 'w').write('executed')\n", encoding='utf-8')
        for target in ('os', 'runpy', 'rankic_engine', 'unexpected_module',
                       '../scripts/rankic', SECRET + '\nprivate'):
            with self.subTest(target=target):
                self.assert_failure(self.invoke([target, SECRET], entry=bundle / 'rankic_entry.py'))
        self.assertFalse(marker.exists())

    def test_missing_target_and_invalid_entry_options_fail_safely(self):
        for arguments in ([], ['--help', SECRET], ['-h', SECRET], ['--target', SECRET]):
            with self.subTest(arguments=arguments):
                self.assert_failure(self.invoke(arguments))

    def test_help_only_describes_fixed_targets_without_importing_business_code(self):
        bundle = self.stub_bundle(f"raise RuntimeError({SECRET!r})\n")
        for flag in ('-h', '--help'):
            result = self.invoke([flag], entry=bundle / 'rankic_entry.py')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertIn('rankic', result.stdout)
            self.assertIn('workflow', result.stdout)
            self.assertNotIn(SECRET, result.stdout)

    def test_customer_cwd_pythonpath_data_and_download_modules_never_execute(self):
        downloads = self.project / 'downloads'
        data, config = self.catalog(config_parent=downloads)
        polluted_path = self.root / 'pythonpath'
        markers = []
        for directory in (self.project, downloads, data, polluted_path):
            markers.extend(self.poison(directory))
        environment = self.environment(PYTHONPATH=str(polluted_path),
                                       PYTHONHOME=str(self.root / 'bogus-python-home'))
        inventory = self.invoke(['rankic', 'inventory', '--data-root', str(data)], env=environment)
        self.assertEqual(inventory.returncode, 0, inventory.stdout + inventory.stderr)
        self.assertEqual(inventory.stderr, '')
        result, _ = self.metadata(self.invoke(
            ['rankic', 'run', '--config', str(config), '--project', str(self.project)],
            cwd=downloads, env=environment), 0)
        self.assertEqual(result['status'], 'success')
        self.assertFalse(any(marker.exists() for marker in markers))

    def test_only_trusted_scripts_path_is_added_and_bytecode_is_disabled(self):
        bundle = self.stub_bundle("""import json
import sys

def main(argv):
    print(json.dumps({'argv': argv, 'path': sys.path, 'isolated': sys.flags.isolated,
                      'no_bytecode': sys.dont_write_bytecode, 'module_file': __file__}))
    return 0
""")
        result = self.invoke(['rankic', 'literal', SECRET], entry=bundle / 'rankic_entry.py')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['argv'], ['literal', SECRET])
        self.assertTrue(payload['isolated'])
        self.assertTrue(payload['no_bytecode'])
        self.assertEqual(Path(payload['path'][0]), bundle / 'scripts')
        self.assertEqual(Path(payload['module_file']), bundle / 'scripts' / 'rankic.py')
        for forbidden in (bundle, self.project, self.project / 'downloads', self.root, SCRIPTS):
            self.assertNotIn(str(forbidden), payload['path'])
        self.assertEqual(list(bundle.rglob('*.pyc')), [])
        self.assertEqual(list(bundle.rglob('__pycache__')), [])

    def test_relocated_real_inventory_and_run_do_not_require_original_path(self):
        bundle = self.copy_bundle()
        data, config = self.catalog()
        result = self.invoke(['rankic', 'inventory', '--data-root', str(data)],
                             entry=bundle / 'rankic_entry.py')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['scope_stock_count'], 3)
        _, metadata = self.metadata(self.invoke(
            ['rankic', 'run', '--config', str(config), '--project', str(self.project)],
            entry=bundle / 'rankic_entry.py'), 0)
        self.assertEqual(metadata['status'], 'success')
        self.assertEqual(metadata['periods'][0]['rank_ic'], 1.0)
        self.assertEqual(list(bundle.rglob('*.pyc')), [])

    def test_business_return_code_and_system_exit_are_preserved(self):
        for name, target, body, expected in (
                ('returned', 'rankic', "def main(argv):\n    return 7\n", 7),
                ('exited', 'rankic_workflow', "def main(argv):\n    raise SystemExit(9)\n", 9)):
            with self.subTest(case=name):
                bundle = self.stub_bundle(body, target=target, name=name)
                selected = 'rankic' if target == 'rankic' else 'workflow'
                result = self.invoke([selected], entry=bundle / 'rankic_entry.py')
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.stderr, '')

    def test_import_and_unhandled_main_failures_hide_exceptions(self):
        for name, body in (
                ('import-error', f"raise RuntimeError({SECRET!r})\n"),
                ('main-error', f"def main(argv):\n    raise RuntimeError({SECRET!r})\n"),
                ('missing-main', 'main = None\n')):
            with self.subTest(case=name):
                bundle = self.stub_bundle(body, name=name)
                self.assert_failure(self.invoke(['rankic', SECRET], entry=bundle / 'rankic_entry.py'))

    def test_missing_or_non_directory_scripts_and_missing_module_fail_safely(self):
        for name, kind in (('no-scripts', 'missing'), ('scripts-file', 'file'), ('no-module', 'directory')):
            with self.subTest(case=name):
                bundle = self.root / name
                bundle.mkdir()
                shutil.copyfile(ENTRY, bundle / 'rankic_entry.py')
                if kind == 'file':
                    (bundle / 'scripts').write_text(SECRET, encoding='utf-8')
                elif kind == 'directory':
                    (bundle / 'scripts').mkdir()
                self.assert_failure(self.invoke(['rankic', SECRET], entry=bundle / 'rankic_entry.py'))

    def test_scripts_directory_symlink_or_windows_junction_is_rejected(self):
        bundle = self.stub_bundle('def main(argv):\n    return 0\n')
        real = bundle / 'actual-scripts'
        (bundle / 'scripts').rename(real)
        self.directory_link(bundle / 'scripts', real)
        self.assert_failure(self.invoke(['rankic'], entry=bundle / 'rankic_entry.py'))

    def test_code_root_symlink_or_windows_junction_is_not_resolved_away(self):
        bundle = self.stub_bundle('def main(argv):\n    return 0\n')
        linked = self.root / 'linked-bundle'
        self.directory_link(linked, bundle)
        self.assert_failure(self.invoke(['rankic'], entry=linked / 'rankic_entry.py'))

    def test_ancestor_symlink_or_windows_junction_is_rejected(self):
        bundle = self.stub_bundle('def main(argv):\n    return 0\n', name='real-parent/nested/bundle')
        linked = self.root / 'linked-parent'
        self.directory_link(linked, bundle.parent.parent)
        self.assert_failure(self.invoke(['rankic'], entry=linked / 'nested/bundle/rankic_entry.py'))

    def test_entry_file_symlink_is_rejected(self):
        bundle = self.stub_bundle('def main(argv):\n    return 0\n')
        real = bundle / 'actual-entry.py'
        (bundle / 'rankic_entry.py').rename(real)
        try:
            (bundle / 'rankic_entry.py').symlink_to(real)
        except OSError:
            self.skipTest('system does not permit synthetic file symlinks')
        self.assert_failure(self.invoke(['rankic'], entry=bundle / 'rankic_entry.py'))

    def test_target_file_symlink_is_rejected(self):
        bundle = self.stub_bundle('def main(argv):\n    return 0\n')
        module = bundle / 'scripts/rankic.py'
        actual = bundle / 'actual-module.py'
        module.rename(actual)
        try:
            module.symlink_to(actual)
        except OSError:
            self.skipTest('system does not permit synthetic file symlinks')
        self.assert_failure(self.invoke(['rankic'], entry=bundle / 'rankic_entry.py'))

    def test_workflow_prepare_preflight_keep_blocked_exit_semantics(self):
        if not (SCRIPTS / 'rankic_workflow.py').is_file():
            self.skipTest('workflow module is still being implemented by the main session')
        request = self.root / 'workflow-request.json'
        request.write_text(json.dumps({'parameters': {
            'factor_name': '流通市值', 'start': '2025-03-01', 'end': '2025-03-31',
            'frequency': 'weekly', 'pool': 'all_a'}}, ensure_ascii=False), encoding='utf-8')
        prepared = self.invoke(['workflow', 'prepare', '--request', str(request),
                                '--project', str(self.project)])
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.assertEqual(prepared.stderr, '')
        payload = json.loads(prepared.stdout)
        self.assertEqual(payload['status'], 'parameters_complete')
        self.assertFalse(payload['can_run'])
        blocked = self.invoke(['workflow', 'preflight', '--task', payload['request_id'],
                               '--project', str(self.project)])
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertEqual(blocked.stderr, '')
        self.assertEqual(json.loads(blocked.stdout)['status'], 'blocked')
        self.assertFalse(json.loads(blocked.stdout)['can_create_export'])
        self.assertFalse((self.project / 'results').exists())


if __name__ == '__main__':
    unittest.main()

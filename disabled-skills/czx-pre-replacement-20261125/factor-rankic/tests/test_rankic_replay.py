"""Relocation must copy configured data roots, not assume the old sample layout."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'verification/verify_catalog.py'
spec = importlib.util.spec_from_file_location('verify_catalog_reference', PATH)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class ReplayLayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        (self.root / 'examples').mkdir()
        self.target = Path(self.tmp.name) / 'relocated'
        self.target.mkdir()
        data = self.root / 'data/local-reference/expanded'
        data.mkdir(parents=True)
        (data / 'calendar.pqt').write_bytes(b'fixture-only')
        self.data = data
        self.set_roots('../data/local-reference/expanded')

    def tearDown(self):
        self.tmp.cleanup()

    def set_roots(self, value):
        for frequency in verify.FREQUENCIES:
            (self.root / f'examples/floatcap-{frequency}.json').write_text(
                json.dumps(dict(data_root=value)), encoding='utf-8')

    def test_selected_root_is_copied_without_old_sample(self):
        old = self.root / 'data/samples/unused'
        old.mkdir(parents=True)
        (old / 'not-needed.pqt').write_bytes(b'old')
        self.assertTrue(hasattr(verify, 'copy_selected_data'), 'configured data relocation is not implemented')
        paths = verify.copy_selected_data(self.root, self.target)
        self.assertEqual(paths, ['data/local-reference/expanded'])
        self.assertEqual((self.target / paths[0] / 'calendar.pqt').read_bytes(), b'fixture-only')
        self.assertFalse((self.target / 'data/samples').exists())

    def test_multiple_selected_roots_and_duplicates(self):
        self.assertTrue(hasattr(verify, 'copy_selected_data'), 'configured data relocation is not implemented')
        second = self.root / 'data/other'
        second.mkdir()
        (second / 'x.pqt').write_bytes(b'x')
        path = self.root / 'examples/floatcap-quarterly.json'
        path.write_text(json.dumps(dict(data_root='../data/other')), encoding='utf-8')
        paths = verify.copy_selected_data(self.root, self.target)
        self.assertEqual(set(paths), {'data/local-reference/expanded', 'data/other'})

    def test_root_escape_or_project_root_is_rejected(self):
        self.assertTrue(hasattr(verify, 'copy_selected_data'), 'configured data relocation is not implemented')
        for relative in ('../..', '..'):
            self.set_roots(relative)
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                verify.copy_selected_data(self.root, self.target)

    def test_new_default_does_not_overwrite_historical_evidence(self):
        self.assertTrue(hasattr(verify, 'parse_args'), 'explicit replay evidence selection is not implemented')
        args = verify.parse_args([])
        self.assertEqual(args.evidence.as_posix(), 'verification/latest-replay-check.json')
        self.assertNotEqual(args.evidence.name, 'catalog-replay-check.json')
        explicit = verify.parse_args(['--evidence', 'verification/price-extension-replay-check.json'])
        self.assertEqual(explicit.evidence.name, 'price-extension-replay-check.json')


if __name__ == '__main__':
    unittest.main()

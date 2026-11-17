"""Check versioned reuse and complete, byte-preserving original-data downloads."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/xyquant/skills/portfolio-attribution/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('original_download', SCRIPTS / 'download_original.py')
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


class OriginalDataDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.version = 'original-20261113-0123456789ab'
        self.files = {'catalog.json': b'{"schema_version":1}', 'market/2025.parquet': b'exact original bytes'}

    def job(self, extra=None):
        manifest = {'origin': 'xyquant_server', 'dataset': 'original_attribution',
                    'data_version': self.version, 'catalog': 'catalog.json',
                    'files': {name: {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                              for name, value in self.files.items()}}
        raw = json.dumps(manifest).encode()
        folder = self.project / 'downloads/attribution-original' / self.version
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'attribution-data.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, value in self.files.items():
                archive.writestr(name, value)
            archive.writestr('original-data.json', raw)
            if extra:
                archive.writestr(extra, b'outside')
        return {'id': '1' * 32, 'status': 'ready', 'tool': 'query_stock_data',
                'datasets': ['original_attribution'], 'data_version': self.version,
                'bytes': path.stat().st_size, 'sha256': download.digest(path),
                'manifest_sha256': hashlib.sha256(raw).hexdigest()}

    def test_downloaded_bytes_and_new_jobs_reuse_one_version_without_http(self):
        job = self.job()
        with patch.object(download, 'transfer', side_effect=AssertionError('unexpected network')):
            self.assertEqual(download.check(self.project, self.version, job['manifest_sha256'])['status'], 'missing')
            first = download.save(job, self.project)
            second = download.save({**job, 'id': '2' * 32}, self.project)
        self.assertEqual(first, second)
        for name, value in self.files.items():
            self.assertEqual((Path(first['data_root']) / name).read_bytes(), value)

    def test_corrupt_local_file_is_not_used_or_silently_replaced(self):
        job = self.job()
        result = download.save(job, self.project)
        (Path(result['data_root']) / 'market/2025.parquet').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, '本地原版数据校验失败'):
            download.save(job, self.project)

    def test_unexpected_archive_file_cannot_escape_cache(self):
        job = self.job('../../outside')
        with self.assertRaisesRegex(ValueError, '文件集合无效'):
            download.save(job, self.project)
        self.assertFalse((self.project / 'downloads/outside').exists())
        self.assertFalse((self.project / 'downloads/attribution-original' / self.version / 'data').exists())

    def test_other_dataset_and_changed_manifest_are_rejected(self):
        job = self.job()
        with self.assertRaises(ValueError):
            download.save({**job, 'datasets': ['stock_returns']}, self.project)
        with self.assertRaisesRegex(ValueError, '清单校验失败'):
            download.save({**job, 'manifest_sha256': 'f' * 64}, self.project)


if __name__ == '__main__':
    unittest.main()

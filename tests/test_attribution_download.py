"""Join two checked public-data downloads without changing their values."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/xyquant/skills/portfolio-attribution/scripts/download_data.py'
spec = importlib.util.spec_from_file_location('attribution_download', SCRIPT)
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


class GroupedDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def job(self, tool, number, start='2024-12-31', extra=None):
        jid = str(number)*32
        folder = self.project/'downloads/attribution-data'/jid
        folder.mkdir(parents=True)
        names = download.GROUP_FILES[tool]-{'data.json'}
        values = {name: (tool+':'+name).encode() for name in names}
        manifest = {'origin': 'xyquant_server',
                    'query': {'start': start, 'end': '2025-01-31', 'codes': ['000001.SZ'], 'frequency': 'monthly'},
                    'files': {name: {'sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)} for name,value in values.items()}}
        if tool == 'query_stock_data':
            manifest.update(market='stock_returns.parquet', industry='industry.parquet')
        else:
            manifest.update(exposures='exposures.parquet', factor_returns={'monthly': 'factor_returns.parquet'}, factor_columns={'Size': '1'})
        with zipfile.ZipFile(folder/'attribution-data.zip', 'w') as archive:
            for name,value in values.items(): archive.writestr(name, value)
            archive.writestr('data.json', json.dumps(manifest))
            if extra: archive.writestr(extra, b'outside')
        content = (folder/'attribution-data.zip').read_bytes()
        return {'id': jid, 'tool': tool, 'status': 'ready', 'bytes': len(content),
                'sha256': hashlib.sha256(content).hexdigest(),
                'download': {'url': download.ORIGIN+'/factors/api/attribution-data/'+jid,
                             'ticket': 'A'*40, 'expires_at': time.time()+600}}

    def test_both_groups_combine_and_retries_reuse_checked_data(self):
        jobs = {tool: self.job(tool,i) for i,tool in enumerate(download.GROUP_FILES,1)}
        result = download.save(jobs, self.project)
        config = json.loads(Path(result['input_config']).read_text())
        for key in ['market','industry','exposures']:
            path = Path(config[key]);self.assertTrue(path.is_absolute())
            self.assertTrue(path.is_file())
            self.assertTrue(path.read_bytes().endswith(path.name.encode()))
        self.assertTrue(Path(config['factor_returns']['monthly']).is_file())
        self.assertEqual(config['calendar'], 'derived from stock_returns.date')
        self.assertEqual(download.save(jobs,self.project), result)
        self.assertFalse(list(self.project.rglob('*calendar*')))

    def test_inconsistent_ranges_cannot_be_merged(self):
        jobs = {'query_stock_data': self.job('query_stock_data',1),
                'query_barra_data': self.job('query_barra_data',2,start='2025-01-01')}
        with self.assertRaisesRegex(ValueError, '范围不一致'):
            download.save(jobs,self.project)

    def test_missing_group_or_swapped_tool_cannot_be_used(self):
        stock = self.job('query_stock_data',1)
        with self.assertRaises(ValueError): download.save({'query_stock_data':stock},self.project)
        barra = self.job('query_barra_data',2)
        with self.assertRaises(ValueError):
            download.save({'query_stock_data':barra,'query_barra_data':stock},self.project)

    def test_extra_archive_entries_are_rejected_before_extraction(self):
        stock = self.job('query_stock_data',1,extra='../../outside')
        barra = self.job('query_barra_data',2)
        with self.assertRaises(ValueError):
            download.save({'query_stock_data':stock,'query_barra_data':barra},self.project)
        self.assertFalse((self.project/'downloads/outside').exists())

    def test_corrupt_cached_archive_never_becomes_an_input_config(self):
        stock = self.job('query_stock_data',1)
        barra = self.job('query_barra_data',2)
        (self.project/'downloads/attribution-data'/stock['id']/'attribution-data.zip').write_bytes(b'corrupted')
        with self.assertRaises(ValueError):
            download.save({'query_stock_data':stock,'query_barra_data':barra},self.project)
        self.assertFalse((self.project/'downloads/attribution-data/combined').exists())

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT/'plugins/xyquant/skills/ipo-return-calculator'
spec = importlib.util.spec_from_file_location('ipo_transfer_test', SKILL/'scripts/ipo_transfer.py')
ipo = importlib.util.module_from_spec(spec);spec.loader.exec_module(ipo)


class IPOTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        book = openpyxl.Workbook();sheet = book.active
        sheet.append(['股票代码', '上市日期', '上市板', '发行价格', '网下申购上限(万股)',
                      '募资总额(亿元)', '上市首日均价', '网下A类投资者中签率(%)'])
        sheet.append(['000001', '2025-01-02', '上证主板', 10, 100, 2, 20, .1])
        sheet.append(['000002', '2025-01-03', '科创板', 10, None, 3, 20, None])
        sheet.append(['000003', '2025-01-04', '北交所', 10, None, 3, 20, None])
        stream = io.BytesIO();book.save(stream);book.close();self.workbook = stream.getvalue()
        sha = hashlib.sha256(self.workbook).hexdigest();now = time.time()
        self.manifest = dict(schema_version=1, origin='xyquant_server', server_contract=ipo.CONTRACT,
                             dataset='ipo', data_version='ipo-'+sha[:32], created_at=now, expires_at=now+3600,
                             files={ipo.FILENAME: {'bytes': len(self.workbook), 'sha256': sha}})
        self.job = dict(id='a'*32, status='ready', tool='query_ipo_data', datasets=['ipo'],
                        data_version=self.manifest['data_version'], server_contract=ipo.CONTRACT,
                        expires_at=now+3600, download={'expires_at': now+900, 'ticket': 'test-secret-do-not-persist'})
        self.make_archive()

    def make_archive(self, workbook=None, extra=None):
        raw = (json.dumps(self.manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('ipo-data.json', raw)
            archive.writestr(ipo.FILENAME, self.workbook if workbook is None else workbook)
            if extra:archive.writestr(extra, b'unexpected')
        self.archive = stream.getvalue()
        self.job.update(bytes=len(self.archive), sha256=hashlib.sha256(self.archive).hexdigest(),
                        manifest_sha256=hashlib.sha256(raw).hexdigest())

    def save(self, job=None):
        with patch.object(ipo, 'transfer', side_effect=lambda job, path: path.write_bytes(self.archive)):
            return ipo.accept(self.project, self.job['id'], json.dumps(job or self.job).encode())

    def test_byte_exact_excel_without_tickets_or_accumulated_archives(self):
        for _ in range(2):
            result = self.save();self.assertEqual(result['status'], 'ready', result)
            self.assertEqual(Path(result['path']).read_bytes(), self.workbook)
        folder = Path(result['path']).parent
        self.assertEqual({p.name for p in folder.iterdir()}, {ipo.FILENAME, '.xyquant-source.json', '.xyquant-install.lock'})
        self.assertNotIn(self.job['download']['ticket'], (folder/'.xyquant-source.json').read_text(encoding='utf-8'))
        self.assertNotIn('download', json.loads((folder/'.xyquant-source.json').read_text(encoding='utf-8')))

    def test_wrong_tool_expiry_and_version_cannot_overwrite_existing_data(self):
        result = self.save();target = Path(result['path'])
        for key, value in [('tool', 'query_stock_data'), ('data_version', 'ipo-'+'0'*32), ('expires_at', 0)]:
            bad = {**self.job, key: value}
            self.assertEqual(self.save(bad)['status'], 'failed')
            self.assertEqual(target.read_bytes(), self.workbook)
        self.assertFalse(list(target.parent.glob('.download-*')))

    def test_corrupt_workbook_and_unexpected_paths_are_rejected(self):
        for mode in ('corrupt', 'path'):
            self.make_archive(workbook=b'bad' if mode == 'corrupt' else None,
                              extra='../outside.xlsx' if mode == 'path' else None)
            self.assertEqual(self.save()['status'], 'failed')
        self.assertFalse((self.project/'xyquant-data/inputs/ipo'/ipo.FILENAME).exists())
        self.assertFalse(list(self.project.rglob('*.zip')))

    def test_download_failure_preserves_previous_excel_and_cleans_temporaries(self):
        result = self.save();target = Path(result['path'])
        with patch.object(ipo, 'transfer', side_effect=ValueError('test download failure')):
            result = ipo.accept(self.project, self.job['id'], json.dumps(self.job).encode())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(target.read_bytes(), self.workbook)
        self.assertFalse(list(target.parent.glob('.download-*')))

    def test_original_calculation_bytes_and_missing_value_behavior_are_preserved(self):
        script = SKILL/'scripts/calculate.py'
        self.assertEqual(hashlib.sha256(script.read_bytes()).hexdigest(),
                         '22052de21e79ca8f033ae397115aac2e3f2a7b79ad9642123309e1cbb0c6f885')
        installed = self.save()
        raw = subprocess.check_output([sys.executable, '-X', 'utf8', str(script), '--input', installed['path'],
                                       '--sizes', '2', '--details'], encoding='utf-8')
        result = json.loads(raw)
        self.assertEqual(result['样本数'], 3)
        summary = result['汇总'][0]
        self.assertEqual((summary['上市数'], summary['可测算数']), (2, 1))
        self.assertAlmostEqual(summary['收益额元'], 9000)
        self.assertAlmostEqual(summary['收益率'], .000045)
        self.assertIsNone(result['个股明细'][1]['2亿收益额元'])
        self.assertTrue(any(r['年度'] == 2025 and r['月份'] == 2 and r['2亿打新收益率'] == 0 for r in result['月度收益']))

"""Exercise business entry points and a complete synthetic local synthesis."""
import csv
from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from zipfile import ZipFile

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from package import build


class BusinessPackageTests(unittest.TestCase):
    def run_cli(self, path, *args, payload=None):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(path), *map(str, args)],
                                input=payload, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_packaged_business_entry_points_have_all_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            with ZipFile(build(Path(tmp))) as archive:
                archive.extractall(tmp)
            plugin = Path(tmp) / 'xyquant'
            self.assertIn('usage:', self.run_cli(plugin / 'shared/dataset_transfer.py', '--help'))
            for entry in ('factor-rankic/scripts/rankic.py', 'factor-rankic/scripts/rankic_transfer.py',
                          'portfolio-attribution/scripts/entry.py', 'portfolio-attribution/scripts/download_original.py',
                          'stock-holding-diagnosis/scripts/diagnosis.py', 'stock-holding-diagnosis/scripts/mcp_transfer.py',
                          'factor-synthesis/scripts/synthesis.py', 'factor-synthesis/scripts/synthesis_transfer.py'):
                with self.subTest(entry=entry):
                    self.assertIn('usage:', self.run_cli(plugin / 'skills' / entry, '--help'))

    def test_synthesis_delivers_three_files_from_checked_export_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            with ZipFile(build(project)) as archive:
                archive.extractall(project)
            scripts = project / 'xyquant/skills/factor-synthesis/scripts'
            jid = 'a' * 32
            folder = project / 'xyquant-data/mcp-cache/factors' / jid
            folder.mkdir(parents=True)
            source = folder / 'factors.zip'
            codes = [f'{i:06d}.SZ' for i in range(1, 21)]
            with ZipFile(source, 'w') as archive:
                for name in ('ROE_TTM', 'EP_TTM'):
                    table = pa.table({'date': [date(2025, 1, 3)] * 20, 'stock_cd': codes,
                                      'alpha': [float(i) for i in range(20)]})
                    buf = BytesIO()
                    pq.write_table(table, buf)
                    archive.writestr(name + '.parquet', buf.getvalue())
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            job = {'id': jid, 'status': 'ready', 'filename': source.name,
                   'bytes': source.stat().st_size, 'sha256': digest,
                   'selection': {'factors': ['ROE_TTM', 'EP_TTM'], 'start': '2025-01-03',
                                 'end': '2025-01-03', 'codes': []},
                   'download': {'url': 'https://47.103.215.251/mcp/downloads/' + jid,
                                'ticket': 'synthetic-test-only', 'expires_at': time.time() + 300}}
            accepted = json.loads(self.run_cli(scripts / 'synthesis_transfer.py', 'save', '--project', project,
                                              '--job', jid, payload=json.dumps(job)))
            self.assertEqual(accepted['status'], 'ready')
            result = json.loads(self.run_cli(scripts / 'synthesis.py', '--project', project, '--job', jid,
                                            '--sha256', digest, '--date', '2025-01-03',
                                            '--factor', 'ROE_TTM', '--factor', 'EP_TTM',
                                            '--direction', 'ROE_TTM=descending', '--direction', 'EP_TTM=descending'))
            self.assertEqual(result['status'], 'success')
            output = Path(result['output_dir'])
            self.assertEqual({p.name for p in output.iterdir()}, {'scores.csv', 'report.md', 'metadata.json'})
            with (output / 'scores.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 20)
            self.assertEqual(float(rows[0]['score']), 0)
            self.assertEqual(float(rows[-1]['score']), 100)
            self.assertNotIn('synthetic-test-only', (folder / '.xyquant-state.json').read_text())

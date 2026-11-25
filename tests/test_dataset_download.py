"""Dataset transport, integrity and server-source integration; no live credentials."""
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/xyquant'
sys.path.insert(0, str(PLUGIN))
import xyquant_dataset_download as transport
from xyquant_catalog import measure


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode() + b'\n'


class DatasetDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.codes = [f'{i:06d}.SZ' for i in range(1, 21)]
        self.days = [date(2025, 1, i) for i in (2, 3, 6, 7, 8, 9, 10)]
        self.sources = self.root / 'source'
        self.files, self.pins = {}, {}
        specs = {
            'adjusted-prices': ('adjusted-close-v1', 'prices.parquet', pa.table({
                'date': [d for d in self.days for _ in self.codes],
                'stock_code': self.codes * len(self.days),
                'adj_close': [100. + (i + 1) * (t + 1) / 100 for t, _ in enumerate(self.days) for i in range(20)]})),
            'trading-calendar': ('trading-calendar-v1', 'calendar.parquet', pa.table({'date': self.days})),
            'listed-history': ('listed-history-v1', 'listed.parquet', pa.table({
                'date': [d for d in self.days for _ in self.codes], 'stock_code': self.codes * len(self.days),
                'listed': [1] * (20 * len(self.days))})),
        }
        for name, (contract, filename, table) in specs.items():
            folder = self.sources / 'datasets' / name; folder.mkdir(parents=True)
            pq.write_table(table, folder / filename)
            manifest = dict(schema_version=1, dataset_id=name, version='test-v1', source='synthetic fixture',
                            published_date='2025-01-10', coverage={'start':'2025-01-02', 'end':'2025-01-10'},
                            contract=contract, known_gaps=[], files={filename: measure(folder / filename)})
            (folder / 'manifest.json').write_bytes(encoded(manifest))
            self.pins[name] = measure(folder / 'manifest.json')['sha256']
            for p in folder.iterdir(): self.files[p.relative_to(self.sources).as_posix()] = measure(p)
        now = time.time()
        self.bundle = dict(schema_version=1, origin='xyquant_server', server_contract=transport.CONTRACT,
                           bundle_group='rankic', data_version='flat-' + 'b'*32, snapshot_id='flat-cache-'+'c'*32,
                           dataset_ids=list(transport.GROUPS['rankic']), created_at=now, expires_at=now+3600,
                           files=self.files, dataset_manifests=self.pins,
                           scope={'kind':'server_public_data', 'codes':self.codes})
        self.archive = self.root / 'dataset.zip'
        self.write_archive()

    def write_archive(self, extra=None):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for name in self.files: archive.write(self.sources / name, name)
            archive.writestr('dataset-bundle.json', encoded(self.bundle))
            if extra: archive.writestr(extra, b'unsafe')
        self.job = dict(id='a'*32, status='ready', tool='query_rankic_data', datasets=['flat_inputs'],
                        **{k:self.bundle[k] for k in ('server_contract','bundle_group','data_version','snapshot_id','dataset_ids','expires_at')},
                        **measure(self.archive), manifest_sha256=hashlib.sha256(encoded(self.bundle)).hexdigest())

    def install(self):
        with patch.object(transport, 'transfer', side_effect=lambda job, target: shutil.copyfile(self.archive, target)) as call:
            result = transport.save(self.project, self.job)
        return result, call.call_count

    def test_install_preserves_bytes_reuses_snapshot_and_cleans_temporary_files(self):
        result, calls = self.install(); self.assertEqual((result['status'], calls), ('ready', 1))
        for name in self.files:
            actual = self.project / 'xyquant-data/inputs' / name
            self.assertEqual(actual.read_bytes(), (self.sources / name).read_bytes())
        self.assertEqual(self.install()[1], 0)
        self.assertEqual(transport.status(self.project, self.bundle['dataset_ids'])['status'], 'ready')
        self.assertEqual(list((self.project/'xyquant-data/.scratch/datasets').iterdir()), [])
        for p in self.project.rglob(transport.RECEIPT):
            self.assertNotIn('download', json.loads(p.read_text(encoding='utf-8')))

    def test_expired_server_receipt_requires_refresh(self):
        self.install()
        with patch.object(transport.time, 'time', return_value=self.bundle['expires_at']+1):
            self.assertEqual(transport.status(self.project, ['adjusted-prices'])['status'], 'refresh_required')
            with self.assertRaises(ValueError): transport.save(self.project, self.job)

    def test_corrupt_or_extra_members_do_not_replace_existing_data(self):
        self.install()
        original = (self.project/'xyquant-data/inputs/datasets/adjusted-prices/prices.parquet').read_bytes()
        self.bundle['snapshot_id'] = 'flat-cache-'+'d'*32
        self.write_archive(extra='../escaped')
        with self.assertRaises(ValueError): self.install()
        self.assertFalse((self.project.parent/'escaped').exists())
        self.assertEqual((self.project/'xyquant-data/inputs/datasets/adjusted-prices/prices.parquet').read_bytes(), original)
        self.assertEqual(list((self.project/'xyquant-data/.scratch/datasets').iterdir()), [])
        (self.sources/'datasets/adjusted-prices/prices.parquet').write_bytes(b'x'*len(original))
        self.write_archive()
        with self.assertRaises(ValueError): self.install()
        self.assertEqual((self.project/'xyquant-data/inputs/datasets/adjusted-prices/prices.parquet').read_bytes(), original)

    def test_unverified_or_mismatched_source_cannot_enable_server_origin(self):
        self.install()
        folder = self.project/'xyquant-data/inputs/datasets/adjusted-prices'
        (folder/transport.RECEIPT).unlink()
        with self.assertRaises(ValueError): transport.verify_source(self.project, 'adjusted-prices')
        self.assertEqual(transport.status(self.project, ['adjusted-prices'])['status'], 'refresh_required')
        bad = dict(self.job, tool='query_stock_data')
        with self.assertRaises(ValueError): transport.save(self.project, bad)

    def test_server_and_offline_input_produce_identical_rankic_results(self):
        self.install()
        inputs = self.project/'xyquant-data/inputs/rankic'; inputs.mkdir()
        pq.write_table(pa.table({'date':[d for d in self.days for _ in self.codes],
                                'stock_code':self.codes*len(self.days), 'value':list(range(20))*len(self.days)}), inputs/'factor.parquet')
        summaries = []
        for origin in ('local_reference', 'xyquant_server'):
            config = {'parameters':{'factor_name':'synthetic', 'start':'2025-01-02','end':'2025-01-07',
                                    'frequency':'daily','pool':'all_a','min_stocks':20},
                      'data':{'origin':origin,'factor_name':'synthetic','authorized_codes':self.codes,
                              'factor':{'path':'factor.parquet','format':'parquet'}}}
            path=inputs/'config.json';path.write_text(json.dumps(config),encoding='utf-8')
            result=subprocess.run([sys.executable,'-B','-X','utf8',str(PLUGIN/'skills/factor-rankic/scripts/rankic.py'),
                                   '--project',str(self.project),'--config',str(path)],capture_output=True,encoding='utf-8',timeout=40)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=json.loads(result.stdout);summaries.append(result['summary'])
            self.assertEqual({p.name for p in Path(result['output_dir']).iterdir()}, {'report.md','metadata.json','rankic.xlsx'})
        self.assertEqual(summaries[0],summaries[1])

    def test_bad_cli_input_is_redacted(self):
        sys.path.insert(0,str(PLUGIN/'shared'))
        try:
            from dataset_transfer import accept
        finally:
            sys.path.pop(0)
        for payload in (b'[]', b'{"ticket":"DO-NOT-ECHO"}', b'not-json'):
            result=accept(self.project, 'a'*32, payload)
            self.assertEqual(result['status'],'failed')
            self.assertNotIn('DO-NOT-ECHO',json.dumps(result))

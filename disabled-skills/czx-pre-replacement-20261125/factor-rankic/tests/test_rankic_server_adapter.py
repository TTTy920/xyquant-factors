"""Current server contracts, hourly cache and customer factor adaptation."""
import copy
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import rankic_data as download
from rankic_store import Store
from rankic_engine import Parameters, calculate
from rankic_factor import prepare
from rankic_inputs import SourceFiles
from rankic_workflow import assessment
from test_rankic_store import make_catalog


class ServerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve();self.source=self.project/'base'
        self.catalog=make_catalog(self.source)
        self.catalog.update(origin='xyquant_server',server_contract='rankic-data-v1',
            snapshot_id='rankic-20261118-0123456789ab',cache_expires_at=time.time()+3600)
        self.catalog['scope']['kind']='server_public_data'
        self.write_catalog()

    def write_catalog(self):
        (self.source/'catalog.json').write_text(json.dumps(self.catalog),encoding='utf-8')

    def calculate(self,root,name='test'):
        store=Store(root);p=Parameters(name,'2025-01-03','2025-01-03','weekly','all_a',min_stocks=3)
        with store.bundle(p) as source:
            return calculate(p,source.calendar,source,calendar_through=source.calendar_through,price_through=source.price_through)

    def test_server_contract_runs_original_calculation_and_preflight(self):
        self.assertEqual(self.calculate(self.source)['summary']['mean_rank_ic'],1.)
        self.assertTrue(assessment(dict(factor_name='test',start='2025-01-03',end='2025-01-03',frequency='weekly',pool='all_a',min_stocks=3),self.source)['can_run'])
        self.catalog['server_contract']='unsupported';self.write_catalog()
        with self.assertRaises(ValueError):Store(self.source)

    def test_user_csv_and_library_indexed_parquet_preserve_factor_values(self):
        frame=pd.DataFrame({'date':pd.to_datetime(['2025-01-03']*3),'stock_code':['000001.SZ','000002.SZ','600000.SH'],'value':[3.,2.,1.]})
        csv=self.project/'user.csv';frame.to_csv(csv,index=False)
        a=prepare(self.source,csv,'custom',self.project)
        library=self.project/'library.parquet'
        frame.rename(columns={'stock_code':'stock_cd','value':'alpha'}).set_index(['date','stock_cd']).to_parquet(library)
        b=prepare(self.source,library,'library',self.project,'factor_library')
        self.assertEqual(self.calculate(a['data_root'],'custom')['summary']['mean_rank_ic'],-1.)
        self.assertEqual(self.calculate(b['data_root'],'library')['summary']['mean_rank_ic'],-1.)
        self.assertEqual(self.calculate(self.source)['summary']['mean_rank_ic'],1.)
        self.assertEqual(json.loads((Path(b['data_root'])/'catalog.json').read_text())['factor_input']['origin'],'factor_library')

    def test_duplicate_factor_keys_and_expired_base_are_rejected(self):
        p=self.project/'factor.csv'
        p.write_text('date,stock_code,value\n2025-01-03,000001.SZ,1\n2025-01-03,000001.SZ,2\n')
        with self.assertRaises(ValueError):prepare(self.source,p,'f',self.project)
        self.catalog['cache_expires_at']=time.time()-1;self.write_catalog()
        with self.assertRaises(ValueError):prepare(self.source,p,'f',self.project)

    def factor_file(self):
        path=self.project/'user.csv'
        path.write_text('date,stock_code,value\n2025-01-03,000001.SZ,3\n2025-01-03,000002.SZ,2\n2025-01-03,600000.SH,1\n')
        return path

    def shared_names(self):
        entries=[self.catalog['calendar'],*self.catalog['market']['files']]
        if 'provenance' in self.catalog:entries.append(self.catalog['provenance'])
        return [item['path'] for item in entries]

    def require_hardlinks(self):
        probe=self.project/'link-probe'
        try:os.link(self.source/'calendar.pqt',probe)
        except OSError:self.skipTest('filesystem does not support hard links')
        finally:probe.unlink(missing_ok=True)

    def test_parallel_tasks_share_files_without_invalidating_an_open_calculation(self):
        self.require_hardlinks()
        factor=self.factor_file();original_factor=factor.read_bytes()
        originals={name:(self.source/name).read_bytes() for name in self.shared_names()}
        store=Store(self.source);parameters=Parameters('test','2025-01-03','2025-01-03','weekly','all_a',min_stocks=3)
        with store.bundle(parameters) as active:
            with ThreadPoolExecutor(max_workers=4) as pool:
                tasks=list(pool.map(lambda i:prepare(self.source,factor,'input'+str(i),self.project),range(4)))
            self.assertEqual(len({task['data_root'] for task in tasks}),4)
            for i,task in enumerate(tasks):
                root=Path(task['data_root'])
                for name,content in originals.items():
                    self.assertTrue(os.path.samefile(self.source/name,root/name))
                    self.assertEqual((root/name).read_bytes(),content)
                self.assertFalse(os.path.samefile(self.source/'catalog.json',root/'catalog.json'))
                self.assertEqual(self.calculate(root,'input'+str(i))['summary']['mean_rank_ic'],-1.)
            factor_outputs=[Path(t['data_root'])/'factors/input/2025.pqt' for t in tasks]
            self.assertFalse(os.path.samefile(factor_outputs[0],factor_outputs[1]))
            active.assert_unchanged()
            shutil.rmtree(tasks[0]['data_root'])
            active.assert_unchanged()
            result=calculate(parameters,active.calendar,active,calendar_through=active.calendar_through,price_through=active.price_through)
            self.assertEqual(result['summary']['mean_rank_ic'],1.)
        self.assertEqual(factor.read_bytes(),original_factor)

    def test_unsupported_hardlinks_fall_back_to_independent_copies(self):
        with patch('rankic_factor.os.link',side_effect=OSError(errno.EXDEV,'different filesystems')):
            task=prepare(self.source,self.factor_file(),'input',self.project)
        for name in self.shared_names():
            target=Path(task['data_root'])/name
            self.assertFalse(os.path.samefile(self.source/name,target))
            self.assertEqual((self.source/name).read_bytes(),target.read_bytes())
        self.assertEqual(self.calculate(task['data_root'],'input')['summary']['mean_rank_ic'],-1.)
        self.assertEqual(self.calculate(self.source)['summary']['mean_rank_ic'],1.)

    def test_shared_content_changes_are_detected_even_with_preserved_mtime(self):
        self.require_hardlinks()
        task=prepare(self.source,self.factor_file(),'input',self.project)
        path=self.source/'market/2025.pqt';alias=Path(task['data_root'])/'market/2025.pqt'
        records=SourceFiles();records.verify(path)
        before=path.stat()
        with alias.open('r+b') as stream:
            byte=stream.read(1);stream.seek(0);stream.write(bytes([byte[0]^1]))
        os.utime(alias,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertEqual(path.stat().st_size,before.st_size)
        self.assertEqual(path.stat().st_mtime_ns,before.st_mtime_ns)
        with self.assertRaisesRegex(ValueError,'changed'):records.verify(path)
        with self.assertRaisesRegex(ValueError,'changed'):records.assert_unchanged()

    def bundle(self):
        files={p.relative_to(self.source).as_posix():p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        manifest=dict(schema_version=1,origin='xyquant_server',dataset='rankic',server_contract='rankic-data-v1',
            data_version=self.catalog['snapshot_id'],catalog='catalog.json',expires_at=self.catalog['cache_expires_at'],
            files={n:dict(bytes=len(v),sha256=hashlib.sha256(v).hexdigest()) for n,v in files.items()})
        raw=json.dumps(manifest).encode();digest=hashlib.sha256(raw).hexdigest()
        folder=download.cache_folder(self.project,self.catalog['snapshot_id'],digest);folder.mkdir(parents=True)
        archive=folder/'rankic-data.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for n,v in files.items():z.writestr(n,v)
            z.writestr('rankic-data.json',raw)
        return dict(id='1'*32,status='ready',tool='query_rankic_data',datasets=['rankic'],data_version=self.catalog['snapshot_id'],
                    filename='rankic-data.zip',bytes=archive.stat().st_size,sha256=download.digest(archive),manifest_sha256=digest,expires_at=manifest['expires_at'])

    def test_downloaded_archive_and_cache_match_contract_and_expire(self):
        job=self.bundle()
        with patch.object(download,'transfer',side_effect=AssertionError('must reuse checked archive')):
            result=download.save(job,self.project);self.assertEqual(result['status'],'ready')
            self.assertEqual(download.save(job,self.project),result)
        self.assertEqual(self.calculate(result['data_root'])['summary']['mean_rank_ic'],1.)
        with patch.object(download.time,'time',return_value=job['expires_at']):
            self.assertEqual(download.check(self.project,job['data_version'],job['manifest_sha256'])['status'],'expired')
            with self.assertRaises(ValueError):download.save(job,self.project)

    def test_isolated_launcher_can_check_missing_server_cache(self):
        entry=Path(__file__).resolve().parents[1]/'rankic_entry.py'
        p=subprocess.run([sys.executable,'-I','-B',str(entry),'data','status','--project',str(self.project),'--data-version',self.catalog['snapshot_id'],'--manifest-sha256','a'*64],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(json.loads(p.stdout)['status'],'missing')
        for target in ['download','factor','workflow']:
            p=subprocess.run([sys.executable,'-I','-B',str(entry),target,'--help'],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)

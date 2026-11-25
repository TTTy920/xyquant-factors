"""Download the original IPO Excel; keep calculations in calculate.py."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import zipfile

PLUGIN = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(PLUGIN), str(PLUGIN/'shared'), str(PLUGIN/'skills/portfolio-attribution/scripts')]
from xyquant_data import data_path
from xyquant_download import DownloadError, file_lock
from download_original import transfer
from mcp_receive import MAX_JOB_BYTES, receive_job

CONTRACT = 'xyquant-ipo-data-v1'
FILENAME = 'IPO发行数据.xlsx'
PARTS = ('inputs', 'ipo')
MAX_BYTES = 17 * 1024**2
HASH = re.compile(r'[a-f0-9]{64}')


def require(condition):
    if not condition:
        raise ValueError('IPO 下载数据无效或已过期')


def valid_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value > time.time()


def checksum(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def save(project, job):
    require(isinstance(job, dict) and job.get('status') == 'ready' and job.get('tool') == 'query_ipo_data'
            and job.get('datasets') == ['ipo'] and job.get('server_contract') == CONTRACT)
    require(isinstance(job.get('data_version'), str) and re.fullmatch(r'ipo-[a-f0-9]{32}', job['data_version'])
            and checksum(job.get('sha256')) and checksum(job.get('manifest_sha256'))
            and type(job.get('bytes')) is int and 0 < job['bytes'] <= MAX_BYTES and valid_time(job.get('expires_at')))
    grant = job.get('download')
    require(isinstance(grant, dict) and valid_time(grant.get('expires_at')))
    parent = data_path(project, PARTS, create=True)
    target = data_path(project, PARTS, parent/FILENAME, directory=False)
    receipt = data_path(project, PARTS, parent/'.xyquant-source.json', directory=False)
    with file_lock(parent/'.xyquant-install.lock', deadline=time.time()+30):
        with tempfile.TemporaryDirectory(prefix='.download-', dir=parent) as temp:
            temporary = Path(temp)
            archive_path = temporary/'ipo.zip'
            transfer(job, archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()
                require(len(entries) == 2 and {i.filename for i in entries} == {FILENAME, 'ipo-data.json'}
                        and not any(stat.S_IFMT(i.external_attr >> 16) == stat.S_IFLNK for i in entries)
                        and sum(i.file_size for i in entries) <= MAX_BYTES
                        and archive.getinfo('ipo-data.json').file_size <= MAX_JOB_BYTES)
                raw = archive.read('ipo-data.json')
                require(hashlib.sha256(raw).hexdigest() == job['manifest_sha256'])
                manifest = json.loads(raw)
                require(isinstance(manifest, dict) and manifest.get('schema_version') == 1
                        and manifest.get('origin') == 'xyquant_server' and manifest.get('dataset') == 'ipo'
                        and manifest.get('server_contract') == CONTRACT
                        and manifest.get('data_version') == job['data_version']
                        and manifest.get('expires_at') == job['expires_at']
                        and valid_time(manifest.get('expires_at')))
                created = manifest.get('created_at')
                require(type(created) in (int, float) and math.isfinite(created)
                        and 0 < manifest['expires_at']-created <= 3600)
                files = manifest.get('files')
                require(isinstance(files, dict) and set(files) == {FILENAME})
                spec = files[FILENAME]
                require(isinstance(spec, dict) and type(spec.get('bytes')) is int
                        and 0 < spec['bytes'] <= 16*1024**2 and checksum(spec.get('sha256'))
                        and job['data_version'] == 'ipo-'+spec['sha256'][:32]
                        and archive.getinfo(FILENAME).file_size == spec['bytes'])
                workbook = archive.read(FILENAME)
                require(len(workbook) == spec['bytes'] and hashlib.sha256(workbook).hexdigest() == spec['sha256'])
            staged = temporary/FILENAME
            staged.write_bytes(workbook)
            record = temporary/'source.json'
            record.write_text(json.dumps({'manifest': manifest, 'manifest_sha256': job['manifest_sha256']},
                                         ensure_ascii=False), encoding='utf-8')
            data_path(project, PARTS, target, directory=False)
            data_path(project, PARTS, receipt, directory=False)
            os.replace(staged, target)
            os.replace(record, receipt)
    return dict(status='ready', path=str(target), data_version=job['data_version'],
                sha256=spec['sha256'], expires_at=job['expires_at'])


def accept(project, job_id, raw):
    try:
        require(0 < len(raw) <= MAX_JOB_BYTES)
        job = json.loads(raw)
        require(isinstance(job, dict) and job.get('id') == job_id)
        return save(project, job)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, DownloadError):
        return dict(status='failed', message='IPO 数据下载或校验失败；请检查本次任务、下载凭据、空间及数据有效期')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('save', 'receive'))
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--job', required=True)
    parser.add_argument('--wait-seconds', type=int, default=120, choices=range(15, 241), metavar='15..240')
    args = parser.parse_args()
    if not args.project.is_absolute() or not args.project.is_dir() or not re.fullmatch(r'[a-f0-9]{32}', args.job):
        parser.error('existing absolute project and valid job ID required')
    result = (accept(args.project, args.job, sys.stdin.buffer.read(MAX_JOB_BYTES+1)) if args.action == 'save'
              else receive_job(args.job, args.wait_seconds, lambda raw: accept(args.project, args.job, raw)))
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] == 'ready' else 2


if __name__ == '__main__':
    raise SystemExit(main())

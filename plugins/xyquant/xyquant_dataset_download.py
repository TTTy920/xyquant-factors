"""Install authorized dataset packages; preserve every source Parquet byte."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import math
import re
import shutil
import stat
import sys
import tempfile
import time
import zipfile

from xyquant_catalog import validate_manifest, measure
from xyquant_data import data_path, check_kind

_helpers = [str(Path(__file__).parent/'shared'), str(Path(__file__).parent/'skills/portfolio-attribution/scripts')]
sys.path[:0] = _helpers
try:
    from xyquant_download import DownloadError, file_lock
    from download_original import transfer
finally:
    del sys.path[:len(_helpers)]

CONTRACT = 'xyquant-flat-data-v1'
MAX_BYTES = 1024**3
HASH = re.compile(r'[a-f0-9]{64}')
VERSION = re.compile(r'flat-[a-f0-9]{32}')
GROUPS = {
    'rankic': ('adjusted-prices', 'trading-calendar', 'listed-history'),
    'attribution_market': ('attribution-market',),
    'barra_exposures': ('barra-exposures',),
    'trading_calendar': ('trading-calendar',),
    'industry_classification': ('industry-classification',),
}
DATASETS = {name for names in GROUPS.values() for name in names}
RECEIPT = '.xyquant-source.json'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def valid_expiry(value):
    return type(value) in (int, float) and math.isfinite(value) and value > time.time()


def safe_name(name):
    require(isinstance(name, str), '数据文件路径无效')
    path = PurePosixPath(name)
    require(len(path.parts)==3 and path.parts[0]=='datasets' and path.parts[1] in DATASETS
            and path.as_posix()==name and '\\' not in name and ':' not in name
            and (path.parts[2]=='manifest.json' or re.fullmatch(r'(?:[0-9]{4}|prices|calendar|listed|monthly)\.parquet',path.parts[2])),
            '数据文件路径无效')
    return path


def manifest_value(raw, expected, *, job=None):
    require(isinstance(expected,str) and HASH.fullmatch(expected) and hashlib.sha256(raw).hexdigest()==expected,
            '基础数据总清单校验失败')
    require(len(raw)<=4*1024**2, '基础数据总清单过大')
    value=json.loads(raw)
    require(isinstance(value,dict) and value.get('schema_version')==1 and value.get('origin')=='xyquant_server'
            and value.get('server_contract')==CONTRACT, '基础数据来源或契约无效')
    group=value.get('bundle_group')
    require(isinstance(group,str) and group in GROUPS and value.get('dataset_ids')==list(GROUPS[group]), '基础数据集合无效')
    require(set(value)==set('schema_version origin server_contract bundle_group data_version snapshot_id dataset_ids created_at expires_at files dataset_manifests'.split())|({'scope'} if group=='rankic' else set()),
            '基础数据总清单字段无效')
    require(isinstance(value.get('data_version'),str) and VERSION.fullmatch(value['data_version'])
            and isinstance(value.get('snapshot_id'),str) and re.fullmatch(r'flat-cache-[a-f0-9]{32}',value['snapshot_id']),
            '基础数据版本无效')
    require(valid_expiry(value.get('expires_at')), '基础数据包已过期，请重新查询和下载')
    created=value.get('created_at')
    require(type(created) in (int,float) and math.isfinite(created) and 0<value['expires_at']-created<=3600,
            '基础数据包有效期无效')
    files=value.get('files')
    require(isinstance(files,dict) and 1<=len(files)<=100, '基础数据文件清单无效')
    for name,spec in files.items():
        path=safe_name(name)
        require(path.parts[1] in GROUPS[group] and isinstance(spec,dict) and set(spec)=={'bytes','sha256'}
                and type(spec['bytes']) is int and 0<spec['bytes']<=MAX_BYTES
                and isinstance(spec['sha256'],str) and HASH.fullmatch(spec['sha256']), '基础数据文件信息无效')
    require(sum(s['bytes'] for s in files.values())<=MAX_BYTES, '基础数据包超过大小限制')
    pins=value.get('dataset_manifests')
    require(isinstance(pins,dict) and set(pins)==set(GROUPS[group]), '数据集清单集合无效')
    for name,sha in pins.items():
        require(files.get('datasets/'+name+'/manifest.json',{}).get('sha256')==sha, '数据集清单身份不符')
    if group=='rankic':
        scope=value.get('scope',{})
        codes=scope.get('codes')
        require(scope.get('kind')=='server_public_data' and isinstance(codes,list) and 1<=len(codes)<=100000
                and all(isinstance(c,str) and re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)',c) for c in codes)
                and len(set(codes))==len(codes), '服务器股票范围无效')
    if job is not None:
        for key in ('server_contract','data_version','snapshot_id','bundle_group','dataset_ids','expires_at'):
            require(value.get(key)==job.get(key), '数据包与授权任务不一致')
    return value


def verify_source(project, name, *, verify_files=False):
    require(name in DATASETS, '未知基础数据集')
    folder=data_path(project,('inputs','datasets',name))
    receipt=data_path(project,('inputs','datasets',name),folder/RECEIPT,directory=False)
    require(receipt.is_file() and receipt.stat().st_size<=4*1024**2, '缺少已校验的服务器数据来源记录')
    record=json.loads(receipt.read_text(encoding='utf-8'))
    raw=json.dumps(record['manifest'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'
    bundle=manifest_value(raw,record['manifest_sha256'])
    require(name in bundle['dataset_ids'], '服务器数据来源记录不适用于此目录')
    manifest_path=data_path(project,('inputs','datasets',name),folder/'manifest.json',directory=False)
    require(manifest_path.is_file() and manifest_path.stat().st_size<=4*1024**2, '数据集清单缺失或过大')
    raw=manifest_path.read_bytes();manifest=json.loads(raw)
    require(hashlib.sha256(raw).hexdigest()==bundle['dataset_manifests'][name], '本地数据集版本与服务器记录不符')
    validate_manifest(manifest,name,manifest['version'])
    for filename,spec in manifest['files'].items():
        key='datasets/'+name+'/'+filename
        require(bundle['files'].get(key)==spec, '数据文件与服务器总清单不符')
        p=data_path(project,('inputs','datasets',name),folder/filename,directory=False)
        require(p.is_file() and p.stat().st_size==spec['bytes'], '基础数据文件缺失或大小不符')
        if verify_files:require(measure(p)==spec,'基础数据文件校验失败')
    return bundle


def status(project, names):
    require(bool(names) and all(n in DATASETS for n in names),'请明确指定基础数据集')
    result={}
    for name in names:
        try:
            bundle=verify_source(project,name,verify_files=True)
            result[name]={'status':'ready','data_version':bundle['data_version'],'expires_at':bundle['expires_at']}
        except (OSError,ValueError,KeyError,TypeError):
            result[name]={'status':'refresh_required'}
    return {'status':'ready' if all(v['status']=='ready' for v in result.values()) else 'refresh_required','datasets':result}


def save(project, job):
    require(isinstance(job,dict) and job.get('status')=='ready' and job.get('datasets')==['flat_inputs']
            and job.get('server_contract')==CONTRACT, '请提供已就绪的基础数据下载任务')
    group=job.get('bundle_group')
    require(isinstance(group,str) and group in GROUPS and job.get('tool')==('query_rankic_data' if group=='rankic' else 'query_stock_data')
            and job.get('dataset_ids')==list(GROUPS[group]), '基础数据工具或集合不一致')
    require(valid_expiry(job.get('expires_at')) and type(job.get('bytes')) is int and 0<job['bytes']<=MAX_BYTES
            and isinstance(job.get('sha256'),str) and HASH.fullmatch(job['sha256']), '基础数据任务已过期或文件信息无效')
    require(re.fullmatch(r'[a-f0-9]{32}',job.get('id','')) is not None, '下载任务编号无效')
    parent=data_path(project,('inputs','datasets'),create=True)
    scratch=data_path(project,('.scratch','datasets'),create=True)
    with file_lock(parent/'.xyquant-install.lock', deadline=time.time()+30):
        try:
            for name in GROUPS[group]:
                cached=verify_source(project,name,verify_files=True)
                require(cached['snapshot_id']==job['snapshot_id'],'刷新数据包')
            return {'status':'ready','datasets':list(GROUPS[group]),'data_root':str(parent),'expires_at':job['expires_at']}
        except (OSError,ValueError,KeyError,TypeError):
            pass
        require(shutil.disk_usage(parent).free>=3*job['bytes'],'本地空间不足以下载并校验基础数据包')
        with tempfile.TemporaryDirectory(dir=scratch,prefix='receive-') as temporary:
            temp=Path(temporary);archive_path=temp/'datasets.zip'
            transfer(job,archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                info=archive.getinfo('dataset-bundle.json')
                require(info.file_size<=4*1024**2,'基础数据总清单过大')
                raw=archive.read(info)
                bundle=manifest_value(raw,job.get('manifest_sha256'),job=job)
                items=archive.infolist();files=bundle['files']
                require(len(items)==len(files)+1 and {i.filename for i in items}==set(files)|{'dataset-bundle.json'}
                        and not any(stat.S_IFMT(i.external_attr>>16)==stat.S_IFLNK for i in items)
                        and sum(i.file_size for i in items)<=MAX_BYTES,'基础数据包包含非预期文件')
                for name,spec in files.items():
                    safe_name(name);target=temp/name;target.parent.mkdir(parents=True,exist_ok=True)
                    require(archive.getinfo(name).file_size==spec['bytes'],'数据文件大小不符')
                    h=hashlib.sha256();size=0
                    with archive.open(name) as src,target.open('wb') as dst:
                        for block in iter(lambda:src.read(1024*1024),b''):
                            size+=len(block);require(size<=spec['bytes'],'数据文件超过声明大小');h.update(block);dst.write(block)
                    require(size==spec['bytes'] and h.hexdigest()==spec['sha256'],'数据文件校验失败')
                receipt=json.dumps({'manifest':bundle,'manifest_sha256':job['manifest_sha256']},ensure_ascii=False,allow_nan=False)
                for name in GROUPS[group]:
                    folder=temp/'datasets'/name
                    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
                    validate_manifest(manifest,name,manifest['version'])
                    expected={'datasets/'+name+'/'+f:spec for f,spec in manifest['files'].items()}
                    actual={f:s for f,s in files.items() if f.startswith('datasets/'+name+'/') and not f.endswith('/manifest.json')}
                    require(actual==expected,'数据集文件与总清单不一致')
                    (folder/RECEIPT).write_text(receipt,encoding='utf-8')
                    (folder/RECEIPT).chmod(0o600)
            require(valid_expiry(job['expires_at']),'基础数据包已过期，请重新查询和下载')
            replaced=[];installed=[]
            try:
                for name in GROUPS[group]:
                    target=data_path(project,('inputs','datasets',name))
                    if check_kind(target,directory=True):
                        backup=temp/('backup-'+name);target.rename(backup);replaced.append((target,backup))
                    (temp/'datasets'/name).rename(target);installed.append(target)
            except BaseException:
                for target in reversed(installed):shutil.rmtree(target)
                for target,backup in reversed(replaced):backup.rename(target)
                raise
        return {'status':'ready','datasets':list(GROUPS[group]),'data_root':str(parent),'expires_at':job['expires_at']}

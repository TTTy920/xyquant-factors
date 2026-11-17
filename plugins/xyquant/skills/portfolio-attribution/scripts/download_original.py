"""Download and reuse the checked original-data snapshot; calculate nothing."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

from download_data import NoRedirect, ORIGIN


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def cache_folder(project, version, manifest_sha256):
    project = Path(project)
    if not project.is_absolute() or not project.is_dir():
        raise ValueError('请指定已有本地项目的绝对路径')
    if not re.fullmatch(r'original-[0-9]{8}-[a-f0-9]{12}', version or ''):
        raise ValueError('原版数据版本无效')
    if not re.fullmatch(r'[a-f0-9]{64}', manifest_sha256 or ''):
        raise ValueError('原版数据清单校验值无效')
    return project / 'downloads/attribution-original' / version


def manifest_value(content, version, expected):
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('原版数据清单校验失败')
    manifest = json.loads(content)
    if (manifest.get('origin') != 'xyquant_server' or manifest.get('dataset') != 'original_attribution'
            or manifest.get('data_version') != version or manifest.get('catalog') != 'catalog.json'):
        raise ValueError('原版数据来源或版本不一致')
    files = manifest.get('files', {})
    if not isinstance(files, dict) or not 1 <= len(files) <= 100 or 'catalog.json' not in files:
        raise ValueError('原版数据文件清单无效')
    for name, spec in files.items():
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or '\\' in name or path.as_posix() != name
                or path.suffix not in {'.parquet', '.json', '.md'} or name == 'original-data.json'
                or type(spec.get('bytes')) is not int or spec['bytes'] < 0
                or not re.fullmatch(r'[a-f0-9]{64}', spec.get('sha256', ''))):
            raise ValueError('原版数据文件信息无效')
    if sum(spec['bytes'] for spec in files.values()) > 1024 ** 3:
        raise ValueError('原版数据超过允许大小')
    return manifest


def check(project, version, manifest_sha256):
    folder = cache_folder(project, version, manifest_sha256)
    root = folder / 'data'
    if not root.exists():
        return {'status': 'missing', 'data_version': version}
    manifest = manifest_value((root / 'original-data.json').read_bytes(), version, manifest_sha256)
    for name, spec in manifest['files'].items():
        path = root / name
        if not path.is_file() or path.stat().st_size != spec['bytes'] or digest(path) != spec['sha256']:
            raise ValueError('本地原版数据校验失败：' + name)
    return {'status': 'ready', 'data_version': version, 'data_root': str(root),
            'manifest_sha256': manifest_sha256}


def transfer(job, target):
    jid = job.get('id')
    grant = job.get('download', {})
    if (not re.fullmatch(r'[a-f0-9]{32}', jid or '')
            or grant.get('url') != ORIGIN + '/factors/api/attribution-data/' + jid
            or grant.get('expires_at', 0) <= time.time()
            or not re.fullmatch(r'[A-Za-z0-9_-]{30,128}', grant.get('ticket', ''))):
        raise ValueError('请通过同一数据任务取得有效下载凭据')
    request = urllib.request.Request(grant['url'], headers={
        'Authorization': 'XYQuant-Data ' + grant['ticket'], 'Accept-Encoding': 'identity'})
    for attempt in range(3):
        pending = None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=40) as response, \
                    tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as dest:
                pending = Path(dest.name)
                value = hashlib.sha256()
                received = 0
                for block in iter(lambda: response.read(1024 * 1024), b''):
                    received += len(block)
                    if received > job['bytes']:
                        raise ValueError('数据下载超过声明大小')
                    dest.write(block)
                    value.update(block)
            if received != job['bytes'] or value.hexdigest() != job['sha256']:
                raise ValueError('数据下载未通过完整性校验')
            pending.replace(target)
            return
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 2:
                raise ValueError('数据下载失败，请重新查询任务状态和下载凭据') from None
        except (OSError, urllib.error.URLError):
            if attempt == 2:
                raise ValueError('数据下载连接失败，请保留任务编号后重试') from None
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
        time.sleep(attempt + 1)


def save(job, project):
    if (job.get('status') != 'ready' or job.get('tool') != 'query_stock_data'
            or job.get('datasets') != ['original_attribution'] or type(job.get('bytes')) is not int
            or not 0 < job['bytes'] <= 1024 ** 3 or not re.fullmatch(r'[a-f0-9]{64}', job.get('sha256', ''))):
        raise ValueError('请提供原版归因数据包的就绪任务')
    version, expected = job.get('data_version'), job.get('manifest_sha256')
    result = check(project, version, expected)
    if result['status'] == 'ready':
        return result
    folder = cache_folder(project, version, expected)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive_path = folder / 'attribution-data.zip'
    if archive_path.exists():
        if archive_path.stat().st_size != job['bytes'] or digest(archive_path) != job['sha256']:
            raise ValueError('已有数据包与本次任务不一致')
    else:
        transfer(job, archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        if archive.getinfo('original-data.json').file_size > 1024 * 1024:
            raise ValueError('原版数据清单过大')
        manifest = manifest_value(archive.read('original-data.json'), version, expected)
        names = {'original-data.json', *manifest['files']}
        if (len(archive.infolist()) != len(names) or set(archive.namelist()) != names
                or any((item.external_attr >> 16) & 0o170000 == 0o120000 for item in archive.infolist())
                or sum(item.file_size for item in archive.infolist()) > 1024 ** 3):
            raise ValueError('原版数据包文件集合无效')
        with tempfile.TemporaryDirectory(dir=folder) as tmp:
            root = Path(tmp) / 'data'
            root.mkdir()
            for name in sorted(names):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, path.open('wb') as dest:
                    shutil.copyfileobj(source, dest)
            for name, spec in manifest['files'].items():
                path = root / name
                if path.stat().st_size != spec['bytes'] or digest(path) != spec['sha256']:
                    raise ValueError('原版数据文件校验失败：' + name)
            root.rename(folder / 'data')
    return check(project, version, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ['status', 'save']:
        p = sub.add_parser(command)
        p.add_argument('--project', required=True)
        if command == 'status':
            p.add_argument('--data-version', required=True)
            p.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    try:
        result = (check(args.project, args.data_version, args.manifest_sha256) if args.command == 'status'
                  else save(json.load(sys.stdin), args.project))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

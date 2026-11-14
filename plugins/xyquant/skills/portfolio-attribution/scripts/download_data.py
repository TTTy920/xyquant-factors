"""Download server public data using the data tool's scoped read credential."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.request
import zipfile

ORIGIN = 'https://47.103.215.251'
FILES = {'data.json', 'stock_returns.parquet', 'industry.parquet', 'exposures.parquet', 'factor_returns.parquet'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('数据地址发生重定向，请重新查询任务')


def save(job, project):
    jid = job.get('id');grant = job.get('download', {})
    if (job.get('status')!='ready' or not isinstance(jid, str) or not re.fullmatch(r'[a-f0-9]{32}', jid)
            or grant.get('url') != ORIGIN+'/factors/api/attribution-data/'+jid or grant.get('expires_at', 0)<=time.time()
            or not re.fullmatch(r'[A-Za-z0-9_-]{30,128}', grant.get('ticket', ''))):
        raise ValueError('请取得该数据任务的有效下载凭据')
    if type(job.get('bytes')) is not int or not 0<job['bytes']<=1024**3 or not re.fullmatch(r'[a-f0-9]{64}', job.get('sha256', '')):
        raise ValueError('数据文件信息无效')
    project = Path(project)
    if not project.is_absolute() or not project.is_dir():
        raise ValueError('请指定已有本地项目的绝对路径')
    folder = project/'downloads'/'attribution-data'/jid
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder/'attribution-data.zip'
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest()!=job['sha256']:
            raise ValueError('已有缓存与本次数据不一致')
    else:
        request = urllib.request.Request(grant['url'], headers={'Authorization': 'XYQuant-Data '+grant['ticket'], 'Accept-Encoding': 'identity'})
        pending = None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=40) as response, tempfile.NamedTemporaryFile(dir=folder, delete=False) as dest:
                pending = Path(dest.name);digest = hashlib.sha256();received = 0
                while True:
                    block = response.read(1024*1024)
                    if not block:
                        break
                    received += len(block)
                    if received>job['bytes']:
                        raise ValueError('数据内容超过声明大小')
                    digest.update(block);dest.write(block)
            if received!=job['bytes'] or digest.hexdigest()!=job['sha256']:
                raise ValueError('数据下载未通过完整性校验')
            pending.rename(target)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
    with zipfile.ZipFile(target) as archive:
        if set(archive.namelist())!=FILES or len(archive.infolist())!=len(FILES) or sum(x.file_size for x in archive.infolist())>2*1024**3:
            raise ValueError('公共数据包的文件集合无效')
        archive.extractall(folder)
    manifest = json.loads((folder/'data.json').read_text())
    if manifest.get('origin')!='xyquant_server':
        raise ValueError('公共数据来源无效')
    for name, spec in manifest['files'].items():
        if name not in FILES or hashlib.sha256((folder/name).read_bytes()).hexdigest()!=spec['sha256']:
            raise ValueError('公共数据表校验失败')
    return {'status': 'ready', 'input_config': str(folder/'data.json')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__);parser.add_argument('--project', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(save(json.load(sys.stdin), args.project), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)

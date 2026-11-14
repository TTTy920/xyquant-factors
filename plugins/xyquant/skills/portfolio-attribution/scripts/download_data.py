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
GROUP_FILES = {
    'query_stock_data': {'data.json', 'stock_returns.parquet', 'industry.parquet'},
    'query_barra_data': {'data.json', 'exposures.parquet', 'factor_returns.parquet'},
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('数据地址发生重定向，请重新查询任务')


def download(job, project, tool=None):
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
    expected = GROUP_FILES[tool] if tool else FILES
    if tool and job.get('tool') != tool:
        raise ValueError('数据任务与取数工具不匹配')
    with zipfile.ZipFile(target) as archive:
        if set(archive.namelist())!=expected or len(archive.infolist())!=len(expected) or sum(x.file_size for x in archive.infolist())>2*1024**3:
            raise ValueError('公共数据包的文件集合无效')
        archive.extractall(folder)
    manifest = json.loads((folder/'data.json').read_text())
    if manifest.get('origin')!='xyquant_server':
        raise ValueError('公共数据来源无效')
    if set(manifest.get('files', {})) != expected-{'data.json'}:
        raise ValueError('公共数据表集合无效')
    for name, spec in manifest['files'].items():
        if name not in expected or hashlib.sha256((folder/name).read_bytes()).hexdigest()!=spec['sha256']:
            raise ValueError('公共数据表校验失败')
    return folder, manifest


def save(jobs, project):
    if jobs.get('status') == 'ready':
        folder, _ = download(jobs, project)
        return {'status': 'ready', 'input_config': str(folder/'data.json')}
    if set(jobs) != set(GROUP_FILES):
        raise ValueError('请提供股票基础数据和Barra两个就绪任务')
    sources = {tool: download(jobs[tool], project, tool) for tool in GROUP_FILES}
    stock_folder, stock = sources['query_stock_data']
    barra_folder, barra = sources['query_barra_data']
    for key in ['start', 'end', 'codes']:
        if stock.get('query', {}).get(key) != barra.get('query', {}).get(key):
            raise ValueError('两个数据任务的日期或股票范围不一致')
    expected_paths = [(stock, 'market', 'stock_returns.parquet'), (stock, 'industry', 'industry.parquet'),
                      (barra, 'exposures', 'exposures.parquet')]
    if any(data.get(key) != name for data, key, name in expected_paths):
        raise ValueError('公共数据表路径无效')
    frequency = barra['query']['frequency']
    if barra.get('factor_returns') != {frequency: 'factor_returns.parquet'}:
        raise ValueError('因子收益率频率或路径无效')
    identity = json.dumps({tool: {'id': jobs[tool]['id'], 'sha256': jobs[tool]['sha256']} for tool in sorted(jobs)}, sort_keys=True).encode()
    folder = Path(project)/'downloads'/'attribution-data'/'combined'/hashlib.sha256(identity).hexdigest()[:32]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = {'schema_version': 1, 'origin': 'xyquant_server',
              'market': str(stock_folder/'stock_returns.parquet'), 'industry': str(stock_folder/'industry.parquet'),
              'exposures': str(barra_folder/'exposures.parquet'),
              'factor_returns': {frequency: str(barra_folder/'factor_returns.parquet')},
              'factor_columns': barra['factor_columns'], 'calendar': 'derived from stock_returns.date'}
    target = folder/'data.json'
    text = json.dumps(config, ensure_ascii=False, indent=2)+'\n'
    if target.exists() and target.read_text(encoding='utf-8') != text:
        raise ValueError('已有本地数据配置与本次任务不一致')
    if not target.exists():
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, delete=False) as stream:
            stream.write(text);pending = Path(stream.name)
        pending.replace(target)
    return {'status': 'ready', 'input_config': str(target)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__);parser.add_argument('--project', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(save(json.load(sys.stdin), args.project), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)

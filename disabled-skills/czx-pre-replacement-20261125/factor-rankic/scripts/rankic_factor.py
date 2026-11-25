"""Normalize one local factor against a downloaded RankIC market snapshot."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rankic_engine import normalize_code
from rankic_inputs import DataContractError, SourceFiles, _check_links
from rankic_store import Store


def load_factor(path):
    path = Path(path).absolute();_check_links(path)
    if not path.is_file() or path.stat().st_size > 1024**3:
        raise DataContractError('因子文件不存在或超过1GiB')
    sources = SourceFiles();fingerprint = sources.verify(path)
    strings = {k:str for k in ('stock_code','stock_cd','股票代码')}
    if path.suffix.lower() in ('.parquet','.pqt'):
        frame = pd.read_parquet(path)
        if isinstance(frame.index, pd.MultiIndex) or frame.index.name is not None:frame = frame.reset_index()
    elif path.suffix.lower() in ('.csv','.tsv'):
        frame = pd.read_csv(path, dtype=strings, sep='\t' if path.suffix.lower()=='.tsv' else ',')
    elif path.suffix.lower() == '.xlsx':
        frame = pd.read_excel(path, dtype=strings)
    else:raise DataContractError('因子文件支持CSV、TSV、XLSX或Parquet长表')
    layouts = [('date','stock_code','value'),('date','stock_cd','alpha'),('日期','股票代码','因子值')]
    matches = [keys for keys in layouts if set(keys) <= set(frame.columns)]
    if len(matches) != 1:raise DataContractError('因子列需明确为date/stock_code/value，或date/stock_cd/alpha，或日期/股票代码/因子值')
    frame = frame.loc[:,list(matches[0])].copy();frame.columns=['date','stock_code','value']
    frame['date'] = pd.to_datetime(frame['date'], errors='raise')
    if frame['date'].isna().any() or frame['date'].dt.tz is not None or not frame['date'].eq(frame['date'].dt.normalize()).all():
        raise DataContractError('因子日期必须为无时区的自然日')
    frame['stock_code'] = frame['stock_code'].map(normalize_code)
    frame['value'] = pd.to_numeric(frame['value'],errors='raise').astype('float64')
    if frame.empty or frame.duplicated(['date','stock_code']).any():raise DataContractError('因子文件为空或存在重复日期/股票键')
    sources.assert_unchanged()
    return frame.sort_values(['date','stock_code']), {k:fingerprint[k] for k in ('sha256','bytes')}


def prepare(data_root, factor_file, factor_name, project, source='user'):
    if not isinstance(factor_name,str) or not factor_name.strip() or len(factor_name)>200 or any(ord(x)<32 for x in factor_name):
        raise DataContractError('请提供有效因子名称')
    project = Path(project)
    if not project.is_absolute() or not project.is_dir():raise DataContractError('请提供已有项目的绝对路径')
    _check_links(project)
    store = Store(data_root)
    if store.catalog['origin']=='xyquant_server' and store.catalog.get('cache_expires_at',0)<=time.time():
        raise DataContractError('基础数据包已过期，请重新通过MCP取数')
    frame, fingerprint = load_factor(factor_file)
    folder = project/'downloads/rankic-inputs'/uuid.uuid4().hex
    folder.mkdir(parents=True,mode=0o700);_check_links(folder)
    try:
        catalog = copy.deepcopy(store.catalog)
        refs = [catalog['calendar'], *catalog['market']['files']]
        if 'provenance' in catalog:refs.append(catalog['provenance'])
        for entry in refs:
            original = store.path(entry['path'])
            store.source_files.verify(original,entry['sha256'],entry['bytes'])
            target=folder/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
            try:
                os.link(original,target)
            except OSError:
                shutil.copy2(original,target)
        entries=[]
        for year, group in frame.groupby(frame['date'].dt.year):
            name=f'factors/input/{year}.pqt';path=folder/name;path.parent.mkdir(parents=True,exist_ok=True)
            schema=pa.schema([('date',pa.timestamp('ns')),('stock_code',pa.string()),('value',pa.float64())])
            pq.write_table(pa.Table.from_pandas(group,schema=schema,preserve_index=False),path,compression='zstd',row_group_size=100000)
            entries.append(dict(path=name,year=int(year),bytes=path.stat().st_size,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),rows=len(group),
                                first_date=group['date'].min().date().isoformat(),last_date=group['date'].max().date().isoformat()))
        catalog['factors']=[dict(id='input-'+fingerprint['sha256'][:12],name=factor_name.strip(),aliases=[],files=entries)]
        catalog['factor_input']={'origin':source,**fingerprint}
        catalog['market_snapshot_id']=catalog['snapshot_id']
        catalog['snapshot_id']=catalog['snapshot_id']+'-input-'+fingerprint['sha256'][:12]
        store.assert_unchanged()
        (folder/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        checked=Store(folder);checked.assert_unchanged()
        return dict(status='ready',data_root=str(folder),factor_name=factor_name.strip(),rows=len(frame),
                    first_date=entries[0]['first_date'],last_date=entries[-1]['last_date'],input_sha256=fingerprint['sha256'])
    except BaseException:
        shutil.rmtree(folder)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--data-root',required=True);p.add_argument('--factor-file',required=True)
    p.add_argument('--factor-name',required=True);p.add_argument('--project',required=True)
    p.add_argument('--source',choices=['user','factor_library'],default='user')
    args=parser.parse_args(argv)
    try:
        result=prepare(args.data_root,args.factor_file,args.factor_name,args.project,args.source)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0
    except Exception as error:
        message=str(error) if isinstance(error,DataContractError) else '因子文件适配失败，请检查日期、代码、数值字段和文件权限'
        print(json.dumps({'status':'error','message':message},ensure_ascii=False));return 2

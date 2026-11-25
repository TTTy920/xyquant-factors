#!/usr/bin/env python3
"""只读净值分析，单利收益相对初始资金。依赖 openpyxl。"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
from contextlib import closing
from openpyxl import load_workbook

def as_date(v):
    if isinstance(v,dt.datetime):return v.date()
    if isinstance(v,dt.date):return v
    if isinstance(v,str):return dt.date.fromisoformat(v.strip()[:10])
    raise ValueError('日期必须为 Excel 日期或 ISO 日期文本')

def drawdown(base,values):
    if base<=0 or any(v<=0 for v in values):return None
    peak=base;worst=0.0
    for v in values:peak=max(peak,v);worst=min(worst,v/peak-1)
    return worst

def analyze(path,start=None,end=None):
    start=as_date(start) if start else None;end=as_date(end) if end else None
    if start and end and start>end:raise ValueError('开始日晚于结束日')
    found=None
    with closing(load_workbook(path,read_only=True,data_only=True)) as book:
        for sheet in book:
            it=sheet.iter_rows(values_only=True);header=next(it,())
            if all(k in header for k in ['日期','策略净值','基准净值']):
                found=(sheet.title,header,list(it));break
    if found is None:raise ValueError('未找到日期、策略净值、基准净值三列')
    sheet,header,rows=found;indices=[header.index(k) for k in ['日期','策略净值','基准净值']];data=[]
    for row in rows:
        if not any(v is not None for v in row):continue
        day,a,b=[row[i] if i<len(row) else None for i in indices]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in [a,b]):
            raise ValueError(f'{day} 净值缺失或非数值，不自动填零')
        data.append((as_date(day),float(a),float(b)))
    if not data:raise ValueError('没有净值观测')
    if len({x[0] for x in data})!=len(data):raise ValueError('日期重复，先核对数据')
    warnings=[]
    if data!=sorted(data):warnings.append('源表日期未排序，已按日期排序分析')
    data.sort()
    if end and end>data[-1][0]:warnings.append('请求结束日晚于文件截止日，仅使用现有数据')
    selected=[i for i,x in enumerate(data) if (not start or x[0]>=start) and (not end or x[0]<=end)]
    if not selected:raise ValueError('请求区间没有有效观测')
    first,last=selected[0],selected[-1];points=data[first:last+1]
    base=data[first-1] if first else (None,1.0,1.0)
    a=points[-1][1]-base[1];b=points[-1][2]-base[2]
    dd=[drawdown(base[j],[x[j] for x in points]) for j in [1,2]]
    if None in dd:warnings.append('净值非正，净值比例回撤不可用')
    return {'文件':Path(path).name,'工作表':sheet,'文件截止日':data[-1][0].isoformat(),
            '请求开始日':start.isoformat() if start else None,'请求结束日':end.isoformat() if end else None,
            '实际开始日':points[0][0].isoformat(),'实际结束日':points[-1][0].isoformat(),'观测数':len(points),
            '基点日期':base[0].isoformat() if base[0] else '完整序列起始资金基点1',
            '策略期末净值':points[-1][1],'基准期末净值':points[-1][2],
            '策略区间收益_初始资金口径':a,'基准区间收益_初始资金口径':b,'区间超额_初始资金口径':a-b,
            '策略净值比例最大回撤':dd[0],'基准净值比例最大回撤':dd[1],
            '单位':'0.01表示1%；单利收益不除以区间起点净值','警告':warnings}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('xlsx');p.add_argument('--start');p.add_argument('--end');a=p.parse_args()
    try:print(json.dumps(analyze(a.xlsx,a.start,a.end),ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,OSError) as e:p.exit(1,f'无法分析：{e}\n')

if __name__=='__main__':main()

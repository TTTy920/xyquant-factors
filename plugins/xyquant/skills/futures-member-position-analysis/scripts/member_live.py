#!/usr/bin/env python3
"""服务端实时取数与确定性计算；凭据仅从环境或私有配置读取。"""
import argparse,contextlib,datetime as dt,json,math,os,re,sys,uuid
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import rqdatac as rq

class PublicError(Exception):pass
class NoData(PublicError):pass
ALIASES={'豆粕':'M','螺纹钢':'RB','螺纹':'RB','白银':'AG','沪铜':'CU','燃油':'FU','甲醇':'MA','PTA':'TA','PVC':'V','黄金':'AU','沪金':'AU','玻璃':'FG','铁矿石':'I','生猪':'LH','豆油':'Y','棕榈油':'P','玉米':'C','纯碱':'SA','菜粕':'RM','原油':'SC','沪铝':'AL','沪锌':'ZN','橡胶':'RU','沪深300':'IF','上证50':'IH','中证500':'IC','中证1000':'IM'}
FIN={'IH','IF','IC','IM','T','TF','TS','TL'}

def symbol_code(value):
    code=ALIASES.get(value.strip(),value.strip().upper())
    if not re.fullmatch('[A-Z]{1,3}',code):raise PublicError('请使用品种代码或已支持的中文名，例如 M、豆粕、RB、螺纹钢')
    return code

def connect():
    if rq.initialized():return
    accounts=[]
    if os.environ.get('RQDATAC_USERNAME') and os.environ.get('RQDATAC_PASSWORD'):
        accounts=[(os.environ['RQDATAC_USERNAME'],os.environ['RQDATAC_PASSWORD'])]
    elif os.environ.get('FUTURES_CREDENTIALS_FILE'):
        try:
            path=Path(os.environ['FUTURES_CREDENTIALS_FILE'])
            if os.name=='posix' and path.stat().st_mode&0o077:raise PublicError('服务端私有配置权限应为600')
            accounts=json.loads(path.read_text())['accounts']
        except PublicError:raise
        except Exception:raise PublicError('服务端私有账号配置不可读') from None
    if not accounts:raise PublicError('服务端尚未配置米筐账号，需管理员配置后调用')
    for user,password in accounts:
        try:
            rq.init(user,password)
            rq.get_price('000001.XSHG',start_date='2026-01-05',end_date='2026-01-05',fields='close')
            return
        except Exception as exc:
            if 'machine' not in str(exc).lower():break
    raise PublicError('米筐登录或权限验证失败，请联系管理员检查账号或机器配额')

def trading_dates(end,n):
    days=rq.get_trading_dates(pd.Timestamp(end)-pd.Timedelta(days=n*4+40),end)
    if len(days)<n:raise NoData('交易日历史不足所需窗口')
    return [pd.Timestamp(x).normalize() for x in days[-n:]]

def resolve_date(value):
    now=dt.datetime.now(ZoneInfo('Asia/Shanghai'))
    if value:
        try:end=dt.date.fromisoformat(value)
        except ValueError:raise PublicError('日期应为 YYYY-MM-DD') from None
        if end>now.date():raise PublicError('不可请求未来日期')
    else:end=now.date() if now.hour>=19 else now.date()-dt.timedelta(days=1)
    day=trading_dates(end,1)[-1]
    return day

def rank(code,date,side):
    try:d=rq.futures.get_member_rank(code,trading_date=date,rank_by=side)
    except Exception:raise PublicError('会员持仓接口读取失败，请检查米筐权限或网络') from None
    if d is None or d.empty:return None
    if 'member_name' not in d.columns:d=d.reset_index()
    if not {'member_name','volume'}<=set(d.columns):raise PublicError('会员排名字段不匹配')
    return d[['member_name','volume']].copy()

def crowd_one(symbol,date):
    tables=[rank(symbol,date,k) for k in ['long','short','volume']]
    if any(x is None for x in tables):raise NoData(f'{date:%Y-%m-%d} 三类会员排名未齐')
    if any(x['member_name'].duplicated().any() for x in tables):raise PublicError('品种会员排名出现重复会员，需核对接口汇总口径')
    d=tables[2].merge(tables[0].rename(columns={'volume':'long'}),on='member_name').merge(tables[1].rename(columns={'volume':'short'}),on='member_name')
    if d.empty:raise NoData('成交、持买、持卖排名没有共同会员')
    try:p=rq.futures.get_dominant_price(symbol,start_date=date,end_date=date,frequency='1d',fields=['volume','open_interest'],adjust_type='pre',adjust_method='prev_close_spread')
    except Exception:raise PublicError('主力行情读取失败，请检查米筐权限或网络') from None
    if p is None or p.empty:raise NoData('该日没有主力成交和持仓数据')
    v=float(p.iloc[-1]['volume']);oi=float(p.iloc[-1]['open_interest'])
    if not math.isfinite(v+oi) or oi<=0 or v<=0:raise NoData('该日成交量或持仓量无效')
    valid=d[['volume','long','short']].notna().all(axis=1)&((d['long']+d['short'])>0)
    d=d.loc[valid].copy();threshold=v/(2*oi)
    d['ratio']=d['volume']/(d['long']+d['short']);chosen=d.loc[d['ratio']>=threshold]
    total=float((chosen['long']+chosen['short']).sum())
    if total<=0:raise NoData('指标1筛选后没有有效会员持仓')
    value=float((chosen['long']-chosen['short']).sum()/total)
    return {'symbol':symbol,'date':date.strftime('%Y-%m-%d'),'indicator':'crowdedness_1','value':value,
            'market_volume':v,'market_open_interest':oi,'screen_threshold':threshold,
            'common_members':len(d),'selected_members':len(chosen),
            'selected_long':float(chosen['long'].sum()),'selected_short':float(chosen['short'].sum()),
            'formula':'筛选 V_m/(L_m+S_m) >= V/(2*OI)，再计算 sum(L-S)/sum(L+S)',
            'meaning':'正负表示所选排名会员的净多/净空偏向，不直接代表交易信号'}

def percentiles(symbol,date,current,windows,cache_dir):
    path=cache_dir/(symbol+'_crowdedness1.json')
    cache=json.loads(path.read_text()) if path.exists() else {}
    days=trading_dates(date,max(windows));changed=False
    for day in days:
        key=day.strftime('%Y-%m-%d')
        if day==date:cache[key]=current;changed=True
        elif key not in cache:
            try:cache[key]=crowd_one(symbol,day)['value'];changed=True
            except NoData:pass
    if changed:
        temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(cache,allow_nan=False));temp.replace(path)
    stats=[]
    for window in windows:
        selected=days[-window:];values=[float(cache[x.strftime('%Y-%m-%d')]) for x in selected if x.strftime('%Y-%m-%d') in cache and cache[x.strftime('%Y-%m-%d')] is not None]
        stats.append({'window_trading_days':window,'window_start':selected[0].strftime('%Y-%m-%d'),'window_end':date.strftime('%Y-%m-%d'),'valid_samples':len(values),'missing_samples':window-len(values),'percentile':sum(x<=current for x in values)/len(values) if values else None,'method':'窗口内小于等于当前值的有效样本占比，含当日，原始指标1未平滑'})
    return stats

def contracts_for(symbol,date,scope,contract):
    if contract:return [contract]
    if symbol in FIN:
        return list(rq.futures.get_contracts(underlying_symbol=symbol,date=date.strftime('%Y-%m-%d'),market='cn') or [])
    value=rq.futures.get_dominant(symbol,start_date=date,end_date=date)
    if value is None or len(value)==0:raise NoData('没有该日主力合约')
    first=str(value.iloc[-1])
    if scope=='dominant':return [first]
    second=rq.futures.get_dominant(symbol,start_date=date,end_date=date,rank=2)
    if second is None or len(second)==0:raise NoData('没有该日次主力合约')
    return list(dict.fromkeys([first,str(second.iloc[-1])]))

def block_mean(matrix):
    values=matrix.values[np.triu_indices(len(matrix),k=1)]
    if len(values)==0 or not np.isfinite(values).all():return {'mean_pair_correlation':None,'label':'无法判定：样本不足或常数序列'}
    mean=float(values.mean())
    return {'mean_pair_correlation':mean,'label':'行为高度一致' if mean>=.6 else ('行为较为一致' if mean>=.3 else '行为分歧')}

def heatmap(symbol,date,lookback,top_n,scope,contract,out):
    if not 3<=lookback<=250 or not 2<=top_n<=20:raise PublicError('热力图窗口范围3~250，分组会员数范围2~20')
    if contract and (not re.fullmatch(re.escape(symbol)+r'\d{3,4}',contract)):raise PublicError('指定合约必须匹配品种，例如 M2701')
    dates=trading_dates(date,lookback)
    # 默认商品热力图沿用原出图脚本：固定目标日主力合约，回看同一个合约。
    fixed=contract or (contracts_for(symbol,date,'dominant',None)[0] if symbol not in FIN and scope=='dominant' else None)
    rows=[];used={}
    for day in dates:
        codes=contracts_for(symbol,day,scope,fixed);used[day.strftime('%Y-%m-%d')]=codes
        if not codes:raise NoData('热力图窗口内可交易合约缺失')
        longs=[];shorts=[]
        for code in codes:
            a=rank(code,day,'long');b=rank(code,day,'short')
            if a is None or b is None:raise NoData(f'{day:%Y-%m-%d} 合约会员排名未齐，未生成不完整窗口热力图')
            longs.append(a);shorts.append(b)
        l=pd.concat(longs).groupby('member_name')['volume'].sum();s=pd.concat(shorts).groupby('member_name')['volume'].sum()
        net=pd.concat([l.rename('long'),s.rename('short')],axis=1,join='inner')
        rows.append(pd.Series(net['long']-net['short'],name=day))
    wide=pd.DataFrame(rows).reindex(dates);clean=wide.dropna(axis=1)
    if len(clean.columns)<2:raise NoData('窗口内完整数据会员不足2家')
    order=clean.loc[date].sort_values().index.tolist();clean=clean[order];corr=clean.corr()
    if not np.isfinite(corr.values).any():raise NoData('相关系数均不可计算')
    k=min(top_n,len(order));low=block_mean(corr.iloc[:k,:k]);high=block_mean(corr.iloc[-k:,-k:])
    warnings=[]
    if len(order)<2*top_n:warnings.append('两端会员分组存在重叠')
    if corr.isna().any().any():warnings.append('部分相关系数因常数序列等原因不可计算，图中留空')
    name=f'{symbol}_会员持仓相关性热力图.png';image=out/name
    draw(corr,clean.loc[date],symbol,fixed,dates,k,low,high,image)
    clean.to_csv(out/f'{symbol}_会员净持仓.csv',encoding='utf-8-sig');corr.to_csv(out/f'{symbol}_相关矩阵.csv',encoding='utf-8-sig')
    return {'symbol':symbol,'date':date.strftime('%Y-%m-%d'),'window_start':dates[0].strftime('%Y-%m-%d'),'window_trading_days':lookback,'scope':'single_contract' if fixed else ('all_contracts' if symbol in FIN else 'dominant_plus_second'),'contract':fixed,'contracts_by_date':used,'complete_members':len(order),'excluded_incomplete_members':len(wide.columns)-len(clean.columns),'low_net_group':low,'high_net_group':high,'low_net_members':order[:k],'high_net_members':order[-k:],'warnings':warnings,'image_path':str(image),'meaning':'净持仓水平的Pearson相关性；排序按目标日净持仓升序，低端不保证均为净空'}

def draw(corr,net,symbol,contract,dates,k,low,high,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    from matplotlib.patches import Rectangle
    font=os.environ.get('FUTURES_CJK_FONT')
    for candidate in [font,'/System/Library/Fonts/STHeiti Light.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']:
        if candidate and Path(candidate).exists():fm.fontManager.addfont(candidate);plt.rcParams['font.family']=fm.FontProperties(fname=candidate).get_name();break
    plt.rcParams['axes.unicode_minus']=False
    n=len(corr);fig,ax=plt.subplots(figsize=(max(11,n*.5+4),max(10,n*.5+3)))
    values=np.ma.masked_invalid(corr.values);im=ax.imshow(values,cmap='RdBu_r',vmin=-1,vmax=1)
    labels=[f'{x} ({net[x]:+,.0f})' for x in corr.columns]
    ax.set_xticks(range(n));ax.set_yticks(range(n));ax.set_xticklabels(labels,rotation=90,fontsize=9);ax.set_yticklabels(labels,fontsize=9)
    for i in range(n):
        for j in range(n):
            v=corr.iloc[i,j]
            if pd.notna(v):ax.text(j,i,f'{v:.2f}',ha='center',va='center',fontsize=7 if n>20 else 8,color='white' if abs(v)>.65 else '#202020')
    for start,color in [(0,'#333333'),(n-k,'#b22222')]:ax.add_patch(Rectangle((start-.5,start-.5),k,k,fill=False,edgecolor=color,lw=2.5))
    fig.colorbar(im,ax=ax,fraction=.035,pad=.025,label='相关系数')
    title=f'{symbol}'+(f' / {contract}' if contract else '')+f' 会员净持仓相关性\n{dates[0]:%Y-%m-%d} 至 {dates[-1]:%Y-%m-%d}，{len(dates)}个交易日'
    ax.set_title(title,fontsize=15,pad=15)
    fig.text(.5,.015,f'低净持仓端{k}家：{low["label"]}；高净持仓端{k}家：{high["label"]}。括号内净持仓单位：手。',ha='center',fontsize=10)
    fig.tight_layout(rect=(0,.04,1,1));fig.savefig(path,dpi=135,bbox_inches='tight');plt.close(fig)

def run(symbol,mode='both',date=None,windows=(60,120,250),lookback=20,top_n=5,scope='dominant',contract=None,output_dir=None):
    symbol=symbol_code(symbol)
    if mode not in ['crowding','heatmap','both'] or scope not in ['dominant','strategy']:raise PublicError('不支持的计算模式')
    windows=sorted(set(int(x) for x in windows))
    if not windows or len(windows)>8 or min(windows)<3 or max(windows)>750:raise PublicError('最多8个分位窗口，每个范围3~750个交易日')
    connect();target=resolve_date(date);date_used=target
    cache=Path(os.environ.get('FUTURES_CACHE_DIR','./futures-cache')).resolve();cache.mkdir(parents=True,exist_ok=True)
    out=Path(output_dir or './futures-output').resolve();out.mkdir(parents=True,exist_ok=True)
    result={'ok':True,'symbol':symbol,'requested_date':date,'actual_date':target.strftime('%Y-%m-%d'),'warnings':[]}
    if mode in ['crowding','both']:
        candidates=[target] if date else list(reversed(trading_dates(target,5)))
        crowd=None
        for day in candidates:
            try:crowd=crowd_one(symbol,day);date_used=day;break
            except NoData:
                if date:raise
        if crowd is None:raise NoData('最近5个交易日均无完整指标1数据')
        if date_used!=target:result['warnings'].append('最近交易日数据未齐，指标与热力图统一使用更早的实际日期')
        crowd['percentiles']=percentiles(symbol,date_used,crowd['value'],windows,cache)
        result['crowding']=crowd
    result['actual_date']=date_used.strftime('%Y-%m-%d')
    if mode in ['heatmap','both']:result['heatmap']=heatmap(symbol,date_used,lookback,top_n,scope,contract,out)
    (out/f'{symbol}_计算结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('symbol');p.add_argument('--mode',choices=['crowding','heatmap','both'],default='both');p.add_argument('--date');p.add_argument('--windows',default='60,120,250');p.add_argument('--lookback',type=int,default=20);p.add_argument('--top-n',type=int,default=5);p.add_argument('--scope',choices=['dominant','strategy'],default='dominant');p.add_argument('--contract');p.add_argument('--output-dir',required=True);a=p.parse_args()
    try:
        with contextlib.redirect_stdout(sys.stderr):result=run(a.symbol,a.mode,a.date,[int(x) for x in a.windows.split(',')],a.lookback,a.top_n,a.scope,a.contract,a.output_dir)
    except PublicError as e:result={'ok':False,'error':str(e)}
    except Exception:result={'ok':False,'error':'计算未完成，请管理员检查后端环境或接口；未返回内部错误详情'}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0 if result['ok'] else 1

if __name__=='__main__':sys.exit(main())

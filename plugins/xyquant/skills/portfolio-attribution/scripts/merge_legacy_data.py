#!/usr/bin/env python3
"""One-time, lossless migration of existing Barra/Brinson skill data."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from data_store import FACTORS, require, fingerprint


def migrate(barra_root,brinson_root,destination):
    ba,br,out=Path(barra_root).resolve(),Path(brinson_root).resolve(),Path(destination).resolve()
    require(not out.is_relative_to(ba) and not out.is_relative_to(br),"迁移输出不能位于原始数据目录内")
    require(not out.exists() or not any(p.is_file() for p in out.rglob("*")),"迁移输出目录需为空")
    out.mkdir(parents=True,exist_ok=True)
    sources={};files=[];conflicts=[]
    def read(p):
        before=fingerprint(p)
        frame=pd.read_csv(p,index_col=0,parse_dates=True,float_precision="round_trip")
        require(not frame.index.has_duplicates and not frame.columns.has_duplicates,"源矩阵重复",path=str(p))
        require(before==fingerprint(p),"迁移期间源文件变化",path=str(p))
        sources[str(p)]=before
        return frame.sort_index()
    def merge(a,b,label):
        d=a.index.intersection(b.index);c=a.columns.intersection(b.columns)
        x,y=a.loc[d,c].to_numpy(),b.loc[d,c].to_numpy()
        both=~pd.isna(x)&~pd.isna(y)
        bad=both&(x!=y)
        # Precision must be preserved: differing finite source values require review.
        require(not bad.any(),"源数据存在数值冲突，不能静默选取",role=label,cells=int(bad.sum()))
        conflicts.append({"role":label,"overlap_dates":len(d),"overlap_stocks":len(c),"conflicting_finite_cells":0})
        return a.combine_first(b).sort_index().sort_index(axis=1)
    def write_table(table,relative):
        path=out/relative;path.parent.mkdir(parents=True,exist_ok=True)
        pq.write_table(table,path,compression="zstd",compression_level=5,row_group_size=131072)
        restored=pq.read_table(path)
        require(restored.num_rows==table.num_rows and restored.column_names==table.column_names,"Parquet结构回读失败",path=relative)
        for column in table.column_names:
            original,again=table[column],restored[column]
            if pa.types.is_dictionary(original.type):
                original=original.cast(original.type.value_type)
            if pa.types.is_dictionary(again.type):
                again=again.cast(again.type.value_type)
            require(original.equals(again),"Parquet无损回读失败",path=relative,column=column)
        files.append({"path":relative,"rows":table.num_rows,"columns":table.num_columns,
                      "bytes":path.stat().st_size,"sha256":fingerprint(path)["sha256"]})
    def write_frame(frame,relative):
        write_table(pa.Table.from_pandas(frame,preserve_index=False),relative)
    def dense_panel(frames,prefix):
        reference=next(iter(frames.values()))
        dates=reference.index;stocks=reference.columns
        paths=[]
        for year in sorted(dates.year.unique()):
            selected=dates[dates.year==year]
            arrays={"date":pa.array(np.repeat(selected.values,len(stocks))),
                    "stock_code":pa.array(np.tile(stocks.to_numpy(),len(selected))).dictionary_encode()}
            for name,frame in frames.items():
                values=frame.reindex(index=selected,columns=stocks).to_numpy(dtype=float).ravel()
                arrays[name]=pa.array(values,from_pandas=True,type=pa.int8() if name=="eligible" else pa.float64())
            table=pa.table(arrays)
            relative=f"{prefix}/{year}.parquet"
            write_table(table,relative);paths.append(relative)
            print(prefix,year,'rows',table.num_rows,'bytes',files[-1]['bytes'],flush=True)
        return paths
    cat={"schema_version":1,"calendar":"calendar.parquet","portfolios":{},"benchmarks":{},
         "snapshots":"weights/snapshots.parquet","industry":"industry/monthly.parquet",
         "regression":{"minimum":100,"max_drop":.1,"condition_limit":1e10}}
    snapshots=[]
    def weights(name,kind,a,b,frequencies,aliases):
        frame=merge(a,b,kind+":"+name)
        require(not (frame<0).any().any(),"源权重有负值",name=name)
        cash=frame.reindex(columns=["CNY","CASH","现金"]).sum(axis=1)
        frame=frame.drop(columns=["CNY","CASH","现金"],errors="ignore")
        for date,row in frame.iterrows():
            total=float(row.sum())
            snapshots.append({"date":date,"kind":kind,"name":name,"valid":total>0,"total":total,"cash":float(cash.loc[date])})
        valid=frame.to_numpy()>0
        ii,jj=np.nonzero(valid)
        long=pd.DataFrame({"date":frame.index.to_numpy()[ii],"stock_code":pd.Categorical(frame.columns.to_numpy()[jj]),"weight":frame.to_numpy()[ii,jj]})
        require(np.array_equal(long.weight.to_numpy(),frame.to_numpy()[ii,jj]),"稀疏权重转换失败")
        relative=f"weights/{kind}_{name}.parquet"
        write_frame(long,relative)
        cat["portfolios" if kind=="portfolio" else "benchmarks"][name]={"path":relative,"frequencies":frequencies,"aliases":aliases}
    for name in ["沪深300","中证500","中证1000","中证2000"]:
        code={"沪深300":"000300.SH","中证500":"000905.SH","中证1000":"000852.SH","中证2000":"932000.CSI"}[name]
        weights(name,"benchmark",read(ba/"benchmarks"/(name+".csv")),read(br/"benchmarks"/(name+"_daily.csv")),
                ["daily","weekly","monthly"],[code,name+"指数"])
    for name in ["300增强","xgb500增强","xgb1000增强","xgb2000增强"]:
        other="300增强持仓.csv" if name=="300增强" else name+".csv"
        aliases=["300指增","300增强策略"] if name=="300增强" else [name.replace("xgb", ""),name.replace("增强", "").upper()]
        weights(name,"portfolio",read(ba/"portfolios"/(name+".csv")),read(br/"portfolios"/other),
                ["monthly"] if name=="300增强" else ["weekly"],aliases)
    write_frame(pd.DataFrame(snapshots),cat["snapshots"])
    returns=merge(read(ba/"market/returns_daily.csv"),read(br/"returns/returns_daily.csv"),"daily_returns")
    caps=read(ba/"market/float_cap.csv").reindex(index=returns.index,columns=returns.columns)
    eligible=read(ba/"market/eligible.csv").reindex(index=returns.index,columns=returns.columns)
    require(eligible.stack().isin([0,1]).all(),"源回归资格不是0/1/空")
    cat["market"]=dense_panel({"return":returns,"float_cap":caps,"eligible":eligible},"market")
    exposure={name:read(ba/"exposures"/(name+".csv")) for name in FACTORS}
    x=next(iter(exposure.values()))
    require(all(f.index.equals(x.index) and f.columns.equals(x.columns) for f in exposure.values()),"十风格日期或股票轴不一致")
    cat["exposures"]=dense_panel(exposure,"exposures")
    industry=read(br/"industry/中信行业_monthly.csv")
    ii,jj=np.nonzero(industry.notna().to_numpy())
    long=pd.DataFrame({"date":industry.index.to_numpy()[ii],"stock_code":industry.columns.to_numpy()[jj],"industry":industry.to_numpy()[ii,jj]})
    long.stock_code=pd.Categorical(long.stock_code);long.industry=pd.Categorical(long.industry)
    write_frame(long,cat["industry"])
    calendar_path=ba/"trading_calendar.csv"
    sources[str(calendar_path)]=fingerprint(calendar_path)
    dates=pd.to_datetime(pd.read_csv(calendar_path).date)
    dates=pd.DatetimeIndex(dates).union(returns.index).sort_values()
    write_frame(pd.DataFrame({"date":dates}),cat["calendar"])
    (out/"catalog.json").write_text(json.dumps(cat,ensure_ascii=False,indent=2),encoding="utf-8")
    for path,fp in sources.items():
        require(fingerprint(path)==fp,"转换期间源数据发生变化",path=path)
    source_data_bytes=sum(fp["bytes"] for fp in sources.values())
    report={"schema_version":1,"sources":list(sources.values()),"files":files,"overlap_checks":conflicts,
            "source_selected_bytes":source_data_bytes,"parquet_bytes":sum(f["bytes"] for f in files),
            "lossless_roundtrip":True,"numeric_precision":"float64; eligibility nullable int8",
            "scope":"Full market universe retained; returns daily; industry monthly; no precomputed factor-return copies",
            "source_gaps":"Original missing returns/exposures/weights remain missing; data conversion does not repair corporate actions."}
    (out/"migration_manifest.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"destination":str(out),"parquet_bytes":report["parquet_bytes"],"files":len(files),"lossless_roundtrip":True},ensure_ascii=False),flush=True)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--barra-data",required=True);parser.add_argument("--brinson-data",required=True);parser.add_argument("--destination",required=True)
    args=parser.parse_args()
    migrate(args.barra_data,args.brinson_data,args.destination)

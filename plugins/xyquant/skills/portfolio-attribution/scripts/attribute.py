#!/usr/bin/env python3
"""Shared-data Barra / Brinson return attribution, local execution only."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid
import numpy as np
import pandas as pd
import barra
import brinson
from report import build_report
from data_store import Store, DataError, require, FREQUENCIES

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from xyquant_data import ATTRIBUTION_RESULTS, DATA_DIR
sys.path.pop(0)

LINK = "C_t=(1+Rb_t)*C_previous+Vp_previous*c_t"
LABELS = {"date":"期末日期","period_start":"期初日期","portfolio_return":"组合重建收益","benchmark_return":"基准重建收益",
          "active_return":"重建超额收益","factor":"风格因子","industry":"行业","allocation":"行业配置贡献",
          "selection":"纯选股贡献","interaction":"交互贡献","selection_including_interaction":"选股贡献（含交互）",
          "total":"合计贡献","contribution":"风格贡献","unexplained":"未解释收益","reconciliation_gap":"核对差额",
          "portfolio_weight":"组合行业权重","benchmark_weight":"基准行业权重","active_weight":"超低配权重",
          "portfolio_exposure":"组合暴露","benchmark_exposure":"基准暴露","active_exposure":"相对暴露","factor_return":"因子收益",
          "linked_contribution":"累计连接贡献","arithmetic_contribution":"算术累计贡献"}


def link(frame, periods):
    out = pd.Series(0., index=frame.columns)
    vp = vb = 1.
    for row in periods.itertuples():
        out = (1+row.benchmark_return)*out + vp*frame.loc[row.date]
        vp *= 1+row.portfolio_return
        vb *= 1+row.benchmark_return
    require(abs(out.sum()-(vp-vb))<1e-9, "累计贡献核对失败")
    return out, vp-1, vb-1


def safe_frame(frame):
    def safe(v):
        return "'"+v if isinstance(v,str) and v.startswith(("=","+","-","@")) else v
    return frame.apply(lambda s:s.map(safe))


def execute(args):
    frequency = FREQUENCIES.get(args.frequency)
    require(frequency is not None, "频率仅支持日、周、月")
    try:
        start,end = [pd.Timestamp(datetime.strptime(x,"%Y-%m-%d")) for x in [args.start,args.end]]
    except ValueError as e:
        raise DataError("日期格式需为 YYYY-MM-DD") from e
    require(start<=end,"起始日期晚于结束日期")
    methods = ["barra","brinson"] if args.method=="both" else [args.method]
    s = Store(args.data_root, flat_project=getattr(args,"flat_project",None))
    s.prepare(args.portfolio,args.benchmark,start,end,frequency,barra="barra" in methods,brinson="brinson" in methods)
    errors = s.method_errors.copy()
    details={m:[] for m in methods}
    diagnostics=[]
    periods=[]
    weight_diagnostics=[]
    params = {"minimum":100,"max_drop":.1,"condition_limit":1e10}
    if s.flat_project is None:
        params.update(s.catalog.get("regression",{}))
    require(params["minimum"]>=12 and 0<=params["max_drop"]<=1 and params["condition_limit"]>=1,"回归校验参数无效")
    for t0,t1 in s.periods:
        date=str(t1.date())
        try:
            wp,pdiag=s.weights("portfolio",t0)
            wb,bdiag=s.weights("benchmark",t0)
            returns=s.period_return(t0,t1)
            held=wp.index.union(wb.index)
            missing=held.difference(returns.dropna().index)
            require(len(missing)==0,"组合或基准股票区间收益缺失（日收益缺失传播）",stocks=missing.tolist())
            rp=float(wp@returns.reindex(wp.index));rb=float(wb@returns.reindex(wb.index))
            require(np.isfinite(rp) and np.isfinite(rb),"重建收益不是有限值")
        except DataError as e:
            e.details.update(period_start=str(t0.date()),period_end=date)
            raise
        weight_diagnostics.extend([pdiag,bdiag])
        period={"period_start":str(t0.date()),"date":date,"portfolio_return":rp,"benchmark_return":rb,"active_return":rp-rb}
        for method in methods:
            if method in errors:
                continue
            try:
                if method=="barra":
                    x,caps,eligible=s.barra_inputs(t0)
                    detail,diag=barra.attribute(wp,wb,x,returns,caps,eligible,params)
                    diag["date"]=date;diagnostics.append(diag)
                    period["barra_style"]=float(detail.contribution.sum())
                    period["unexplained"]=rp-rb-period["barra_style"]
                else:
                    detail=brinson.attribute(wp,wb,s.industry_aligned.loc[t0],returns,rb)
                    gap=rp-rb-float(detail.total.sum())
                    require(abs(gap)<1e-10,"Brinson单期核对失败",gap=gap)
                    period["allocation"]=float(detail.allocation.sum())
                    period["selection_including_interaction"]=float(detail.selection_including_interaction.sum())
                    period["reconciliation_gap"]=gap
                detail.insert(0,"date",date)
                details[method].append(detail)
            except DataError as e:
                errors[method]={"message":str(e),"details":getattr(e,"details",{}),"period_start":str(t0.date()),"period_end":date}
        periods.append(period)
    successful=[m for m in methods if m not in errors]
    require(successful,"归因未能完成",method_errors=errors)
    periods=pd.DataFrame(periods)
    # A method that fails anywhere is not reported on a shortened cohort.
    if "barra" not in successful:
        periods=periods.drop(columns=["barra_style","unexplained"],errors="ignore")
    if "brinson" not in successful:
        periods=periods.drop(columns=["allocation","selection_including_interaction","reconciliation_gap"],errors="ignore")
    tables={"收益核对":periods}
    linked={}
    for method in successful:
        detail=pd.concat(details[method],ignore_index=True)
        if method=="barra":
            frame=detail.pivot(index="date",columns="factor",values="contribution")
            frame["未解释收益"]=periods.set_index("date").unexplained
            contributions,pr,br=link(frame,periods)
            cumulative=contributions.rename_axis("factor").rename("linked_contribution").reset_index()
            cumulative["arithmetic_contribution"]=frame.sum().reindex(cumulative.factor).to_numpy()
            tables.update({"Barra风格明细":detail,"Barra累计贡献":cumulative,"Barra回归诊断":pd.DataFrame(diagnostics)})
        else:
            frame=detail.pivot(index="date",columns="industry",values="total").fillna(0)
            contributions,pr,br=link(frame,periods)
            pieces=[]
            for col in ["allocation","selection","interaction","selection_including_interaction","total"]:
                p=detail.pivot(index="date",columns="industry",values=col).fillna(0)
                # Individual component sums need not equal active return: use the same recurrence directly.
                acc=pd.Series(0.,index=p.columns);vp=1.
                for row in periods.itertuples():
                    acc=(1+row.benchmark_return)*acc+vp*p.loc[row.date]
                    vp*=1+row.portfolio_return
                pieces.append(acc.rename(col))
            cumulative=pd.concat(pieces,axis=1).rename_axis("industry").reset_index()
            require(np.isclose(cumulative.total.sum(),pr-br,atol=1e-9),"Brinson累计核对失败")
            tables.update({"Brinson行业明细":detail,"Brinson累计贡献":cumulative})
        linked[method]={"contributions":contributions.to_dict(),"gap":float(contributions.sum()-(pr-br))}
    tables["权重诊断"]=pd.DataFrame(weight_diagnostics)
    # Calendar boundaries use actual trading days, including holiday-shortened periods.
    used=[d for d in s.calendar if s.periods[0][0]<d<=s.periods[-1][1]]
    metadata={"status":"partial_success" if errors else "success","portfolio":s.portfolio,"benchmark":s.benchmark,
              "requested_method":args.method,"successful_methods":successful,"method_errors":errors,"frequency":frequency,
              "requested_start":args.start,"requested_end":args.end,"effective_start_snapshot":str(s.periods[0][0].date()),
              "effective_first_trading_date":str(used[0].date()),"effective_end":str(s.periods[-1][1].date()),"period_count":len(periods),
              "portfolio_compound_return":pr,"benchmark_compound_return":br,"compound_return_difference":pr-br,
              "linking_method":LINK,"linked":linked,"returns":"daily simple returns compounded, missing values propagate",
              "industry":"monthly classifications forward-filled per stock to period start; never backward-filled",
              "scope":"normalized long-only equity sleeve, beginning-period static holdings, excluding cash/trading/fees",
              "regression":params,"source_files":list(s.sources.values()),"created_at":datetime.now(timezone.utc).isoformat()}
    if s.flat_project is not None:
        metadata["foundation_datasets"] = s.foundation
        metadata["barra_sample_policy"] = "full_market_valid_start_exposures_and_cap" if "barra" in methods else None
    summary=["# 组合收益归因", "",f"组合：{s.portfolio}；基准：{s.benchmark}；频率：{frequency}。",
             f"有效交易期间：{used[0].date()} 至 {used[-1].date()}，共{len(periods)}期；期初快照：{s.periods[0][0].date()}。", "",
             f"组合重建累计收益{pr*100:.3f}%，基准{br*100:.3f}%，两者相差{(pr-br)*100:+.3f}个百分点。"]
    if "brinson" in successful:
        c=tables["Brinson累计贡献"]
        summary.append(f"Brinson：配置贡献{c.allocation.sum()*100:+.3f}个百分点；选股（含交互）贡献{c.selection_including_interaction.sum()*100:+.3f}个百分点。")
    for method in successful:
        values=pd.Series(linked[method]["contributions"]).drop(labels=["未解释收益"],errors="ignore")
        pos=values[values>0].nlargest(3);neg=values[values<0].nsmallest(3)
        fmt=lambda v:"、".join(f"{k}（{x*100:+.3f}个百分点）" for k,x in v.items()) or "无"
        summary.append(f"{method} 主要正贡献：{fmt(pos)}；主要负贡献：{fmt(neg)}。")
    if "barra" in successful:
        residual=linked["barra"]["contributions"]["未解释收益"]
        summary.append(f"Barra累计连接未解释收益为{residual*100:+.3f}个百分点；十风格回归未控制行业，剩余项不等于纯Alpha。")
    if errors:
        for name,error in errors.items():
            stocks=error.get("details",{}).get("stocks",[])
            examples=f"；涉及{len(stocks)}只股票，例如{'、'.join(stocks[:5])}" if stocks else ""
            period=f"；期初{error['period_start']}，期末{error['period_end']}" if "period_start" in error else ""
            summary.append(f"{name}未完成：{error['message']}{period}{examples}。完整缺口见metadata.json。")
    summary += ["", "两种归因是不同视角，贡献不能相加。累计贡献采用同一种基准增长连接，Barra加未解释项后与重建超额闭合。行业内选股仍可能包含风格收益。",
                "结果按股票部分归一化，采用期初静态持仓，未包含期中交易、现金及费用。行业按月度历史分类向前填充，周/月区间收益从日收益连乘。"]
    s.verify_unchanged()
    out=Path(args.output_root).resolve()/(datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:12])
    out.mkdir(parents=True,exist_ok=False)
    try:
        with pd.ExcelWriter(out/"归因结果.xlsx",engine="openpyxl") as writer:
            for name,frame in tables.items():
                safe_frame(frame).rename(columns=LABELS).to_excel(writer,sheet_name=name,index=False)
                sheet=writer.sheets[name];sheet.freeze_panes="B2";sheet.auto_filter.ref=sheet.dimensions
                for col in sheet.columns:
                    sheet.column_dimensions[col[0].column_letter].width=22
                    original=frame.columns[col[0].column-1]
                    if original in {"portfolio_return","benchmark_return","active_return","barra_style","unexplained","allocation","selection",
                                    "interaction","selection_including_interaction","total","contribution","reconciliation_gap","portfolio_weight",
                                    "benchmark_weight","active_weight","portfolio_industry_return","benchmark_industry_return","factor_return",
                                    "linked_contribution","arithmetic_contribution"}:
                        for cell in col[1:]:
                            cell.number_format="0.0000%"
        (out/"summary.md").write_text("\n\n".join(summary)+"\n",encoding="utf-8")
        (out/"report.md").write_text(build_report(tables,metadata),encoding="utf-8")
        (out/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    except Exception:
        (out/"FAILED.txt").write_text("产物写入失败，不能作为成功结果交付。",encoding="utf-8")
        raise
    return {"status":metadata["status"],"output_dir":str(out),"successful_methods":successful,"method_errors":errors,
            "files":{p.name:str(p) for p in sorted(out.iterdir())}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    for cmd in ["inventory","run"]:
        p=sub.add_parser(cmd)
        p.add_argument("--data-root",default=os.environ.get("ATTRIBUTION_DATA_ROOT"),required=not bool(os.environ.get("ATTRIBUTION_DATA_ROOT")))
        sources=p.add_mutually_exclusive_group()
        sources.add_argument("--flat-project",type=Path,help=argparse.SUPPRESS)
        sources.add_argument("--legacy-replay",action="store_true",help=argparse.SUPPRESS)
        if cmd=="run":
            for key in ["portfolio","benchmark","start","end","frequency"]:
                p.add_argument("--"+key,required=True)
            p.add_argument("--method",choices=["barra","brinson","both"],default="both")
            p.add_argument("--output-root",default=os.environ.get("ATTRIBUTION_OUTPUT_ROOT",str(Path.cwd().joinpath(DATA_DIR, *ATTRIBUTION_RESULTS))))
    args=parser.parse_args()
    args.flat_project = None if args.legacy_replay else args.flat_project or Path.cwd()
    try:
        if args.command=="inventory":
            s=Store(args.data_root,flat_project=args.flat_project)
            result={"data_root":str(s.root),"catalog":s.catalog,"calendar_start":str(s.calendar.min().date()),"calendar_end":str(s.calendar.max().date())}
            if s.flat_project is not None:
                result["catalog"] = {key: s.catalog[key] for key in ("portfolios", "benchmarks", "snapshots")}
                result["foundation_datasets"] = s.foundation
        else:
            result=execute(args)
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        return 0
    except (DataError,OSError,ValueError,KeyError,ImportError,TypeError) as e:
        print(json.dumps({"status":"error","message":str(e),"details":getattr(e,"details",{})},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=="__main__":
    sys.exit(main())

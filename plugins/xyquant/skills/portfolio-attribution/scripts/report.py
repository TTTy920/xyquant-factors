"""Deterministic Markdown analysis for direct presentation in conversation."""
import numpy as np
import pandas as pd

FACTOR_CN={"Size":"市值","Beta":"贝塔","Momentum":"动量","ResidualVolatility":"残差波动",
           "NonlinearSize":"非线性市值","BookToPrice":"账面市值比","Liquidity":"流动性",
           "EarningsYield":"盈利收益率","Growth":"成长","Leverage":"杠杆"}


def text(v):
    return str(v).replace("|",r"\|").replace("\n"," ").replace("\r"," ")


def table(headers,rows):
    return "\n".join(["| "+" | ".join(map(text,headers))+" |",
                       "| "+" | ".join(["---"]*len(headers))+" |",
                       *["| "+" | ".join(map(text,row))+" |" for row in rows]])


def pct(v):return f"{v*100:+.3f}%"
def pp(v):return f"{v*100:+.3f}"
def positive(v):return int((v>1e-12).sum())


def build_report(tables,meta):
    nav_gap_name = "实际净值与持仓重建差额"
    observed = meta.get("return_source") == "provided_nav"
    periods=tables["收益核对"]
    methods=meta["successful_methods"]
    n=len(periods)
    freq={"daily":"日频","weekly":"周频","monthly":"月频"}[meta["frequency"]]
    unit="个月" if meta["frequency"]=="monthly" else "期"
    pr,br=meta["portfolio_compound_return"],meta["benchmark_compound_return"]
    active=meta["compound_return_difference"]
    sections=[f"# {text(meta['portfolio'])}组合收益归因报告",
              f"组合：{text(meta['portfolio'])}；基准：{text(meta['benchmark'])}；频率：{freq}。"
              f"请求区间为{meta['requested_start']}至{meta['requested_end']}，实际交易区间为"
              f"{meta['effective_first_trading_date']}至{meta['effective_end']}，共{n}{unit}；期初快照为{meta['effective_start_snapshot']}。",
              "## 1. 总体表现",
              table(["指标","区间结果"],[["组合实际净值累计收益" if observed else "组合重建累计收益",pct(pr)],["基准重建累计收益",pct(br)],
                                        ["累计超额（两者复利收益之差）",pp(active)+"个百分点"],
                                        ["跑赢基准的期数",f"{positive(periods.active_return)}/{n}"]])]
    best=periods.loc[periods.active_return.idxmax()]
    worst=periods.loc[periods.active_return.idxmin()]
    sections.append(f"区间内组合相对基准{'跑赢' if active>=0 else '跑输'}{abs(active)*100:.3f}个百分点。"
                    f"单期超额最高的是{best.date}，为{pp(best.active_return)}个百分点；"
                    f"最低的是{worst.date}，为{pp(worst.active_return)}个百分点。")
    headers=["收益期末","组合收益","基准收益","超额（百分点）"]
    if "brinson" in methods:headers += ["行业配置（百分点）","选股含交互（百分点）"]
    if observed:headers += ["实际净值与持仓重建差额（百分点）"]
    rows=[]
    for r in periods.tail(12).itertuples():
        row=[r.date,pct(r.portfolio_return),pct(r.benchmark_return),pp(r.active_return)]
        if "brinson" in methods:row += [pp(r.allocation),pp(r.selection_including_interaction)]
        if observed:row += [pp(r.nav_gap)]
        rows.append(row)
    sections.append(table(headers,rows))
    if n>12:sections.append(f"上表只展示最近12期，胜率、区间累计及以下结论均基于全部{n}期；完整逐期数据见Excel。")
    sections.append("单期贡献与累计连接贡献是不同口径，不能直接将上表各期相加当作复利区间贡献。")

    conclusions=[]
    if "brinson" in methods:
        c=tables["Brinson累计贡献"]
        allocation=float(c.allocation.sum());selection=float(c.selection_including_interaction.sum())
        nav_linked = float(meta["linked"]["brinson"]["contributions"].get(nav_gap_name, 0.))
        brinson_rows = [["行业配置",pp(allocation),f"{positive(periods.allocation)}/{n}"],
                        ["行业内选股（含交互）",pp(selection),f"{positive(periods.selection_including_interaction)}/{n}"]]
        if observed:brinson_rows.append([nav_gap_name, pp(nav_linked), "独立差额"])
        brinson_rows.append(["合计",pp(allocation+selection+nav_linked),"与累计超额核对"])
        sections += ["## 2. Brinson：行业配置与行业内选股",
                     table(["来源","累计贡献（百分点）","正贡献期数"],
                           brinson_rows)]
        if selection>0 and selection>max(0,allocation):
            conclusions.append("行业分解中，行业内选股是主要正贡献来源。")
        elif allocation>0 and allocation>max(0,selection):
            conclusions.append("行业分解中，行业配置是主要正贡献来源。")
        else:
            conclusions.append(f"行业配置与选股的累计贡献分别为{pp(allocation)}和{pp(selection)}个百分点。")
        sections.append(f"行业配置有{positive(periods.allocation)}{unit}为正，行业内选股有{positive(periods.selection_including_interaction)}{unit}为正。"
                        f"配置累计{'增厚' if allocation>=0 else '拖累'}超额收益，选股累计{'增厚' if selection>=0 else '拖累'}超额收益。")
        positive_rows=c[c.total>0].nlargest(3,"total")
        negative_rows=c[c.total<0].nsmallest(3,"total")
        leaders=pd.concat([positive_rows,negative_rows]).drop_duplicates("industry")
        sections.append(table(["主要行业","配置贡献","选股贡献（含交互）","合计贡献"],
                              [[r.industry,pp(r.allocation),pp(r.selection_including_interaction),pp(r.total)] for r in leaders.itertuples()]))
        sections.append("上表单位均为百分点，仅列主要正、负贡献行业，不代表全部行业；完整明细见Excel。")
        if len(positive_rows):
            r=positive_rows.iloc[0]
            component="行业内选股" if r.selection_including_interaction>=r.allocation else "行业配置"
            sections.append(f"{text(r.industry)}是贡献最高的行业，累计贡献{pp(r.total)}个百分点，其中配置为{pp(r.allocation)}、"
                            f"选股（含交互）为{pp(r.selection_including_interaction)}个百分点，主要来自{component}。")
        if len(negative_rows):
            r=negative_rows.iloc[0]
            sections.append(f"{text(r.industry)}是主要拖累行业，累计贡献{pp(r.total)}个百分点；应结合其超低配权重与行业内持仓表现复核，不能仅凭合计贡献判断配置是否正确。")

    if "barra" in methods:
        detail=tables["Barra风格明细"]
        cumulative=tables["Barra累计贡献"].set_index("factor").linked_contribution
        average=detail.groupby("factor").active_exposure.mean()
        factor_returns=detail.groupby("factor").factor_return.sum()
        style=float(cumulative.drop(labels=["未解释收益",nav_gap_name],errors="ignore").sum())
        residual=float(cumulative.get("未解释收益",0.))
        nav_linked=float(cumulative.get(nav_gap_name,0.))
        barra_rows=[["十个风格合计",pp(style)],["未解释收益",pp(residual)]]
        if observed:barra_rows.append([nav_gap_name,pp(nav_linked)])
        barra_rows.append(["合计",pp(style+residual+nav_linked)])
        sections += ["## 3. Barra：风格暴露与收益来源",
                     table(["来源","累计贡献（百分点）"],barra_rows)]
        rows=[]
        for factor in FACTOR_CN:
            rows.append([FACTOR_CN[factor],f"{average.get(factor,np.nan):+.3f}",pct(factor_returns.get(factor,0.)),pp(cumulative.get(factor,0.))])
        sections.append(table(["风格因子","平均期初相对暴露","因子收益算术累计","组合累计贡献（百分点）"],rows))
        sections.append("相对暴露是模型暴露单位，不是持仓比例；因子收益列为各期回归系数的算术累计，用于观察风格方向，不是可交易指数净值。"
                        "组合贡献使用累计连接口径，不能用平均暴露乘累计因子收益替代。")
        values=cumulative.drop(labels=["未解释收益",nav_gap_name],errors="ignore")
        for sign in ["positive","negative"]:
            candidates=values[values>0] if sign=="positive" else values[values<0]
            if not len(candidates):continue
            factor=candidates.idxmax() if sign=="positive" else candidates.idxmin()
            d=detail[detail.factor==factor]
            row=d.loc[d.contribution.idxmax() if sign=="positive" else d.contribution.idxmin()]
            sections.append(f"{FACTOR_CN.get(factor,factor)}是主要{'正贡献' if sign=='positive' else '拖累'}风格，累计贡献{pp(values[factor])}个百分点。"
                            f"以{row.date}这一期为例，期初相对暴露为{row.active_exposure:+.3f}，因子收益为{pct(row.factor_return)}，"
                            f"两者相乘得到单期贡献{pp(row.contribution)}个百分点。")
        conclusions.append(f"十风格对区间超额的净贡献为{pp(style)}个百分点，未解释项为{pp(residual)}个百分点；未解释项不等于纯选股Alpha。")
        diag=tables.get("Barra回归诊断")
        if diag is not None and len(diag) and "sample_count" in diag:
            sections.append(f"各期有效回归样本为{int(diag.sample_count.min())}至{int(diag.sample_count.max())}只，"
                            f"最高合格样本剔除比例为{diag.dropped_fraction.max()*100:.3f}%，回归诊断见Excel。")

    sections.append("## 4. 结论与适用范围")
    for index,line in enumerate(conclusions,1):sections.append(f"{index}. {line}")
    if n<12:
        sections.append(f"本次只有{n}{unit}观测，以上是样本区间内的收益分解，不能据此认定长期选股能力、稳定胜率或风格长期有效。")
    sections.append("Brinson与Barra从行业和风格两个视角解释同一收益，不能将两套贡献相加。行业内选股仍可能包含风格收益。"
                    "结果基于期初股票静态持仓并重新归一化，未计现金收益、费用和期中交易，不等同实际产品净值。")
    if observed:
        sections.append(f"本次总收益采用客户实际净值。持仓重建累计收益为{pct(meta['reconstructed_compound_return'])}。"
                        f"原行业和风格归因保持原计算口径，{nav_gap_name}独立列示并使用同一累计连接公式。"
                        "该差额可能包含仓位、交易、费用及数据口径差异，仅凭现有数据不能进一步确定来源，也不直接视为Alpha。")
    if meta.get("factor_return_source") == "supplied":
        sections.append("因子收益率直接读取部署方提供的对应频率文件，本次没有重新估计回归系数。仅展示十个风格贡献，其他来源保留在未解释项。")
    for method,error in meta.get("method_errors",{}).items():
        stocks=error.get("details",{}).get("stocks",[])
        suffix=f"，涉及{len(stocks)}只股票" if stocks else ""
        sections.append(f"**部分方法未完成：{method}，原因：{text(error['message'])}{suffix}。本报告不展示该方法的缩短区间结果，详细缺口见metadata.json。**")
    gaps=[abs(v["gap"]) for v in meta["linked"].values()]
    sections.append(f"数据来源：本次服务器共享数据快照。累计收益核对最大绝对差额为{max(gaps,default=0.):.3g}，属于数值校验结果，不代表模型经济解释充分。")
    return "\n\n".join(sections)+"\n"

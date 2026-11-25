import argparse
import json
import math
from pathlib import Path

import pandas as pd


def calculate(frame, sizes, valid_rate):
    issue = frame.groupby(
        [frame["上市日期"].dt.year.rename("年度"),
         frame["上市日期"].dt.month.rename("月份"), "上市板"]
    ).agg(上市数=("股票代码", "count"), 募资亿元=("募资总额(亿元)", "sum")).reset_index()
    result = frame[frame["上市板"] != "北交所"].copy()
    change = result["上市首日均价"] / result["发行价格"] - 1
    summaries = []
    for size in sizes:
        label = f"{size:g}亿"
        capital = size * 100_000_000
        valid = (result["网下申购上限(万股)"] * 10_000 * result["发行价格"]).clip(upper=capital)
        profit = valid * valid_rate * (result["网下A类投资者中签率(%)"] / 100) * change
        result[f"{label}有效申购额元"] = valid
        result[f"{label}收益额元"] = profit
        result[f"{label}打新收益率"] = profit / capital
        summaries.append(dict(账户规模亿元=size, 上市数=int(result["股票代码"].count()),
                              可测算数=int(profit.notna().sum()), 收益额元=float(profit.sum()),
                              收益率=float((profit / capital).sum())))
    columns = [f"{size:g}亿打新收益率" for size in sizes]
    aggregation = {"股票代码": "count", "募资总额(亿元)": "sum"} | {c: "sum" for c in columns}
    monthly = result.groupby([result["上市日期"].dt.year.rename("年度"),
                              result["上市日期"].dt.month.rename("月份")]).agg(aggregation)
    annual = result.groupby([result["上市日期"].dt.year.rename("年度"), "上市板"]).agg(aggregation)
    return issue, result, summaries, monthly, annual


def records(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False,
                                    double_precision=15))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--sizes", nargs="+", type=float, default=[1, 2, 3, 5, 10, 15, 20, 25, 30])
    parser.add_argument("--valid-rate", type=float, default=.9)
    parser.add_argument("--year", type=int)
    parser.add_argument("--board")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if any(not math.isfinite(s) or s <= 0 for s in args.sizes):
        parser.error("账户规模必须为有限正数")
    if not math.isfinite(args.valid_rate) or not 0 <= args.valid_rate <= 1:
        parser.error("入围率必须在 0 到 1 之间")
    if args.output and args.output.resolve() == args.input.resolve():
        parser.error("输出不能覆盖输入文件")
    frame = pd.read_excel(args.input)
    required = ["股票代码", "上市日期", "上市板", "发行价格", "网下申购上限(万股)",
                "募资总额(亿元)", "上市首日均价", "网下A类投资者中签率(%)"]
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError("缺少字段 " + "、".join(sorted(missing)))
    frame["上市日期"] = pd.to_datetime(frame["上市日期"], errors="raise")
    if frame[["股票代码", "上市日期", "上市板"]].isna().any().any():
        raise ValueError("代码、上市日期和板块不能为空")
    for column in required[3:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not frame[column].dropna().map(math.isfinite).all():
            raise ValueError(column + " 存在非有限值")
    if (frame["发行价格"] <= 0).any():
        raise ValueError("发行价格必须为正")
    if args.year is not None:
        frame = frame[frame["上市日期"].dt.year == args.year]
    if args.board:
        frame = frame[frame["上市板"] == args.board]
    issue, detail, summary, monthly, annual = calculate(frame, args.sizes, args.valid_rate)
    if args.year in (None, 2025) and (2025, 2) not in monthly.index:
        monthly.loc[(2025, 2), :] = 0
    output = dict(报价入围率=args.valid_rate, 样本数=len(frame),
                  最早上市日期=None if frame.empty else str(frame["上市日期"].min().date()),
                  最晚上市日期=None if frame.empty else str(frame["上市日期"].max().date()),
                  汇总=summary, 发行统计=records(issue),
                  月度收益=records(monthly.sort_index().reset_index()),
                  年度分板块收益=records(annual.reset_index()))
    if args.details:
        output["个股明细"] = records(detail.sort_values("上市日期"))
    text = json.dumps(output, ensure_ascii=False, allow_nan=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf8")
    else:
        print(text)


if __name__ == "__main__":
    main()

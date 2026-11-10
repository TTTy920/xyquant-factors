"""Calculate monthly Barra style attribution from three local tables."""

import argparse
from datetime import date
import json
import math
from pathlib import Path
import re
import sys


FACTORS = (
    "Size", "Beta", "Momentum", "ResidualVolatility", "NonlinearSize",
    "BookToPrice", "Liquidity", "EarningsYield", "Growth", "Leverage",
)


def month_key(value, allow_day):
    pattern = r"[0-9]{4}-[0-9]{2}" + (r"(?:-[0-9]{2})?" if allow_day else "")
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"月份或日期格式无效：{value!r}")
    date.fromisoformat(value + "-01" if len(value) == 7 else value)
    return value[:7]


def following_month(month):
    year, number = map(int, month.split("-"))
    return f"{year + (number == 12):04d}-{number % 12 + 1:02d}"


def number(value, label):
    if type(value) not in (int, float):
        raise ValueError(f"{label} 需要数值")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} 超出数值范围") from exc
    if not math.isfinite(value):
        raise ValueError(f"{label} 需要有限数值")
    return value


def index_rows(rows, table, month_field, *, per_stock):
    """Keep exactly one observation per month, or per month and security."""
    if not isinstance(rows, list):
        raise ValueError(f"{table} 需要逐行记录")
    indexed = {}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"{table} 第 {index} 行格式无效")
        month = month_key(row.get(month_field), allow_day=(month_field == "日期"))
        key = month
        if per_stock:
            code = row.get("股票代码")
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code.strip()):
                raise ValueError(f"{table} 第 {index} 行需要带交易所后缀的股票代码")
            key = month, code.strip()
        if key in indexed:
            raise ValueError(f"{table} 出现重复记录：{key}")
        indexed[key] = row
    return indexed


def calculate(holdings, stock_exposures, factor_returns):
    """Return the two attribution tables. All arguments are local monthly records."""
    positions = index_rows(holdings, "持仓", "日期", per_stock=True)
    if not positions:
        raise ValueError("请提供至少一行月末持仓")
    exposures = index_rows(stock_exposures, "个股月末暴露", "月份", per_stock=True)
    returns = index_rows(factor_returns, "月度因子收益", "月份", per_stock=False)

    by_month = {}
    for (month, code), row in positions.items():
        weights = by_month.setdefault(month, {})
        weights[code] = number(row.get("持仓比例"), f"{month} {code} 持仓比例")

    exposure_rows = []
    contribution_rows = []
    for month, weights in sorted(by_month.items()):
        terms = {factor: [] for factor in FACTORS}
        for code, weight in weights.items():
            if weight == 0:
                continue
            stock = exposures.get((month, code))
            if stock is None:
                raise ValueError(f"{month} 的 {code} 缺少月末暴露")
            for factor in FACTORS:
                label = f"{month} {code} {factor} 暴露"
                value = number(stock.get(factor), label)
                terms[factor].append(number(weight * value, label))

        exposure = {factor: number(math.fsum(terms[factor]), f"{month} {factor} 组合暴露")
                    for factor in FACTORS}
        exposure_rows.append({"月份": month, **exposure})

        next_month = following_month(month)
        returns_row = returns.get(next_month)
        if returns_row is None:
            continue
        for factor in FACTORS:
            if factor not in returns_row:
                raise ValueError(f"{next_month} 的因子收益缺少 {factor} 列")
        # The original model drops a whole contribution month if any return is missing.
        if any(returns_row[factor] is None for factor in FACTORS):
            continue
        contribution = {"月份": next_month}
        for factor in FACTORS:
            rate = number(returns_row[factor], f"{next_month} {factor} 因子收益")
            contribution[factor] = number(exposure[factor] * rate, f"{next_month} {factor} 贡献")
        contribution_rows.append(contribution)

    return {"月末组合暴露": exposure_rows, "月度风格收益贡献": contribution_rows}


def main():
    parser = argparse.ArgumentParser(description="根据本地月度数据计算 Barra 风格归因")
    parser.add_argument("input", type=Path, help="包含三张输入表的 UTF-8 JSON 文件")
    args = parser.parse_args()
    try:
        with args.input.open(encoding="utf-8-sig") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or not {"holdings", "stock_exposures", "factor_returns"} <= data.keys():
            raise ValueError("需要 holdings、stock_exposures、factor_returns 三张本地表")
        result = calculate(data["holdings"], data["stock_exposures"], data["factor_returns"])
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    except (OSError, ValueError, OverflowError) as exc:
        parser.exit(1, f"{exc}\n")
    sys.stdout.buffer.write((output + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()

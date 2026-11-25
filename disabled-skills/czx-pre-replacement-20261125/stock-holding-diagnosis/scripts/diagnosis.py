"""Rank one stock in each authorized, same-day XYQuant factor cross-section."""
import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from io import BytesIO
import json
import math
from pathlib import Path
import re
import sys
from zipfile import ZipFile

import pyarrow as pa
import pyarrow.parquet as pq
from news_context import read_payload, recent_news


FACTORS = {
    "ROE_TTM": "descending",
    "EP_TTM": "descending",
    "CloseTrend_240D_Reg": "ascending",
    "Amount_20D_Avg": "ascending",
}
STRATEGIES = ("300增强", "xgb500增强", "xgb1000增强", "xgb2000增强")
POLICY = "four-factor-30pct-v1"
MAX_AGE_DAYS = 7  # calendar days, in Asia/Shanghai; never guessed from stale market calendars
MAX_FACTOR_BYTES = 16 * 1024 * 1024
SHANGHAI = timezone(timedelta(hours=8))
CODE = re.compile(r"\d{6}\.(?:SH|SZ|BJ)")


class DataError(ValueError):
    """A fixed, safe-to-report data contract error."""


def iso_date(text):
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise DataError("日期须为 YYYY-MM-DD")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise DataError("日期无效") from None


def shanghai_today():
    return datetime.now(SHANGHAI).date()


def data_date(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.time() != time.min or getattr(value, "nanosecond", 0):
            raise DataError("数据日期必须是不含时区的午夜日期")
        return value.date()
    if isinstance(value, date):
        return value
    raise DataError("数据日期无效")


def normalize_code(value):
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    if re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", value):
        value = f"{value[2:]}.{value[:2]}"
    if re.fullmatch(r"\d{6}", value):
        venue = ("BJ" if value.startswith(("4", "8", "92")) else
                 "SH" if value.startswith(("6", "9")) else "SZ")
        value = f"{value}.{venue}"
    return value if CODE.fullmatch(value) else None


def a_share(code):
    return bool(code and (
        (code.endswith(".SH") and code.startswith("6")) or
        (code.endswith(".SZ") and code.startswith(("00", "30"))) or
        (code.endswith(".BJ") and code.startswith(("43", "83", "87", "88", "92")))
    ))


def rank_panel(table, stock, day, direction):
    if direction not in ("ascending", "descending"):
        raise DataError("因子排序方向不受支持")
    schema = table.schema
    if (set(schema.names) != {"date", "stock_cd", "alpha"} or len(schema) != 3
            or not (pa.types.is_string(schema.field("stock_cd").type)
                    or pa.types.is_large_string(schema.field("stock_cd").type))
            or not (pa.types.is_floating(schema.field("alpha").type)
                    or pa.types.is_integer(schema.field("alpha").type))):
        raise DataError("因子表字段与已验收的 date/stock_cd/alpha 契约不符")
    seen, values = set(), {}
    non_a = 0
    for stamp, raw, value in zip(table["date"].to_pylist(), table["stock_cd"].to_pylist(),
                                 table["alpha"].to_pylist()):
        if data_date(stamp) != day:
            raise DataError("因子导出包含请求日之外的日期")
        code = normalize_code(raw)
        if code:
            if code in seen:
                raise DataError("因子截面含重复股票键")
            seen.add(code)
        if not a_share(code):
            non_a += 1
        elif value is not None and math.isfinite(value):
            values[code] = value

    value = values.get(stock)
    n = len(values)
    row = {
        "direction": direction, "value": value, "rank": None, "band": None,
        "top_position_pct": None, "tie_count": None, "valid_count": n,
        "missing_count": table.num_rows - non_a - n,
        "excluded_non_a": non_a,
        "status": "valid" if value is not None else "no_finite_value" if stock in seen else "not_in_export",
    }
    if value is not None:
        better = sum(v > value for v in values.values()) if direction == "descending" else sum(v < value for v in values.values())
        tied = sum(v == value for v in values.values())
        row.update(rank=better + 1, tie_count=tied)
        if n >= 2:
            pos = Fraction(2 * better + tied - 1, 2 * (n - 1))
            row["top_position_pct"] = round(float(pos * 100), 4)
            row["band"] = "top" if pos <= Fraction(3, 10) else "bottom" if pos >= Fraction(7, 10) else "middle"
    return row


def factor_ranks(archive, stock, day):
    expected = {name + ".parquet": name for name in FACTORS}
    tables = {}
    with ZipFile(BytesIO(archive)) as bundle:
        entries = bundle.infolist()
        if (len(entries) > 12 or sum(f.file_size for f in entries) > MAX_FACTOR_BYTES
                or len({f.filename for f in entries}) != len(entries)):
            raise DataError("因子压缩包大小或文件集合不合法")
        for entry in entries:
            if Path(entry.filename).suffix.lower() not in (".parquet", ".pqt"):
                continue  # metadata is never unpacked or interpreted
            if entry.filename not in expected:
                raise DataError("因子压缩包含未预期的 Parquet 文件")
            f = expected[entry.filename]
            with bundle.open(entry) as member:
                # These are individual in-memory files; avoid dataset background
                # workers that can hang during Arrow shutdown on macOS.
                table = pq.ParquetFile(BytesIO(member.read())).read(use_threads=False)
            tables[f] = rank_panel(table, stock, day, FACTORS[f])
    if set(tables) != set(FACTORS):
        raise DataError("因子压缩包未包含全部四个因子文件")
    return {name: tables[name] for name in FACTORS}


def decision(ranks, factor_day, as_of):
    counts = Counter(result["band"] for result in ranks.values() if result["band"] is not None)
    age = (as_of - factor_day).days if factor_day is not None else None
    if age is None or age < 0:
        status, reason = "insufficient_data", "no_common_factor_date"
    elif age > MAX_AGE_DAYS:
        status, reason = "insufficient_data", "stale_factor_date"
    elif sum(counts.values()) < 3:
        status, reason = "insufficient_data", "too_few_rankable_factors"
    elif counts["top"] >= 3 and not counts["bottom"]:
        status, reason = "supports_holding", "three_or_more_top_no_bottom"
    elif counts["bottom"] >= 3 and not counts["top"]:
        status, reason = "does_not_support_holding", "three_or_more_bottom_no_top"
    else:
        status, reason = "review", "mixed_or_not_concentrated"
    return {
        "status": status, "reason": reason, "policy": POLICY, "factor_age_days": age,
        "max_factor_age_days": MAX_AGE_DAYS, "rankable_factors": sum(counts.values()),
        "bands": {band: counts[band] for band in ("top", "middle", "bottom")},
        "missing_factors": [name for name in FACTORS if ranks.get(name, {}).get("band") is None],
        "holdings_used": False, "risk_checks": "not_verified", "backtest_verified": False,
    }


def summarize_holdings(holdings):
    states = Counter(row["status"] for row in holdings.values())
    changes = Counter(row["change"] for row in holdings.values() if row["status"] == "compared")
    verified = [row["current"] for row in holdings.values() if row["current"] is not None]
    return {
        "requested": len(holdings), "two_verified_snapshots": states["compared"],
        "one_verified_snapshot": states["no_comparable_previous"],
        "reported_only": states["reported_only"], "catalog_only": states["catalog_only"],
        "verified_held": sum(row["held"] for row in verified),
        "verified_not_held": sum(not row["held"] for row in verified),
        "unknown_holding_state": len(holdings) - len(verified),
        "changes": dict(changes),
    }


def report(stock, as_of, factor_day, ranks, holdings, errors, news=None):
    verdict = decision(ranks, factor_day, as_of)
    any_result = any(row["rank"] is not None for row in ranks.values()) or any(
        row["current"] or row["reported_observations"] for row in holdings.values()
    )
    complete = not errors and verdict["status"] != "insufficient_data" and all(
        row["status"] == "compared" and not row["skipped_snapshot_dates"] for row in holdings.values()
    )
    return {
        "status": ("success" if complete else "partial_success") if any_result else "no_result",
        "stock": stock, "as_of": as_of.isoformat(), "factor_date": factor_day.isoformat() if factor_day else None,
        "universe": "authorized_export_a_share_codes_not_verified_all_a",
        "ranks": ranks, "holding_decision": verdict, "holdings": holdings,
        "holdings_role": "historical_reference", "holdings_summary": summarize_holdings(holdings),
        "news": recent_news(news, stock, as_of), "errors": errors,
    }


def main(argv=None):
    from sources import CACHE_ERRORS, AttributionCache, catalog_coverage, empty_holding, factor_archive, holding_changes

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", required=True)
    parser.add_argument("--as-of", help="optional historical cut-off, YYYY-MM-DD; default Shanghai date")
    parser.add_argument("--project", required=True, type=Path, help="existing project containing verified MCP downloads")
    parser.add_argument("--factor-job")
    parser.add_argument("--factor-sha256")
    parser.add_argument("--factor-date")
    parser.add_argument("--attribution-version")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--strategy", choices=STRATEGIES, action="append", help="repeatable; default all four")
    stdin = parser.add_mutually_exclusive_group()
    stdin.add_argument("--news-stdin", action="store_true", help="read optional public news evidence JSON from stdin")
    stdin.add_argument("--context-stdin", action="store_true", help="read optional news and MCP holdings catalog JSON from stdin")
    args = parser.parse_args(argv)
    result, code = {}, 2
    try:
        stock = normalize_code(args.stock)
        today = shanghai_today()
        as_of = iso_date(args.as_of) if args.as_of else today
        if not a_share(stock):
            raise DataError("请输入有效的 A 股代码，建议带交易所后缀")
        if as_of > today:
            raise DataError("查询日期不能晚于上海时间的今天")
        if not args.project.is_absolute() or not args.project.is_dir():
            raise DataError("请指定已存在项目的绝对路径")
        factor_args = (args.factor_job, args.factor_sha256, args.factor_date)
        attribution_args = (args.attribution_version, args.manifest_sha256)
        if (any(factor_args) and not all(factor_args)) or (any(attribution_args) and not all(attribution_args)):
            raise DataError("因子任务需同时提供编号、SHA-256 和日期；归因需同时提供版本和清单 SHA-256")
        factor_day = iso_date(args.factor_date) if args.factor_date else None
        if factor_day and factor_day > as_of:
            raise DataError("因子截面不能晚于查询日期")
        context = read_payload(sys.stdin.buffer) if args.context_stdin else {}
        news = read_payload(sys.stdin.buffer) if args.news_stdin else context.get("news")
        strategies = tuple(dict.fromkeys(args.strategy or STRATEGIES))
        coverage = catalog_coverage(context.get("holdings_catalog"), strategies, as_of, *attribution_args)
        errors, ranks, holdings = {}, {}, {}
        if all(factor_args):
            try:
                ranks = factor_ranks(factor_archive(args.project, *factor_args), stock, factor_day)
            except DataError as exc:
                errors["factors"] = str(exc)
            except CACHE_ERRORS:
                errors["factors"] = "因子导出缓存或校验不可用"
        else:
            errors["factors"] = "没有取得已校验的同日因子导出"
        if all(attribution_args):
            try:
                cache = AttributionCache(args.project, *attribution_args)
                for name in strategies:
                    try:
                        holdings[name] = holding_changes(*cache.strategy(name), stock, as_of)
                        holdings[name]["snapshot_summary_available"] = cache.snapshot_summary_available
                    except (DataError, *CACHE_ERRORS):
                        errors[name] = "策略权重或快照不可用"
            except (DataError, *CACHE_ERRORS):
                errors["holdings"] = "归因清单或持仓缓存不可用"
        else:
            errors["holdings"] = "没有取得已校验的归因数据版本"
        holdings = {
            name: {**holdings.get(name, empty_holding("catalog_only" if name in coverage else "not_available")),
                   "catalog_coverage": coverage.get(name)}
            for name in strategies
        }
        result = report(stock, as_of, factor_day if ranks else None, ranks, holdings, errors, news)
        code = 2 if result["status"] == "no_result" else 0
    except DataError as exc:
        result = {"status": "error", "message": str(exc)}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    # Keep one module identity for helpers importing the engine.
    sys.modules["diagnosis"] = sys.modules[__name__]
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

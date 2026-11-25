"""Estimate positive weights from comparable, explicitly supplied RankIC reports."""
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from statistics import mean, stdev

from synthesis_core import SynthesisError, iso_date, stock_pool
from synthesis_source import download, read_local


IC_MODES = {"rankic-mean", "rankicir"}
DEFAULT_MIN_PERIODS = 10
MAX_REPORT_BYTES = 8 * 1024 * 1024


def hash_string(value):
    if not isinstance(value, str) or not download.HASH.fullmatch(value):
        raise SynthesisError("历史 RankIC 缺少可比的 SHA-256 证据；请用新版 RankIC 重跑")
    return value


def market_signature(data, pool):
    if data.get("origin") != "local_reference":
        raise SynthesisError("RankIC 定权只接受市场基础数据为本地参考的报告，不启用未验收服务端来源")
    adjustment = data.get("adjustment_id")
    if not isinstance(adjustment, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", adjustment):
        raise SynthesisError("历史 RankIC 报告缺少有效复权口径标识")
    roles = ("calendar", "price") if pool else ("calendar", "price", "listed")
    sources = data.get("sources")
    if not isinstance(sources, dict):
        raise SynthesisError("历史 RankIC 缺少市场数据来源")
    signature = []
    for role in roles:
        source = sources.get(role)
        if not isinstance(source, dict) or source.get("format") not in ("csv", "parquet", "hdf5"):
            raise SynthesisError("历史 RankIC 缺少可核对的市场数据文件")
        signature.append((source["format"], hash_string(source.get("sha256")), hash_string(source.get("input_schema_sha256"))))
    covered = tuple(iso_date(data.get(k)) for k in ("calendar_from", "calendar_through", "price_through"))
    scope = hash_string(data.get("authorized_scope_sha256"))
    pins = data.get("catalog_inputs", {})
    if not isinstance(pins, dict) or not set(pins) <= set(roles):
        raise SynthesisError("历史 RankIC 的本地登记版本记录无效")
    versions = []
    for role, pin in sorted(pins.items()):
        if (not isinstance(pin, dict)
                or any(not isinstance(pin.get(key), str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", pin[key])
                       for key in ("dataset", "version"))
                or hash_string(pin.get("file_sha256")) != sources[role].get("sha256")):
            raise SynthesisError("历史 RankIC 的登记版本与实际基础文件不相符")
        versions.append((role, pin["dataset"], pin["version"], hash_string(pin.get("manifest_sha256")), pin["file_sha256"]))
    return (adjustment, scope, tuple(signature), tuple(versions), covered)


def read_history(path, name, pool, score_date, minimum_stocks):
    try:
        raw = read_local(Path(path), MAX_REPORT_BYTES)
        payload = json.loads(raw)
    except (OSError, ValueError, TypeError, RecursionError, download.DownloadError):
        raise SynthesisError("无法读取历史 RankIC metadata.json") from None
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1 or payload.get("status") not in (
            "success", "partial_success", "no_result"):
        raise SynthesisError("输入须为有逐期结果的 RankIC metadata.json")
    params, method, data = (payload.get(k) for k in ("parameters", "method", "data"))
    if not all(isinstance(item, dict) for item in (params, method, data)):
        raise SynthesisError("历史 RankIC 报告结构不完整")
    if (method.get("correlation") != "Spearman; average-tie ranks"
            or method.get("return_formula") != "adj_close(exit) / adj_close(T) - 1"
            or method.get("factor_direction") != "raw_unflipped"
            or type(method.get("std_ddof")) is not int or method["std_ddof"] != 1 or method.get("icir_annualized") is not False):
        raise SynthesisError("历史报告并非已知的原始方向、未年化 RankIC 口径；请用新版 RankIC 重跑")
    start, end = iso_date(params.get("start")), iso_date(params.get("end"))
    frequency, historical_minimum = params.get("frequency"), params.get("min_stocks")
    if (params.get("factor_name") != name or frequency not in ("daily", "weekly", "monthly", "quarterly")
            or type(historical_minimum) is not int or historical_minimum < 3):
        raise SynthesisError("历史报告因子 ID、频率或最低样本口径无效")
    if not start <= end < score_date:
        raise SynthesisError("训练观察窗口必须结束于合成日之前")
    if params.get("pool") != ("custom" if pool else "all_a") or stock_pool(params.get("codes")) != pool:
        raise SynthesisError("历史 RankIC 与合成的股票池类型或自定义代码不匹配")
    signature = market_signature(data, pool)
    calendar_from, calendar_through, price_through = signature[-1]
    if not calendar_from <= start <= end <= calendar_through:
        raise SynthesisError("历史 RankIC 日历未覆盖训练窗口")

    rows = payload.get("periods")
    if not isinstance(rows, list) or len(rows) > 20_000:
        raise SynthesisError("历史 RankIC 逐期结果格式或大小无效")
    accepted, seen, future, unavailable = {}, set(), 0, 0
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in ("valid", "invalid", "incomplete"):
            raise SynthesisError("RankIC 逐期状态无效")
        observed = iso_date(row.get("observation_date"))
        exit_date = iso_date(row["exit_date"]) if row.get("exit_date") is not None else None
        if observed in seen or not start <= observed <= end or (exit_date is not None and exit_date <= observed):
            raise SynthesisError("RankIC 逐期日期冲突、重复或越出观察窗口")
        seen.add(observed)
        if row["status"] != "valid":
            continue
        ic, n, n_pool = (row.get(k) for k in ("rank_ic", "n_valid", "n_pool"))
        unknown, denied = (row.get(k) for k in ("unknown_universe", "excluded_authorization"))
        if (exit_date is None or exit_date > min(calendar_through, price_through)
                or type(ic) not in (float, int) or not -1 <= ic <= 1
                or type(n) is not int or type(n_pool) is not int or not historical_minimum <= n <= n_pool
                or type(unknown) is not int or unknown < 0 or type(denied) is not int or denied < 0):
            raise SynthesisError("标为有效的 RankIC 行缺少可核对的收益日或数值")
        if exit_date >= score_date:
            future += 1
        elif unknown or denied or n < minimum_stocks or (pool and n_pool != len(pool)):
            unavailable += 1
        else:
            accepted[observed] = (exit_date, n_pool, ic, n)
    return ((start, end, frequency, historical_minimum, signature), accepted,
            {"report_sha256": sha256(raw).hexdigest(), "eligible_periods": len(accepted),
             "excluded_unrealized_periods": future, "excluded_sample_or_scope_periods": unavailable})


def estimate(specs, paths, pool, score_date, method, minimum_periods, minimum_stocks, *, required_pins=None):
    if set(paths) != {spec.name for spec in specs}:
        raise SynthesisError("须为每个指定因子提供一个可比的历史 RankIC 报告")
    if method not in IC_MODES or type(minimum_periods) is not int or not 3 <= minimum_periods <= 20_000:
        raise SynthesisError("RankIC 权重模式或最少历史期数无效（最少 3 期）")
    histories = {f.name: read_history(paths[f.name], f.name, pool, score_date, minimum_stocks) for f in specs}
    first, first_periods, _ = next(iter(histories.values()))
    if required_pins is not None:
        versions = tuple((role, pin["dataset"], pin["version"], pin["manifest_sha256"], pin["file_sha256"])
                         for role, pin in sorted(required_pins.items()))
        if any(contract[-1][-2] != versions for contract, _, _ in histories.values()):
            raise SynthesisError("RankIC 权重报告必须由当前 inputs/datasets 的基础数据重算，不能复用旧快照报告")
    if any(contract != first for contract, _, _ in histories.values()):
        raise SynthesisError("历史 RankIC 的训练窗口、频率、样本门槛、市场数据或授权范围不一致")
    common = sorted(set.intersection(*(set(rows) for _, rows, _ in histories.values())))
    for day in common:
        if len({rows[day][:2] for _, rows, _ in histories.values()}) != 1:
            raise SynthesisError("同一训练观测日的收益结束日或股票池不一致")
    start, end, frequency, historical_minimum, _ = first
    info = {
        "mode": method, "origin": "local_rankic_reports",
        "training_start": start.isoformat(), "training_end": end.isoformat(),
        "frequency": frequency, "pool": "custom" if pool else "all_a",
        "common_periods": len(common), "minimum_periods": minimum_periods,
        "low_effective_periods": len(common) < DEFAULT_MIN_PERIODS,
        "minimum_stocks_per_period": max(minimum_stocks, historical_minimum),
        "last_eligible_return_date": max(first_periods[day][0] for day in common).isoformat() if common else None,
        "icir_annualized": False, "factors": {},
    }
    if required_pins is not None:
        info["catalog_inputs"] = required_pins
    raw_weights = {}
    for spec in specs:
        _, periods, diagnostic = histories[spec.name]
        sign = 1 if spec.direction == "descending" else -1
        values = [sign * periods[day][2] for day in common]
        avg = mean(values) if values else None
        sd = stdev(values) if len(values) >= 2 else None
        ir = avg / sd if sd and avg is not None else None
        ir = ir if ir is not None and math.isfinite(ir) else None
        metric = avg if method == "rankic-mean" else ir
        reason = ("too_few_common_periods" if len(common) < minimum_periods else
                  "undefined_metric" if metric is None else "nonpositive_metric" if metric <= 0 else "eligible")
        raw_weights[spec.name] = Fraction(str(metric)) if reason == "eligible" else Fraction(0)
        sample_counts = [periods[day][3] for day in common]
        info["factors"][spec.name] = {
            **diagnostic, "direction_sign": sign, "aligned_mean_rankic": avg, "sample_std": sd,
            "rankicir": ir, "metric_status": reason,
            "min_common_period_stocks": min(sample_counts) if sample_counts else None,
            "max_common_period_stocks": max(sample_counts) if sample_counts else None,
        }
    if sum(w > 0 for w in raw_weights.values()) < 2:
        info["reason"] = "too_few_common_periods" if len(common) < minimum_periods else "fewer_than_two_positive_factors"
        return None, info
    total = sum(raw_weights.values())
    return tuple(replace(f, input_weight=raw_weights[f.name], weight=raw_weights[f.name] / total) for f in specs), info

"""Deterministic direction-aligned percentile scores and weighted composition."""
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from itertools import combinations, groupby
import math
import re


DIRECTIONS = {"ascending", "descending"}
MIN_STOCKS = 20


class SynthesisError(ValueError):
    """Fixed user-safe contract errors."""


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise SynthesisError("日期须为 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SynthesisError("日期无效") from None


def factor_ids(names):
    if (not isinstance(names, (list, tuple)) or not 2 <= len(names) <= 10
            or any(not isinstance(n, str) or not re.fullmatch(r"\w[\w.-]{0,99}", n) for n in names)
            or len({n.casefold() for n in names}) != len(names)):
        raise SynthesisError("请明确指定 2–10 个不重复的 MCP 因子 ID")
    return tuple(names)


def normalize_code(value):
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    if re.fullmatch(r"(SH|SZ|BJ)[0-9]{6}", value):
        value = f"{value[2:]}.{value[:2]}"
    if re.fullmatch(r"[0-9]{6}", value):
        exchange = ("BJ" if value.startswith(("4", "8", "92")) else
                    "SH" if value.startswith(("6", "9")) else "SZ")
        value = f"{value}.{exchange}"
    return value if re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", value) else None


def is_a_share(code):
    return bool(code and (
        (code.endswith(".SH") and code.startswith("6")) or
        (code.endswith(".SZ") and code.startswith(("00", "30"))) or
        (code.endswith(".BJ") and code.startswith(("43", "83", "87", "88", "92")))
    ))


def stock_pool(values):
    if not isinstance(values, (list, tuple)) or len(values) > 1000:
        raise SynthesisError("自定义股票池最多 1000 个明确的 A 股代码")
    codes = [normalize_code(v) for v in values]
    if not all(is_a_share(code) for code in codes):
        raise SynthesisError("股票池仅接受可识别的 A 股代码")
    return tuple(sorted(set(codes)))


@dataclass(frozen=True)
class Factor:
    name: str
    catalog_direction: str
    direction: str
    input_weight: Fraction
    weight: Fraction


def configure(names, directions, weights=None, overrides=None):
    names = factor_ids(names)
    overrides = overrides or {}
    if (set(directions) != set(names) or not set(overrides) <= set(names)
            or any(v not in DIRECTIONS | {"unspecified"} for v in directions.values())
            or any(v not in DIRECTIONS for v in overrides.values())):
        raise SynthesisError("需为每个因子提供 MCP 目录方向，且只允许显式覆盖已指定的因子")
    if any(directions[n] == "unspecified" and n not in overrides for n in names):
        raise SynthesisError("目录缺少方向的因子须由用户明确指定方向")
    if weights is not None and set(weights) != set(names):
        raise SynthesisError("自定义权重须覆盖全部指定因子")
    raw = {}
    for name in names:
        value = "1" if weights is None else weights[name]
        if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)%?", value) or len(value) > 24:
            raise SynthesisError("权重须为有限非负小数，可全用百分比")
        raw[name] = Fraction(value.rstrip("%"))
    if weights is not None and len({value.endswith("%") for value in weights.values()}) != 1:
        raise SynthesisError("自定义权重不能混用百分比和数值")
    if sum(weight > 0 for weight in raw.values()) < 2:
        raise SynthesisError("至少两个启用因子须有正权重")
    total = sum(raw.values())
    return tuple(Factor(n, directions[n], overrides.get(n, directions[n]), raw[n], raw[n] / total) for n in names)


def rank_ticks(panel, codes, ascending):
    """Integer rank-position numerators, before scaling by 2(N−1)."""
    ordered = sorted(codes, key=panel.__getitem__, reverse=not ascending)
    ticks, offset = {}, 0
    for _, group in groupby(ordered, key=panel.__getitem__):
        tied = list(group)
        tick = 2 * (len(codes) - 1 - offset) - len(tied) + 1
        ticks.update(dict.fromkeys(tied, tick))
        offset += len(tied)
    return ticks


def rank_correlations(ticks, codes):
    if not ticks:
        return []
    # The mean doubled-rank score is N−1, including average ties.
    centered = {name: [values[code] - (len(codes) - 1) for code in codes]
                for name, values in ticks.items()}
    square = {name: sum(v * v for v in vec) for name, vec in centered.items()}
    rows = []
    for a, b in combinations(centered, 2):
        rho = None
        if square[a] and square[b]:
            rho = sum(x * y for x, y in zip(centered[a], centered[b])) / math.sqrt(square[a] * square[b])
            rho = min(1.0, max(-1.0, rho))
        rows.append({"factors": [a, b], "spearman": rho, "sample_count": len(codes),
                     "high_absolute_correlation": rho is not None and abs(rho) >= .8})
    return rows


def synthesize(panels, specs, pool=(), minimum=MIN_STOCKS):
    active = {f.name: f for f in specs if f.weight}
    if set(panels) != set(active):
        raise SynthesisError("因子面板未与全部启用因子对齐")
    if type(minimum) is not int or not 2 <= minimum <= 100_000:
        raise SynthesisError("最小共同样本须为 2–100000")
    universe = set(pool) if pool else set().union(*(set(panel) for panel in panels.values()))
    finite = {f: {code for code, value in panel.items() if code in universe and value is not None and math.isfinite(value)}
              for f, panel in panels.items()}
    common = sorted(set.intersection(*finite.values()))
    rejected = {code: [f for f in active if code not in finite[f]]
                for code in sorted(universe.difference(common))}
    n = len(common)
    result = {
        "status": "success" if n >= minimum else "no_result",
        "universe_count": len(universe), "common_count": n, "minimum": minimum,
        "valid_counts": {f: len(finite[f]) for f in active}, "excluded": rejected, "scores": [],
        "correlations": [], "constant_factors": [],
    }
    if n < minimum:
        return result

    ticks = {f: rank_ticks(panels[f], common, spec.direction == "ascending") for f, spec in active.items()}
    result["constant_factors"] = [f for f in active if len(set(ticks[f].values())) == 1]
    result["correlations"] = rank_correlations(ticks, common)
    # Use exact normalized weights; convert to decimal display only at the output boundary.
    multipliers = {f: spec.weight * Fraction(100, 2 * (n - 1)) for f, spec in active.items()}
    result["scores"] = [{"code": code, "score": float(sum(ticks[f][code] * multipliers[f] for f in active))}
                        for code in common]
    return result

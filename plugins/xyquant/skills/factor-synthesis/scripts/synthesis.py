"""Calculate and report a single verified multi-factor cross-section."""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import uuid

from synthesis_core import MIN_STOCKS, SynthesisError, configure, is_a_share, iso_date, normalize_code, stock_pool, synthesize
from synthesis_source import CACHE_ERRORS, checked_archive, read_export
from synthesis_training import generate_reports
from synthesis_weights import DEFAULT_MIN_PERIODS, IC_MODES, estimate

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from xyquant_catalog import CatalogError
from xyquant_data import DataPathError, SYNTHESIS_RESULTS, data_path
from xyquant_flat import rankic_datasets
sys.path.pop(0)


POLICY = "directional-percentile-score-only-v2"


def mapping(entries):
    result = {}
    for entry in entries or ():
        name, sign, value = entry.partition("=")
        if not sign or not name or not value or name in result:
            raise SynthesisError("因子参数须使用不重复的 因子ID=值")
        result[name] = value
    return result


def factor_info(specs):
    return [{"id": f.name, "catalog_direction": f.catalog_direction, "direction": f.direction,
             "direction_overridden": f.direction != f.catalog_direction, "enabled": bool(f.weight),
             "input_weight": str(f.input_weight), "weight_fraction": str(f.weight),
             "weight": float(f.weight)} for f in specs]


def summarize(result, specs, day, pool, stock, non_a, job, digest, weighting):
    scores = result["scores"]
    target = None
    if stock:
        target = next((dict(status="scored", **row) for row in scores if row["code"] == stock), None)
        if target is None:
            reason = ("outside_custom_pool" if pool and stock not in pool else
                      "missing_factor_values" if stock in result["excluded"] else
                      "insufficient_common_sample" if result["status"] == "no_result" else "not_in_authorized_export")
            target = {"code": stock, "status": "not_scored", "reason": reason,
                      "missing_factors": result["excluded"].get(stock, [])}
    return {
        "status": "no_result" if target and target["status"] == "not_scored" else result["status"],
        "policy": POLICY, "date": day.isoformat(),
        "universe": "custom_authorized" if pool else "authorized_export_a_share_codes_not_verified_all_a",
        "requested_pool_codes": list(pool), "universe_count": result["universe_count"],
        "common_count": result["common_count"], "minimum": result["minimum"],
        "excluded_count": len(result["excluded"]), "valid_counts": result["valid_counts"],
        "filtered_non_a": non_a, "constant_factors": result["constant_factors"],
        "factors": factor_info(specs), "weighting": weighting,
        "source": {"kind": "mcp_factor_export", "job_id": job, "sha256": digest},
        "correlations": result["correlations"], "target": target,
        "exported_score_count": len(scores) if not target else int(target["status"] == "scored"),
    }


def markdown(info):
    body = [
        "# 多因子合成得分", "",
        f"- 截面日期：{info['date']}",
        "- 比较池：" + ("用户指定股票池，与 MCP 授权结果取交集" if info["requested_pool_codes"]
                        else "授权导出中的 A 股代码范围，未独立验证完整全 A"),
        f"- 股票池基数 / 共同有效 / 排除：{info['universe_count']} / {info['common_count']} / {info['excluded_count']}",
        "- 方向统一后按共同样本取分位分；缺任一启用因子不评分。不是收益预测或交易建议。", "",
        "| 因子 | 使用方向 | 合成权重 | 有效股票数 |", "|---|---|---:|---:|",
    ]
    for f in info["factors"]:
        direction = ("升序" if f["direction"] == "ascending" else "降序")
        if f["direction_overridden"]:
            direction += "（目录未给方向，用户指定）" if f["catalog_direction"] == "unspecified" else "（用户覆盖）"
        body.append(f"| `{f['id']}` | {direction} | {f['weight']:.6%} | {info['valid_counts'].get(f['id'], '未启用')} |")
    mode = info["weighting"]["mode"]
    label = {"equal": "等权", "manual": "手工权重", "rankic-mean": "RankIC 均值", "rankicir": "RankICIR（未年化）"}[mode]
    body += ["", f"权重方式：{label}。"]
    if mode in IC_MODES:
        w = info["weighting"]
        body += [f"历史观察窗口 {w['training_start']} 至 {w['training_end']}；频率 `{w['frequency']}`；"
                 f"共同有效 {w['common_periods']} 期；最后使用的收益结束日 {w['last_eligible_return_date']}。",
                 "历史权重来自本地 RankIC 报告；定权使用的复权价、日历及适用的在市数据仍为本地参考。"]
        if w["low_effective_periods"]:
            body.append("共同有效期少于 10，权重估计不稳定。")
    if info["common_count"] < info["minimum"]:
        body += ["", f"共同样本不足 {info['minimum']} 只，未生成合成分。"]
    target = info["target"]
    if target:
        body += ["", f"## 指定股票 `{target['code']}`", ""]
        if target["status"] != "scored":
            reasons = {"outside_custom_pool": "不在指定股票池", "missing_factor_values": "缺少启用因子有效值",
                       "insufficient_common_sample": "共同样本不足", "not_in_authorized_export": "本次授权导出没有该代码"}
            body.append("未生成得分：" + reasons[target["reason"]] + "。")
            if target["missing_factors"]:
                body.append("缺项：" + "、".join(f"`{f}`" for f in target["missing_factors"]) + "。")
        else:
            body.append(f"复合因子得分 **{target['score']:.6f}**。")
    elif info["exported_score_count"]:
        body += ["", f"共 {info['exported_score_count']} 只股票的复合因子得分，见 `scores.csv`（按代码排列）。"]
    if info["constant_factors"]:
        body += ["", "常量因子在该截面均为 50 分，不提供区分度：" +
                 "、".join(f"`{f}`" for f in info["constant_factors"]) + "。"]
    high = [x for x in info["correlations"] if x["high_absolute_correlation"]]
    if high:
        body += ["", "## 高相关提醒", ""]
        body.extend(f"- `{r['factors'][0]}` / `{r['factors'][1]}`：Spearman {r['spearman']:+.4f}"
                    for r in high)
        body.append("高正相关可能重复加权；高负相关可能互相抵消。没有自动改变权重。")
    return "\n".join(body) + "\n"


def write_results(project, info, result):
    root = data_path(project, SYNTHESIS_RESULTS, create=True)
    out = root / uuid.uuid4().hex
    with tempfile.TemporaryDirectory(dir=root, prefix=".pending-") as scratch:
        bundle = Path(scratch) / "report"
        bundle.mkdir(mode=0o700)
        (bundle / "metadata.json").write_text(json.dumps(info, indent=2, ensure_ascii=False, allow_nan=False),
                                             encoding="utf-8")
        (bundle / "report.md").write_text(markdown(info), encoding="utf-8")
        if info["exported_score_count"]:
            with (bundle / "scores.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["stock_code", "score"])
                selected = [info["target"]] if info["target"] else result["scores"]
                writer.writerows((row["code"], row["score"]) for row in selected)
        bundle.rename(out)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--job")
    parser.add_argument("--sha256")
    parser.add_argument("--date", required=True)
    parser.add_argument("--factor", required=True, action="append", help="explicit MCP factor ID, repeat 2–10 times")
    parser.add_argument("--direction", required=True, action="append", help="catalog ID=ascending|descending|unspecified")
    parser.add_argument("--weight", action="append", help="ID=weight (all IDs if provided; default equal)")
    parser.add_argument("--weighting", choices=("equal", "manual", "rankic-mean", "rankicir"))
    parser.add_argument("--ic-report", action="append", help="ID=local RankIC metadata.json, repeat for every factor")
    parser.add_argument("--ic-config", type=Path, help="shared local RankIC config; used to generate reports in --plan-weights")
    parser.add_argument("--ic-export", action="append", help="ID=historical_job_id:sha256, repeat for every factor")
    parser.add_argument("--min-ic-periods", type=int, help="default 10, minimum 3 by explicit request")
    parser.add_argument("--plan-weights", action="store_true", help="determine weights and active factor IDs before exporting data")
    parser.add_argument("--override-direction", action="append", help="explicit user override ID=ascending|descending")
    parser.add_argument("--pool-code", action="append", help="explicit custom comparison pool, same as MCP codes")
    parser.add_argument("--stock", help="optional target display; never filters the comparison pool")
    parser.add_argument("--min-stocks", type=int, default=MIN_STOCKS)
    args = parser.parse_args(argv)
    try:
        day = iso_date(args.date)
        if day > datetime.now(timezone(timedelta(hours=8))).date():
            raise SynthesisError("截面日期不能晚于上海时间的今天")
        if not args.project.is_absolute() or not args.project.is_dir():
            raise SynthesisError("请指定已存在项目的绝对路径")
        if not 2 <= args.min_stocks <= 100_000:
            raise SynthesisError("最小共同样本须为 2–100000")
        pool = stock_pool(args.pool_code or [])
        stock = normalize_code(args.stock)
        if args.stock is not None and not is_a_share(stock):
            raise SynthesisError("指定股票必须是可识别的 A 股代码")
        mode = args.weighting or ("manual" if args.weight else "equal")
        if bool(args.weight) != (mode == "manual"):
            raise SynthesisError("手工权重模式需为所有指定因子传 --weight；其他模式不能混用")
        train = args.ic_config is not None or args.ic_export is not None
        if mode not in IC_MODES and (args.ic_report or args.min_ic_periods is not None or train):
            raise SynthesisError("只有 RankIC 均值和 RankICIR 模式接收历史定权参数")
        if train and (args.ic_report or not args.plan_weights or args.ic_config is None or not args.ic_export):
            raise SynthesisError("自动跑 RankIC 须在 --plan-weights 中同时传 --ic-config 和 --ic-export，不混用 --ic-report")
        specs = configure(args.factor, mapping(args.direction), mapping(args.weight) if args.weight else None,
                          mapping(args.override_direction))
        weighting = {"mode": mode}
        plan_outputs = {}
        if mode in IC_MODES:
            minimum_periods = DEFAULT_MIN_PERIODS if args.min_ic_periods is None else args.min_ic_periods
            if not 3 <= minimum_periods <= 20_000:
                raise SynthesisError("最少历史期数无效（须为 3–20000）")
            datasets = rankic_datasets(args.project, not pool)
            for dataset in datasets.values():
                dataset.file(verify=True)
            market_pins = {role: dataset.pin() for role, dataset in datasets.items()}
            if train:
                reports = generate_reports(args.project, specs, pool, day, args.ic_config, mapping(args.ic_export))
                plan_outputs["rankic_reports"] = {name: str(path) for name, path in reports.items()}
            else:
                reports = {name: data_path(args.project, (), path, directory=False)
                           for name, path in mapping(args.ic_report).items()}
            specs, weighting = estimate(specs, reports, pool, day, mode, minimum_periods, args.min_stocks,
                                        required_pins=market_pins)
            if specs is None:
                print(json.dumps({"status": "no_result", "date": day.isoformat(), "weighting": weighting, **plan_outputs},
                                 ensure_ascii=False, allow_nan=False))
                return 2
        active = [f.name for f in specs if f.weight]
        if args.plan_weights:
            print(json.dumps({"status": "ready", "date": day.isoformat(), "active_factors": active,
                              "factors": factor_info(specs), "weighting": weighting, **plan_outputs},
                             ensure_ascii=False, allow_nan=False))
            return 0
        if not args.job or not args.sha256:
            raise SynthesisError("计算得分须提供已经校验的 MCP 下载任务和 SHA-256")
        archive = checked_archive(args.project, args.job, args.sha256, day, active, pool)
        panels, non_a = read_export(archive, active, day, pool)
        result = synthesize(panels, specs, pool, args.min_stocks)
        info = summarize(result, specs, day, pool, stock, non_a, args.job, args.sha256, weighting)
        out = write_results(args.project, info, result)
        print(json.dumps(dict(info, output_dir=str(out)), ensure_ascii=False, allow_nan=False))
        return 0 if info["status"] == "success" else 2
    except (SynthesisError, DataPathError, CatalogError) as exc:
        msg = str(exc)
    except CACHE_ERRORS:
        msg = "无法读取或校验同日 MCP 导出，或本地报告目录不可写"
    print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

#!/usr/bin/env python3
"""Select user or preset holdings; load all other market data from inputs/datasets."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import numpy as np
import pandas as pd
from data_store import Store, DataError, FREQUENCIES, fingerprint, require

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from xyquant_data import ATTRIBUTION_RESULTS, HOLDINGS_INPUTS, data_path
from xyquant_catalog import relative
sys.path.pop(0)

BASE = Path(__file__).resolve().parents[1]
CASH = {"CNY", "CASH", "现金"}
ALIASES = {
    "date": ("date", "日期"),
    "stock_code": ("stock_code", "股票代码", "code"),
    "weight": ("weight", "权重", "持仓权重", "持仓比例"),
}


def read_weights(path, kind, name):
    path = Path(path).expanduser().resolve()
    before = fingerprint(path)
    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, encoding="utf-8-sig", float_precision="round_trip")
    elif path.suffix.lower() == ".xlsx":
        frame = pd.read_excel(path)
    else:
        raise DataError("持仓文件支持 Parquet、CSV 或 XLSX")
    require(before == fingerprint(path), "读取期间持仓文件发生变化")
    frame = frame.rename(columns={c: str(c).strip() for c in frame.columns})
    mapping = {}
    for column, aliases in ALIASES.items():
        found = [c for c in aliases if c in frame.columns]
        require(len(found) == 1, "持仓字段缺失或存在多个对应列", field=column)
        mapping[found[0]] = column
    frame = frame.rename(columns=mapping)[list(ALIASES)].copy()
    require(len(frame) > 0, "持仓文件没有记录")
    frame["date"] = pd.to_datetime(frame.date, errors="raise")
    require(frame.date.dt.tz is None and frame.date.notna().all()
            and frame.date.eq(frame.date.dt.normalize()).all(), "持仓日期需为不含时区和盘中时间的日期")
    require(frame.stock_code.notna().all(), "股票代码缺失")
    frame["stock_code"] = frame.stock_code.astype(str).str.strip().str.upper()
    frame["weight"] = pd.to_numeric(frame.weight, errors="raise").astype(float)
    require(np.isfinite(frame.weight).all() and frame.weight.ge(0).all(), "权重需为有限非负数，使用小数表示")
    require(not frame.duplicated(["date", "stock_code"]).any(), "持仓日期/股票重复")
    is_cash = frame.stock_code.isin(CASH)
    require((is_cash | frame.stock_code.str.fullmatch(r"\d{6}\.(SH|SZ|BJ)")).all(),
            "股票代码需含交易所后缀；现金代码仅支持 CNY、CASH、现金")
    snapshots = []
    for date, rows in frame.groupby("date", sort=True):
        cash = rows.stock_code.isin(CASH)
        total = float(rows.loc[~cash, "weight"].sum())
        snapshots.append({"date": date, "kind": kind, "name": name,
                          "valid": total > 0, "total": total,
                          "cash": float(rows.loc[cash, "weight"].sum())})
    # The original data contract stores positive equity weights sparsely.
    stocks = frame.loc[~is_cash & frame.weight.gt(0)].copy()
    return stocks, pd.DataFrame(snapshots), before


def local_data(args):
    base = Store(args.data_root, holdings_only=True)
    if args.source == "preset" and not args.benchmark_holdings:
        return base.root, {"source": "preset"}
    frequency = FREQUENCIES.get(args.frequency)
    require(frequency is not None, "频率仅支持日、周、月")
    catalog = {key: copy.deepcopy(base.catalog[key]) for key in ("schema_version", "snapshots", "portfolios", "benchmarks")}
    replacements = []
    if args.source == "user":
        replacements.append(("portfolio", args.portfolio, args.holdings))
    if args.benchmark_holdings:
        replacements.append(("benchmark", args.benchmark, args.benchmark_holdings))
    converted = [(kind, name, *read_weights(path, kind, name)) for kind, name, path in replacements]
    root = data_path(args.project, ATTRIBUTION_RESULTS, args.output_root / "_inputs" / uuid.uuid4().hex, create=True)
    shared = set()
    for key in ("portfolios", "benchmarks"):
        shared.update(value["path"] for value in catalog[key].values())
    for name in sorted(shared):
        source = base.path(name)
        target = root.joinpath(*relative(name))
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    snapshots = base.read(catalog["snapshots"])
    sources = []
    for kind, name, weights, new_snapshots, origin in converted:
        name_path = "weights/user_" + kind + ".parquet"
        (root / "weights").mkdir(exist_ok=True)
        weights.to_parquet(root / name_path, index=False, compression="zstd")
        key = "portfolios" if kind == "portfolio" else "benchmarks"
        catalog[key] = {name: {"path": name_path, "frequencies": [frequency], "aliases": []}}
        snapshots = pd.concat([snapshots.loc[snapshots.kind != kind], new_snapshots], ignore_index=True)
        sources.append({"kind": kind, **origin})
    target = root.joinpath(*relative(catalog["snapshots"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshots.to_parquet(target, index=False, compression="zstd")
    (root / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    info = {"source": args.source, "input_data_root": str(root), "holdings_files": sources}
    (root / "input-source.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    base.verify_unchanged()
    return root, info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("inventory", "run"):
        p = sub.add_parser(command)
        p.add_argument("--project", type=Path, default=Path.cwd(), help="project containing xyquant-data (default: current directory)")
        p.add_argument("--data-root", default=os.environ.get("ATTRIBUTION_DATA_ROOT"),
                       required=not bool(os.environ.get("ATTRIBUTION_DATA_ROOT")),
                       help="source of portfolio/benchmark holdings and snapshots only")
        if command == "run":
            p.add_argument("--source", choices=["user", "preset"], required=True)
            for field in ("portfolio", "benchmark", "start", "end", "frequency"):
                p.add_argument("--" + field, required=True)
            p.add_argument("--holdings")
            p.add_argument("--benchmark-holdings")
            p.add_argument("--method", choices=["barra", "brinson", "both"], default="both")
            p.add_argument("--output-root", help="optional subdirectory of xyquant-data/results/portfolio-attribution")
    args = parser.parse_args()
    if args.command == "run":
        if args.source == "user" and not args.holdings:
            parser.error("source=user 必须提供 --holdings；不会改用预置策略")
        if args.source == "preset" and args.holdings:
            parser.error("source=preset 不接收 --holdings；自带持仓请用 source=user")
    try:
        info = None
        args.data_root = data_path(args.project, (), args.data_root)
        if args.command == "run":
            require(bool(args.portfolio.strip()) and bool(args.benchmark.strip()), "组合与基准名称不能为空")
            for field in ("holdings", "benchmark_holdings"):
                path = getattr(args, field)
                if path:
                    setattr(args, field, data_path(args.project, HOLDINGS_INPUTS, path, directory=False))
            args.output_root = data_path(args.project, ATTRIBUTION_RESULTS, args.output_root, create=True)
            data, info = local_data(args)
        else:
            data = args.data_root
        cmd = [sys.executable, "-X", "utf8", str(BASE / "scripts" / "attribute.py"), args.command,
               "--data-root", str(data), "--flat-project", str(args.project.resolve())]
        if args.command == "run":
            for key in ("portfolio", "benchmark", "start", "end", "frequency", "method", "output_root"):
                cmd += ["--" + key.replace("_", "-"), str(getattr(args, key))]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
        if result.returncode == 0 and info is not None:
            reply = json.loads(result.stdout)
            reply["input_source"] = info
            print(json.dumps(reply, ensure_ascii=False, indent=2))
        else:
            sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    except (DataError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "message": str(error),
                          "details": getattr(error, "details", {})}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())

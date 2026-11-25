"""One read-only, date-filtered data layer for both attribution engines."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

FACTORS = ["Size", "Beta", "Momentum", "ResidualVolatility", "NonlinearSize", "BookToPrice",
           "Liquidity", "EarningsYield", "Growth", "Leverage"]
FREQUENCIES = {"daily": "daily", "日频": "daily", "日": "daily", "weekly": "weekly", "周频": "weekly", "周": "weekly",
               "monthly": "monthly", "月频": "monthly", "月": "monthly"}


class DataError(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def require(condition, message, **details):
    if not condition:
        raise DataError(message, **details)


def fingerprint(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return {"path": str(path), "bytes": Path(path).stat().st_size, "sha256": h.hexdigest()}


def schedule(calendar, start, end, frequency):
    d = pd.DatetimeIndex(calendar)
    require(len(d)>2 and d.is_monotonic_increasing and not d.has_duplicates, "交易日历需完整、升序、无重复")
    require(start <= end, "起始日期不能晚于结束日期")
    require(d.min() < start and d.max() > end, "交易日历需要覆盖请求起点之前和终点之后")
    if frequency == "daily":
        return [(d[i-1], d[i]) for i in range(1, len(d)) if start <= d[i] <= end]
    code = "W-SUN" if frequency == "weekly" else "M"
    labels = d.to_period(code)
    require(labels[0] < start.to_period(code) and labels[-1] > end.to_period(code),
            "周/月日历需覆盖请求前后各一周期，以确认完整周期")
    groups = [(key, d[labels == key]) for key in labels.unique()]
    result = [(groups[i-1][1][-1], groups[i][1][-1]) for i in range(1, len(groups)-1)
              if start <= groups[i][1][0] and groups[i][1][-1] <= end]
    require(result, "区间内没有完整周期")
    return result


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.sources = {}
        p = self.path("catalog.json")
        self.catalog = json.loads(p.read_text(encoding="utf-8-sig"))
        require(self.catalog.get("schema_version") == 1, "不支持的数据清单版本")
        self.sources[str(p)] = fingerprint(p)
        self.calendar = pd.DatetimeIndex(self.read(self.catalog["calendar"])["date"])

    def path(self, relative):
        p = (self.root / relative).resolve()
        require(p.is_relative_to(self.root), "数据路径越界", path=str(relative))
        require(p.is_file(), "缺少数据文件", path=str(relative))
        return p

    def read(self, relative, start=None, end=None, columns=None):
        p = self.path(relative)
        require(p.suffix == ".parquet", "统一数据层只读取 Parquet", path=str(relative))
        before = fingerprint(p)
        if str(p) in self.sources:
            require(before == self.sources[str(p)], "运行期间源文件发生变化", path=str(p))
        filters = []
        if start is not None:
            filters.append(("date", ">=", pd.Timestamp(start)))
        if end is not None:
            filters.append(("date", "<=", pd.Timestamp(end)))
        frame = pd.read_parquet(p, columns=columns, filters=filters or None)
        require(before == fingerprint(p), "读取期间源文件发生变化", path=str(p))
        self.sources[str(p)] = before
        if "date" in frame:
            frame["date"] = pd.to_datetime(frame["date"])
        return frame

    def panel(self, files, start, end, columns):
        frames = [self.read(f, start, end, ["date", "stock_code", *columns]) for f in files]
        frames = [f for f in frames if len(f)>0]
        require(frames,"请求范围没有面板数据")
        f = pd.concat(frames, ignore_index=True)
        require(not f.duplicated(["date", "stock_code"]).any(), "面板日期/股票重复")
        require(len(f)>0, "请求范围没有面板数据")
        return f

    def resolve(self, kind, requested, frequency):
        entries = self.catalog[kind]
        target = str(requested).strip().casefold()
        found = [name for name, spec in entries.items()
                 if target in {str(x).strip().casefold() for x in [name, *spec.get("aliases", [])]}]
        require(len(found)==1, "名称不存在或不唯一", requested=requested, available=list(entries))
        name = found[0]
        spec = entries[name]
        require(frequency in spec.get("frequencies", ["daily", "weekly", "monthly"]),
                "组合没有该频率的真实持仓", name=name, available=spec.get("frequencies"))
        return name, spec

    def prepare(self, portfolio, benchmark, start, end, frequency, barra=False, brinson=False):
        self.periods = schedule(self.calendar, start, end, frequency)
        require(self.periods, "没有可分析的周期")
        self.portfolio, ps = self.resolve("portfolios", portfolio, frequency)
        self.benchmark, bs = self.resolve("benchmarks", benchmark, frequency)
        low, high = self.periods[0][0], self.periods[-1][1]
        self.snapshots = self.read(self.catalog["snapshots"], low, high)
        self.holdings = {"portfolio": self.read(ps["path"], low, high), "benchmark": self.read(bs["path"], low, high)}
        market_columns = ["return", "float_cap", "eligible"] if barra else ["return"]
        self.market = self.panel(self.catalog["market"], low, high, market_columns)
        require(set(self.calendar[(self.calendar>=low)&(self.calendar<=high)]).issubset(set(self.market.date)),
                "日收益面板缺少交易日")
        self.daily_returns = self.market.pivot(index="date", columns="stock_code", values="return").astype(float)
        require(not np.isinf(self.daily_returns.to_numpy()).any() and not (self.daily_returns < -1).any().any(), "日收益数值无效")
        self.method_errors = {}
        if barra:
            try:
                self.exposures = self.panel(self.catalog["exposures"], low, high, FACTORS)
            except (DataError, KeyError, ValueError) as e:
                self.method_errors["barra"] = {"message": str(e), "details": getattr(e,"details",{})}
        if brinson:
            try:
                self.industry = self.read(self.catalog["industry"], end=high)
                require(not self.industry.duplicated(["date", "stock_code"]).any(), "行业日期/股票重复")
                g = self.industry.pivot(index="date", columns="stock_code", values="industry").replace("", np.nan)
                targets = pd.DatetimeIndex([p[0] for p in self.periods])
                self.industry_aligned = g.reindex(g.index.union(targets)).sort_index().ffill().reindex(targets)
            except (DataError, KeyError, ValueError) as e:
                self.method_errors["brinson"] = {"message": str(e), "details": getattr(e,"details",{})}

    def weights(self, kind, date):
        name = self.portfolio if kind == "portfolio" else self.benchmark
        snapshots = self.snapshots
        snap = snapshots[(snapshots.kind==kind)&(snapshots.name==name)&(snapshots.date==date)]
        require(len(snap)==1 and bool(snap.iloc[0].valid), "缺少有效期初权重快照", kind=kind, name=name, date=str(date.date()))
        f = self.holdings[kind]
        row = f[f.date==date].set_index("stock_code").weight.astype(float)
        require(not row.index.has_duplicates and len(row)>0 and np.isfinite(row).all() and (row>0).all(), "权重快照异常")
        require(np.isclose(row.sum(), snap.iloc[0].total, rtol=1e-10, atol=1e-12), "稀疏权重合计与快照不符")
        return row/row.sum(), {"kind": kind, "date": str(date.date()), "raw_equity_sum": float(row.sum()), "removed_cash": float(snap.iloc[0].cash)}

    def period_return(self, t0, t1):
        expected = self.calendar[(self.calendar>t0)&(self.calendar<=t1)]
        raw = self.daily_returns.reindex(expected)
        require(len(expected)>0, "区间没有交易日")
        # skipna=False preserves missing daily observations, including delisting gaps.
        return (1+raw).prod(axis=0, skipna=False)-1

    def barra_inputs(self, date):
        f = self.exposures
        x = f[f.date==date].set_index("stock_code")[FACTORS].astype(float)
        m = self.market[self.market.date==date].set_index("stock_code")
        require(len(x)>0 and len(m)>0, "缺少期初风格或市场快照", date=str(date.date()))
        return x, m.float_cap.astype(float), m.eligible.astype(float)

    def verify_unchanged(self):
        for p, fp in self.sources.items():
            require(fingerprint(Path(p))==fp, "计算期间输入发生变化", path=p)

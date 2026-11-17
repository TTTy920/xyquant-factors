"""Read MCP-downloaded public data and customer inputs on the local machine."""
from functools import lru_cache
import json
from pathlib import Path
import zipfile
import h5py
import numpy as np
import pandas as pd
from data_store import Store, FACTORS, DataError, require, schedule, fingerprint
from nav import read_table, aligned_dates


def member_name(info):
    try:
        return info.filename.encode("cp437").decode("gbk")
    except UnicodeError:
        return info.filename


def member(archive, requested):
    found = [f for f in archive.infolist() if member_name(f) == requested]
    require(len(found) == 1, "共用压缩包内缺少唯一数据表", file=requested)
    return found[0]


@lru_cache(maxsize=8)
def calendar_from_zip(path, size, modified):
    with zipfile.ZipFile(path) as archive:
        f = member(archive, "日收益率.csv")
        dates = pd.read_csv(archive.open(f), usecols=[0], encoding="utf-8-sig").iloc[:, 0]
    d = pd.DatetimeIndex(pd.to_datetime(dates)).sort_values()
    require(not d.has_duplicates, "日收益率交易日期重复")
    return d


def weight_input(path, kind, name, calendar, frequency):
    frame = read_table(path).rename(columns={"日期": "date", "股票代码": "stock_code", "权重": "weight", "持仓比例": "weight"})
    require({"date", "stock_code", "weight"} <= set(frame.columns), "持仓表需要日期、股票代码、权重", kind=kind)
    frame = frame[["date", "stock_code", "weight"]].copy()
    frame["date"] = aligned_dates(frame.date, calendar, frequency)
    frame["stock_code"] = frame.stock_code.astype(str).str.strip().str.upper()
    def number(value):
        text = str(value).strip()
        return float(text[:-1])/100 if text.endswith("%") else float(value)
    frame["weight"] = frame.weight.map(number)
    require(np.isfinite(frame.weight).all() and (frame.weight >= 0).all(), "持仓权重需为非负有限数")
    require(not frame.duplicated(["date", "stock_code"]).any(), "持仓日期和股票代码重复", kind=kind)
    cash = frame.stock_code.isin(["CASH", "现金", "CNY"])
    totals = frame.loc[~cash].groupby("date").weight.sum()
    cash_totals = frame.loc[cash].groupby("date").weight.sum()
    snapshots = pd.DataFrame({"date": frame.date.drop_duplicates().sort_values()})
    snapshots["total"] = snapshots.date.map(totals).fillna(0.)
    snapshots["cash"] = snapshots.date.map(cash_totals).fillna(0.)
    snapshots["valid"] = snapshots.total > 0
    snapshots["kind"] = kind
    snapshots["name"] = name
    return frame.loc[~cash & (frame.weight > 0)].copy(), snapshots


class InputStore(Store):
    def __init__(self, config_path, holdings, benchmark_holdings=None):
        require(holdings, "开始归因前必须提供策略持仓及权重")
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        self.root = self.config_path.parent
        self.sources = {}
        self._stats = {}
        self.holdings_path = Path(holdings).resolve()
        self.benchmark_path = Path(benchmark_holdings).resolve() if benchmark_holdings else None
        self.catalog = {"regression": {}}
        self.track(self.config_path)
        if self.config.get("market"):
            market = pd.read_parquet(self.track(self.config["market"]), columns=["date"])
            self.calendar = pd.DatetimeIndex(pd.to_datetime(market.date).unique()).sort_values()
            self.raw = None
        else:
            self.raw = self.track(self.config["raw_zip"])
            stat = self.raw.stat()
            self.calendar = calendar_from_zip(str(self.raw), stat.st_size, stat.st_mtime_ns)

    def track(self, path):
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        p = p.resolve()
        require(p.is_file(), "缺少本地公共数据文件", file=p.name)
        stat = p.stat()
        self._stats[str(p)] = (stat.st_size, stat.st_mtime_ns)
        self.sources[str(p)] = {"path": str(p), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        return p

    def wide(self, requested, encoding, stocks, start, end, historical=False):
        pieces = []
        carry = pd.Series(index=stocks, dtype=object)
        with zipfile.ZipFile(self.raw) as archive:
            f = member(archive, requested)
            header = pd.read_csv(archive.open(f), encoding=encoding, nrows=0).columns
            first = header[0]
            columns = [first] + [c for c in header[1:] if c in stocks]
            reader = pd.read_csv(archive.open(f), encoding=encoding, usecols=columns, float_precision="round_trip", chunksize=256)
            previous_date = None
            for a in reader:
                a[first] = pd.to_datetime(a[first])
                require(a[first].is_monotonic_increasing and not a[first].duplicated().any(), "CSV交易日期需升序且唯一", file=requested)
                require(previous_date is None or a[first].iloc[0] > previous_date, "CSV交易日期重复或顺序异常", file=requested)
                previous_date = a[first].iloc[-1]
                a = a.set_index(first).reindex(columns=stocks)
                if historical:
                    history = a.loc[a.index <= start]
                    if len(history):
                        carry = history.ffill().iloc[-1].combine_first(carry)
                    selected = a.loc[(a.index > start) & (a.index <= end)]
                else:
                    selected = a.loc[(a.index >= start) & (a.index <= end)]
                if len(selected):
                    pieces.append(selected)
        if historical:
            pieces.insert(0, pd.DataFrame([carry], index=pd.DatetimeIndex([start])))
        require(pieces, "请求范围没有股票面板数据", file=requested)
        return pd.concat(pieces).sort_index().reindex(columns=stocks)

    def prepare(self, portfolio, benchmark, start, end, frequency, barra=False, brinson=False):
        self.periods = schedule(self.calendar, start, end, frequency)
        self.portfolio, self.benchmark = portfolio, benchmark
        low, high = self.periods[0][0], self.periods[-1][1]
        p, ps = weight_input(self.track(self.holdings_path), "portfolio", portfolio, self.calendar, frequency)
        require(self.benchmark_path is not None, "请提供所选基准的历史成分及权重", benchmark=benchmark)
        b, bs = weight_input(self.track(self.benchmark_path), "benchmark", benchmark, self.calendar, frequency)
        self.holdings = {"portfolio": p, "benchmark": b}
        self.snapshots = pd.concat([ps, bs], ignore_index=True)
        stocks = sorted(set(p.loc[p.date.between(low, high), "stock_code"]) | set(b.loc[b.date.between(low, high), "stock_code"]))
        if self.raw is None:
            market = pd.read_parquet(self.track(self.config["market"]))
            market["date"] = pd.to_datetime(market.date)
            require(not market.duplicated(["date", "stock_code"]).any(), "股票日收益日期和代码重复")
            self.daily_returns = market.pivot(index="date", columns="stock_code", values="return").loc[low:high].reindex(columns=stocks)
        else:
            self.daily_returns = self.wide("日收益率.csv", "utf-8-sig", stocks, low, high)
        require(set(self.calendar[(self.calendar >= low) & (self.calendar <= high)]).issubset(self.daily_returns.index), "日收益面板缺少交易日")
        require(not np.isinf(self.daily_returns.to_numpy()).any() and not (self.daily_returns < -1).any().any(), "日收益数值无效")
        self.method_errors = {}
        if barra:
            try:
                self.prepare_barra(stocks, frequency)
            except (DataError, KeyError, ValueError, OSError) as error:
                self.method_errors["barra"] = {"message": str(error), "details": getattr(error, "details", {})}
        if brinson:
            try:
                if self.raw is None:
                    industry = pd.read_parquet(self.track(self.config["industry"]))
                    industry["date"] = pd.to_datetime(industry.date)
                    require(not industry.duplicated(["date", "stock_code"]).any(), "行业日期和股票重复")
                    g = industry.pivot(index="date", columns="stock_code", values="industry").reindex(columns=stocks).replace("", np.nan)
                else:
                    g = self.wide("中信行业.csv", "gb18030", stocks, low, high, historical=True).replace("", np.nan)
                targets = pd.DatetimeIndex([t[0] for t in self.periods])
                self.industry_aligned = g.reindex(g.index.union(targets)).sort_index().ffill().reindex(targets)
            except (DataError, KeyError, ValueError, OSError) as error:
                self.method_errors["brinson"] = {"message": str(error), "details": getattr(error, "details", {})}

    def prepare_barra(self, stocks, frequency):
        target_dates = pd.DatetimeIndex([t[0] for t in self.periods])
        self.x = {d: pd.DataFrame(index=stocks, columns=FACTORS, dtype=float) for d in target_dates}
        exposure_source = self.root / self.config["exposures"]
        if exposure_source.suffix == ".parquet":
            exposures = pd.read_parquet(self.track(exposure_source))
            exposures["date"] = pd.to_datetime(exposures.date)
            require(not exposures.duplicated(["date", "stock_code"]).any(), "风格暴露日期和代码重复")
            for date in target_dates:
                rows = exposures.loc[exposures.date == date]
                require(len(rows)>0, "缺少期初风格暴露日期", date=str(date.date()))
                self.x[date] = rows.set_index("stock_code")[FACTORS].reindex(stocks).astype(float)
        else:
            self.hdf_exposures(stocks, target_dates)
        spec = self.config["factor_returns"].get(frequency)
        require(spec, "缺少对应频率的现成因子收益率", frequency=frequency)
        if isinstance(spec, dict) and spec.get("member"):
            with zipfile.ZipFile(self.raw) as archive:
                a = pd.read_excel(archive.open(member(archive, spec["member"])), index_col=0)
        elif str(spec).endswith(".parquet"):
            a = pd.read_parquet(self.track(spec)).set_index("date")
        else:
            a = pd.read_excel(self.track(spec), index_col=0)
        a.index = pd.to_datetime(a.index).normalize()
        a.columns = a.columns.map(str)
        require(not a.index.has_duplicates, "因子收益率日期重复")
        mapping = self.config["factor_columns"]
        require(set(mapping) == set(FACTORS) and len(set(mapping.values())) == len(FACTORS), "十因子列映射不完整或重复")
        require(set(map(str, mapping.values())) <= set(a.columns), "因子收益率缺少所需列")
        self.f = pd.DataFrame({factor: pd.to_numeric(a[str(mapping[factor])], errors="raise") for factor in FACTORS})

    def hdf_exposures(self, stocks, target_dates):
        for factor in FACTORS:
            directory = Path(self.config["exposures"])
            path = self.track(directory / (factor + ".hdf5"))
            with h5py.File(path, "r") as f:
                dates = pd.to_datetime(f["DateTime"][:], unit="s", utc=True).tz_convert("Asia/Shanghai").tz_localize(None).normalize()
                ids = [c.decode() if isinstance(c, bytes) else str(c) for c in f["ID"][:]]
                require(not dates.has_duplicates and len(ids) == len(set(ids)), "风格暴露的日期或股票代码重复", factor=factor)
                rows = {date: i for i, date in enumerate(dates)}
                for date in target_dates:
                    require(date in rows, "缺少期初风格暴露日期", date=str(date.date()), factor=factor)
                    self.x[date][factor] = pd.Series(f["Data"][rows[date], :], index=ids).reindex(stocks)

    def barra_inputs(self, date):
        return self.x[date], None, None

    def factor_return(self, date):
        require(date in self.f.index, "缺少本期因子收益率", date=str(date.date()))
        row = self.f.loc[date]
        require(np.isfinite(row).all(), "本期因子收益率缺失", date=str(date.date()))
        return row

    def verify_unchanged(self):
        for path, expected in self._stats.items():
            stat = Path(path).stat()
            require((stat.st_size, stat.st_mtime_ns) == expected, "计算期间共用数据发生变化", file=Path(path).name)
        for path, source in self.sources.items():
            if "sha256" in source:
                require(fingerprint(Path(path)) == source, "计算期间输入发生变化", file=Path(path).name)

"""Optional observed NAV; the holdings reconstruction remains the fallback."""
from pathlib import Path
import numpy as np
import pandas as pd
from data_store import require


def read_table(path):
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return pd.read_excel(path, dtype=object)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    require(path.suffix.lower() == ".csv", "输入文件只支持 CSV、XLSX、Parquet")
    try:
        return pd.read_csv(path, encoding="utf-8-sig", dtype=object)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="gb18030", dtype=object)


def aligned_dates(values, calendar, frequency):
    dates = pd.to_datetime(values, errors="raise").dt.normalize()
    if frequency in {"monthly", "weekly"}:
        code = "M" if frequency == "monthly" else "W-SUN"
        ends = pd.Series(calendar, index=calendar.to_period(code)).groupby(level=0).max()
        result = []
        for date in dates:
            period = date.to_period(code)
            if date == period.end_time.normalize() and date not in calendar:
                require(period in ends.index, "期末日期不在已有交易日历内", date=str(date.date()))
                date = ends.loc[period]
            result.append(date)
        dates = pd.Series(result, index=values.index, dtype="datetime64[ns]")
    return dates


class ObservedNAV:
    def __init__(self, path, calendar, frequency):
        frame = read_table(path).rename(columns={"日期": "date", "净值": "nav", "组合净值": "nav", "复权净值": "nav"})
        require({"date", "nav"} <= set(frame.columns), "净值表需要日期、净值两列")
        frame["date"] = aligned_dates(frame.date, calendar, frequency)
        frame["nav"] = pd.to_numeric(frame.nav, errors="raise")
        require(not frame.date.duplicated().any(), "净值日期重复")
        require(np.isfinite(frame.nav).all() and (frame.nav > 0).all(), "净值必须为有限正数")
        self.values = frame.set_index("date").nav.sort_index()

    def period_return(self, start, end):
        missing = [str(x.date()) for x in [start, end] if x not in self.values.index]
        require(not missing, "实际净值缺少期初或期末值", dates=missing)
        return float(self.values.loc[end] / self.values.loc[start] - 1)

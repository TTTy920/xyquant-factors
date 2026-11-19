"""Validate the input cache and compare actual, complete holding snapshots."""
from collections import defaultdict
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import re
import time
from zipfile import BadZipFile

import pyarrow as pa
import pyarrow.parquet as pq

from diagnosis import DataError, MAX_FACTOR_BYTES, data_date, iso_date, normalize_code
from xyquant_cache import attribution_download, factor_download


CACHE_ERRORS = (OSError, ValueError, KeyError, TypeError, BadZipFile, pa.ArrowException, factor_download.DownloadError)
MAX_HOLDINGS_FILE_BYTES = 32 * 1024 * 1024
WEIGHTS_COLUMNS = ("date", "stock_code", "weight")
SNAPSHOT_COLUMNS = ("date", "kind", "name", "valid", "total", "cash")


def local_bytes(path, limit, size=None, digest=None):
    if not factor_download.regular(path):
        raise DataError("缓存数据文件不存在")
    if size is not None and path.stat().st_size != size:
        raise DataError("缓存文件大小不匹配")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if (len(content) > limit or (size is not None and len(content) != size)
            or (digest is not None and sha256(content).hexdigest() != digest)):
        raise DataError("缓存文件未通过大小或 SHA-256 校验")
    return content


def factor_archive(project, job, digest, day):
    if not factor_download.HASH.fullmatch(digest):
        raise DataError("因子 SHA-256 无效")
    # Check before the general-purpose status helper (which can hash large exports).
    folder = factor_download.job_folder(project, job, False)
    scope = json.loads(local_bytes(folder / ".xyquant-diagnosis-scope.json", 4096))
    if scope != {"job_id": job, "sha256": digest, "date": day, "scope": "authorized_no_code_filter"}:
        raise DataError("因子导出没有已核对的同日、无代码筛选范围")
    state = json.loads(local_bytes(folder / ".xyquant-state.json", 4096))
    if (state.get("status") != "ready" or state.get("job_id") != job or state.get("sha256") != digest
            or type(state.get("total")) is not int or not 0 < state["total"] <= MAX_FACTOR_BYTES):
        raise DataError("因子缓存未就绪或与 MCP 任务不匹配")
    path = Path(state["path"])
    if path.parent != folder or path.suffix != ".zip":
        raise DataError("因子缓存须为同一任务的四因子 ZIP")
    return local_bytes(path, MAX_FACTOR_BYTES, state["total"], digest)


class AttributionCache:
    def __init__(self, project, version, manifest_digest):
        self.root = attribution_download.cache_folder(project, version, manifest_digest) / "data"
        # Check every project/cache path component before reading.
        for parent in (*reversed(self.root.parents), self.root):
            if not factor_download.regular(parent, directory=True):
                raise DataError("归因缓存数据根不存在")
        content = local_bytes(self.root / "original-data.json", 1024 * 1024)
        manifest = attribution_download.manifest_value(content, version, manifest_digest)
        expiry = manifest.get("expires_at")
        if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= time.time():
            raise DataError("数据包已过期，请重新查询服务器目录并下载当前版本")
        self.files = manifest["files"]
        self.catalog = json.loads(self.read("catalog.json"))
        if self.catalog.get("schema_version") != 1:
            raise DataError("归因目录版本不受支持")
        try:
            snapshots = self.parquet(self.catalog["snapshots"], SNAPSHOT_COLUMNS)
            self.snapshot_summary_available = True
        except CACHE_ERRORS:
            # Independently verified positive weights can still be shown as records,
            # never as a complete holding state or a comparison.
            snapshots = []
            self.snapshot_summary_available = False
        self.snapshots = defaultdict(list)
        for row in snapshots:
            if row["kind"] == "portfolio":
                self.snapshots[row["name"]].append(row)

    def read(self, name):
        spec = self.files[name]
        relative = PurePosixPath(name)
        if any(":" in part or part.endswith((".", " ")) for part in relative.parts):
            raise DataError("归因缓存文件名无效")
        path = self.root
        for part in relative.parts[:-1]:
            path = path / part
            if not factor_download.regular(path, directory=True):
                raise DataError("归因缓存缺少数据目录")
        return local_bytes(path / relative.name, MAX_HOLDINGS_FILE_BYTES, spec["bytes"], spec["sha256"])

    def parquet(self, name, columns):
        if not name.endswith(".parquet"):
            raise DataError("归因权重只接受清单中的 Parquet")
        return pq.ParquetFile(BytesIO(self.read(name))).read(columns=list(columns), use_threads=False).to_pylist()

    def strategy(self, name):
        return self.snapshots.get(name, []), self.parquet(self.catalog["portfolios"][name]["path"], WEIGHTS_COLUMNS)


def positive_finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def complete_weight(metas, weights, stock):
    if len(metas) != 1 or metas[0]["valid"] is not True or not positive_finite(metas[0]["total"]):
        return None
    by_stock = {}
    for row in weights:
        code = normalize_code(row["stock_code"])
        if code is None or code in by_stock or not positive_finite(row["weight"]):
            return None
        by_stock[code] = row["weight"]
    if not by_stock:
        return None
    equity_total = math.fsum(by_stock.values())
    if not math.isclose(equity_total, metas[0]["total"], rel_tol=1e-10, abs_tol=1e-12):
        return None
    # Some originals store raw holdings, not fractions, in the column named weight.
    # Match the attribution engine: compare shares of verified stock holdings only.
    raw_weight = by_stock.get(stock, 0.0)
    return {"weight": raw_weight / equity_total, "raw_weight": raw_weight, "equity_total": equity_total}


def empty_holding(status):
    return {
        "status": status, "current": None, "previous": None, "change_pp": None,
        "change": None, "skipped_snapshot_dates": [], "reported_observations": [],
        "weight_basis": "share_of_verified_equity_holdings",
    }


def catalog_coverage(catalog, strategies, as_of, expected_version=None, expected_digest=None):
    """MCP directory metadata, not evidence of a stock's position."""
    if not isinstance(catalog, dict):
        return {}
    version, digest = catalog.get("data_version"), catalog.get("manifest_sha256")
    if (not isinstance(version, str) or not isinstance(digest, str)
            or not re.fullmatch(r"original-[0-9]{8}-[a-f0-9]{12}", version)
            or not factor_download.HASH.fullmatch(digest)
            or (expected_version is not None and version != expected_version)
            or (expected_digest is not None and digest != expected_digest)):
        return {}
    configured = catalog.get("portfolios")
    if not isinstance(configured, list) or len(configured) > 32:
        return {}
    rows = defaultdict(list)
    for spec in configured:
        if isinstance(spec, dict) and isinstance(spec.get("name"), str) and spec["name"] in strategies:
            rows[spec["name"]].append(spec)
    result = {}
    for name, matches in rows.items():
        if len(matches) != 1:
            continue
        spec = matches[0]
        try:
            first, last = iso_date(spec.get("first_date")), iso_date(spec.get("last_date"))
        except DataError:
            continue
        if first > last:
            continue
        freqs = spec.get("frequencies")
        freqs = [f for f in ("daily", "weekly", "monthly") if f in freqs] if isinstance(freqs, list) else []
        result[name] = {
            "first_date": first.isoformat() if first <= as_of else None,
            "latest_date": last.isoformat() if last <= as_of else None,
            "latest_age_days": (as_of - last).days if last <= as_of else None,
            "frequencies": freqs,
            "covers_dates_on_or_before_as_of": first <= as_of,
        }
    return result


def reported_weight(metas, weights, stock):
    # A positive observed row is not proof that the whole snapshot was complete.
    # Do not promote entries from a period explicitly rejected by the provider.
    if len(metas) > 1 or (metas and metas[0]["valid"] is not True):
        return None
    matches = [row["weight"] for row in weights if normalize_code(row["stock_code"]) == stock]
    return matches[0] if len(matches) == 1 and positive_finite(matches[0]) else None


def holding_changes(snapshots, weights, stock, as_of):
    metas, panels = defaultdict(list), defaultdict(list)
    for table, groups in ((snapshots, metas), (weights, panels)):
        for row in table:
            stamp = data_date(row["date"])
            if stamp <= as_of:
                groups[stamp].append(row)
    result = empty_holding("no_verified_snapshot")
    chosen = []
    for stamp in sorted(metas.keys() | panels.keys(), reverse=True):
        weight = complete_weight(metas[stamp], panels[stamp], stock)
        if weight is None:
            result["skipped_snapshot_dates"].append(stamp.isoformat())
            if len(result["reported_observations"]) < 2:
                observed = reported_weight(metas[stamp], panels[stamp], stock)
                if observed is not None:
                    result["reported_observations"].append({
                        "date": stamp.isoformat(), "reported_raw_weight": observed,
                        "age_days": (as_of - stamp).days, "complete": False,
                    })
            continue
        chosen.append({"date": stamp.isoformat(), **weight, "held": weight["raw_weight"] > 0,
                       "age_days": (as_of - stamp).days})
        if len(chosen) == 2:
            break
    if not chosen:
        if result["reported_observations"]:
            result["status"] = "reported_only"
        return result
    result.update(status="no_comparable_previous", current=chosen[0])
    if len(chosen) < 2:
        return result
    current, previous = (row["weight"] for row in chosen)
    if current > 0 and previous == 0:
        change = "entered"
    elif current == 0 and previous > 0:
        change = "exited"
    elif current == previous == 0:
        change = "not_held_both"
    elif math.isclose(current, previous, rel_tol=0, abs_tol=1e-12):
        change = "unchanged"
    else:
        change = "increased" if current > previous else "decreased"
    return dict(result, status="compared", previous=chosen[1], change=change,
                change_pp=0.0 if change in ("unchanged", "not_held_both") else (current - previous) * 100)

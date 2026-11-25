"""Use only a complete, scope-pinned same-day MCP factor export."""
from datetime import date, datetime, time
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from zipfile import BadZipFile, ZipFile

import pyarrow as pa
import pyarrow.parquet as pq

from synthesis_core import SynthesisError, factor_ids, is_a_share, iso_date, normalize_code, stock_pool


helpers = str(Path(__file__).resolve().parents[3] / "shared")
sys.path.insert(0, helpers)
try:
    import xyquant_download as download
    from mcp_receive import MAX_JOB_BYTES, receive_job
finally:
    sys.path.remove(helpers)


MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_ROWS = 100_000
SCOPE_FILE = ".xyquant-synthesis-scope.json"
CACHE_ERRORS = (OSError, ValueError, TypeError, KeyError, OverflowError, BadZipFile,
                pa.ArrowException, download.DownloadError)


def scope(job_id, digest, day, factors, pool):
    return {"job_id": job_id, "sha256": digest, "date": day.isoformat(),
            "factors": sorted(factors), "codes": list(pool)}


def scope_from_job(job):
    jid, filename, size, digest, _ = download.checked_job(job)
    selection = job.get("selection")
    if (not filename.endswith(".zip") or size > MAX_ARCHIVE_BYTES or not isinstance(selection, dict)
            or selection.get("start") != selection.get("end") or not isinstance(selection.get("codes"), list)):
        raise SynthesisError("需要同日的 MCP 多因子 ZIP 和明确的导出范围")
    ids = factor_ids(selection.get("factors"))
    codes = stock_pool(selection["codes"])
    return scope(jid, digest, iso_date(selection.get("start")), ids, codes)


def read_local(path, limit):
    if not download.regular(path):
        raise SynthesisError("未找到该任务的本地缓存")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise SynthesisError("缓存文件超出单日大小限制")
    return content


def checked_archive(project, job_id, digest, day, factors, pool):
    if not download.HASH.fullmatch(digest):
        raise SynthesisError("MCP 文件 SHA-256 无效")
    folder = download.job_folder(project, job_id, False)
    if json.loads(read_local(folder / SCOPE_FILE, MAX_JOB_BYTES)) != scope(job_id, digest, day, factors, pool):
        raise SynthesisError("缓存与请求的因子、日期或股票池不匹配")
    state = json.loads(read_local(folder / ".xyquant-state.json", 4096))
    if (state.get("status") != "ready" or state.get("job_id") != job_id or state.get("sha256") != digest
            or type(state.get("total")) is not int or not 0 < state["total"] <= MAX_ARCHIVE_BYTES):
        raise SynthesisError("本地 MCP 缓存未校验完成")
    path = download.state_target(folder, state)
    if path.suffix != ".zip":
        raise SynthesisError("因子缓存不是该任务的 ZIP")
    content = read_local(path, MAX_ARCHIVE_BYTES)
    if len(content) != state["total"] or sha256(content).hexdigest() != digest:
        raise SynthesisError("因子 ZIP 的大小或 SHA-256 不匹配")
    return content


def panel_rows(file, day, pool):
    parquet = pq.ParquetFile(file)
    schema = parquet.schema_arrow
    if parquet.metadata.num_rows > MAX_ROWS or len(schema) != 3 or set(schema.names) != {"date", "stock_cd", "alpha"}:
        raise SynthesisError("因子文件不是单日 date/stock_cd/alpha 截面")
    dt, code, value = (schema.field(name).type for name in ("date", "stock_cd", "alpha"))
    if (not (pa.types.is_date(dt) or (pa.types.is_timestamp(dt) and dt.tz is None))
            or not (pa.types.is_string(code) or pa.types.is_large_string(code))
            or not (pa.types.is_floating(value) or pa.types.is_integer(value))):
        raise SynthesisError("因子文件的日期、代码或数值类型无效")
    table = parquet.read()
    seen, values, excluded = set(), {}, 0
    for stamp, raw, alpha in zip(table["date"].to_pylist(), table["stock_cd"].to_pylist(), table["alpha"].to_pylist()):
        if isinstance(stamp, datetime):
            if stamp.time() != time.min or getattr(stamp, "nanosecond", 0):
                raise SynthesisError("因子日期包含日内时间")
            stamp = stamp.date()
        if not isinstance(stamp, date) or stamp != day:
            raise SynthesisError("因子文件含非请求日期，不能拼接截面")
        normalized = normalize_code(raw)
        if normalized:
            if normalized in seen:
                raise SynthesisError("因子文件有重复的日期/股票键")
            seen.add(normalized)
        if pool and normalized not in pool:
            raise SynthesisError("自定义股票池的导出含范围以外的记录")
        if not is_a_share(normalized):
            excluded += 1
        else:
            values[normalized] = alpha
    return values, excluded


def read_export(content, active, day, pool=()):
    expected = {name + ".parquet": name for name in active}
    panels, non_a = {}, {}
    with ZipFile(BytesIO(content)) as archive:
        items = archive.infolist()
        if (len(items) != len(expected) or {item.filename for item in items} != set(expected)
                or sum(item.file_size for item in items) > MAX_EXPANDED_BYTES):
            raise SynthesisError("因子 ZIP 文件集合或大小与指定因子不符")
        for item in items:
            name = expected[item.filename]
            if item.file_size > MAX_ARCHIVE_BYTES:
                raise SynthesisError("单因子截面文件超出大小限制")
            # Never extract archive members to the filesystem.
            with archive.open(item) as stream:
                panels[name], non_a[name] = panel_rows(BytesIO(stream.read(MAX_ARCHIVE_BYTES + 1)), day, set(pool))
    return {name: panels[name] for name in active}, non_a

"""Scope-pinned historical factor exports; never substitutes for market inputs."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from zipfile import BadZipFile, ZipFile

from rankic_engine import iso_date, is_a_share, normalize_code
from rankic_inputs import DataContractError

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
import xyquant_download as download
from mcp_receive import MAX_JOB_BYTES, receive_job
sys.path.pop(0)


SCOPE_FILE = ".xyquant-rankic-scope.json"
MAX_FACTOR_BYTES = 1024**3


def factor_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", value):
        raise DataContractError("Use the exact factor ID returned by the MCP catalog")
    return value


def expected_scope(job_id, digest, name, start, end, codes):
    return dict(job_id=download.check_id(job_id), sha256=digest, factor_id=factor_id(name),
                start=start, end=end, codes=list(codes))


def scope_from_job(job):
    job_id, _, _, digest, _ = download.checked_job(job)
    selection = job.get("selection")
    if (not isinstance(selection, dict) or not isinstance(selection.get("factors"), list)
            or len(selection["factors"]) != 1 or not isinstance(selection.get("codes"), list)):
        raise DataContractError("RankIC requires a single-factor MCP export with an explicit stock scope")
    start, end = iso_date(selection.get("start")), iso_date(selection.get("end"))
    codes = sorted(set(normalize_code(code) for code in selection["codes"]))
    if start > end or any(not is_a_share(code) for code in codes):
        raise DataContractError("Invalid observation window or stock scope in the MCP export")
    return expected_scope(job_id, digest, selection["factors"][0], start, end, codes)


@contextmanager
def factor_input(project, p, job_id, digest, scratch):
    if not download.HASH.fullmatch(digest):
        raise DataContractError("Invalid SHA-256 for the MCP factor export")
    try:
        expected = expected_scope(job_id, digest, p.factor_name, p.start, p.end, p.codes)
        folder = download.job_folder(project, job_id, False)
        marker = folder / SCOPE_FILE
        if not download.regular(marker):
            raise DataContractError("Use rankic_transfer.py to save the scoped MCP export first")
        with marker.open("rb") as stream:
            raw = stream.read(MAX_JOB_BYTES + 1)
        if len(raw) > MAX_JOB_BYTES or json.loads(raw) != expected:
            raise DataContractError("MCP export does not match the requested factor, observation window or stock pool")
        state = download.status(project, job_id)
        if (state.get("status") != "ready" or state.get("sha256") != digest
                or type(state.get("total")) is not int or not 0 < state["total"] <= MAX_FACTOR_BYTES):
            raise DataContractError("MCP factor export is not verified or no longer matches the task")
        path = Path(state["path"])
    except DataContractError:
        raise
    except (download.DownloadError, ValueError, TypeError, OSError, KeyError, RecursionError):
        raise DataContractError("The local MCP factor export or its scope could not be verified") from None

    spec = {"format": "parquet", "columns": {"stock_code": "stock_cd", "value": "alpha"}}
    provenance = {"kind": "xyquant_mcp_factor_export", **expected}
    if path.suffix == ".parquet":
        yield path, {**spec, "sha256": digest}, provenance
        return
    with tempfile.TemporaryDirectory(prefix="factor-", dir=scratch) as temporary:
        target = Path(temporary) / "factor.parquet"
        try:
            with ZipFile(path) as archive:
                members = archive.infolist()
                if (len(members) != 1 or members[0].filename != p.factor_name + ".parquet"
                        or not 0 < members[0].file_size <= MAX_FACTOR_BYTES):
                    raise DataContractError("The MCP ZIP must contain only the requested factor Parquet")
                with archive.open(members[0]) as stream, target.open("xb") as out:
                    shutil.copyfileobj(stream, out, length=1024 * 1024)
                if target.stat().st_size != members[0].file_size:
                    raise DataContractError("MCP factor Parquet size mismatch")
        except (BadZipFile, RuntimeError, OSError) as exc:
            raise DataContractError("Cannot read the verified MCP factor ZIP") from exc
        yield target, spec, provenance

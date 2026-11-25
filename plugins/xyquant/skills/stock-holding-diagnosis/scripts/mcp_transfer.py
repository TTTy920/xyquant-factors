"""Accept an authorized MCP job via private stdin or a one-shot OpenCode loopback."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

from diagnosis import FACTORS, MAX_FACTOR_BYTES, DataError, iso_date
from xyquant_cache import MAX_JOB_BYTES, attribution_download, factor_download, receive_job


HOLDINGS_DOWNLOAD_ERRORS = {
    "归因数据下载失败，请重新查询任务状态和下载凭据": "holdings_http",
    "归因数据下载连接失败，请保留任务编号后重试": "holdings_network",
    "数据下载超过声明大小": "holdings_archive_size",
    "数据下载未通过完整性校验": "holdings_archive_checksum",
    "请通过同一数据任务取得有效下载凭据": "holdings_grant",
    "原版数据清单校验失败": "holdings_manifest_checksum",
    "请提供原版归因数据包的就绪任务": "holdings_not_ready",
}


def save_job(kind, project, job_id, payload):
    if not isinstance(payload, dict) or payload.get("id") != job_id:
        raise DataError("MCP 任务编号与本次传输不符")
    if kind == "holdings":
        grant = payload.get("download")
        if (not isinstance(grant, dict)
                or grant.get("url") != attribution_download.ORIGIN + "/factors/api/attribution-data/" + job_id
                or not isinstance(grant.get("ticket"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{30,128}", grant["ticket"])
                or type(grant.get("expires_at")) not in (float, int) or not math.isfinite(grant["expires_at"])
                or grant["expires_at"] <= time.time()):
            raise DataError("归因任务需要有效下载凭据")
        folder = attribution_download.cache_folder(project, payload.get("data_version"), payload.get("manifest_sha256"))
        for parent in (*reversed(folder.parents), folder, folder / "data"):
            factor_download.regular(parent, directory=True)
        factor_download.regular(folder / "attribution-data.zip")
        return attribution_download.save(payload, project)

    jid, _, size, digest, grant = factor_download.checked_job(payload)
    selection = payload.get("selection")
    if (size > MAX_FACTOR_BYTES or payload.get("extension") != "zip" or grant["expires_at"] <= time.time()
            or not isinstance(selection, dict) or selection.get("codes") != []
            or not isinstance(selection.get("factors"), list)
            or sorted(selection["factors"]) != sorted(FACTORS)
            or selection.get("start") != selection.get("end")):
        raise DataError("需要同日、仅四个指定因子、无股票代码筛选的 MCP 导出")
    day = iso_date(selection.get("start")).isoformat()
    result = factor_download.save(project, payload)
    folder = factor_download.job_folder(project, jid, False)
    scope = dict(job_id=jid, sha256=digest, date=day, scope="authorized_no_code_filter")
    marker = folder / ".xyquant-diagnosis-scope.json"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as stream:
        pending = Path(stream.name)
        try:
            json.dump(scope, stream)
        except BaseException:
            stream.close()
            pending.unlink(missing_ok=True)
            raise
    try:
        factor_download.regular(marker)
        os.replace(pending, marker)
    finally:
        pending.unlink(missing_ok=True)
    return {**result, "factor_date": day}


def transfer(kind, project, job_id, raw):
    try:
        if not 0 < len(raw) <= MAX_JOB_BYTES:
            raise DataError("MCP 任务描述大小不合法")
        return save_job(kind, project, job_id, json.loads(raw))
    except factor_download.DownloadError as exc:
        return {"status": "failed", "code": exc.code, "message": str(exc)}
    except DataError as exc:
        return {"status": "failed", "code": "invalid_job", "message": str(exc)}
    except TimeoutError:
        return {"status": "failed", "code": "download_timeout", "message": "读取下载响应超时；请检查同一任务状态"}
    except ValueError as exc:
        return {"status": "failed", "code": HOLDINGS_DOWNLOAD_ERRORS.get(str(exc), "download_error"),
                "message": "下载或校验失败，请检查任务、权限及网络"}
    except Exception:
        return {"status": "failed", "code": "download_error", "message": "下载或校验失败，请检查任务、权限及网络"}


def receive(project, kind, job_id, wait_seconds):
    return receive_job(job_id, wait_seconds, lambda raw: transfer(kind, project, job_id, raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("save", "receive"))
    parser.add_argument("--kind", required=True, choices=("factors", "holdings"))
    parser.add_argument("--job", required=True, help="non-secret MCP job ID")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--wait-seconds", default=120, type=int, choices=range(15, 241), metavar="15..240")
    args = parser.parse_args()
    if not factor_download.JOB.fullmatch(args.job) or not args.project.is_absolute() or not args.project.is_dir():
        parser.error("expected a job ID and an existing absolute project directory")
    if args.action == "save":
        result = transfer(args.kind, args.project, args.job, sys.stdin.buffer.read(MAX_JOB_BYTES + 1))
    else:
        result = receive(args.project, args.kind, args.job, args.wait_seconds)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())

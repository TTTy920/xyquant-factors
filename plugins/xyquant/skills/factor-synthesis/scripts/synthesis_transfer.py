"""Save a multi-factor MCP ZIP and its credential-free selection metadata."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from synthesis_core import SynthesisError
from synthesis_source import MAX_JOB_BYTES, SCOPE_FILE, download, receive_job, scope_from_job


def save(project, job_id, raw):
    try:
        if not 0 < len(raw) <= MAX_JOB_BYTES:
            raise SynthesisError("MCP 任务描述大小不合法")
        job = json.loads(raw)
        if not isinstance(job, dict) or job.get("id") != job_id:
            raise SynthesisError("MCP 任务编号不匹配")
        marker = scope_from_job(job)
        if job["download"]["expires_at"] <= time.time():
            raise SynthesisError("MCP 下载票据已过期")
        result = download.save(project, job)
        folder = download.job_folder(project, job_id, False)
        target = folder / SCOPE_FILE
        download.regular(target)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as stream:
            pending = Path(stream.name)
            try:
                json.dump(marker, stream, ensure_ascii=False)
            except BaseException:
                stream.close()
                pending.unlink(missing_ok=True)
                raise
        try:
            os.replace(pending, target)
        finally:
            pending.unlink(missing_ok=True)
        return {**result, "factor_date": marker["date"]}
    except download.DownloadError as error:
        return {"status": "failed", "code": error.code, "message": str(error)}
    except SynthesisError as error:
        return {"status": "failed", "code": "invalid_job", "message": str(error)}
    except (ValueError, RecursionError, TypeError, KeyError, OSError, OverflowError):
        return {"status": "failed", "code": "download_error", "message": "任务描述或本地下载不可用"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("save", "receive"))
    parser.add_argument("--job", required=True)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--wait-seconds", type=int, default=120, choices=range(15, 241), metavar="15..240")
    args = parser.parse_args()
    if not download.JOB.fullmatch(args.job) or not args.project.is_absolute() or not args.project.is_dir():
        parser.error("expected a job ID and an existing absolute project directory")
    result = (save(args.project, args.job, sys.stdin.buffer.read(MAX_JOB_BYTES + 1))
              if args.action == "save" else receive_job(args.job, args.wait_seconds,
                                                       lambda raw: save(args.project, args.job, raw)))
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())

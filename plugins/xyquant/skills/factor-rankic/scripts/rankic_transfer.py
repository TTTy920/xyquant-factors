"""Save a verified single-factor MCP export and its credential-free scope."""
import argparse
import json
from pathlib import Path
import sys
import time

from rankic_mcp import MAX_JOB_BYTES, SCOPE_FILE, download, receive_job, scope_from_job


def save(project, job_id, raw):
    try:
        if not 0 < len(raw) <= MAX_JOB_BYTES:
            raise ValueError
        job = json.loads(raw)
        if not isinstance(job, dict) or job.get("id") != job_id:
            raise ValueError
        scope = scope_from_job(job)
        if job["download"]["expires_at"] <= time.time():
            download.fail("grant_expired", "请为同一任务重新取得下载凭据。")
        result = download.save(project, job)
        folder = download.job_folder(project, job_id, False)
        # The common atomic writer can also publish the scope without retaining a grant.
        download.store(folder, scope, filename=SCOPE_FILE)
        return result
    except download.DownloadError as exc:
        return dict(status="failed", code=exc.code, message=str(exc))
    except (ValueError, TypeError, KeyError, OSError, RecursionError, OverflowError):
        return dict(status="failed", code="invalid_job", message="历史因子导出任务或本地缓存不可用。")


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

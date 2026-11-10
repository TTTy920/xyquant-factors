"""Save one authorized XYQuant export. The job descriptor is read from stdin."""
import argparse
from contextlib import contextmanager
import errno
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request

ORIGIN = "https://47.103.215.251"
CHUNK = 1024 * 1024
RETRIES = (2, 5)
JOB = re.compile(r"[0-9a-f]{32}")
HASH = re.compile(r"[0-9a-f]{64}")


class DownloadError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise DownloadError(code, message)


def check_id(value):
    if not isinstance(value, str) or not JOB.fullmatch(value):
        fail("invalid_job", "任务编号无效。")
    return value


def checked_job(job):
    if not isinstance(job, dict) or job.get("status") != "ready":
        fail("not_ready", "请先查询已就绪的导出任务。")
    jid = check_id(job.get("id"))
    name, size, checksum = job.get("filename"), job.get("bytes"), job.get("sha256")
    if (not isinstance(name, str) or len(name.encode("utf-8")) > 255
            or not re.fullmatch(r'[^/\\:<>"|?*\x00-\x1f\x7f-\x9f]+\.(?:parquet|zip)', name)
            or name.startswith(".") or re.match(r"(?i)^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³]) *\.", name)):
        fail("invalid_filename", "导出文件名无效。")
    if type(size) is not int or not 0 < size <= 1024 ** 3 or not isinstance(checksum, str) or not HASH.fullmatch(checksum):
        fail("invalid_manifest", "导出文件的大小或校验值无效。")
    grant = job.get("download")
    if not isinstance(grant, dict) or grant.get("url") != ORIGIN + "/mcp/downloads/" + jid:
        fail("download_unavailable", "请取得该任务的下载凭据，或在网站的最近导出中下载。")
    token, expiry = grant.get("ticket"), grant.get("expires_at")
    if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", token)
            or type(expiry) not in (float, int) or not math.isfinite(expiry)):
        fail("invalid_grant", "下载凭据格式无效。")
    return jid, name, size, checksum, grant


def regular(path, directory=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    required = stat.S_ISDIR if directory else stat.S_ISREG
    if getattr(info, "st_file_attributes", 0) & 0x400 or not required(info.st_mode):
        fail("invalid_path", "下载路径包含链接或类型不匹配的文件。")
    return True


def job_folder(project, jid, create):
    path = Path(project)
    if not path.is_absolute() or ".." in path.parts or str(path).startswith(("\\\\", "//")):
        fail("invalid_project", "请指定已存在的本地项目绝对路径。")
    for parent in (*reversed(path.parents), path):
        if not regular(parent, directory=True):
            fail("invalid_project", "项目目录不存在。")
    for component in ("downloads", "factors", check_id(jid)):
        path = path / component
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
        if not regular(path, directory=True):
            fail("not_found", "项目中没有此下载任务。")
    return path


@contextmanager
def file_lock(path, deadline=None):
    regular(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b", buffering=0) as handle:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                handle.write(b"0")
            handle.seek(0)
            def acquire():
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                acquire()
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if deadline is None or time.time() >= deadline:
                    fail("busy", "项目中已有下载在运行。")
                time.sleep(0.25)
        yield


def store(folder, state):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                     dir=folder, prefix=".xyquant-state-") as out:
        tmp = Path(out.name)
        try:
            json.dump(state, out, ensure_ascii=False)
        except BaseException:
            out.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, folder / ".xyquant-state.json")
    finally:
        tmp.unlink(missing_ok=True)


def matches(path, size, checksum):
    if not regular(path) or path.stat().st_size != size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest() == checksum


def status(project, jid):
    folder = job_folder(project, jid, False)
    state_file = folder / ".xyquant-state.json"
    if not regular(state_file) or state_file.stat().st_size > 4096:
        fail("not_found", "没有本地任务记录。")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    if state["status"] in ("queued", "downloading", "verifying"):
        try:
            with file_lock(folder / ".xyquant.lock"):
                # Read again under the lock: a writer may just have finished.
                state = json.loads(state_file.read_text(encoding="utf-8"))
                if state["status"] in ("queued", "downloading", "verifying"):
                    state["status"] = "interrupted"
        except DownloadError as error:
            if error.code != "busy":
                raise
    if state["status"] == "ready":
        target = Path(state["path"])
        if target.parent != folder or not matches(target, state["total"], state["sha256"]):
            fail("file_changed", "任务文件已移动、删除或发生变化。")
    return state


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fail("redirect_refused", "下载地址发生重定向，请重新查询导出任务。")


def transfer(grant, part, size, checksum, progress):
    if grant["expires_at"] <= time.time():
        fail("grant_expired", "请为同一任务重新取得下载凭据。")
    request = urllib.request.Request(grant["url"], headers={
        "Authorization": "XYQuant-Download " + grant["ticket"], "Accept-Encoding": "identity"})
    opener = urllib.request.build_opener(RefuseRedirect)
    digest = hashlib.sha256()
    received = 0
    last = time.monotonic()
    with opener.open(request, timeout=35) as response, part.open("xb") as dest:
        if (response.status != 200 or response.headers.get("Content-Length") != str(size)
                or response.headers.get("X-Checksum-SHA256") != checksum):
            fail("invalid_response", "下载响应与导出文件信息不符。")
        while True:
            block = response.read(CHUNK)
            if not block:
                break
            received += len(block)
            if received > size:
                fail("invalid_size", "下载内容超出声明的大小。")
            dest.write(block)
            digest.update(block)
            if time.monotonic() - last >= 1:
                progress(received)
                last = time.monotonic()
        if received < size:
            raise http.client.IncompleteRead(b"")
        progress(received)
        if digest.hexdigest() != checksum:
            fail("checksum_mismatch", "SHA-256 校验失败。")
        dest.flush()
        os.fsync(dest.fileno())


def download_attempts(grant, part, size, checksum, update):
    for attempt in range(len(RETRIES) + 1):
        update(status="downloading", bytes=0, attempt=attempt + 1)
        pause = RETRIES[min(attempt, len(RETRIES) - 1)]
        try:
            transfer(grant, part, size, checksum, lambda n: update(bytes=n))
            return
        except urllib.error.HTTPError as error:
            with error:
                if error.code in (401, 410):
                    fail("grant_expired", "凭据或文件已到期，请重新查询同一任务。")
                if error.code == 403:
                    fail("permission_denied", "当前账号或连接的下载权限不足。")
                if error.code == 404:
                    fail("not_found", "文件不存在或已过期。")
                if error.code not in (408, 429, 500, 502, 503, 504):
                    fail("http_error", "下载服务无法处理此请求。")
                retry_after = error.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    pause = min(30, max(pause, int(retry_after)))
                code = "busy" if error.code == 429 else "network_error"
        except (urllib.error.URLError, socket.timeout, ConnectionError, http.client.HTTPException) as error:
            if isinstance(getattr(error, "reason", None), ssl.SSLCertVerificationError):
                fail("tls_error", "无法验证下载服务的 HTTPS 证书。")
            code = "network_error"
        if attempt == len(RETRIES):
            fail(code, "下载重试次数已用完，可凭同一任务编号重试。")
        part.unlink(missing_ok=True)
        time.sleep(pause)


def save(project, job):
    jid, name, size, checksum, grant = checked_job(job)
    folder = job_folder(project, jid, True)
    dest, part = folder / name, folder / ".xyquant.partial"
    state = dict(job_id=jid, status="queued", total=size, bytes=0)

    def update(**changes):
        state.update(changes)
        store(folder, state)

    with file_lock(folder / ".xyquant.lock", deadline=time.time() + 30):
        try:
            if regular(dest):
                if not matches(dest, size, checksum):
                    fail("file_conflict", "目标文件存在且内容不同。")
            else:
                update(status="queued")
                with file_lock(folder.parent / ".xyquant-queue.lock", deadline=grant["expires_at"]):
                    if regular(part):
                        part.unlink()
                    if shutil.disk_usage(folder).free < size:
                        fail("disk_full", "目标磁盘空间不足。")
                    download_attempts(grant, part, size, checksum, update)
                    update(status="verifying", bytes=size)
                    # Publish atomically without overwriting a file created concurrently.
                    try:
                        if os.name == "nt":
                            os.rename(part, dest)
                        else:
                            os.link(part, dest)
                    except FileExistsError:
                        if not matches(dest, size, checksum):
                            fail("file_conflict", "目标文件已被其他程序创建。")
            update(status="ready", bytes=size, path=str(dest), sha256=checksum)
            return state
        except DownloadError as error:
            update(status="failed", code=error.code)
            raise
        except OSError as error:
            code = "disk_full" if error.errno == errno.ENOSPC else "local_io"
            update(status="failed", code=code)
            fail(code, "无法写入项目下载目录。")
        finally:
            if regular(part):
                part.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("save").add_argument("--project", required=True)
    inspect = actions.add_parser("status")
    inspect.add_argument("--project", required=True)
    inspect.add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        if args.action == "save":
            raw = sys.stdin.buffer.read(262145)
            if len(raw) > 262144:
                fail("invalid_manifest", "任务描述过大。")
            result = save(args.project, json.loads(raw))
        else:
            result = status(args.project, args.job)
    except DownloadError as error:
        result = dict(status="failed", code=error.code, message=str(error))
    except (ValueError, OSError, KeyError, TypeError, OverflowError):
        result = dict(status="failed", code="invalid_input", message="请检查任务信息和项目目录。")
    print(json.dumps(result, ensure_ascii=True))
    return int(result.get("status") == "failed")


if __name__ == "__main__":
    sys.exit(main())

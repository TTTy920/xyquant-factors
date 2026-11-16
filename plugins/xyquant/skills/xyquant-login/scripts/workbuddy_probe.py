"""Verify the bridge with a read-only MCP catalog query."""

import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

from xyquant_network import AUTH_URL, HOST


def _authorization_url(line):
    if len(line) > 8192 or any(c.isspace() or ord(c) < 32 for c in line):
        return False
    try:
        url = urlsplit(line)
        if url.fragment or url._replace(query="").geturl() != AUTH_URL:
            return False
        fields = parse_qs(url.query, strict_parsing=True, keep_blank_values=True, max_num_fields=12)
        allowed = {"client_id", "redirect_uri", "response_type", "scope", "state",
                   "code_challenge", "code_challenge_method", "resource"}
        if set(fields) - allowed or any(len(v) != 1 or not v[0] for v in fields.values()):
            return False
        callback = urlsplit(fields["redirect_uri"][0])
        return (fields["resource"] == [f"https://{HOST}/mcp"] and fields["response_type"] == ["code"]
                and fields["code_challenge_method"] == ["S256"] and bool(fields["client_id"][0])
                and bool(re.fullmatch(r"[A-Za-z0-9_~.-]{1,1024}", fields["state"][0]))
                and bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", fields["code_challenge"][0]))
                and callback.scheme == "http" and callback.hostname in {"127.0.0.1", "localhost", "::1"}
                and bool(callback.port) and callback.username is None and not callback.query
                and not callback.fragment)
    except (KeyError, ValueError):
        return False


def _read(stream, kind, events):
    awaiting_url = False
    try:
        while True:
            line = stream.readline(4 * 1024 * 1024 + 1)
            if not line:
                break
            if len(line) > 4 * 1024 * 1024:
                events.put(("invalid", None))
                return
            if kind == "stderr":
                # mcp-remote prints the URL on its own line after this exact marker.
                text = line.decode("utf-8", errors="replace").strip()
                if awaiting_url and _authorization_url(text):
                    events.put(("authorization", text))
                awaiting_url = text == "Please authorize this client by visiting:"
            else:
                events.put(("protocol", line))
    except (OSError, ValueError):
        pass
    finally:
        if kind == "stdout":
            events.put(("ended", None))
        stream.close()


def _stop(proc):
    if os.name == "nt":
        if proc.poll() is None:
            tool = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
            try:
                subprocess.run([str(tool), "/pid", str(proc.pid), "/t", "/f"],
                               capture_output=True, timeout=4)
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=2)
    finally:
        if os.name != "nt":
            # The launcher can exit before a child that ignores SIGTERM.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass


def _send(proc, message):
    record = {"jsonrpc": "2.0", **message}
    proc.stdin.write(json.dumps(record).encode("utf-8") + b"\n")
    proc.stdin.flush()


def _check(proc, timeout, show_link):
    events = queue.Queue()
    for stream, kind in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
        threading.Thread(target=_read, args=(stream, kind, events), daemon=True).start()
    _send(proc, {"id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "xyquant-login-bridge-check", "version": "1.0"},
    }})
    deadline = time.monotonic() + timeout
    expected_id = 1
    catalog_call_id = None
    cursors = set()
    while time.monotonic() < deadline:
        try:
            kind, data = events.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            break
        if kind == "authorization":
            print("请在本机浏览器完成 XYQuant 授权。", flush=True)
            if show_link:
                print(data, flush=True)
            continue
        if kind != "protocol":
            raise RuntimeError("桥接进程未返回完整的 MCP 响应。")
        message = json.loads(data)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RuntimeError("桥接进程返回了无法识别的 MCP 消息。")
        if "method" in message:
            if message["method"] == "ping" and "id" in message:
                _send(proc, {"id": message["id"], "result": {}})
            continue
        if type(message.get("id")) is not int or message["id"] != expected_id:
            continue
        if "error" in message or not isinstance(message.get("result"), dict):
            raise RuntimeError("MCP 服务未能完成当前验证请求。")
        result = message["result"]
        if expected_id == catalog_call_id:
            content = result.get("content")
            has_content = (isinstance(content, list) and bool(content)
                           and all(isinstance(block, dict) and isinstance(block.get("type"), str)
                                   for block in content))
            if (result.get("isError", False) is not False
                    or not (isinstance(result.get("structuredContent"), dict) or has_content)):
                raise RuntimeError("桥接的因子目录查询失败或未返回有效的工具结果。")
            print("桥接已成功调用 query_factor_catalog 查询 ROE_TTM。", flush=True)
            return True
        params = {}
        if expected_id == 1:
            if not isinstance(result.get("serverInfo"), dict) or not result.get("protocolVersion"):
                raise RuntimeError("MCP 服务返回了无效的初始化响应。")
            _send(proc, {"method": "notifications/initialized"})
        else:
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise RuntimeError("MCP 服务未返回工具列表。")
            if any(isinstance(tool, dict) and tool.get("name") == "query_factor_catalog" for tool in tools):
                expected_id += 1
                catalog_call_id = expected_id
                _send(proc, {"id": expected_id, "method": "tools/call", "params": {
                    "name": "query_factor_catalog", "arguments": {"search": "ROE_TTM"},
                }})
                continue
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in cursors or len(cursors) >= 31:
                raise RuntimeError("桥接工具列表中未找到 query_factor_catalog。")
            cursors.add(cursor)
            params["cursor"] = cursor
        expected_id += 1
        _send(proc, {"id": expected_id, "method": "tools/list", "params": params})
    raise RuntimeError("等待浏览器授权或 MCP 查询结果超时。")


def verify(command, env, show_link=False, timeout=300):
    """Return True only when the configured subprocess completes the catalog query."""
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    handle_termination = threading.current_thread() is threading.main_thread()
    previous_handler = signal.getsignal(signal.SIGTERM)
    try:
        if handle_termination:
            signal.signal(signal.SIGTERM, interrupted)
        proc = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, **flags)
        try:
            return _check(proc, timeout, show_link)
        finally:
            _stop(proc)
    except KeyboardInterrupt:
        print("已取消桥接验证。")
    except RuntimeError as exc:
        print(str(exc))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print("桥接进程未能启动或未返回有效的 MCP 数据。")
    finally:
        if handle_termination:
            signal.signal(signal.SIGTERM, previous_handler)
    return False

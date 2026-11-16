"""Run a native Codex OAuth login over a temporary route to XYQuant."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import queue
import select
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

from xyquant_network import AUTH_URL, HOST, open_direct_socket


LOGIN_SECONDS = 300


class ProviderTunnel(socketserver.StreamRequestHandler):
    # This proxy only forwards encrypted traffic to the configured provider.
    def handle(self):
        self.connection.settimeout(10)
        try:
            request = self.rfile.readline(2048).split()
            target = (HOST + ":443").encode("ascii")
            if len(request) != 3 or request[:2] != [b"CONNECT", target]:
                self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                return
            for _ in range(64):
                header = self.rfile.readline(8192)
                if header == b"\r\n":
                    break
                if not header or not header.endswith(b"\n"):
                    return
            else:
                return
            with open_direct_socket(self.server.interface) as remote:
                self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                ends = (remote, self.connection)
                while True:
                    readable, _, _ = select.select(ends, [], [], LOGIN_SECONDS)
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        destination = self.connection if source is remote else remote
                        destination.sendall(data)
        except OSError:
            return


class LoopbackServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


def _browser_candidates():
    if sys.platform == "darwin":
        for name in ("Google Chrome", "Chromium", "Microsoft Edge"):
            for base in (Path("/Applications"), Path.home() / "Applications"):
                yield base / (name + ".app") / "Contents/MacOS" / name
    elif sys.platform == "win32":
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                for app in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe",
                            "Chromium/Application/chrome.exe"):
                    yield Path(base) / app
    names = ("chrome.exe", "msedge.exe", "chromium.exe") if sys.platform == "win32" else (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
        "microsoft-edge-stable",
    )
    for name in names:
        executable = shutil.which(name)
        if executable:
            yield Path(executable)


def _browser_binary():
    if sys.platform.startswith("linux") and not any(os.environ.get(key) for key in
                                                      ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET")):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime or not (Path(runtime) / "wayland-0").is_socket():
            raise RuntimeError("临时直连窗口需要图形桌面。请在运行 Codex 的桌面会话重试，或让普通网络可达 XYQuant 后使用 Codex 原生登录。")
    for binary in _browser_candidates():
        if binary.is_file() and os.access(binary, os.X_OK):
            return binary
    raise RuntimeError("直连窗口需要本机可用的 Chrome、Chromium 或 Microsoft Edge。也可让普通网络可达 XYQuant 后使用 Codex 原生登录。")


def _check_url(url):
    parsed = urlsplit(url)
    if parsed.fragment or parsed._replace(query="", fragment="").geturl() != AUTH_URL:
        raise ValueError
    fields = parse_qs(parsed.query)
    for key in ("client_id", "state", "redirect_uri", "code_challenge"):
        if len(fields.get(key, [])) != 1:
            raise ValueError
    if fields.get("code_challenge_method") != ["S256"] or fields.get("response_type") != ["code"]:
        raise ValueError
    callback = urlsplit(fields["redirect_uri"][0])
    if (callback.scheme != "http" or callback.hostname not in {"127.0.0.1", "::1", "localhost"}
            or not callback.port or callback.username is not None or callback.fragment):
        raise ValueError


def _stop(proc):
    if proc.poll() is not None:
        return
    # A browser or a CLI launcher may have child processes holding the profile
    # or stdio pipes. Stop only the process tree created for this login.
    if os.name == "nt":
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
        if taskkill.is_file():
            try:
                subprocess.run([str(taskkill), "/pid", str(proc.pid), "/t", "/f"], timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if proc.poll() is None and os.name == "nt":
        proc.terminate()
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait()


def _send(proc, **message):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    proc.stdin.flush()


def _read_lines(stream, inbox):
    try:
        for line in stream:
            inbox.put(line)
    finally:
        inbox.put(None)


def _run(interface, codex, resources, show_link):
    codex = shutil.which(codex)
    if not codex:
        raise RuntimeError("当前终端找不到 Codex CLI，请使用已安装插件的 Codex 环境。")
    browser_binary = _browser_binary()
    server = resources.enter_context(LoopbackServer(("127.0.0.1", 0), ProviderTunnel))
    server.interface = interface
    threading.Thread(target=server.serve_forever, daemon=True).start()
    resources.callback(server.shutdown)
    proxy = "http://127.0.0.1:" + str(server.server_address[1])
    env = os.environ.copy()
    for name in ("https_proxy", "http_proxy", "all_proxy"):
        env[name] = env[name.upper()] = proxy
    env["no_proxy"] = env["NO_PROXY"] = "127.0.0.1,localhost,::1"

    profile = resources.enter_context(tempfile.TemporaryDirectory(prefix="xyquant-login-"))
    proc = subprocess.Popen([codex, "app-server", "--listen", "stdio://"], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL,
                            start_new_session=os.name != "nt")
    resources.callback(proc.stdin.close)
    resources.callback(proc.stdout.close)
    resources.callback(_stop, proc)
    inbox = queue.Queue()
    threading.Thread(target=_read_lines, args=(proc.stdout, inbox), daemon=True).start()
    _send(proc, id=1, method="initialize", params={
        "clientInfo": {"name": "xyquant_login", "version": "1.0"},
        "capabilities": {"experimentalApi": True},
    })
    browser = None
    login_id = None
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        try:
            line = inbox.get(timeout=0.5)
        except queue.Empty:
            if browser is not None and browser.poll() is not None:
                raise RuntimeError("授权窗口已关闭或未能启动，Codex 尚未确认授权。请在发起授权的图形桌面内重试。")
            continue
        if line is None:
            raise RuntimeError("Codex 登录进程已退出，未收到授权结果。")
        message = json.loads(line)
        request_id = message.get("id")
        if request_id in (1, 2) and message.get("error"):
            raise RuntimeError("Codex 未能发起 XYQuant 授权，请核对插件已启用，并确认此版本支持 app-server OAuth 登录。")
        if request_id == 1:
            _send(proc, method="initialized")
            _send(proc, id=2, method="mcpServer/oauth/login", params={"name": "xyquant", "timeoutSecs": LOGIN_SECONDS})
        elif request_id == 2:
            result = message["result"]
            url = result["authorizationUrl"]
            _check_url(url)
            login_id = result.get("loginId")
            private = "--inprivate" if "edge" in browser_binary.name.lower() else "--incognito"
            browser = subprocess.Popen([str(browser_binary), "--user-data-dir=" + profile, private,
                                        "--no-first-run", "--no-default-browser-check", "--disable-sync",
                                        "--disable-background-networking", "--disable-component-update",
                                        "--proxy-server=" + proxy, "--app=" + url], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       start_new_session=os.name != "nt")
            resources.callback(_stop, browser)
            deadline = time.monotonic() + LOGIN_SECONDS + 5
            print("已打开 XYQuant 授权窗口，请在本机浏览器登录并确认。", flush=True)
            if show_link:
                print("本次授权链接如下。建议使用这台电脑上刚打开的专用窗口。", flush=True)
                print(url, flush=True)
        elif message.get("method") == "mcpServer/oauthLogin/completed" and browser is not None:
            result = message["params"]
            if result.get("name") != "xyquant" or (login_id and result.get("loginId") != login_id):
                continue
            if result.get("success") is True:
                print("Codex 已确认 XYQuant 授权成功。", flush=True)
                return 0
            raise RuntimeError("Codex 报告此次授权未完成，包括用户取消、链接过期或回调失败。")
    raise RuntimeError("等待 XYQuant 授权超时。" if browser is not None else "等待 Codex 生成授权链接超时。")


def authenticate(interface, codex="codex", show_link=False):
    """Return zero only after Codex confirms this login."""
    def cancel(signum, frame):
        raise KeyboardInterrupt

    try:
        with ExitStack() as resources:
            if threading.current_thread() is threading.main_thread():
                previous = signal.signal(signal.SIGTERM, cancel)
                resources.callback(signal.signal, signal.SIGTERM, previous)
            return _run(interface, codex, resources, show_link)
    except KeyboardInterrupt:
        print("已取消本次 XYQuant 登录。", file=sys.stderr)
        return 130
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
    except (ValueError, KeyError, TypeError, AttributeError):
        print("Codex 返回的授权响应与当前 XYQuant 协议不匹配。", file=sys.stderr)
    except OSError as error:
        print(f"本机登录助手运行失败（系统错误 {error.errno}）。", file=sys.stderr)
    return 1

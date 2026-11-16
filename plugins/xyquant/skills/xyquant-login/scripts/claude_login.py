"""Authenticate the configured XYQuant server with Claude Code's native client."""

import re
import shutil
import subprocess
import sys

from xyquant_network import HOST


MCP_URL = f"https://{HOST}/mcp"
KNOWN_NAMES = ("plugin:xyquant:xyquant", "xyquant")
PENDING = 3


def _handoff(reason, show_link):
    print(reason)
    print("请在使用 XYQuant 的 Claude Code 会话输入 /mcp，选择地址为 "
          f"{MCP_URL} 的服务，按界面提示连接或授权。")
    if show_link:
        print("需要复制本次授权网址时，可从 Claude Code 打开的网页获取。")
    return PENDING


def _inspect(cli, *args):
    try:
        result = subprocess.run([cli, "mcp", *args], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                encoding="utf-8", errors="replace", timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _matches_provider(cli, name):
    # Read only the service URL field. Never emit a server configuration dump.
    details = _inspect(cli, "get", name)
    for line in details.splitlines():
        key, _, value = line.strip().partition(":")
        if key.strip().lower() == "url":
            return value.strip().rstrip("/") == MCP_URL
    return False


def login(server=None, show_link=False):
    # Windows installations may expose the CLI through a batch shim. Restrict all
    # caller-supplied arguments before dispatching to that shim or any other CLI.
    if server is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}", server):
        return _handoff("请在原生界面选择这个服务名。", show_link)
    cli = shutil.which("claude")
    if not cli:
        return _handoff("当前终端未找到 Claude Code 命令。", show_link)

    help_text = _inspect(cli, "login", "--help")
    if not re.search(r"\bclaude mcp login\b", help_text, re.IGNORECASE):
        return _handoff("当前 CLI 未提供可识别的 MCP 登录命令。", show_link)
    candidates = (server,) if server else KNOWN_NAMES
    name = next((item for item in candidates if _matches_provider(cli, item)), None)
    if name is None:
        return _handoff("当前 CLI 未确认该名称对应 XYQuant，请查看本会话加载的服务。", show_link)

    command = [cli, "mcp", "login", name]
    if show_link:
        if "--no-browser" not in help_text:
            return _handoff("当前 CLI 未提供展示授权链接的参数。", True)
        if not sys.stdin.isatty():
            print(f"请在本机交互终端运行 claude mcp login {name} --no-browser。"
                  "该命令会显示本次链接，并在同一终端等待回调。")
            return PENDING
        command.append("--no-browser")
        print("请在此终端按 Claude Code 的提示完成回调。", flush=True)

    try:
        result = subprocess.run(command)
    except OSError:
        return _handoff("无法启动 Claude Code 登录命令。", show_link)
    if result.returncode == 0:
        print("Claude Code 已确认本次 XYQuant 登录。")
        return 0
    print("Claude Code 未确认本次 XYQuant 登录成功。", file=sys.stderr)
    return 1

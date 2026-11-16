"""Route an XYQuant login to the client that will use its credentials."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from xyquant_network import HOST, diagnose


def codex_executable():
    executable = shutil.which("codex")
    if executable or sys.platform != "darwin":
        return executable
    # Desktop distributions can bundle the CLI without adding it to PATH.
    for apps in (Path("/Applications"), Path.home() / "Applications"):
        for bundle in ("ChatGPT.app", "Codex.app"):
            resources = apps / bundle / "Contents/Resources"
            for name in ("codex-cli/CodexCLI.app/Contents/MacOS/codex", "codex"):
                candidate = resources / name
                if candidate.is_file() and candidate.stat().st_mode & 0o111:
                    return str(candidate)
    return None


def check_configuration(codex):
    try:
        result = subprocess.run(
            [codex, "mcp", "get", "xyquant", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return "无法读取本机 Codex 的 MCP 配置，请检查 Codex CLI 是否可用。"
    if result.returncode:
        return "Codex 未能读取 xyquant 服务配置。请在当前 Codex 环境安装或启用 XYQuant 插件，并检查 codex mcp get xyquant。"
    try:
        config = json.loads(result.stdout)
        if not config["enabled"]:
            return "本机 xyquant 服务已停用，请在 Codex 中启用它。"
        endpoint = config["transport"]["url"]
    except (TypeError, KeyError, ValueError):
        return "当前配置未提供 XYQuant 远程 MCP 地址，请核对插件版本和连接方式。"
    if not isinstance(endpoint, str) or endpoint.rstrip("/") != f"https://{HOST}/mcp":
        return "当前 xyquant 配置与此技能支持的服务地址不同，请按已安装插件的说明连接。"
    return None


def codex_login(args):
    codex = codex_executable()
    if not codex:
        print("请在使用 XYQuant 的 Codex 客户端打开 MCP 设置，选择 xyquant 并按连接提示授权。")
        return 3
    problem = check_configuration(codex)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    route = diagnose(allow_direct=not args.system_only)
    print(route.detail, flush=True)
    if route.mode not in ("default", "direct"):
        return 1
    if route.mode == "direct":
        from codex_direct import authenticate
        return authenticate(route.interface, codex=codex, show_link=args.show_link)
    print("正在由 Codex 发起 XYQuant 授权，请在这台电脑上完成网页确认。", flush=True)
    try:
        command = [codex, "mcp", "login", "xyquant"]
        if args.show_link:
            print("可从 Codex 本次打开的授权页面复制网址。", flush=True)
        result = subprocess.run(command)
    except OSError:
        print("无法启动 Codex 登录命令，请核对本机可执行文件。", file=sys.stderr)
        return 2
    if result.returncode:
        print("Codex 未确认本次登录成功。请根据上面的客户端信息处理。", file=sys.stderr)
        return 1
    return 0


def current_client():
    # Agents pass their actual host explicitly; PATH can contain competing CLIs.
    hints = []
    if os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID"):
        hints.append("codex")
    if os.environ.get("CLAUDECODE") == "1" or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        hints.append("claude")
    return hints[0] if len(hints) == 1 else None


def main():
    parser = argparse.ArgumentParser(description="用目标客户端授权 XYQuant，或独立检查此电脑的网络。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="检查本机到公开授权服务的连通性")
    mode.add_argument("--login", action="store_true", help="使用指定客户端的授权入口")
    parser.add_argument("--client", choices=("codex", "claude", "workbuddy"), help="将要使用 XYQuant 的客户端")
    parser.add_argument("--server", help="Claude Code 中显示的 XYQuant MCP 服务名")
    parser.add_argument("--system-only", action="store_true", help="助手只使用系统网络路径")
    parser.add_argument("--show-link", action="store_true", help="显示或指引获取本次授权网址")
    args = parser.parse_args()
    if args.show_link and not args.login:
        parser.error("--show-link 与 --login 一起使用")
    client = args.client or current_client()
    if args.server and (client != "claude" or not args.login):
        parser.error("--server 仅用于 Claude Code 登录")
    if args.check:
        route = diagnose(allow_direct=not args.system_only)
        print(route.detail)
        print("本次只检查这台电脑的网络。账号授权和客户端连接状态请在目标客户端中核实。")
        return 0 if route.mode in ("default", "direct") else 1
    if client == "workbuddy":
        from workbuddy_login import login
        return login(show_link=args.show_link)
    if client == "claude":
        from claude_login import login
        return login(args.server, args.show_link)
    if client == "codex":
        return codex_login(args)
    parser.error("请用 --client codex、--client claude 或 --client workbuddy 指定要授权的客户端。")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

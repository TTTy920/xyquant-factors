"""Connect WorkBuddy Desktop to XYQuant using a local, standard MCP connector."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from xyquant_network import HOST, diagnose


MCP_URL = f"https://{HOST}/mcp"
PACKAGE = "mcp-remote@0.16.6"
PENDING = 3


def native(show_link=False):
    print("在 WorkBuddy 当前任务点击「连接」或「重新连接」。也可打开「专家·技能·连接器 → "
          "连接器 → 自定义连接器」，选择远程服务 XYQuant 并连接。")
    print(f"已有服务的地址应为 {MCP_URL}。授权后回到原任务查询一次因子目录。")
    if show_link:
        print("WorkBuddy 原生授权地址可从它打开的页面复制。")
    return PENDING


def node_launcher(node):
    npx = shutil.which("npx")
    if not node or not npx:
        raise ValueError("本地连接器需要含 npm/npx 的 Node.js 20.18.1 或以上。也可以使用 WorkBuddy 原生连接。")
    version = subprocess.run([node, "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, encoding="ascii", errors="replace", timeout=8)
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version.stdout.strip())
    if not match or tuple(map(int, match.groups())) < (20, 18, 1):
        raise ValueError("当前 Node.js 版本不满足本地连接器要求，最低为 20.18.1。")
    # Run npm's JS entrypoint directly on Windows so its shell shim never
    # reinterprets connector arguments. A symlink to that same file is usual on Unix.
    resolved = Path(npx).resolve()
    if resolved.name == "npx-cli.js":
        return [node, str(resolved)]
    for base in (Path(npx).parent, Path(node).resolve().parent):
        script = base / "node_modules/npm/bin/npx-cli.js"
        if script.is_file():
            return [node, str(script)]
    if os.name != "nt":
        return [npx]
    raise ValueError("当前 npm 安装缺少 npx-cli.js，请使用带有 npm 的 Node.js 安装或 WorkBuddy 原生连接。")


def connector():
    # Both callers use *identical* transport options and the helper's own cache.
    node = shutil.which("node")
    launcher = node_launcher(node)
    cache = Path.home() / ".xyquant" / "workbuddy-mcp-auth"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    # WorkBuddy persists this path after the versioned plugin installation expires.
    metadata = cache / "workbuddy_oauth.json"
    public_metadata = Path(__file__).with_name(metadata.name).read_bytes()
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
        temporary = Path(output.name)
    try:
        temporary.write_bytes(public_metadata)
        os.replace(temporary, metadata)
    finally:
        temporary.unlink(missing_ok=True)
    command = launcher + [
        "--yes", "--package=" + PACKAGE, "--", "mcp-remote", MCP_URL,
        "--transport", "http-only", "--host", "127.0.0.1", "--resource", MCP_URL,
        "--static-oauth-client-metadata", "@" + str(metadata),
    ]
    # npm's children still look up "node" on PATH. WorkBuddy's desktop process
    # can start with a much smaller PATH than the shell that generated the entry.
    paths = [str(Path(node).parent), str(Path(command[0]).parent)]
    if os.name == "nt":
        system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        paths.extend(str(p) for p in (system / "System32", system, system / "System32/WindowsPowerShell/v1.0"))
    else:
        paths.extend(("/usr/local/bin", "/usr/bin", "/bin"))
    settings = {"MCP_REMOTE_CONFIG_DIR": str(cache),
                "PATH": os.pathsep.join(dict.fromkeys(paths))}
    return {"type": "stdio", "command": command[0], "args": command[1:], "env": settings}


def print_config(entry):
    print(json.dumps({"mcpServers": {"xyquant-local": entry}}, ensure_ascii=False, indent=2))


def login(show_link=False, config_only=False, timeout=300):
    try:
        entry = connector()
        if config_only:
            print_config(entry)
            return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"无法准备 WorkBuddy 本地连接器。{error}", file=sys.stderr)
        return 2 if config_only else native(show_link)

    from workbuddy_probe import verify
    command = [entry["command"], *entry["args"]]
    print("正在连接 XYQuant。首次使用时 npm 会获取固定版本的 mcp-remote。若需授权，"
          "请在本机浏览器完成确认。", flush=True)
    if not verify(command, {**os.environ, **entry["env"]}, show_link=show_link, timeout=timeout):
        print("可用 --check 检查本机到 XYQuant 的网络。首次启动还需要 npm 能获取 mcp-remote。")
        return 1
    print("本地连接器已成功调用 XYQuant 的因子目录查询。")
    print("如果 WorkBuddy 尚未添加 xyquant-local，请打开「自定义连接器 → 添加 MCP」，"
          "将下列 xyquant-local 条目合并到 mcpServers 后保存。已有此配置时，选择它并重新连接。")
    print_config(entry)
    print("请在 WorkBuddy 用 xyquant-local 查询 ROE_TTM 的定义，确认原任务已连通。")
    return PENDING


def main():
    parser = argparse.ArgumentParser(description="XYQuant 的 WorkBuddy 连接器")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--native", action="store_true", help="使用 WorkBuddy 自带的远程连接入口")
    modes.add_argument("--login", action="store_true", help="启动本地连接器，完成或复用授权并验证工具")
    modes.add_argument("--config", action="store_true", help="只打印可合并到 WorkBuddy 的无令牌配置")
    modes.add_argument("--check", action="store_true", help="仅检查这台电脑访问公开 OAuth 服务的网络")
    parser.add_argument("--show-link", action="store_true", help="在发生新授权时显示这一次的登录入口")
    parser.add_argument("--timeout", type=int, default=300, help="本地连接器的最长等待秒数")
    parser.add_argument("--system-only", action="store_true", help="网络检查仅使用系统路径")
    args = parser.parse_args()
    if args.timeout <= 0 or args.show_link and not (args.login or args.native):
        parser.error("等待时间须大于零；--show-link 与 --login 或 --native 一起使用。")
    if args.check:
        route = diagnose(allow_direct=not args.system_only)
        print(route.detail)
        return 0 if route.mode in ("default", "direct") else 1
    if args.native:
        return native(args.show_link)
    return login(show_link=args.show_link, config_only=args.config, timeout=args.timeout)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

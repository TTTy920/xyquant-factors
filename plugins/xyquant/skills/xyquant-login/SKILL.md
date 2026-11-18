---
name: xyquant-login
description: 在 Codex、Claude Code 或 WorkBuddy 中连接、重新授权 XYQuant 插件，获取本次授权入口并诊断 MCP 连接超时。可在 Windows、macOS、Linux 上使用各客户端提供的原生入口。
---

# XYQuant 插件授权

确认用户接下来要在哪个客户端使用 XYQuant。用户点名的客户端优先，否则沿用当前会话的宿主。多客户端共存时，执行脚本显式传 `--client`。如果在普通终端且目标不明确，先确认目标。服务器地址是 `https://47.103.215.251/mcp`。各客户端各自生成授权链接、接收回调并管理自己的授权状态。

如果用户想查询或下载数据，先调用该宿主已加载的 XYQuant 工具。遇到授权过期、需要登录或明确要求重连时，选择下表对应的入口。已有有效连接时继续原任务。

| 目标客户端 | 如何连接 |
|---|---|
| Codex | 已配置 `xyquant` 时使用 `codex mcp login xyquant`。有脚本环境时，运行 `scripts/login.py --client codex --login`，可同时处理本地网络超时。 |
| Claude Code | 在当前会话输入 `/mcp`，选择地址匹配 XYQuant 的服务并按提示授权。也可运行 `scripts/login.py --client claude --login`，由脚本识别服务名并调用该版本的 `claude mcp login`。用户在 `/mcp` 中看到自定义名称时可带 `--server 实际名称`。旧版 CLI 或只提供会话界面时沿用 `/mcp`。 |
| WorkBuddy | 已有远程连接时优先点当前任务的「连接」或「重新连接」。桌面客户端需要独立方法、原生授权出现 `invalid_target` 或要使用本地连接器时，使用下文的 WorkBuddy 脚本。云端任务使用平台的连接卡片。 |

在用户原来的客户端中检查连接结果。用户确认、回到客户端后，用一次只读 XYQuant 查询验证可用性，例如查询 `ROE_TTM` 的目录定义，再继续原任务。出现拒绝、取消或过期时，按实际结果告知用户。仅进入授权页、列出操作步骤或通过网络检查时，状态仍为待授权或待验证。

## 脚本和网络诊断

从本技能目录执行，或把 `scripts/login.py` 换成此次安装的实际绝对路径。使用可用的 Python 3.9+：macOS/Linux 通常是 `python3`，Windows 可用 `py -3` 或 `python`。各客户端的原生授权入口可以直接使用。

```sh
python3 scripts/login.py --client codex --login
python3 scripts/login.py --client claude --login
python3 scripts/login.py --client workbuddy --login
python3 scripts/login.py --check
```

脚本退出码 3 表示还需在目标客户端操作。工作区与技能安装位置不同时，用当前已安装技能的绝对路径运行脚本。

## WorkBuddy 桌面本地连接器

在装有 WorkBuddy 的电脑上运行。使用 Python 3.9+ 和带 npm/npx 的 Node.js 20.18.1 或以上。独立入口和 `login.py --client workbuddy --login` 调用同一个实现。

1. 运行 `python3 scripts/workbuddy_login.py --login`。脚本使用固定版本的 `mcp-remote@0.16.6` 连接 XYQuant；npm 首次可能需要下载依赖。首次授权时，用户在本机浏览器登录并确认。明确索取本次链接时加 `--show-link`。可复用的本机授权会直接进入工具验证。
2. 验证通过后，在 WorkBuddy 的「专家·技能·连接器 → 连接器 → 自定义连接器 → 添加 MCP」把输出的 `xyquant-local` 条目加入已有的 `mcpServers` 并保存。也可以先用 `python3 scripts/workbuddy_login.py --config` 单独生成配置，由 WorkBuddy 在启用该连接时发起授权。配置中的路径属于当前电脑，请在接收者电脑安装技能后生成。若本机已配置这项且路径有效，可以直接使用。
3. 回到原 WorkBuddy 任务，让它通过 `xyquant-local` 查询 `ROE_TTM` 的定义。脚本会先用 `query_factor_catalog` 实际检索 `ROE_TTM`。在 WorkBuddy 中的查询用于确认当前任务也已加载该连接。

本地连接器在注册、PKCE 授权码交换和刷新时使用其独立的本机缓存。对 `invalid_target`，核对授权及换取令牌时的 OAuth 资源均为 `https://47.103.215.251/mcp`，包括末尾的 `/mcp`。本方法在连接器启动参数中显式指定该资源。生成的 WorkBuddy 配置只包含启动参数和缓存目录。需要原生入口指引时可运行 `python3 scripts/workbuddy_login.py --native`。[WorkBuddy 连接器说明](https://open.workbuddy.cn/docs/connector) · [mcp-remote](https://www.npmjs.com/package/mcp-remote/v/0.16.6)

`--check` 只访问公开的 OAuth 服务信息。默认网络连接失败时，会在 Windows、macOS 或 Linux 上检测可用的物理网卡。网络要求所有流量遵循系统代理或 VPN 时，增加 `--system-only`。这些检查只说明运行脚本的机器能否访问 XYQuant。WorkBuddy 云端连接器的结果以其连接状态和工具调用为准。

对于 Codex，如果仅备用网卡能访问 XYQuant，脚本可为本次登录打开临时直连窗口。这条路径依赖可用网卡、图形桌面及 Chrome、Chromium 或 Edge。Claude Code 和 WorkBuddy 继续使用自身的登录与网络设置。遇到同样的网络现象时，使用允许访问 XYQuant 的网络或按本机网络策略配置直连，再从原客户端重试。临时窗口关闭后，后续查询仍需该客户端通常使用的网络能访问服务。

## 授权链接

用户需要固定的网站入口时，提供[个人空间](https://47.103.215.251/factors/preview/#personal-space)，用于查看账号资料、API Key 和进入因子下载。查看或撤销 OAuth 连接使用当前客户端的连接管理入口，需要管理员处理时联系管理员。用户要本次授权链接时，由指定客户端发起新请求；脚本登录命令可加 `--show-link`。在需手动回调的 CLI 流程中，让用户直接按本机终端提示操作。登录密码、授权码、完整回调地址与令牌均由客户端和 XYQuant 处理。保持该登录流程运行直到成功、取消或超时。

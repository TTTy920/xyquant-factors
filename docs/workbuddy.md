# 在 WorkBuddy 中使用 XYQuant

WorkBuddy 与 Codex 共用本仓库中的客户端、Skills、登录和下载程序。兼容层只提供市场清单、插件元数据和启动配置；没有另外实现取数服务。

## 安装

1. 在 WorkBuddy 的插件／技能套件市场中选择添加市场，输入[WorkBuddy 专用市场清单](https://raw.githubusercontent.com/TTTy920/xyquant-factors/codex/workbuddy-compat/docs/workbuddy-marketplace.json)的地址。清单按需从独立兼容分支拉取所选插件的完整目录。
2. 按操作系统安装其中一个插件：Windows 10/11 x64 选择 `xyquant-factors`；macOS 11 或以上（Apple 芯片或 Intel）选择 `xyquant-factors-macos`。宿主本身的系统要求另计。
3. 按 WorkBuddy 提示刷新插件或重新打开项目对话，说“连接我的 XYQuant 账号”。在同一台电脑的浏览器打开返回的授权链接，登录并确认授权；密码不输入对话。
4. 返回同一对话提出需求，例如：“下载 ROE_TTM，2020-01-01 至 2026-10-31，600519.SH，保存到当前项目。”

兼容层保存在 `codex/workbuddy-compat` 分支，不需要先合并到 `main`。不要选旧的 `v0.3.0-rc.1` 或 `macos-v0.3.0-rc.1` 标签；这些不可变快照不包含本次兼容清单。GitHub 市场地址不要附加 `#分支名`；部分腾讯客户端会把它原样传给 Git，导致克隆失败。专用市场清单使用明确的 `ref` 和插件子目录，避免依赖这种 URL 解析行为。

授权及下载期间保持当前 WorkBuddy 对话运行。完整数据保存在 `<项目目录>/downloads/factors/<任务编号>/`，只有本机状态为 `ready` 且字节数和 SHA-256 校验成功才算下载完成。实际可见的因子与研究资料以当前账号和服务器权限为准。

## 兼容入口

- 仓库根目录提供 `.codebuddy-plugin/marketplace.json` 与 `.claude-plugin/marketplace.json`，兼容腾讯原生及 Claude 风格的市场发现入口。
- 两份市场清单引用同一组 `plugins/` 目录。插件中的 `.codebuddy-plugin/plugin.json`、`.claude-plugin/plugin.json` 显式引用 `.mcp.workbuddy.json`。
- 远程市场清单 `docs/workbuddy-marketplace.json` 使用 `git-subdir` 源，按所选系统取完整插件；没有把本地相对目录当成 HTTP 文件地址。
- WorkBuddy 配置使用其兼容的 `${CLAUDE_PLUGIN_ROOT}` 变量定位程序，避免把研究项目的工作目录当作插件目录。Windows 直接运行随包 EXE；Mac 使用随包的 shell 启动器与解释器。
- Codex 的 `.agents/plugins/marketplace.json`、`.codex-plugin/plugin.json` 和原有 MCP 启动配置保持独立。不要同时安装两个系统的插件，也不要为了安装兼容层删除现有账号连接。

本次验证涵盖清单完整性、Windows/macOS 的启动路径和可执行程序离线检查。WorkBuddy 桌面界面的安装、网页登录和真实下载需在目标版本上验收；通过静态校验不代表已经完成这些验收。

参考：[WorkBuddy 插件系统](https://www.codebuddy.cn/docs/workbuddy/Plugins)、[腾讯插件市场规范](https://www.codebuddy.cn/docs/cli/plugin-marketplaces)、[插件配置及变量](https://www.codebuddy.cn/docs/cli/plugins-reference)。

## 维护

在原有客户端和 Skills 中修改业务逻辑。更新插件版本或市场条目后运行：

```sh
python3 scripts/workbuddy/package.py
python3 scripts/workbuddy/package.py --check
python3 -B -m unittest discover -s scripts/workbuddy -p 'test_*.py'
```

兼容 JSON 从现有 Codex 市场和插件身份生成，不手工维护第二套版本号。Mac 打包器也会把兼容文件收入新 ZIP；历史标签与 Release 资产保持原样。

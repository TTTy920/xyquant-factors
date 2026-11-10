# xyquant

在 Codex、WorkBuddy 或 Claude Code 中查询已授权的因子与策略资料，把选定的数据保存到当前项目，并用本地持仓或评分明细做归因。Windows 和 macOS 使用同一份插件。

## 安装与授权

下载开发分支的仓库 ZIP 并解压，或在本地检出 `feature/direct-mcp`。在客户端添加本地插件市场时，选择解压后含有 `.agents/` 和 `plugins/` 的目录，再安装 **xyquant**。支持指定 Git 分支的客户端也可以直接添加该分支。[下载开发分支 ZIP](https://github.com/TTTy920/xyquant-factors/archive/refs/heads/feature/direct-mcp.zip)

在 Codex 桌面端打开「插件」添加市场后安装。在 WorkBuddy 打开「插件市场」，添加市场来源并安装。Claude Code 可在 `/plugin` 中添加本地仓库目录并安装 `xyquant@xyquant`。解压的插件包本身包含清单、连接配置和全部 Skill。

第一次查询时，按客户端的连接提示打开 XYQuant 网站，使用自己的账号登录并授权，回到客户端继续提问。因子、日期和研究资料的可见范围由该账号的授权决定。运行本地下载或归因脚本需要 Python 3.9 或以上。

## 使用示例

- “有哪些成交量相关因子？我可以查哪些日期？”
- “把 ROE_TTM 在 2026 年 9 月、所有已授权股票的数据下载到当前项目。”
- “科技策略按什么原理选股？这只股票在指定月份的策略因子值是多少？”
- “用我的月末持仓、个股暴露和因子收益做 Barra 风格归因。”
- “按本地的评分明细和分组名额，解释这只股票为什么入选。”

因子数据通过远程 MCP 查询和导出。下载成功后，客户端会给出经过校验的文件路径。也可以登录[因子网站](https://47.103.215.251/factors/)，在「最近导出」下载同一份文件。Barra 和策略选股归因所需的本地输入、运行方法和结果口径见各自的 Skill。

## 维护

每个 Skill 放在 `plugins/xyquant/skills/<名称>/` 中。插件入口在 `plugins/xyquant/`，各客户端的市场清单位于仓库根目录。

```sh
python3 -m unittest discover -s tests
python3 scripts/package.py
```

安装包和 SHA-256 校验文件输出到 `dist/`。

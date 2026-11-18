# xyquant

在 Codex、WorkBuddy 或 Claude Code 中查询已授权的因子与策略资料，把选定的数据保存到当前项目，并在本地完成组合风格与行业收益归因。Windows、macOS 和 Linux 共用同一份插件包。

## 安装与授权

下载开发分支的仓库 ZIP 并解压，或在本地检出 `feature/direct-mcp`。在客户端添加本地插件市场时，选择解压后含有 `.agents/` 和 `plugins/` 的目录，再安装 **xyquant**。支持指定 Git 分支的客户端也可以直接添加该分支。[下载开发分支 ZIP](https://github.com/TTTy920/xyquant-factors/archive/refs/heads/feature/direct-mcp.zip)

在 Codex 桌面端打开「插件」添加市场后安装。在 WorkBuddy 打开「插件市场」，添加市场来源并安装。Claude Code 可在 `/plugin` 中添加本地仓库目录并安装 `xyquant@xyquant`。解压的插件包本身包含清单、连接配置和全部 Skill。

第一次查询时，按客户端的连接提示打开 XYQuant 网站，使用自己的账号登录并授权，回到客户端继续提问。因子、日期和研究资料的可见范围由该账号的授权决定。运行本地下载脚本需要 Python 3.9 或以上。

插件已内置 [xyquant-login](plugins/xyquant/skills/xyquant-login/SKILL.md)。在使用数据的客户端说“帮我连接 XYQuant”即可；已有授权时可以直接查询。WorkBuddy 桌面用户若遇到 `invalid_target`，可说“用本地连接器授权 XYQuant”。这条可选路径需要 Python 3.9+ 和带 npm/npx 的 Node.js 20.18.1+，首次使用按提示把生成的 `xyquant-local` 配置添加到 WorkBuddy。

组合归因需要 Python 3.10 或以上，先安装 `plugins/xyquant/skills/portfolio-attribution/requirements.txt` 中的依赖。

## 网站入口

[研究网站](https://47.103.215.251/factors/preview/) · [个人空间](https://47.103.215.251/factors/preview/#personal-space) · [因子下载与最近导出](https://47.103.215.251/factors/preview/#factor-download) · [注册账号](https://47.103.215.251/factors/preview/register.html) · [接入说明](https://47.103.215.251/factors/preview/#plugin)

OAuth 连接通过客户端原生入口授权和管理；个人空间用于账号资料、API Key 与因子下载。

## 使用示例

- “有哪些成交量相关因子？我可以查哪些日期？”
- “把 ROE_TTM 在 2026 年 9 月、所有已授权股票的数据下载到当前项目。”
- “科技策略按什么原理选股？这只股票在指定月份的策略因子值是多少？”
- “用我的月末持仓做组合收益归因，基准选沪深300。”

因子数据通过远程 MCP 查询和导出。通用因子下载沿用当前 MCP 授权：`get_export(include_download=true)` 返回仅适用于该文件、五分钟内有效的下载凭据，本地脚本保存并校验文件，无需再次网页登录。下载成功后，客户端会给出经过校验的文件路径。也可以登录[个人空间的因子下载](https://47.103.215.251/factors/preview/#factor-download)，在「最近导出」下载同一份文件；网页会话独立管理。

组合收益归因使用 `portfolio-attribution`：支持客户自带持仓，或选择300增强、xgb500增强、xgb1000增强、xgb2000增强四个预置策略。原始数据通过 `query_stock_data(dataset="attribution_bundle")` 下载到本地项目，按版本与逐文件校验复用缓存；插件包不附数据。收益重建、全市场横截面WLS因子收益估计、Barra及Brinson均沿用原版计算，不读取现成因子收益率。可选原包四个指数基准，其他基准由客户提供权重。用户文件留在本地；数据覆盖与原始缺口以目录及运行校验为准。

Barra数据支持直接数值预览和单因子导出：在 `query_barra_data` 中设置 `factors=["Size"]`，加 `preview=true` 可查看少量数值，提供 `request_id` 则创建完整文件导出。不传 `factors` 时保留原来的整组数据格式，组合归因计算不变。

当前插件加载 `xyquant-login`、`factor-download` 和 `portfolio-attribution`。开发阶段暂时停用 `barra-attribution` 和 `strategy-attribution`。

通用因子保留目录、预览和导出四个工具，`get_export` 增加可选的文件下载凭据；策略与团队介绍统一使用 `query_strategy_research`，市场信号继续使用 `query_market_signals`。科技策略知识及策略个股因子值接口开发阶段暂不开放。

## 维护

启用的 Skill 放在 `plugins/xyquant/skills/<名称>/` 中。暂时停用的 Skill 和本次替换的旧版归因 Skill 完整保存在仓库根目录的 `disabled-skills/`，不进入插件包；以后移回原位置即可恢复。插件入口在 `plugins/xyquant/`，各客户端的市场清单位于仓库根目录。

```sh
python3 -m unittest discover -s tests
python3 scripts/package.py
```

安装包和 SHA-256 校验文件输出到 `dist/`。

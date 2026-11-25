# xyquant

在 Codex、WorkBuddy 或 Claude Code 中查询已授权的因子与策略资料，把选定的数据保存到当前项目，并在本地完成个股持有诊断、因子RankIC测试、多因子合成、组合风格与行业收益归因。Windows、macOS 和 Linux 共用同一份插件包。

## 安装与授权

下载开发分支的仓库 ZIP 并解压，或在本地检出 `feature/direct-mcp`。在客户端添加本地插件市场时，选择解压后含有 `.agents/` 和 `plugins/` 的目录，再安装 **xyquant**。支持指定 Git 分支的客户端也可以直接添加该分支。[下载开发分支 ZIP](https://github.com/TTTy920/xyquant-factors/archive/refs/heads/feature/direct-mcp.zip)

在 Codex 桌面端打开「插件」添加市场后安装。在 WorkBuddy 打开「插件市场」，添加市场来源并安装。Claude Code 可在 `/plugin` 中添加本地仓库目录并安装 `xyquant@xyquant`。解压的插件包本身包含清单、连接配置和全部 Skill。

第一次查询时，按客户端的连接提示打开 XYQuant 网站，使用自己的账号登录并授权，回到客户端继续提问。因子、日期和研究资料的可见范围由该账号的授权决定。运行本地下载脚本需要 Python 3.9 或以上。

插件已内置 [xyquant-login](plugins/xyquant/skills/xyquant-login/SKILL.md)。在使用数据的客户端说“帮我连接 XYQuant”即可；已有授权时可以直接查询。WorkBuddy 桌面用户若遇到 `invalid_target`，可说“用本地连接器授权 XYQuant”。这条可选路径需要 Python 3.9+ 和带 npm/npx 的 Node.js 20.18.1+，首次使用按提示把生成的 `xyquant-local` 配置添加到 WorkBuddy。

四个业务 Skill 需要 Python 3.10 或以上，各自依赖位于 `plugins/xyquant/skills/<名称>/requirements.txt`。

## 网站入口

[研究网站](https://47.103.215.251/factors/preview/) · [个人空间](https://47.103.215.251/factors/preview/#personal-space) · [因子下载与最近导出](https://47.103.215.251/factors/preview/#factor-download) · [注册账号](https://47.103.215.251/factors/preview/register.html) · [接入说明](https://47.103.215.251/factors/preview/#plugin)

OAuth 连接通过客户端原生入口授权和管理；个人空间用于账号资料、API Key 与因子下载。

## 使用示例

- “有哪些成交量相关因子？我可以查哪些日期？”
- “把 ROE_TTM 在 2026 年 9 月、所有已授权股票的数据下载到当前项目。”
- “科技策略按什么原理选股？这只股票在指定月份的策略因子值是多少？”
- “用 XYQuant 诊断一下 600519.SH，看看四因子排名和策略持仓变化。”
- “用我的月末持仓做组合收益归因，基准选沪深300。”
- “用我的因子文件做2025年3月的周频RankIC测试，股票池为全A。”
- “把 ROE_TTM 和 EP_TTM 等权合成，查看指定日期的综合分数。”

因子数据通过远程 MCP 查询和导出。通用因子下载沿用当前 MCP 授权：`get_export(include_download=true)` 返回仅适用于该文件、五分钟内有效的下载凭据，本地脚本保存并校验文件，无需再次网页登录。下载成功后，客户端会给出经过校验的文件路径。也可以登录[个人空间的因子下载](https://47.103.215.251/factors/preview/#factor-download)，在「最近导出」下载同一份文件；网页会话独立管理。

组合收益归因使用 `portfolio-attribution`：支持客户自带持仓，或选择300增强、xgb500增强、xgb1000增强、xgb2000增强四个预置策略。策略和基准权重、快照通过 `query_stock_data(dataset="attribution_bundle")` 取得，本地缓存按服务端声明的有效期校验。市场收益、市值、Barra暴露、行业和日历从项目基础数据目录读取。Barra 使用期初十个风格完整、流通市值为正的股票构建回归样本；Brinson 分析行业配置和选择贡献。计算和报告交付在本地完成。

RankIC测试使用 `factor-rankic`：因子可以来自用户文件或 MCP 授权导出；复权价格、交易日历和历史在市状态来自项目基础数据目录。按日、周、月、季频计算，在本地交付 `report.md`、`rankic.xlsx` 和 `metadata.json`。

个股持有诊断使用 `stock-holding-diagnosis`：通过现有 MCP 获取 ROE_TTM、EP_TTM、CloseTrend_240D_Reg、Amount_20D_Avg 四个因子的同日授权截面，以及四个预置策略的持仓和快照。在本地计算单只 A 股的因子排名与两期持仓变化，交付判断依据、因子排名、策略持仓三部分。

多因子合成使用 `factor-synthesis`：用户指定2–10个因子，按因子方向计算同日共同样本的分位分数，支持等权、手动权重、历史RankIC均值或RankICIR权重。在本地交付 `scores.csv`、`report.md` 和 `metadata.json`。历史权重需要可比且覆盖足够历史期数的RankIC结果。

期货会员持仓策略分析使用 [futures-member-position-analysis](plugins/xyquant/skills/futures-member-position-analysis/SKILL.md)：原样收录净值分析、拥挤度指标1与历史分位、会员持仓相关性热力图的说明和代码。随附的 `compute_futures_crowding`、`get_futures_member_heatmap` 工具尚未接入 XYQuant MCP。

A 类网下打新测算使用 [ipo-return-calculator](plugins/xyquant/skills/ipo-return-calculator/SKILL.md)：通过 `query_ipo_data` 获取 IPO 发行工作簿，在本地按账户规模、年份和板块测算打新收益，支持月度、年度与个股明细。用户也可直接提供本地 Excel。

## 基础数据与本地目录

四个业务 Skill 将缓存、用户输入和结果放在项目的 `xyquant-data/` 内。基础数据严格读取 `xyquant-data/inputs/datasets/<数据集>/` 下的文件及对应 `manifest.json`：`adjusted-prices`、`trading-calendar`、`listed-history`、`attribution-market`、`barra-exposures`、`industry-classification`。具体计算按需要读取其中的数据集。

RankIC 基础数据通过 `query_rankic_data` 下载；归因基础数据通过 `query_stock_data` 分成市场、暴露、日历、行业四包，持仓包独立提供。公共下载器校验后直接放入项目基础数据目录，详细流程见[基础数据下载](plugins/xyquant/references/datasets.md)。服务器按需生成每包、共享固定一小时，到期清理；客户端下载临时文件在完成后清理。因子查询、导出和既有持仓取数继续使用现有 MCP。数据文件、用户输入和授权凭据不进入插件包。

通用因子下载保存在 `downloads/factors/`；四个业务 Skill 使用自己的 `xyquant-data/mcp-cache/` 缓存。客户端应按对应 Skill 的接收命令保存下载任务。

当前插件加载 `xyquant-login`、`factor-download`、`portfolio-attribution`、`factor-rankic`、`stock-holding-diagnosis`、`factor-synthesis`、`futures-member-position-analysis` 和 `ipo-return-calculator`。

通用因子保留目录、预览和导出四个工具，`get_export` 增加可选的文件下载凭据；策略与团队介绍统一使用 `query_strategy_research`，市场信号继续使用 `query_market_signals`。科技策略知识及策略个股因子值接口开发阶段暂不开放。

## 维护

启用的 Skill 放在 `plugins/xyquant/skills/<名称>/` 中。公共数据模块位于插件根目录，业务下载辅助代码位于 `plugins/xyquant/shared/`。归档 Skill 位于仓库根目录的 `disabled-skills/`，不进入插件包；本次替换前的三个完整 Skill 位于 `disabled-skills/czx-pre-replacement-20261125/`。

```sh
python3 -m pip install -r plugins/xyquant/skills/factor-rankic/requirements.txt -r plugins/xyquant/skills/factor-synthesis/requirements.txt -r plugins/xyquant/skills/portfolio-attribution/requirements.txt -r plugins/xyquant/skills/stock-holding-diagnosis/requirements.txt
python3 -B -m unittest discover -s tests
python3 -B -m unittest discover -s plugins/xyquant/skills/portfolio-attribution/tests
python3 scripts/package.py
```

安装包和 SHA-256 校验文件输出到 `dist/`。

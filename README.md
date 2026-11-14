# xyquant

在 Codex、WorkBuddy 或 Claude Code 中查询已授权的因子与策略资料，把选定的数据保存到当前项目，并在本地完成组合风格与行业收益归因。Windows 和 macOS 使用同一份插件。

## 安装与授权

下载开发分支的仓库 ZIP 并解压，或在本地检出 `feature/direct-mcp`。在客户端添加本地插件市场时，选择解压后含有 `.agents/` 和 `plugins/` 的目录，再安装 **xyquant**。支持指定 Git 分支的客户端也可以直接添加该分支。[下载开发分支 ZIP](https://github.com/TTTy920/xyquant-factors/archive/refs/heads/feature/direct-mcp.zip)

在 Codex 桌面端打开「插件」添加市场后安装。在 WorkBuddy 打开「插件市场」，添加市场来源并安装。Claude Code 可在 `/plugin` 中添加本地仓库目录并安装 `xyquant@xyquant`。解压的插件包本身包含清单、连接配置和全部 Skill。

第一次查询时，按客户端的连接提示打开 XYQuant 网站，使用自己的账号登录并授权，回到客户端继续提问。因子、日期和研究资料的可见范围由该账号的授权决定。运行本地下载脚本需要 Python 3.9 或以上。

组合归因需要 Python 3.10 或以上，先安装 `plugins/xyquant/skills/portfolio-attribution/requirements.txt` 中的依赖。

## 使用示例

- “有哪些成交量相关因子？我可以查哪些日期？”
- “把 ROE_TTM 在 2026 年 9 月、所有已授权股票的数据下载到当前项目。”
- “科技策略按什么原理选股？这只股票在指定月份的策略因子值是多少？”
- “用我的月末持仓和基准成分权重做组合收益归因；我有实际净值时一并提供。”

因子数据通过远程 MCP 查询和导出。通用因子下载沿用当前 MCP 授权：`get_export(include_download=true)` 返回仅适用于该文件、五分钟内有效的下载凭据，本地脚本保存并校验文件，无需再次网页登录。下载成功后，客户端会给出经过校验的文件路径。也可以登录[因子网站](https://47.103.215.251/factors/)，在「最近导出」下载同一份文件；网页会话独立管理。

组合收益归因使用 `portfolio-attribution`：客户提供组合持仓及权重、所选基准的历史成分权重，实际净值可选；未提供净值时按原有持仓算法重建收益。风格暴露和现成因子收益率通过 `query_barra_data` 获取，股票日收益和历史行业通过 `query_stock_data` 获取，交易日历由日收益率日期提取。持仓及净值留在本地，归因在本地执行，不重新计算因子收益率。

Barra数据支持直接数值预览和单因子导出：在 `query_barra_data` 中设置 `factors=["Size"]`，加 `preview=true` 可查看少量数值，提供 `request_id` 则创建完整文件导出。不传 `factors` 时保留原来的整组数据格式，组合归因计算不变。

开发阶段暂时停用 `barra-attribution` 和 `strategy-attribution`，当前插件加载 `factor-download` 和 `portfolio-attribution`。

通用因子保留目录、预览和导出四个工具，`get_export` 增加可选的文件下载凭据；策略与团队介绍统一使用 `query_strategy_research`，市场信号继续使用 `query_market_signals`。科技策略知识及策略个股因子值接口开发阶段暂不开放。

## 维护

启用的 Skill 放在 `plugins/xyquant/skills/<名称>/` 中。暂时停用的两个 Skill 完整保存在仓库根目录的 `disabled-skills/`，不进入插件包；以后移回原位置即可恢复。插件入口在 `plugins/xyquant/`，各客户端的市场清单位于仓库根目录。

```sh
python3 -m unittest discover -s tests
python3 scripts/package.py
```

安装包和 SHA-256 校验文件输出到 `dist/`。

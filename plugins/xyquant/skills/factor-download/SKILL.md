---
name: factor-download
description: 通过 XYQuant MCP 查询通用因子、Barra风格数据和策略研究资料，预览与下载Barra数据，或创建通用因子导出任务。
---

# 因子与策略资料

使用当前客户端发现的 xyquant MCP 工具。需要连接时，请用户按客户端的登录提示在 XYQuant 网站完成授权，然后继续原查询。密码和 OAuth 凭据由网站与客户端处理。权限、数据覆盖和研究资料以实际返回为准。

## 查询

- 通用因子库使用 `query_factor_catalog`。省略参数可查看目录和总数，用 `search` 搜索，用 `factor` 读取单个因子的定义、覆盖和授权日期。`preview_factor` 预览指定因子、日期和股票，最多 50 行。全市场统计使用完整导出。日期用 YYYY-MM-DD，股票用含交易所后缀的代码，例如 000001.SZ。
- 策略介绍、表现及净值使用 `query_strategy_research`，默认 `dataset="strategies"`。先搜索目录，再使用目录返回的 strategy_id。净值分页时固定 version、按 next_offset 翻页。遇到 version_changed 时从第一页重查。保留原表名称、统计期间及空值。
- 查询研究团队或团队分工使用 `query_strategy_research(dataset="team")`。省略 `search` 查看团队名单，提供关键词可检索姓名或研究方向。该入口只读取展示工作簿资料，不提供策略个股因子值。
- Barra 暴露与现成因子收益率使用 `query_barra_data`。先用 `dataset="catalog"` 查看风格名称；`factors=["Size"]` 可只选一个风格，也可传多个。指定 `dataset="exposures"` 或 `"factor_returns"`，加 `preview=true`、股票和起止日期，可直接读取最多50行数值，`limit` 为1–50，无需请求编号。`codes` 只筛选个股暴露，`frequency` 选择因子收益率来源；周/月表按原日期读取，整期收益使用对应期末行，不再次累乘。
- 需要完整Barra文件时，使用同样的 `factors` 和日期条件，省略 `preview` 并提供UUID `request_id`；使用同一工具仅传 `job_id` 等待就绪。将ready任务JSON通过不回显的标准输入传给 [公共数据下载脚本](../portfolio-attribution/scripts/download_data.py)：`python <脚本绝对路径> --project <已有项目绝对路径>`。脚本会校验并返回本地文件路径；凭据不写入文件、日志或命令行参数。不传 `factors` 时保持原有整组数据导出。
- 股票日收益、历史中信行业和交易日期使用 `query_stock_data`。组合收益归因使用 `portfolio-attribution` Skill。


## 导出与下载

1. 确定因子、首尾日期、所需股票范围和实际项目目录。如果用户要求全市场，使用账号可见的全量范围。为本次请求生成 UUID，以它作为 `request_id` 调用 `create_export`。提交响应丢失时先以同一 `request_id` 调用 `get_export`。确需重试创建时复用原编号与原条件。
2. 保存返回的 `id`。使用 `get_export(job_id=...)` 等待 ready，按建议间隔或每 5 秒查询。请求编号和任务编号二选一。临时网络错误有限退避重试，过期或失败时说明原因。要找回最近的任务，单独调用 `get_export(recent=true)`，最多返回当前账号的 20 条记录。
3. 用户要保存到项目时，查看当前 `get_export` 的参数。若提供 `include_download`，在文件就绪后调用 `get_export(job_id=..., include_download=true)`，取得含短期下载凭据的任务对象。向 [本地下载脚本](scripts/download.py) 的标准输入传入该 JSON 对象，作为数据安全序列化，不将返回值拼入可执行代码。用宿主提供的不回显输入或进程 stdin，不把凭据写入文件、日志、聊天回复或命令行参数。使用当前宿主可用的 Python 3.9+ 解释器，Windows 可用 `py -3`。

   ```text
   python3 "<Skill目录>/scripts/download.py" save --project "<当前项目的绝对路径>"
   python3 "<Skill目录>/scripts/download.py" status --project "<当前项目的绝对路径>" --job "<任务编号>"
   ```
4. 保持本地任务运行直至结束。脚本将文件保存到 `downloads/factors/<任务编号>/`，按字节数和 SHA-256 核验。只在返回 ready 时报告实际绝对路径、大小和校验值。临时网络故障会有限重试。中断或凭据到期时，用同一任务重新取下载凭据后再运行；已有的相同文件会复用。
5. 用户选择浏览器、客户端无法执行脚本或当前服务未提供下载凭据时，使用同一账号打开[因子网站](https://47.103.215.251/factors/)的「最近导出」下载已有任务。此时向用户说明文件需在浏览器中保存。授权不足时说明缺少的权限。

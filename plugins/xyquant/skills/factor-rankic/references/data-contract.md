# RankIC 输入契约（schema_version 1）

## 接入状态与信任边界

本 Skill 复用既有通用因子目录和授权导出。**MCP 因子值 + 本地市场基础数据**可计算 RankIC。服务器复权定义、完整交易日历和历史在市全集尚未验收，不能宣称全部数据可由服务器提供。

配置是本地数据声明，不是授权凭据，也不是密码学证明。`verified` 必须对应提供方文档、可复核样本及下载清单；模型不能为通过校验而自我认证。客户端保持 OAuth 权限及下载票据隔离。现有下载脚本白名单不放宽；不猜测服务器不存在的导出路径。

`origin=xyquant_server` 在本版**固定拒绝**，即使本地文件哈希正确、调用方把 `verified` 全部填为 true 也不能启用。正式验收后需要维护者交付已审的服务器适配版本。以下是待完成的验收资料清单，不是本版可用的启用开关：
- 明确的 `verification_reference`，以及 `verified.authorization/adjusted_close/calendar=true`；全 A 还需 `historical_universe=true`。
- 每份 CSV/Parquet 源文件与授权下载清单一致的 SHA-256。程序重新校验文件哈希；缺失、不符或未核实即拒绝。
- 唯一 `snapshot_id`、因子唯一 `factor_name`、统一的复权快照标识 `adjustment_id`、显式 `authorized_codes`。
- 真实交易日历覆盖观察区间及确认周期末所需日期；下一周期价格不足保持未完成，不伪造。

`origin=local_reference` 是开发/用户明确提供的离线参考，不证明服务器授权、复权语义、历史全集或独立交易日历。所有报告强制携带此限制；它不是绕过服务器授权的降级方式。

## MCP 因子值模式

- 按通用因子目录确认的唯一 ID 创建单因子导出。`rankic_transfer.py` 用原下载流程核验文件大小和 SHA-256，保存到 `xyquant-data/mcp-cache/factors/<任务>/`，另写不含凭据的范围文件。
- 启动 `rankic.py` 时同时传 `--factor`（目录 ID）、`--factor-job` 和 `--factor-sha256`。ID、观测起止日、代码范围必须与导出任务完全一致；全 A 应无显式代码筛选。程序读取单因子 Parquet 或仅含 `<因子ID>.parquet` 的 ZIP；因子表按物理列 `date, stock_cd, alpha` 读取，兼容导出文件中的 pandas 索引声明。范围不匹配、文件校验失败或缺少范围记录均不回退到旧因子文件。
- 配置仍由本地声明 `price`、`calendar`、`authorized_codes`、全 A 所需的 `listed` 及其标识。`parameters.factor_name` 由 `--factor` 覆盖；`data.factor_name` 和 `data.factor` 在该模式下可省略且不读取。本地声明的股票范围不因选用 MCP 而扩大。报告中 `data.origin=local_reference` 表示基础市场数据，`data.factor_export` 单独记录 MCP 因子身份、日期、文件 SHA-256 和筛选范围。

## 表结构

| 角色 | 必需列 | 含义 |
|---|---|---|
| factor | date, stock_code, value | 单个唯一因子；数值可缺失，由计算期剔除 |
| price | date, stock_code, adj_close | 同一复权快照的收盘价，不是收益率 |
| calendar | date | 真实交易日，仅一行一日；不是自然工作日猜测 |
| listed | date, stock_code, listed | 全 A 必需；历史 T 的 1=在市、0=不在市；未知不能当 0 |

### 正式入口的唯一基础数据源

`data.origin` 为 `local_reference`，配置仍放 `xyquant-data/inputs/rankic/`。`rankic.py` 只认 `xyquant-data/inputs/datasets/{adjusted-prices/prices.parquet,trading-calendar/calendar.parquet,listed-history/listed.parquet}`，并校验对应 `manifest.json` 的契约、覆盖和文件 SHA-256。用自定义股票池时不读在市表。配置里手填的 `price/calendar/listed` 路径、覆盖或旧版目录选择均不作为输入。目录缺失时不回退其他市场源。

市场数据的起止和截止日取自扁平清单 `coverage.start/end`。报告 `data.catalog_inputs` 留存实际版本、清单和文件 SHA-256、已知覆盖缺口；价格口径、由价格轴派生的日历等限制仍需披露。本地校验不等于 MCP 市场数据正式开放。

- 日期是 YYYY-MM-DD；Parquet 也可为无时区午夜日期类型，不能输入日内时间。
- 代码必须为字符串，保留前导零。接受 `000001.SZ`、`SZ000001`、六位代码；自动补交易所时 0/3→SZ、6/9→SH、4/8/92→BJ。建议始终给后缀。自定义池仅 A 股；全 A 同时依据历史上市标记及 A 股代码范围，排除 B 股、指数等非股票代码。北交所普通股票范围按 `43`（历史）、`83/87/88`、`92` 号段识别，不把所有 4/8 开头代码视为股票；号段校验不代替历史在市与授权范围验证，也不自动映射新旧证券代码。号段依据见[北交所证券代码编制指引](https://www.bse.cn/jygl_list/200021626.html)及[历史代码对照表](https://www.bse.cn/service/code_mapping.html)。
- 每个使用范围内的日期/代码键必须唯一，重复即报错，即使数值相同也不静默合并。日历不允许重复。
- 自定义池是静态代码集合，规范化去重后再与授权范围、有效数据相交；不要求完整历史全集。全 A 的历史成员必须完整、包含退市历史；列缺失/NaN 是覆盖缺口，不能宣称为完整市场。
- 缺失/非有限因子、非有限/非正买卖价格及非有限计算收益均不前填，按互斥优先顺序计数：授权范围外→因子无效→价格无效。未完成期的有效数/剔除数未计算，不解读为全数缺失。
- `complete_from` / `complete_through` 声明日历在自然日期上的完整范围。周频必须确认到周日，月/季度须确认到自然月/季度末；只有截至周三的行情轴不能证明周三是周末。提供未来交易安排可确认卖出日，未产生的价格由 `price_through` 区分。

CSV 按十万行批次读取；Parquet 先利用所需日期及 row-group 的日期最小/最大统计跳过无关组，再按十万行批次读取和过滤。统计缺失、截断或不能安全解释时保守读取，不猜测可跳过的组；未读取的范围不做行级校验。选定日期缓存到任务临时 SQLite 后逐期查询，不一次载入整个面板。SHA-256 仍完整流式读取源文件，跳过 row-group 减少的是解码和数据转换，不免除完整性校验。`snapshot_id` / `adjustment_id` 只能是 1–160 字符的非敏感标识（首字符为英文或数字，其余为英文、数字、点、下划线、连字符）；拒绝 URL 和自由文本，不能拿票据当标识。缓存包含数值，只存在本地临时目录，正常退出/异常处理时清理。源文件只读；运行结束再次核对大小及修改时间。未使用日期的非日历重复键不影响本次计算范围。

## 配置示例

以下是本地因子文件配置示例。正式入口的复权价和日历由扁平数据目录决定，不写在运行配置内：

```json
{
  "parameters": {
    "factor_name": "ROE_TTM",
    "start": "2025-01-01",
    "end": "2025-12-31",
    "frequency": "quarterly",
    "pool": "custom",
    "codes": ["000001.SZ", "000002.SZ", "600000.SH"],
    "min_stocks": 20
  },
  "data": {
    "origin": "local_reference",
    "factor_name": "ROE_TTM",
    "authorized_codes": ["000001.SZ", "000002.SZ", "600000.SH"],
    "factor": {"path": "factor.parquet", "format": "parquet"}
  }
}
```

三只股票不足默认 20，示例会得到 no_result，不能偷偷改为 3。用户明确要求小池测试才配置较低阈值。全 A 则 `pool=all_a` 并删除 `codes`，程序从扁平目录读取历史在市。频率只能是 daily/weekly/monthly/quarterly。

可显式映射字段，例如 factor 增加 `"columns":{"date":"日期","stock_code":"代码","value":"因子值"}`；映射必须一一对应，不猜列名。路径相对配置文件所在目录；也接受 `xyquant-data/inputs/rankic/` 内用户明确授权的绝对本地路径。配置/报告不能存票据、cookie、密钥或下载 URL。计算输入及配置进入 `xyquant-data/inputs/rankic/`，报告进入 `xyquant-data/results/rankic/`，均不打包。

## 本地 HDF5 参考适配

只支持显式标记的本地参考，不接收声称已验收的服务器 HDF5。数据集：`Data` 为 dates×securities 的数值矩阵、`DateTime` 为 Unix 秒、`ID` 为 UTF-8 字符串；日历仅用 DateTime。键、轴重复、矩阵形状和时区均验证，不读取/执行序列化元数据或 `_TableInfo.h5`。

```json
{
  "path": "user-provided-adjusted-close.hdf5",
  "format": "hdf5",
  "date_encoding": "unix_seconds",
  "timezone": "UTC+08:00",
  "adjustment": "adjusted_close",
  "price_through": "2026-11-06"
}
```

先读取日期/代码轴，每次只读取一个所需日期行，按规范化代码索引对齐，绝不按不同文件的列位置相乘。默认拒绝非标准代码轴；本地文件混有非股票标签时，只有显式设置 `ignore_non_stock_labels=true` 才排除这些列，并在元数据记录数量及报告警告，不改写原文件。报告记录轴 SHA-256、实际读取行序列的 SHA-256、文件大小/mtime；这不是整个 HDF5 文件的哈希。可选提供 sha256 时才额外流式校验全文件。所需 h5py 不存在即明确失败，不改用别的价格口径。

2026-11-17 的授权本地结构核对发现：复权收盘/流通市值/日收益的 Data 为 6504×6173，日期 2000-01-04 至 2026-11-06；是否在市为 5304×5922，日期 2005-01-04 至 2026-11-06；日期为 UTC 前一天 16:00，即 UTC+08:00 午夜。股票轴顺序不同，代码/日期覆盖也不同。这些仅是本地存储证据，不证明复权算法、历史全集或官方日历完整性。对数流通市值只有 30 行，不能假定与其他文件同区间。开发目录不属于客户路径。

## 结果解释

`success` 全部已生成的观察截面有效且没有已发现的历史股票覆盖未知；`partial_success` 有有效截面同时含无效/未完成截面或历史在市覆盖未知（逐期 `unknown_universe`，不把未知当未上市）；`no_result` 无有效截面；输入契约或读取失败为 `error`。尾部尚未关闭的自然周期不是观察截面；报告应说明观察区间和日历范围，不能制造一个“截至今天”的周期末。

逐期保留 T、实际卖出日、有效样本数、状态、RankIC、互斥剔除数和原因。均值、ddof=1 标准差、未年化 ICIR、严格大于 0 的比例仅来自有效期；不足两期标准差为空，标准差为零时 ICIR 为空。同秩/反秩精确记为 ±1；完全相同的期间数值标准差精确记为 0，不把舍入误差当波动，也不抹去真实的小差异。JSON 不含 NaN/Infinity、原始因子面板、文件路径或凭据。

三个报告文件全部在同一文件系统的临时目录完成后，才整体发布到 UUID 结果目录；可处理的生成/发布异常清理临时产物，不留下标为成功的半成品任务。Excel 汇总公式通过 `valid_rank_ic` 与 `valid_stock_count` 辅助列按 valid 状态筛选；相关性辅助列还要求 RankIC 为数值，空单元格不会被复算成 0。缓存来自 Python，不声称已经独立 Excel/LibreOffice 计算验证。

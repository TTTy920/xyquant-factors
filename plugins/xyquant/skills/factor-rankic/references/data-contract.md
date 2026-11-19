# RankIC 数据包契约（schema_version 1）

## 接入状态

客户工作流是服务器授权取数、本地缓存和计算。基础包由 `query_rankic_data` 提供，因子库原件由 `create_export` / `get_export` 提供，用户因子留在本地。服务器接入不改变下文的计算、数据缺失和交付规则。

服务器包使用 `origin=xyquant_server`、`server_contract=rankic-data-v1` 和 `scope.kind=server_public_data`；授权由 MCP 服务端验证，调用方自填 verified、来源标签或正确哈希不是授权凭证，也不证明数据语义完整。本地参考只用于用户明确选择的离线任务，不能冒充服务器来源或成为服务器失败的自动回退。连接/下载边界见 [download-workflow.md](download-workflow.md)。

## 数据包与任务分离

任务 JSON **只有 parameters 和 data_root**；data_root 相对配置文件，也可为用户明确选择的绝对本地目录。任务不能覆盖 catalog 的来源、授权/参考范围或快照。

```text
<root>/catalog.json
<root>/provenance.json
<root>/calendar.pqt
<root>/market/YYYY.pqt
<root>/factors/<factor-id>/YYYY.pqt
```

一个年度分片一个年份，不允许同一角色重复年份或声明跨年、重复路径。不同角色/因子可按需选择年份，不整体解码多年数据。客户配置实例见 [daily example](../examples/floatcap-daily.json)，由助手在客户项目填写本次实际 `data_root`；清单为该根目录的 `catalog.json`。内部 DataBundle 的低层 manifest 不是第二套客户 CLI 格式。

## catalog 字段

| 字段 | 含义 |
|---|---|
| schema_version / dataset | 整数1 / rankic |
| origin / server_contract | local_reference；服务器包为 xyquant_server / rankic-data-v1 |
| snapshot_id / adjustment_id | 同批来源和统一复权快照的非敏感标识 |
| scope.kind / scope.codes | local_reference 的参考代码范围，或 server_public_data 的服务端公开数据范围；本地字段不是服务器授权证明 |
| calendar | Parquet 文件描述＋complete_from、complete_through |
| market.files | 年度市场文件描述列表 |
| market.adjustment | 必须为 adjusted_close，不接受收益/eligible替代 |
| market.price_through | 价格实际可用到哪一天，不能超过 price_coverage |
| market.price_coverage / listed_coverage | 两种角色各自 rows、first_date、last_date，不混为一个市场覆盖 |
| factors | 每个因子的 id、name、aliases、年度files |
| provenance | 来源说明 JSON 的相对 path、bytes、sha256（有则校验） |
| cache_expires_at / data_quality | 服务器包有效期及复权定义、日历来源、历史在市缺口说明 |
| factor_input / market_snapshot_id | 用户或库内因子适配后的原件 SHA、大小、来源及所用基础市场快照 |

Parquet 文件描述必须含相对 `path`、正整数 `bytes`、小写64位 SHA-256、正整数 `rows`、`first_date`、`last_date`；年度文件另含整数 `year`。日期范围必须匹配年份。日历完整声明可以包含首尾非交易日，实际日期轴必须位于声明内。空角色覆盖 rows=0 时首尾日期为null。

路径必须是包内规范相对路径；拒绝越界、绝对路径、反斜杠、盘符/ADS及符号链接/重解析点。计算对基础数据只读。任务目录的日历、市场表及来源说明优先通过硬链接共用下载文件，跨磁盘或文件系统限制导致链接失败时才复制；用户因子和任务清单独立生成。id/快照标识为1–160字符（首字符英文/数字，其余英文/数字、点、下划线、连字符），不能为 URL、自由文本或票据。名称/别名最多200字符，匹配忽略大小写及首尾空白；跨因子别名冲突报错，不自动取第一项。

清单哈希、参考范围指纹和实际读取的年度文件哈希进入结果元数据，不携带绝对输入路径、原始面板或下载授权对象。参考范围的字段名为 scope，不在报告中伪称已获服务器授权。

## 规范 Parquet 表

所有规范表为 Parquet/ZSTD（扩展名 .pqt 或 .parquet），date 为无时区午夜 timestamp[ns]；stock_code 为保留前导零的字符串/字典字符串。因子与价格 float64，listed 为可空 int8，presence 为非空布尔。

| 表 | 字段 |
|---|---|
| calendar | date |
| factors/<id>/YYYY | date, stock_code, value |
| market/YYYY | date, stock_code, adj_close, listed, price_present, listed_present |

市场表是两个原角色键的**外连接**。例如：

| price_present | listed_present | 含义 |
|---|---|---|
| true | true | 原价格/在市表都含此键，数值仍可能为空 |
| true | false | 只有价格键，在市不是原表中的空值行，而是完全缺键 |
| false | true | 只有在市键，不能为了对齐而丢弃 |

某 role 的 flag=false 时其 value 必须为空；读取该 role 时先投影 flag=true 的行。flag=true 且 listed=null 表示已存在记录但在市未知，不可填0。整个观测日没有任何 listed 原记录仍是数据契约错误，不能被外连接改成“全是未知”进而改变状态。

listed 仅允许1/0/null；因子/价格非有限值保留并在逐期剔除，非正价格同样剔除。不同文件不能依赖行位置对齐，所有键以日期/代码匹配。不能用归因 return、eligible 或当前存活股票列表替代这些字段。

## 周期、股票池和缺失

- 起止日期含首尾，仅限制因子观察 T；不限制卖出日。日频卖出为下一真实交易日。周/月/季 T 为已关闭自然周期末最后交易日，卖出为下一同频自然周期末最后交易日。不能用查询截止日当周期末，不能跳过一个空自然周期。
- `complete_from/complete_through` 声明日历在自然日期上的完整性。周末须确认至周日，月/季须确认到对应自然末日；截至周三的行情轴不能证明周三是周末。未关闭自然周期不制造观察截面。
- 已知日历下未来价格未形成：未完成，不是0；缺少正式服务/必要基础数据：取数预检即停止。
- 收益 = 同快照 adj_close(exit) / adj_close(T) − 1；逐 T 独立 Spearman，平均并列秩。最低有效数默认20，用户明确配置可降至3，不能自动降低。不做去极值、中性化、方向反转、成本或可交易性过滤。
- 全 A 使用授权/参考范围及历史 T 在市，涵盖退市历史。未知在市计入逐期 unknown_universe，不能当未上市、不能缩池掩盖。自定义池为静态代码列表，规范化去重，再与允许范围及有效值相交。
- 自定义池只接受 A 股；代码规范化规则沿用原引擎（优先使用带交易所后缀）。全 A 排除 B 股、指数等；北交所普通股按43/83/87/88/92号段，号段校验不证明历史上市，也不自动映射新旧代码。
- 重复日期/股票键报错，即使数值相同。缺失/非有限因子、非有限/非正价格或计算收益不前填；互斥计数优先级为范围外→因子无效→价格无效。未完成期没有执行的计数不解读为全部缺失。

## 有界读取与完整性

Store 先校验清单结构和唯一名称；DataBundle 先读日历、按本来的 period_pairs 求观察/卖出日期，再只打开所需年份。Parquet 用 row-group 日期统计保守跳过无关组，按十万行批次读取，过滤日期后存入任务临时 SQLite。统计缺失/不能安全解释则保守读取；不宣称对未解码日期完成行级校验。

选中完整文件仍流式计算 SHA，不以统计裁剪代替完整性校验；市场文件供两个角色共用校验记录。检查字节数、footer行数、字段类型和实际读取键，记录读前/读后大小、mtime/ctime/文件身份。硬链接增减引起的元信息变化按内容重新核验，共享硬链接文件会重核 SHA；实际内容变化仍拒绝。临时 SQLite 每个任务隔离，正常退出或可处理异常时清理，不共享不同权限/版本/复权数据。inventory 只列声明，data_integrity_checked=false，不代表全部文件通过。

内部保留显式 CSV/HDF 参考适配器用于回归测试及明确的本地价格/在市导出，客户回测 CLI 不直接接入这些模式。HDF适配不反序列化元数据，只认 DateTime/ID/Data、显式 unix_seconds 和UTC/UTC+08:00，轴/形状/代码重复校验；按明确允许列表排除非股票标签并披露数量。补充工具按年有界读取并全量回读核对，生成独立 Parquet 包；普通 Parquet 回测路径不导入 h5py，更不读取原 HDF 目录。

## 服务器数据能力核对（不是来源标签的启用开关）

1. 原生授权下真实可用的因子和基础数据导出契约、权限/覆盖声明、导出原件大小/SHA；不靠服务器磁盘路径猜 MCP 表结构。
2. 唯一因子标识、稳定快照/版本、同一 adjustment_id 的复权算法定义与可复核价格样本，明确是否含分红/拆并股等处理；不能用日收益率拼猜复权价。
3. 完整交易日历：覆盖观察期和确认下一周期终点所需范围，说明完整到哪一天；价格实际可用日与未来交易安排分开。
4. 历史上市/退市成员全集、unknown说明、代码变更处理；不能用今日存活股票列表或归因合格标记替代。
5. 实际 MCP 因子文件长/宽表、字段/类型、ZIP成员/分片布局经过校验；转换保留键、非空值、缺失位置，记录原件与规范化产物哈希。
6. 本地适配及真实服务器→下载→规范化→四频计算→报告样本需要逐环节对照。接入可用不等于复权定义、独立交易所日历或历史全集已完备；供应文件仍有相应待补充信息和缺口，应保留并披露。verified 和 verification_reference 若采用，必须引用外部可审证据，不由模型自我认证。

服务器基础包按需生成，已授权用户共享一小时，访问不续期，到期清理后从当前文件重新打包。下载时核对 `rankic-data.json` 的版本、全文件大小/SHA、目录和有效期；每次新任务先查目录。本地下载状态、因子适配和预检会检查包是否过期。新增年份需在 catalog 登记；重新打包会更新文件大小、SHA 和覆盖，日历完整边界须随数据明确维护。

## 样例和结果

四频样例参数对应源参考包 `market-202001-202610`，实际 `data_root` 由本次授权下载或明确的本地离线数据选择确定。2025年3月FloatCap因子文件按原字节保留，scope代码集合与复权口径标识不变。此前价格/日期轴从本地 `复权收盘价.hdf5` 只读扩充，请求2020-01-01～2026-10-31，实际2020-01-02～2026-10-30、1653个源日期、10,202,316个价格键；非股票标签仅显式排除 `T00018.SH`。历史证据见 [price-extension-check.json](../verification/price-extension-check.json)。价格行包括源空值/非有限/非正值，不等于有效报价数。

本轮从 `是否在市.hdf5` 扩充历史在市：同一实际日期范围、5,922个源代码、9,789,066个在市键。0/1/null严格校验并按年外连接，保留原124,362个在市重叠键的值/空值；价格角色、日历文件和因子文件不变。参考范围中250个代码不在该源股票轴内，仍缺键；源中的在市空值仍是已存在但未知的记录。完整源SHA、年度回读和独立核对见 [listed-extension-check.json](../verification/listed-extension-check.json) 与 [listed-extension-final-audit.json](../verification/listed-extension-final-audit.json)。

**因子仍仅2025年3月，不能因基础数据变长就声称全期间可回测。** 正常回测只读独立Parquet，不依赖原HDF；显式补充工具才读取HDF。日历仍来自价格日期轴，非独立交易所日历；复权算法与历史全集完整性未正式验收。原短样例、旧价格快照、旧布局/备份均保留，不覆盖。2025年3月样例各期348个未知历史在市原样保留；不能把代码轴差等同于逐期全A未知数。provenance保留嵌套旧来源链，旧层的覆盖限制不是当前覆盖声明；当前以顶层catalog和listed_extension为准。

success 表示本次已生成截面有效且未发现历史成员未知；partial_success 有有效截面且伴无效/未完成或成员未知；no_result 无有效截面；契约/读取失败为 error。CLI success/partial_success=0，no_result/error=2。

汇总仅使用有效期，均值、ddof=1标准差、未年化ICIR、严格正值比例和有效股票数；不足两期/零标准差时未定义指标为null。同秩/反秩精确±1，完全相同的期间数值标准差精确0，不抹去真实微小差异。JSON 不含 NaN/Infinity。

三份报告在同文件系统临时目录完成后整体发布到 UUID 结果目录。对话展示参数、实际收益范围、状态、有效/无效/未完成期、均值、ddof=1标准差、未年化ICIR、正值比例、有效股票数和覆盖缺口，同时链接 report.md、rankic.xlsx、metadata.json。不足两期或零标准差时解释未定义指标，少于10个有效期提示短样本。Excel 汇总按 valid 辅助列筛选，数值缓存来自 Python，不能声称独立 Excel/LibreOffice 重算通过。

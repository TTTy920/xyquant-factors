# RankIC 测试 Skill

本目录独立维护因子 RankIC。数据参考组合归因 Skill 的“统一清单＋年度 Parquet 长表”，但保留 RankIC 的复权价格和历史在市口径；正常回测不依赖原插件或原 HDF5 目录。

**目标客户流程：给出因子名称 → Skill 只补问缺失参数 → 服务器授权按需取数 → 本地缓存和计算 → 返回报告。** 基础数据通过 query_rankic_data 取得，用户文件和因子库因子在本地适配，再沿用原计算引擎和报告格式。接入支持不等于复权算法、独立交易所日历和历史全集完整性已验收。本地样例不是服务器失败后的自动替代品。

迁移到新电脑请先看 [迁移说明](MIGRATION.md)。插件只分发代码、说明、示例参数、测试和验证资料，不带本机虚拟环境、基础数据或历史报告。

## 快速运行随附样例

由助手按 [Skill](SKILL.md) 的流程取得数据，读取 `examples/floatcap-*.json` 的原样例参数，在客户项目生成配置并填写实际 `data_root`。明确的离线任务可使用已有本地参考数据。示例中的原相对路径是模板，不代表安装包自带行情。

使用已安装依赖的解释器；Windows 可用 `python` 或虚拟环境解释器替换 `python3`：

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py rankic inventory --data-root <实际data_root>
```

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py rankic run --config <本次配置绝对路径> --project <已有项目绝对路径>
```

`inventory` 仅检查清单声明和名称，不解码全部数据、不宣称数据已完整校验。`run` 实际检查所用分片的完整 SHA、大小、行数、字段、日期/股票唯一键和运行前后快照，产生 Markdown、Excel、JSON 三份报告。

新电脑用 Python 3.10+ 重新创建环境，不跨电脑搬运 `.venv`：

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

首次安装依赖可能需要网络；依赖安装在所选解释器环境内，不使用 pip --user。macOS/Linux 使用 .venv/bin/python。显式本地离线样例不需要服务器账号/密钥或 h5py；服务器取数沿用 MCP 授权，计算阶段在本地完成。

## 目录与数据格式

```text
SKILL.md                              助手交互与执行说明
rankic_entry.py                       受信任启动器（必须使用 Python -I）
scripts/rankic_workflow.py             参数准备与下载数据预检
scripts/rankic_data.py                 基础包下载、校验与缓存检查
scripts/rankic_factor.py               用户或库内因子的本地适配
scripts/rankic.py                      inventory/run CLI
scripts/rankic_store.py                清单、名称、年度分片和完整性
scripts/rankic_inputs.py               按日期流式读取、临时 SQLite
scripts/rankic_engine.py               原有四频 RankIC 计算（未改口径）
scripts/rankic_report.py               Markdown/Excel/JSON
scripts/rankic_download.py             独立授权下载器
scripts/rankic_archive.py              校验后安全提取 ZIP 数据
scripts/prepare_sample.py              本地参考 pqt 一次性规范化工具
scripts/export_reference_prices.py     显式本地 HDF 价格/日期轴导出工具
scripts/export_reference_listed.py     显式本地 HDF 历史在市导出工具
references/data-contract.md            清单/字段/覆盖/验收契约
references/download-workflow.md        下载边界与安全输入要求
<实际data_root>/                       数据位于客户项目，不随插件分发
  catalog.json                        本次任务使用的统一清单
  provenance.json                     原来源链＋价格/在市扩充审计和限制
  calendar.pqt
  market/2020.pqt ... market/2026.pqt
  factors/FloatCap/2025.pqt             原因子文件字节不变
examples/floatcap-*.json                只含参数和相对 data_root
verification/                          基线、导出及重放证据
downloads/factors/<job>/                授权因子原件（位于客户项目）
downloads/rankic-tasks/<UUID>/           无凭据参数请求，不是已授权数据或可运行任务
downloads/rankic/<snapshot>/data/       本次授权下载的基础包
downloads/rankic-inputs/<UUID>/         独立因子任务，基础资料优先硬链接
results/rankic/<UUID>/                  本次三份报告，不覆盖历史报告
```

`.pqt` 是 Apache Parquet，ZSTD 压缩；日期为无时区午夜 `timestamp[ns]`，代码为保留前导零的字符串/字典编码，价格/因子为 float64，listed 为可空 int8。不是 HDF5，也不是每种频率一份数据。

以下为随附参考资料的源快照记录；服务器持续更新后的覆盖以本次目录为准。

| 文件/角色 | 字段 | 实际覆盖 | 行数 |
|---|---|---|---:|
| calendar.pqt | date | 2020-01-02～2026-10-30 | 1,653 |
| market/2020.pqt～2026.pqt | date, stock_code, adj_close, listed, price_present, listed_present | 2020-01-02～2026-10-30 | 10,202,316 |
| factors/FloatCap/2025.pqt | date, stock_code, value | 2025-03-03～2025-03-31 | 129,612 |
| market 的价格角色 | price_present=true | 2020-01-02～2026-10-30 | 10,202,316 |
| market 的在市角色 | listed_present=true | 2020-01-02～2026-10-30 | 9,789,066 |

价格行数表示源日期/股票键数，包含空值、非有限值和非正价格，不是全部有效报价数。市场表按日期/代码外连接；presence 保留原表键存在性。价格有值不能制造一个本来不存在的在市记录，null 在市也不能填0。价格/因子有6,172个代码，在市轴有5,922个；不能按列位置相乘，也不能用轴数量差直接代替逐期未知数。清单分别记录实际覆盖、日历完整边界、价格可用日和在市覆盖。

源资料的导出和迁移证据保存在 verification；历史路径记录当时的来源，不是插件运行依赖。当前数据入口为本次 data_root 下的 catalog.json。MIGRATION-MANIFEST.json 记录原始迁移包的文件清单，供追溯源资料使用，不是当前插件安装包的完整性清单。

## 基础资料的价格与日期来源记录

此前从用户授权的 `D:\HDF5Data\ElementaryFactor\复权收盘价.hdf5` 只读导出请求范围 **2020-01-01～2026-10-31（含）**；实际1653个源日期为2020-01-02～2026-10-30。2026-10-31没有源交易记录，不造日期或价格；日历自然日期声明到10月31日，价格可用日到10月30日。

- 按年有界导出和全量回读，核对价格键、数值、空值和 presence；只显式排除源中的非股票标签 `T00018.SH`。
- 与原样例重叠的499,932条价格记录精确一致；当时原因子文件SHA、scope、历史在市原角色及覆盖不变。
- 仅导出/补充工具需要 h5py 和源 HDF；已生成的数据包可独立复制使用，正常回测不读 HDF。
- 源参考资料为 local_reference；服务器交付使用 xyquant_server 及 rankic-data-v1 契约。日历仍来自价格日期轴，复权算法、独立交易所日历和历史全集完整性未正式验收。

逐年源与产物哈希、行数和完整核对记录见 [price-extension-check.json](verification/price-extension-check.json)。这是上一轮价格快照证据；旧 [catalog-migration-check.json](verification/catalog-migration-check.json)、[catalog-replay-check.json](verification/catalog-replay-check.json) 则是原短样例历史证据。

## 基础资料的历史在市来源记录

从 `D:\HDF5Data\ElementaryFactor\是否在市.hdf5` 只读补充同一请求区间，实际覆盖2020-01-02～2026-10-30，共1,653日、5,922个源代码、9,789,066条在市键，按年合并到新快照。源值仅允许0/1/空值；保留原始空值与缺键，不填0、不前填、不使用当前存活股票列表倒推。

- 原2025年3月124,362条在市键、值和空值精确一致；价格角色10,202,316条逐值不变，日历和因子文件按原字节保留，scope与复权口径标识不变。
- 参考范围中250个代码不在该在市源的股票轴内，保持 `listed_present=false`；源本身还含在市空值，保持 `listed_present=true, listed=null`。两者不能混同，不能用轴差推断任意日期的全A未知数。
- 源快照为 `market-202001-202610`。provenance嵌套保留旧来源链，其中“在市未扩充”是旧快照的历史限制，当前覆盖以顶层catalog和listed_extension为准。
- 因子仍仅2025年3月；基础数据扩充不代表已经具备2020—2026任意区间的流通市值因子回测。

该次全量导出见 [listed-extension-check.json](verification/listed-extension-check.json)，独立逐值审计见 [listed-extension-final-audit.json](verification/listed-extension-final-audit.json)，四频及搬迁重放见 [listed-extension-replay-check.json](verification/listed-extension-replay-check.json)。

## 运行配置与四种频率

data_root 相对配置文件或使用本次返回的绝对路径；下例保留原离线路径，插件任务由助手替换为实际下载或因子适配的 data_root。来源、代码范围、文件路径和哈希集中在 catalog，不在四份任务中重复：

```json
{
  "parameters": {
    "factor_name": "流通市值",
    "start": "2025-03-01",
    "end": "2025-03-31",
    "frequency": "daily",
    "pool": "all_a",
    "min_stocks": 20
  },
  "data_root": "../data/local-reference/market-202001-202610"
}
```

因子可用清单的准确ID、名称或无歧义别名。普通正式请求仍须查真实服务器目录，不能把此清单当服务器目录。客户侧 inline `data` 清单配置不作为第二种 CLI 模式。内部 CSV/HDF 适配器用于回归测试和明确的本地导出，当前客户回测 CLI 使用规范 Parquet 数据包。

| 配置 | 有效期数 | 最后收益终点 |
|---|---:|---|
| floatcap-daily.json | 21 | 2025-04-01 |
| floatcap-weekly.json | 4 | 2025-04-03 |
| floatcap-monthly.json | 1 | 2025-04-30 |
| floatcap-quarterly.json | 1 | 2025-06-30 |

例如季度：

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py rankic run --config <使用季度样例参数的本次配置绝对路径> --project <已有项目绝对路径>
```

日频参考均值约 -0.017783，样本标准差约0.180142，未年化ICIR约 -0.098716。四频完整数值基线仍为 [hdf-reference-baseline.json](verification/hdf-reference-baseline.json)，本轮未改计算口径或基线。旧 [migration-check.json](verification/migration-check.json) 是早期分离 HDF 时的历史证据，不是当前运行配置。

## 正式任务的客户端准备

prepare 只整理请求，不代表数据已就绪。基础包通过 query_rankic_data 授权下载，用户文件或单因子库原件本地适配后才执行计算。完整情况见 [接入状态](references/server-integration-status.md) 和 [取数说明](references/download-workflow.md)。

仅给因子名称的样例是 [server-request-name-only.json](examples/server-request-name-only.json)，只含参数，没有任何因子数据或授权对象。在当前项目准备任务：

```powershell
$task = .\.venv\Scripts\python.exe -I -B -X utf8 .\rankic_entry.py workflow prepare --request .\examples\server-request-name-only.json --project (Get-Location).Path | ConvertFrom-Json; $task
```

返回 `request_id`、`task_file`、已给参数和 `missing_parameters`；此例只需再补日期、频率与股票池。每次写入新的 `downloads/rankic-tasks/<UUID>/request.json`，不覆盖旧请求；输入只允许 parameters 和既有参数字段，不能夹带票据、下载对象或自填授权能力。JSON 文件上限256KiB，自定义输入列表最多10,000项，超限报错而不是静默截断。

在同一个 PowerShell 终端使用返回的任务 ID：

```powershell
.\.venv\Scripts\python.exe -I -B -X utf8 .\rankic_entry.py workflow preflight --task $task.request_id --project (Get-Location).Path
```

预检只读，重新核验任务身份和字段。未提供数据根时返回 blocked；提供 --data-root 后核对当前服务器目录、有效期及准确因子，满足条件才返回 can_run=true。它不自行创建导出，can_create_export 保持 false。**prepare 的退出码0 / parameters_complete 只表示参数准备完成，不是服务器就绪或回测成功；preflight 的 blocked 退出码为2。** 因子名称仍待真实目录消歧，不能把名称整理当准确ID解析。

根目录 `rankic_entry.py` 必须用 Python `-I` 启动，只将本包受信任 scripts 放入导入路径；不从客户项目或下载目录导入同名模块。原 `scripts/rankic.py` 入口保留，但不要简单给它加 `-I` 后假定同目录导入仍可用。workflow 参数准备和预检不会读取凭据、访问网络或创建导出；网络下载由 data/download 入口显式执行。

## 真实服务器接入的边界

- 基础数据和库内因子从服务器按需下载，用户文件留在本地；基础面板不随 Skill 分发。
- 先检查基础数据能力和覆盖，再创建因子导出。缺少本次所需字段、权限或覆盖时停止，不能消耗导出任务后再用本地样例顶替。
- 插件提供 MCP 配置，原生客户端负责 OAuth；不另读登录凭据，不改全局连接设置。工具是否可用以客户端实际列表为准。
- 基础包使用 rankic-data-v1，因子文件按支持的长表格式适配；下载/ZIP 校验不替代数据语义核对。详见 [取数说明](references/download-workflow.md)。
- 缓存必须匹配来源、当前授权、版本、复权及覆盖；本地清单不是授权凭证。不能把服务器文件改成 local_reference 或填写 verified 来绕过契约检查。

## 保留的样例限制

- FloatCap/流通市值因子沿用2025年3月原本地参考切片，价格、日期轴和历史在市已从明确的本地HDF独立扩充。FloatCap 是描述性标识，不证明与服务器同名因子逐值一致。
- 日历取自本地价格日期轴；复权算法、独立日历及历史全集完整性仍未正式验收。实际 origin、来源链和数据质量限制始终披露。
- 每期348个参考 A 股代码的历史在市未知，四频均为 `partial_success`，不是完整全 A 验收。不能填0、前填或缩池掩盖。
- **因子仍只有2025年3月。价格、日历和历史在市扩展至2026年10月不等于已具备2020—2026全区间因子回测能力；扩大观察区间仍需对应的因子数据，不能仅改配置。**
- 月/季各1期，标准差与ICIR未定义；不能填0或推断显著性。Excel保留公式及Python缓存，未经独立表格引擎重算。

## 测试与输出

开发测试和显式 HDF 导出需 h5py，但普通回归用合成文件、不需要原 HDF 目录：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-dev.txt
```

```powershell
.\.venv\Scripts\python.exe -I -B -X utf8 -m unittest discover -s .\tests -p "test_rankic*.py"
```

实际目录迁移重放检查只复制四份配置选中的数据根，不带旧样例或原 HDF。默认写 `verification/latest-replay-check.json`，避免覆盖历史证据。此前历史在市扩充的147项测试（145通过、2跳过）见 [unit-tests-listed-extension-final.txt](verification/unit-tests-listed-extension-final.txt)，其8次重放见 [listed-extension-replay-check.json](verification/listed-extension-replay-check.json)。

源包客户端前置能力的历史回归记录为 **191项：187通过、4跳过**，耗时58.687秒，见 [最终测试日志](verification/unit-tests-plugin-client-foundation-final.txt)。跳过项为此前被阻止创建的项目 MCP 配置检查，以及本机权限不允许的3项文件/面板符号链接测试；目录 junction 检查实际通过。新入口原目录四频＋搬迁四频共 **8次真实隔离重放**，status、summary、periods、return_range 全部精确匹配原基线；网络/HDF/禁止目录访问计数均为0，见 [本轮重放证据](verification/plugin-integration-client-replay.json)。这不代表正式服务器联调或独立表格引擎重算通过。

当前合成回归使用上面的 unittest 命令。历史八次重放工具需在独立开发工作副本放置原本地参考数据和示例路径，不能把服务器包改标 local_reference；具备该离线数据时，重复检查使用：

```powershell
.\.venv\Scripts\python.exe -I -B -X utf8 .\verification\verify_catalog.py
```

新输出始终在 `results/rankic/<UUID>/`；`success`/`partial_success` 退出码0，`no_result`/`error` 为2。必须同时查看状态、覆盖和诊断，不将退出码0等同于完整数据通过。对话按 SKILL.md 展示本次统计和诊断，同时提供 report.md、rankic.xlsx、metadata.json 三份链接。少于10个有效期提示短样本；未定义指标解释原因，Excel 仅声明实际完成的校验。

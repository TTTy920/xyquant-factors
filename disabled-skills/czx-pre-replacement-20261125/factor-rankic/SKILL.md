---
name: factor-rankic
description: Use when a user provides a factor file or names a factor for RankIC, rank information coefficient or 因子预测能力测试, including requests missing dates, frequency or stock pool, or asks to run a bundled RankIC sample.
---

# 因子 RankIC 测试

客户只需说出因子名称；不要要求客户编写配置或自己整理面板。目标为**服务器授权取数 → 客户本地缓存 → 本地计算 → 对话摘要及报告附件**。计算是理论收盘到收盘的横截面预测能力测试，不是可执行交易策略回测。

基础数据通过 XYQuant MCP 的 `query_rankic_data` 授权下载，计算和报告在客户本地完成；用户因子文件不上传。服务器包使用 `origin=xyquant_server`、`server_contract=rankic-data-v1`。来源标记和文件哈希不证明复权算法、独立交易所日历或全市场历史成员已经完整验收；实际覆盖和缺口以本次目录、provenance 和结果为准。

## 先区分正式任务与开发样例

- 只有用户明确要求“随附/离线/开发样例”才使用随附 FloatCap。服务器随附因子通过 `query_rankic_data` 获取；明确的离线任务使用用户提供的本地参考数据根。四份 `examples/floatcap-*.json` 保留原样例参数，由助手在客户项目生成任务配置并填入实际 `data_root`。普通“测流通市值”或恰好与样例同区间的请求，都不是同意用样例。
- 正式任务不能因服务器缺数据、无权限或运行失败而自动降级为样例；也不能把服务器文件改标成 `local_reference`。
- 显式本地离线样例不登录、不调用 MCP、不访问原插件/原 HDF5；服务器随附因子仍按原生 MCP 授权取数。

## 正式客户流程

### 1. 唯一识别因子

用户提供因子文件时使用该文件，在本地适配；仅给出名称时按下面的目录查询流程识别。不能把用户文件换成库内同名因子，也不能将随附 FloatCap 当作因子库同名数据。

使用当前客户端实际提供的 XYQuant `query_factor_catalog` 搜索用户名称；必要时翻页，并用返回的准确因子标识查询定义、覆盖和授权日期。不能把本地样例目录当作服务器目录，不能凭文件名认定同名数据相同。

- 多个候选：展示实际返回的名称/定义，让用户选，不取第一个、不猜 ID。
- 未找到或无权限：如实说明；不发明因子或导出接口。
- MCP 未连接：由客户端原生 OAuth 完成连接。不要读取 token 缓存、私钥，不自行建立 SSH/代理旁路。插件提供 MCP 配置，工具是否加载以当前客户端实际列表为准。

### 2. 只补问缺失明细

从用户已给信息中提取，不重复问已经确定的项目。将缺失项目合并成一次简短提问：

> 请补充测试起止日期、频率（日/周/月/季）和股票池（全 A / 自定义）。

示例：用户已说“2025年3月、周频”，则只问股票池。自定义池只有缺代码才再问代码；规范化、去重并确认静态集合。最低有效股票数默认20；仅用户明确要求小池测试时可降低，不能低于3。不自动改变方向、频率、日期或股票池来迎合现有数据。

参数准备完成不代表数据已就绪；完成基础数据预检和授权取数后才执行计算。

### 3. 基础数据预检必须先于导出

读取 [数据契约](references/data-contract.md)，调用 `query_rankic_data(dataset="catalog")`，检查当前版本、清单 SHA、覆盖、数据来源限制及以下数据能力：

- 唯一因子、授权范围、快照/版本和所需观察日期；
- 同一复权快照的 T 与卖出日收盘价；
- 确认真实自然周期末所需的完整交易日历；
- 全 A 所需历史 T 在市状态，包含退市历史与覆盖说明。

缺少运行所需字段、无权限或覆盖不满足时，向客户列出缺项并保留已确认参数，不先创建无法用于本次计算的因子导出。不得发明工具/参数，不用股票日收益、归因 `eligible` 或当前存活股票列表替代本包字段，不自动用本地样例。保留供应数据的日历来源、复权定义待补充和历史在市缺口说明，给 `verified=true` 或自行改来源标签不算验收。

### 4. 取数与本地运行顺序

1. 每次新任务先查询当前目录，按来源、权限范围、快照/版本、复权标识、覆盖及文件哈希匹配本地缓存，不能只按因子名/年份命中。不同授权或复权快照不能拼接。基础包按需生成，已授权用户共享一小时，访问不续期；到期重新查询、下载。具体命令见 [取数边界](references/download-workflow.md)。
2. 先取合格日历，按真实交易日确定 T 和下一同频周期末，再确定所需价格终点。观察截止日不能代替周期末。
3. 基础包使用 `query_rankic_data(dataset="bundle", data_version=..., request_id=<UUID>)`，返回任务后仅传 `job_id` 查询同一工具直到 ready/failed。因子库因子使用真实 `create_export` / `get_export`，用户文件直接在本地读取。创建请求的 UUID 在网络重试时复用，等任务实际就绪后取下载凭据，不自行拼 URL。
4. 基础包通过 `rankic_entry.py data save` 保存，因子原件通过 `rankic_entry.py download save` 调用独立 `scripts/rankic_download.py` 的完整安全检查保存；详见 [取数边界](references/download-workflow.md)。授权任务 JSON 只走非回显 stdin，不能进入聊天、命令参数、echo/Here-String、临时明文文件、日志或报告。没有安全输入通道就停止，不绕过。
5. 因子原件保存于 `downloads/factors/<job>/`，基础包保存于 `downloads/rankic/<snapshot>/data/`。用户文件或库内因子通过本地 `factor prepare` 生成独立任务清单；不猜列名或 ZIP 成员。ZIP 只通过 `rankic_archive.extract_zip` 校验后解压到新数据目录；读取下载物的 Python 使用 `-I`，不从下载目录运行解释器/构建工具。
6. 助手生成仅含 `parameters` 和 `data_root` 的任务 JSON，`data_root` 相对配置文件或使用本次下载返回的绝对路径；实际执行 `rankic_entry.py rankic run`，而不是口头计算或只给客户命令。检查状态与退出码，再读本次报告。

用户文件支持 CSV、TSV、XLSX、Parquet 长表，明确列布局为 `date, stock_code, value`、`日期, 股票代码, 因子值`，或因子库的 `date, stock_cd, alpha`（含 Pandas 索引元数据）。格式不匹配先确认映射，不猜列、不填缺值。一次适配一个因子；多因子 ZIP 选择一个因子重新导出。

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py factor prepare --data-root <基础包data_root> --factor-file <因子文件绝对路径> --factor-name <准确名称> --source user --project <已有项目绝对路径>
```

因子库文件使用 `--source factor_library`，后续运行使用返回的新 `data_root`。任务目录的基础资料优先使用硬链接，失败时才复制；基础资料只读，用户因子与任务清单独立生成。随附因子直接使用基础包 `data_root` 和目录返回的名称，不运行适配器。

## 显式运行随附样例

Python 3.10+，依赖见 `requirements.txt`。使用兼容环境的解释器运行隔离入口；依赖安装在该解释器环境中，不使用 `pip --user`。Windows 可将下面的 `python3` 替换为 `python` 或虚拟环境解释器。环境准备见 [README.md](README.md)。

由助手从 `examples/floatcap-*.json` 读取原样例参数，在客户项目生成配置并填写实际 `data_root`。用户明确提供的本地参考数据可离线运行；通过服务器取得的随附因子先完成上面的授权下载。先检查目录：

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py rankic inventory --data-root <实际data_root>
```

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py rankic run --config <本次任务JSON绝对路径> --project <已有项目绝对路径>
```

周/月/季分别使用 `floatcap-weekly.json`、`floatcap-monthly.json`、`floatcap-quarterly.json` 的参数。首次安装依赖可能需要网络，不能承诺新电脑离线安装。不要读取原 HDF5。

- 样例 FloatCap/流通市值源于本地参考切片，服务器随附包保留其数值；它不是通用因子库导出的同名因子，未证明两者相同。
- 观察范围2025-03-01～2025-03-31；FloatCap因子仍只有2025年3月。价格、日期轴和历史在市已从本地HDF独立导出，实际覆盖2020-01-02～2026-10-30，日历自然日期声明为2020-01-01～2026-10-31；它仍是本地价格日期轴，不是独立交易所日历验收。在市保留源0/1/null及缺键，不代表已补齐全市场历史成员。只改日期不能扩展因子覆盖，也不能据此声称可回测2020—2026任意区间。
- 四频预期有效期数21/4/1/1；每期348个参考 A 股代码在市状态未知，均应为 `partial_success`。不要将未知填0或缩池掩盖缺口。
- 所有角色由实际 `data_root/catalog.json` 定义，来源限制见同目录 `provenance.json`。`inventory` 只检查清单声明，不宣称全文件完整性或服务器验收。

## 计算与交付

- T 范围含首尾；日频是下一交易日，周/月/季是自然周期最后交易日到下一同周期最后交易日。收益终点可以超过观察截止日。未形成的卖出日/价格为未完成，不是0。
- 收益 = adj_close(exit) / adj_close(T) − 1。逐 T 对齐日期/代码，Spearman 使用平均并列秩；不前填、不去极值、中性化或方向反转。常量和样本不足不是 IC=0。
- `success`/`partial_success` 退出码0；`no_result`/`error` 为2。不能仅因产生报告或退出码0就称完整全 A 通过。
- 读本次 `results/rankic/<UUID>/report.md`，在对话中展示参数、实际收益范围、有效/无效/未完成期、均值、ddof=1标准差、未年化ICIR、正值比例、有效股票数和覆盖缺口；链接同目录 `report.md`、`rankic.xlsx` 与 `metadata.json`。
- 不足两期或零标准差时统计量为空并解释；少于10期提示短样本。Excel 有公式及 Python 缓存，未经独立 Excel/LibreOffice 重算，不能声称独立重算通过。

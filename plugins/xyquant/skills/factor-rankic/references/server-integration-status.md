# RankIC 服务器接入与客户端边界

## 数据和计算职责

- `query_factor_catalog` 识别真实库内因子、定义、覆盖和授权范围；用户文件在本地读取，不替换成同名库内因子。
- `query_rankic_data` 提供 RankIC 目录、基础包及任务状态；基础包采用 `rankic-data-v1`，包含规范日历、复权价格、历史在市及随附 FloatCap。
- `create_export` / `get_export` 提供单因子库原件。MCP 只取数，RankIC 计算及 Markdown、Excel、JSON 报告全部在本地完成。
- 每次新任务先确认当前 MCP 授权、版本和覆盖；基础包共享一小时、访问不续期，到期申请当前包。用户因子和任务清单独立，基础资料优先硬链接、失败才复制。

## 参数准备与预检

- 根目录 `rankic_entry.py` 要求 Python `-I`，只分发到本包固定的 rankic/workflow/data/factor/download 入口，防止项目或下载目录同名模块参与导入。
- `workflow prepare` 整理部分参数、规范化自定义股票、给出缺项，保存独立的无凭据任务；不替客户选择股票池，不自动降低最低样本数，不把名称整理当准确因子识别。
- 任务位于 `downloads/rankic-tasks/<UUID>/request.json`，只含 schema_version、request_id、origin 和 parameters；schema_version=1 是参数任务格式，不是授权凭证。
- `workflow preflight` 从白名单字段重新检查参数。没有数据根时仍为 blocked；提供 `--data-root` 后核对服务器来源契约、有效期和实际因子，满足条件才返回 can_run=true。can_create_export 保持 false；预检本身不创建任务、不连接、不下载、不计算。
- 调用方不能通过在参数文件中添加 verified、ready、票据、下载对象或 capabilities 取得授权。名称不接受 URI、协议相对地址或 UNC；输入异常不回显凭据。

## 数据能力与来源限制

来源、结构和文件哈希核验与数据语义完整性是不同的检查。供应价格文件的复权算法定义仍待补充；日历来自价格日期轴；历史在市保留原始空值和缺键。上传服务器不补齐这些信息，也不构成独立交易所日历或完整全 A 历史成员验收。

复权价格、观察/收益终点、日历完整边界、历史成员及各类缺失须按 [数据契约](data-contract.md) 核对。不能用股票日收益、Barra eligible、当前存活股票或临时缩池替代。没有本次所需字段、授权或覆盖时保留已确定参数并说明缺项，不自动改用样例或改来源标签。

## 下载与运行

授权票据只经可信的非回显 stdin 交给下载器，不进入聊天、argv、环境变量、明文文件、日志或报告；不读取 OAuth 缓存或私钥，不自行建立 SSH/代理旁路。宿主不提供安全输入通道时停止下载，不能把脚本具备 stdin 接口等同于所有宿主已验证安全交接。详细命令与重试边界见 [取数流程](download-workflow.md)。

隔离防护以实际使用 Python `-I` 为前提；非隔离解释器可能在入口执行前加载 sitecustomize，入口无法补救已经发生的加载。普通计算只读取规范 Parquet，不导入 h5py，也不访问原 HDF 目录。

## 原始验证资料与交付

保留源包的 [测试记录](../verification/unit-tests-plugin-client-foundation-final.txt)、[四频重放](../verification/plugin-integration-client-replay.json)、[审查记录](../verification/plugin-integration-client-review.md) 和 [计算基线](../verification/hdf-reference-baseline.json)。这些文件描述原参考快照，历史路径与版本不是当前客户端运行依赖；当前改动的验证以仓库对应提交的 CI 为准。

原计算引擎、报告生成和数值基线保持不变。任务结束按 SKILL.md 展示本次参数、统计、实际收益范围和覆盖缺口，提供 report.md、rankic.xlsx、metadata.json。少于10期提示短样本，空值统计量解释原因；Excel 的公式缓存来自 Python，未经独立表格引擎重算不能声称已重算通过。

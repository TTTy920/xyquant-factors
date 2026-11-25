---
name: factor-synthesis
description: 按用户明确指定的 2–10 个 XYQuant 因子计算复合因子得分，只输出得分、不出股票名次或榜单；支持等权、手工权重、RankIC 均值和 RankICIR 加权。可直接调用 RankIC 测试定权，也可复用现成报告。
---

# 多因子合成得分

**必须先由用户明确指定 2–10 个不同因子。** 未指定或只给一个就追问，不套模板、不按当前股票自行挑选。自然语言别名用 `query_factor_catalog` 消歧；只使用已确认的 MCP 因子 ID。客户可给定日期、权重、方向覆盖、比较池和想查的股票。默认等权、最近共同交易日、授权导出中的 A 股代码范围。查询单股不缩小比较池，不输出其他股票的得分；没有指定目标股票则按代码导出得分。不提供股票名次、榜单或各因子对个股的分解。

历史 RankIC 定权需要基础数据时，先按[基础数据下载](../../references/datasets.md)通过 `query_rankic_data` 获取复权价格、日历和历史在市；等权及手动权重不需要这三套基础数据。服务器下载模式的训练配置使用 `data.origin=xyquant_server`，由 RankIC 入口验证来源。

## 授权与同日导出

1. 用当前宿主授权的 **XYQuant MCP**；没有连接时按 [xyquant-login](../xyquant-login/SKILL.md) 选择宿主的登录方式（OpenCode 用 `/mcps`）。不用 SSH、网页账号或历史本地样例代替 MCP 授权。账号切换或权限丢失后，不把原账号缓存当作本次授权数据。
2. 对每个已确认的 ID 查询目录，核对原定义、覆盖、授权日期和方向；`降序` 对应 `descending`，值大优先；`升序` 对应 `ascending`，值小优先。目录没给方向时向用户确认，不猜；运行传 `--direction ID=unspecified --override-direction ID=<用户确认方向>`，报告注明目录没有方向。只有用户要求才能改用不同方向。自定义权重须覆盖全部指定因子，用非负数且格式统一；零明确表示不启用，至少保留两项，不把零权重因子纳入导出或交集。
3. 若选择 RankIC 定权，先确定候选合成日，按下节训练并确定启用因子。若未指定日期，从目录覆盖、授权末期和上海时间今天的最早值开始，核对启用因子同一天是否都有数据。可用 `preview_factor` 确认日期存在，默认候选找不到共同数据时在最近 7 个自然日内回查，不用预览结果打分。用户指定的日期没有共同数据时直接说明，不悄悄改日。不把不同日期拼为截面。候选日变化时，用实际合成日重新核对历史定权，可复用合格报告。
4. 对所有启用因子发一次 `create_export(factors=[...], start=T, end=T, request_id=<UUID>)`。默认不传 `codes`；仅在用户指定**比较池**时传该池的规范代码（最多 1000，以服务器实际限制为准），不是 `--stock` 的目标代码。按服务建议查询 `get_export`，同一任务网络重试复用编号。就绪后 `get_export(job_id=..., include_download=true)`，只在可信层交接短期票据。下载失败或授权范围有歧义时不扩大、拼接不同授权或用预览顶替。

## 安全交接和本地执行

Python 3.10+，安装 [requirements.txt](requirements.txt)。已有可信非回显 stdin 的宿主将实际就绪任务 JSON 作为数据传给 `synthesis_transfer.py save`，不放进聊天、命令行参数、日志、脚本字符串或文件。OpenCode 可用同机 [单次回环交接说明](../stock-holding-diagnosis/references/host-transfer.md) 的同一协议：将命令换成 `synthesis_transfer.py receive --job <任务ID> --project <项目绝对路径>`（**不带 `--kind`**），在同一次 Code Mode 调用取 `get_export(..., include_download=true)` 并 POST，不返回票据。`202` 仅表示接受交接，须等下载器结束并确认 `ready`。没有安全交接能力就报告阻塞，不输出凭据。

```text
python <本Skill>/scripts/synthesis_transfer.py save --job <任务ID> --project <已有项目绝对路径>
python <本Skill>/scripts/synthesis.py --project <已有项目绝对路径> --job <任务ID> --sha256 <MCP SHA-256> --date YYYY-MM-DD --factor ROE_TTM --factor EP_TTM --direction ROE_TTM=descending --direction EP_TTM=descending --stock 600519.SH
```

示例仅演示用户**已指定**两个因子的命令，不是默认因子。各 `--factor` 和 `--direction` 重复传入。可加 `--weight ID=50`（给全体因子）、`--override-direction ID=ascending`、`--pool-code <规范代码>`（重复，与 MCP `codes` 完全相同）、`--stock <代码>`。默认共同有效样本至少 **20** 只；只有用户明确允许小样本时才可用较低 `--min-stocks`（最低 2）。有 `no_result` 或 `error` 时退出 2，不填零或伪造得分。

## 用历史 RankIC 定权

设 `--weighting rankic-mean` 或 `--weighting rankicir`，不能混用手工 `--weight`。先加 `--plan-weights` 定权，不传合成日截面任务：

- **没有现成报告**：按 [RankIC Skill](../factor-rankic/SKILL.md) 确认同一训练窗口、频率、股票池和本地市场数据配置，只追问缺失项。对每个指定因子分别发起历史单因子 MCP 导出，全部用相同起止日和股票范围，通过 `rankic_transfer.py` 安全落地。合成用 `--ic-config <本项目RankIC配置>` 加每因子的 `--ic-export ID=历史任务ID:SHA-256`，会逐个调用 RankIC 测试并用新生成的报告定权。不要要求客户手工跑每个 RankIC，也不把未经校验的历史预览当报告。
- **已有合格报告**：每因子传 `--ic-report ID=<本项目内的RankIC metadata.json>`；必须是当前 `inputs/datasets/` 同一基础数据版本生成的报告，旧快照或基础数据已换版的报告一律重跑。项目报告可直接引用 `xyquant-data/results/rankic/`。不与自动测试参数混用。

只有定权 `ready` 时才按 `active_factors` 导出合成日截面。若刚自动测试，读取输出 `rankic_reports`，在最终算分阶段把这些报告路径逐项传为 `--ic-report`，不重复训练。沿用同一组因子、方向、日期、股票池和样本门槛。默认至少 10 个收益已结束的共同有效期，且至少两个因子能够取得正权重；不足时说明原因，不自动改成等权。复权价、日历和历史在市仍必须来自合适的本地快照。详细参数见 [RankIC 权重契约](references/rankic-weights.md)。

## 计算和交付

以通过同日、文件内容和范围校验的股票为输入。在比较池内求所有**启用因子都有效**的共同样本；在同一个共同样本上逐项计算分位分，并列使用平均秩。高分始终为优先端，分位分 `100 × (N − 平均秩)/(N − 1)`。权重按总和归一，分位分加权求和得到复合因子得分。不因个股缺项临时重分权重。计算说明可保留常量因子（均 50 分）及方向对齐的 Spearman 高相关提醒（`|ρ| ≥ 0.8`），不自行删因子。详见[数据和结果契约](references/data-contract.md)。

读取实际 stdout JSON 和退出状态。对话中只展示客户要求的复合因子得分，必要时注明日期、样本数及所用权重或未生成原因；不算名次、不按分数排序，也不附个股因子分解。成功时提供本地 `xyquant-data/results/factor-synthesis/<UUID>/scores.csv`，只有股票代码和得分两列；`report.md`、`metadata.json` 留存计算和数据覆盖说明。目录不回写服务器。历史 RankIC 仅用于确定权重，当前复合因子得分不是历史回测或交易建议。

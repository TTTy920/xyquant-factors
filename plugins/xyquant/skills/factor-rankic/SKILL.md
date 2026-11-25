---
name: factor-rankic
description: 用于因子 RankIC、rank information coefficient 或因子预测能力测试。按用户指定的因子名称从 XYQuant MCP 获取授权历史因子值，用项目本地的复权收盘价、日历和股票池数据做日/周/月/季度 RankIC；也保留离线文件模式。
---

# 因子 RankIC 测试

对每个观测日独立计算因子值与下一周期收益的 Spearman。只做理论收盘到收盘 RankIC，不是可执行交易回测。

## 按因子名称从 MCP 获取历史值

1. 用户给因子名称或 ID 时，用 `query_factor_catalog` 查询并确认唯一 ID；中文名先按名称搜索，候选不唯一时让用户确认，不自行替换成相似因子。确认观测起止日期、日/周/月/季度、全 A 或自定义代码；只追问缺失项。默认每期至少 20 只有效股票，用户明确允许才可降至 3。
2. 在 `xyquant-data/inputs/rankic/` 找到运行配置。**复权价、交易日历、全 A 历史在市只从 `xyquant-data/inputs/datasets/` 及各自扁平清单读取。** 核对代码范围、频率、观测窗口及下一期收益覆盖；缺数据就说明，不回退旧参考快照、旧登记版本、MCP 市场数据或现成收益率。
3. 用目录中的 ID 及授权区间，调用 `create_export(factors=[ID], start=..., end=..., request_id=...)` 导出**观测范围**的因子值，不为下一期卖出日另取因子值。自定义池传明确的规范化 `codes`，全 A 不传筛选。UUID 网络重试复用；用 `get_export` 等同一任务 `ready`。预览最多 50 行，只能确认存在，不能用于回测。
4. 用 [factor-download](../factor-download/SKILL.md) 的授权流程。将 `get_export(include_download=true)` 的就绪任务作为数据，通过可信非回显 stdin 传给 `rankic_transfer.py save`。OpenCode 同机可用[既有单次回环交接](../stock-holding-diagnosis/references/host-transfer.md)，命令换成 `rankic_transfer.py receive --job <任务ID> --project <项目绝对路径>`，不传 `--kind`。在同一次 Code Mode 调用里取任务并直接交接，不输出票据、URL 或下载头；写入文件的是校验后的因子包及无凭据的任务范围。等待本地返回 `ready`，不能把 HTTP 202 当成已下载。

**MCP 取的是因子值，RankIC 在本地计算。** 服务器复权收盘、完整交易日历及历史在市数据仍未通过验收，CLI 对 `origin=xyquant_server` 保持拒绝，不能以 `verified=true` 或归因包日历绕过。MCP 未授权、缺数或导出失败时如实说明，不偷偷改用本地因子。

## 精确时间规则

- 日期范围仅限制因子观测日 T，含首尾。日频卖出日为下一交易日；周/月/季度用真实日历的自然周期末 T 和下一同周期末卖出。
- 例如 2025-01-03 的周频收益到 2025-01-10；观测截止 2025-01-02 不产生周频截面。2025 年末季度观测需要 2026 年一季度末价格。
- 查询截止日不能代替周期末。下一周期无交易日不跳到更后周期；日历/未来价格不足的尾期留空标记，不记零。
- 收益 = 同快照复权收盘价(exit) / 复权收盘价(T) − 1。全 A 使用 T 日历史在市且获授权的 A 股，不用今天名单回填；自定义池规范化去重。
- 不前填、不改因子方向、不去极值/中性化。非有限因子、非有限或非正价格剔除计数；重复键报错。每期平均并列秩相关，常量或样本不足为无效，不是 0。

## 本地执行与交付

客户侧 Python 3.10+，安装本 Skill `requirements.txt`。无凭据配置放 `xyquant-data/inputs/rankic/`。配置包含 `parameters.start/end/frequency/pool`（自定义池还需 `codes`）、`data.origin=local_reference` 和 `data.authorized_codes`。正式入口不使用配置中的旧 `price/calendar/listed` 路径或版本；实际版本由 `inputs/datasets/` 清单固定并写入报告。MCP 模式下 `--factor` 传目录确认的 ID，无需配置本地因子文件。

```text
python "<本Skill目录>/scripts/rankic_transfer.py" save --project "<客户项目>" --job "<任务ID>"
python "<本Skill目录>/scripts/rankic.py" --project "<客户项目>" --config "<项目内配置>" --factor "<MCP 因子ID>" --factor-job "<任务ID>" --factor-sha256 "<SHA-256>"
```

因子也可用本地文件：省略三个因子任务参数，按契约声明本地因子及其 ID，文件放 `xyquant-data/inputs/rankic/`。这只切换因子来源，基础数据不变。

```bash
python "<本Skill目录>/scripts/rankic.py" --config "<客户项目>/xyquant-data/inputs/rankic/rankic-config.json" --project "<客户项目>"
```

必须实际运行，检查 JSON 状态及退出码。`success` / `partial_success` 退出 0；后者也包括历史股票覆盖未知的参考结果，必须披露 `unknown_universe`。`no_result` / `error` 退出 2。不能因有报告文件就宣称成功。

展示 `xyquant-data/results/rankic/<UUID>/report.md` 的参数、观测及实际收益范围、有效/无效/未完成期数、均值、样本标准差(ddof=1)、**未年化** ICIR、正值比例、样本数和主要限制，并链接 `rankic.xlsx`、`metadata.json`。用 MCP 模式时明确「因子值来自 MCP、复权价等来自本地参考」。不把 RankIC 测试称作策略净值或可执行交易回测。

不足两期或标准差为零时，相关未定义统计量显示空值并解释。少于 10 个有效期提示不稳定，不声称显著或可获利。读取失败、契约未验收和未完成期必须如实披露，不提供编造数值或将本地样例称为服务器端到端通过。

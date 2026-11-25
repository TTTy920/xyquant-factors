# RankIC 均值与 RankICIR 权重

可复用用户确认的 `factor-rankic` `metadata.json`，也可从已校验的历史 MCP 单因子导出直接调用 RankIC 脚本生成报告。报告都在项目的 `xyquant-data/` 内；项目新生成的报告直接从 `xyquant-data/results/rankic/` 引用。旧报告如缺原始方向、授权范围或市场数据文件指纹，需用新版 `factor-rankic` 重跑。MCP 提供的是历史**因子值**，复权价、日历和历史在市仍使用同一个本地配置，来源限制不变。

## 权重怎么算

1. 每个指定因子必须有一份报告，`parameters.factor_name` 与 MCP 因子 ID 精确相同。训练报告必须使用相同观察窗口、频率、股票池、授权范围、每期最低股票数；市场输入必须与当前 `inputs/datasets/` 的清单和实际文件一致，不复用基于旧目录或旧快照的报告。自定义训练池须与本次 `--pool-code` 相同。默认用历史在市 A 股口径训练，当前打分仍受实际 MCP 授权导出限制，不声称当前完整全 A。
2. 训练观察截止日须**早于**合成日。只取全部指定因子都具备有效 RankIC 的共同期，且同一观测日的收益结束日和池口径一致。收益结束日等于或晚于合成日的期、授权或在市状态不完整的期、样本不足的期均不用。不同因子因各自值缺失，实际有效股票数仍可能不同，记入诊断。默认至少 **10 个共同有效期**；只有用户明确允许才可传更低 `--min-ic-periods`，最低 3，并提示稳定性不足。
3. 日、周、月或季度频率及下一期收益定义沿用训练报告。历史 RankIC 原本是**原始因子**与未来收益的 Spearman；合成升序时将历史 RankIC 乘 `−1`，降序不变。重新计算这些已对齐的逐期值的均值与样本标准差（`ddof=1`）；`RankICIR = 均值 / 样本标准差`，不年化。不直接抄报告摘要，标准差为零时 RankICIR 未定义。
4. 只用选定指标严格为正且有效的因子，按其正值和归一。指标为零、负数或未定义的因子权重为 0，并说明排除原因；不取绝对值、不自动反转方向。共同期不足或剩余不足两个正权重因子时返回 `no_result`，不自动退回等权，不输出分数。

## CLI 流程

先根据用户指定的训练窗口、频率和股票池准备一份项目内 `xyquant-data/inputs/rankic/` 配置。该配置与 [RankIC 因子导出模式](../../factor-rankic/references/data-contract.md) 一致，不需要本地因子文件或在配置内反复替换因子名。宿主为每个指定因子调用 MCP 并使用 RankIC 下载器保存单因子导出。用其任务 ID 和 SHA-256 定权：

```text
python <本Skill>/scripts/synthesis.py --project <项目绝对路径> --date YYYY-MM-DD --factor ROE_TTM --factor EP_TTM --direction ROE_TTM=descending --direction EP_TTM=descending --weighting rankic-mean --ic-config <训练配置> --ic-export ROE_TTM=<历史任务ID>:<SHA-256> --ic-export EP_TTM=<历史任务ID>:<SHA-256> --plan-weights
```

也可跳过训练、使用已有报告（两种方式不混用）：

```text
python <本Skill>/scripts/synthesis.py --project <项目绝对路径> --date YYYY-MM-DD --factor ROE_TTM --factor EP_TTM --direction ROE_TTM=descending --direction EP_TTM=descending --weighting rankic-mean --ic-report ROE_TTM=<第一个报告的metadata.json> --ic-report EP_TTM=<第二个报告的metadata.json> --plan-weights
```

因子仅为用户选择的示例，不是默认值。`rankicir` 只替换权重模式。`ready` 后按 `active_factors` 走同日 MCP 导出；算分时去掉 `--plan-weights` 并加当天截面 `--job`、`--sha256`。自动训练模式将 `--ic-config` 和 `--ic-export` 替换为定权结果中 `rankic_reports` 的 `--ic-report ID=路径`，沿用所有因子、方向、日期、池、样本要求和未修改的报告，不重跑测试。最终 `metadata.json` 记录实际权重、训练窗口、最后使用的收益结束日及报告哈希，不写原始路径、权限凭据或单期历史股票列表。

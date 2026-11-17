# 部署和共享数据

Python 3.10+，安装 `requirements.txt`。插件只附计算代码；原始数据通过 `query_stock_data(dataset="attribution_bundle")` 下载到客户的本地项目，先按Skill入口检查可复用缓存。数据下载之后计算不依赖网络，客户持仓和权重不上传。两种方法使用同一个下载快照，不混合其他数据版本或现成因子收益率。

通过 `entry.py` 执行时用 `--data-root` 指定下载返回的数据根，也可使用 `ATTRIBUTION_DATA_ROOT`。结果用 `--output-root` 写入项目目录。下面保留原版数据契约与计算说明；服务器数据快照已完成格式准备，日常归因不运行迁移脚本。

## 清单和格式

`data/catalog.json` 的 `schema_version` 为1，所有路径相对数据根，不允许越界。`calendar` 为完整交易日历；`portfolios`、`benchmarks` 为名称到 `path/frequencies/aliases` 的映射。`market` 与 `exposures` 为按年保存的文件路径列表；`snapshots` 和 `industry` 为单个路径。不要直接把不兼容表追加进清单。

所有输入使用 Parquet + Zstandard，无损保留 float64。按日期/股票列读取，逐年文件无日期重叠，不把同一批数据复制给两个引擎。

| 数据 | 列与规则 |
|---|---|
| calendar | date，完整交易日期，升序且无重复；需覆盖请求前后周期，不以周末自行推断节假日 |
| market/YYYY.parquet | date, stock_code, return, float_cap, eligible；日简单收益小数、原始流通市值、期初回归合格标记。eligible为可空int8，仅0/1/空，空与0不同。保留全市场股票，不局限持仓并集 |
| exposures/YYYY.parquet | date, stock_code，以及 Size/Beta/Momentum/ResidualVolatility/NonlinearSize/BookToPrice/Liquidity/EarningsYield/Growth/Leverage；日度原始模型暴露，不填零、不重新标准化 |
| weights/portfolio_*.parquet | date, stock_code, weight；仅非零正股票权重，原始频率，不伪造高频持仓 |
| weights/benchmark_*.parquet | 同上，日度基准权重，不保存周/月副本 |
| weights/snapshots.parquet | date, kind, name, valid, total, cash；kind为portfolio/benchmark。记录每期是否真实可用、原始股票权重合计和剔除现金合计。缺失整期必须标无效或无记录，不能解释为零持仓 |
| industry/monthly.parquet | date, stock_code, industry；月度非空历史分类。运行时仅按股票向前填充到期初，不回填未来、不插值 |

股票代码保留交易所后缀和前导零；每表日期/股票唯一。稀疏权重行合计必须等于快照total，total>0才可归因。权重是该日期收盘后适用于下一期间的快照。现金不进入股票归因。非有限值和负权重不可用。

周/月股票收益按完整交易日序列连乘，任一日缺失传播为区间缺失。不用现成周/月因子收益，也不持久保存聚合收益副本。停牌、退市、换股等缺口需要数据维护方按明确口径修复，不能由归因脚本猜测。Barra对全市场回归样本的缺失按剔除阈值诊断，组合/基准持有股票缺收益或暴露仍严格报错。

## 迁移和验证

`scripts/merge_legacy_data.py` 只用于一次性从原两个 Skill 的CSV数据目录准备共享Parquet：

```sh
python scripts/merge_legacy_data.py --barra-data /old/barra/data --brinson-data /old/brinson/data --destination /new/portfolio-attribution/data
```

输出目录必须为空；输入只读。重叠有限值必须精确相同，否则停止。CSV采用round-trip浮点解析，避免默认解析产生尾数差异。迁移按表取日期/股票并集，不用未来数据填缺失；逐列回读校验Parquet值，原始float64不降精度，输出SHA-256、来源和重叠检查写入 `migration_manifest.json`。十个风格合在一个年度面板，日期/股票轴不再重复十份；股票代码采用字典编码。

数据更新时应由部署数据流水线维护完整日历、年度面板、权重及快照，不将今天的合格股票池回填历史。单次任务使用的输入文件须保持不变，脚本前后检查指纹。

## 运行和结果

```sh
python scripts/entry.py inventory --data-root <本地数据根>
python scripts/entry.py run --source preset --data-root <本地数据根> --method both --portfolio "300增强" --benchmark "沪深300" --start 2025-01-01 --end 2025-02-28 --frequency monthly
```

stdout为JSON。完成状态可为success或partial_success；共同数据缺失或所有方法失败时退出码2并在stderr给出JSON错误。输出包括 `归因结果.xlsx`、`report.md`、`summary.md`、`metadata.json`，每次单独目录。`report.md` 是面向对话框的完整分析，包括总体收益、分期表现、行业贡献、风格暴露、数值解释、结论及适用范围；Excel保留全量明细，summary仅用于内部速览。数字底层为小数，报告收益率用%，贡献用百分点。默认不生成PDF或图像文件。`partial_success` 只交付完整跑通的方法，不混用各方法不同的样本区间。

回归参数 `regression.minimum` 默认100且至少12，`max_drop` 默认0.1，`condition_limit` 默认1e10。需回归秩11、样本足够、条件数稳定；参数是质量检查，不保证经济有效性。

旧Brinson严格按自然月末日期排除不完整月，而统一版依据交易日历实际首末交易日判断，例如截止某月最后交易日且日历有下月确认时可以计算完整月。累计统一使用基准增长递推连接；Barra算术累计只作为辅助列。相比旧Barra直接读取周/月收益，统一版从日收益复利聚合，个别因停牌或缺失处理不同而产生差异需在数据诊断中明确。

# 本地输入与服务器公共数据

## 客户文件留在本地

CSV、XLSX 或 Parquet。日期为 YYYY-MM-DD，股票代码保留前导零和交易所后缀。

| 文件 | 字段 | 要求 |
| --- | --- | --- |
| 策略持仓，必需 | date/日期、stock_code/股票代码、weight/权重/持仓比例 | 一日一股一行；非负有限数；权重可为小数、百分数字符串或统一单位的持仓金额 |
| 基准成分权重，必需 | 与持仓相同 | 客户按所选基准提供每期实际期初快照；不使用原 Skill 附带的基准样本 |
| 实际净值，可选 | date/日期、nav/净值/组合净值/复权净值 | 正数、日期唯一；采用含分红再投资的可比净值，包含每期初末值，不向前填充 |

持仓为该日期收盘后适用于下一期的快照。自然月最后一天或周日标记可在同一周期内映射到最后交易日，其他日期不移动。缺整期持仓不填充，不据月末持仓伪造日/周持仓。

沿用原版：显式现金行 CASH、现金、CNY 剔除，股票部分按总额归一化；不支持负权重，零股票权重不参与。实际净值与股票部分口径的不同进入独立净值差额，不修改行业或风格贡献公式。

## MCP只提供公共数据

唯一新增工具为 get_attribution_data，具有 catalog、calendar、bundle 和 job_id 查询模式。股票代码与日期可作为取数条件，客户持仓权重、净值、组合名称、归因方法及运行参数不属于工具输入。

| 数据 | 服务器来源 | 本地用途 |
| --- | --- | --- |
| 个股十风格暴露 | 服务器已有十个 HDF5 | 期初组合及基准暴露 |
| 月频因子收益 | 服务器已有月频工作簿 | 本期风格收益贡献 |
| 日/周频因子收益 | 用户 wang.zip 中相应工作簿 | 对应频率的风格贡献 |
| 个股日收益 | 用户 wang.zip 的日收益率.csv | 区间收益、组合重建净值、Brinson |
| 历史中信行业 | 用户 wang.zip 的中信行业.csv | 期初历史行业分类 |
| 交易日期 | 从同一日收益率数据的日期读取 | 完整周期和期初末对齐；不单独准备文件 |

原 Skill 包的 market、exposures、industry、calendar、策略和基准等数据不进入生产取数源。

calendar 模式仅返回从股票日收益率读取的日期数组，规划脚本通过标准输入接收。bundle 导出 stock_returns.parquet、exposures.parquet、factor_returns.parquet、industry.parquet 和 data.json。没有日历文件、持仓文件或净值文件。股票日收益表保留请求前后62个自然日范围内的实际交易日期，以便原周期判断确认边界；不会生成未来交易日。

| 导出表 | 字段 |
| --- | --- |
| stock_returns.parquet | date、stock_code、return，小数日简单收益，空值保留 |
| exposures.parquet | date、stock_code、十个英文风格列，不重新标准化 |
| factor_returns.parquet | date、源工作簿原始数字列；data.json 明确十风格列映射 |
| industry.parquet | date、stock_code、industry，保留请求股票此前历史分类，向前填充仅在本地执行 |

十风格为 Size、Beta、Momentum、ResidualVolatility、NonlinearSize、BookToPrice、Liquidity、EarningsYield、Growth、Leverage，沿用原生成代码列1–10的对应关系。日/周表为0–10，月表为0–40；仅提取十风格，不把截距和行业列混入。更换来源时由维护方确认列映射和模型口径。

所有公共数值按原值导出为 float64，不在服务器计算因子收益、组合收益、暴露加权、归因或累计连接。行业不在服务器填补，缺失股票值不填零。用户请求中包含未知股票或日期时，保持缺失，由本地原有规则报出具体缺口。

## 本地执行

Python 3.10+，依赖见 requirements.txt。data.json 仅引用同一已校验下载目录的公共数据表。本地 InputStore 从 stock_returns.date 取交易日历，读取客户持仓及基准文件，继续使用原有计算函数；有实际净值时增加独立差额分支。

本次生产工作流使用 --input-config，不使用旧 Skill 的样本目录。保留的 --data-root 和内部 WLS 代码仅用于旧接口兼容与公式测试，不进入本次 MCP 取数工作流。没有有效公共数据时停止并说明，不自动转用样本数据。

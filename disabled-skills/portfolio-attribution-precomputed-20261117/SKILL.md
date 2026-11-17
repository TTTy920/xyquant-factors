---
name: portfolio-attribution
description: 通过 XYQuant MCP 读取服务器公共数据，在本地根据客户持仓完成 Barra 风格和 Brinson 行业收益归因。实际净值可选，未提供时按持仓重建收益；不用于解释选股评分。
---

# 组合收益归因

## 输入与数据来源

计算前必须取得客户的逐股持仓及权重。复用本次对话中已提供或明确授权的本地文件，不要求重复上传。仅有净值不能完成这套持仓归因。

一次补问缺失的组合名称、基准、区间、频率及持仓文件。沿用原版相对基准计算，所选基准的历史成分和权重由客户提供；不使用原 Skill 包的策略、基准或其他样本数据。没有实际净值可以继续计算。

公共数据只用当前 XYQuant 服务器已有数据和用户明确提供、上传到服务器的数据。持仓、基准权重和策略净值留在本地。MCP仅接收股票代码、数据日期、因子收益频率等取数条件，不上传权重或净值，不在服务器运行归因。

交易日历直接取自股票日收益率的日期。本地从下载后的 stock_returns.date 读取，不单独准备或生成日历文件。

## 取数与本地执行

1. 分别用 query_barra_data(dataset="catalog") 和 query_stock_data(dataset="catalog") 核对服务器数据类别。Barra 工具提供十风格暴露与现成因子收益率，基础数据工具提供股票日收益与历史行业；原包示例数据不能补缺口。
2. 用 query_stock_data(dataset="calendar")，传请求起止日期，取得从日收益率派生的交易日期。该响应仅用于本地规划，直接传给规划脚本标准输入，不另存日历文件：
   ```text
   python <Skill绝对路径>/scripts/prepare_inputs.py --holdings <客户持仓> --benchmark-holdings <客户基准权重> --start YYYY-MM-DD --end YYYY-MM-DD --frequency monthly
   ```
   标准输入为工具的 calendar 响应 JSON。输出 requests 包含 query_stock_data 和 query_barra_data 两组取数参数，只含股票、日期等公共数据选择条件，不含权重和净值。
3. 按 requests 中的两组参数分别调用 query_stock_data 和 query_barra_data。两组各保留自己的 request_id，重试同一组时沿用原 UUID。返回任务编号后，分别用创建任务的工具仅传 job_id 查询，直到两个任务均 ready。失败时说明实际缺口，不虚构取数成功。
4. 向本地下载脚本的标准输入传入 {"query_stock_data": <基础数据ready任务>, "query_barra_data": <Barra ready任务>} JSON 对象，保存、校验两份公共数据并生成本地统一配置：
   ```text
   python <Skill绝对路径>/scripts/download_data.py --project <已有本地项目绝对路径>
   ```
   下载凭据仅供传输，不在回答中展示。脚本返回本地 input_config 路径。
5. 运行本地归因脚本：
   ```text
   python <Skill绝对路径>/scripts/attribute.py run --input-config <本地input_config> --holdings <客户持仓> --benchmark-holdings <客户基准权重> --portfolio <组合名> --benchmark <基准名> --start YYYY-MM-DD --end YYYY-MM-DD --frequency monthly --method both --output-root <本地结果目录>
   ```
   有实际复权净值增加 --nav <本地净值文件>。明确 Barra 或 Brinson 时只运行对应方法；泛称收益归因时运行 both。客户文字作为独立参数，不拼成 shell 代码。
6. 读取本次 report.md 和 metadata.json，按 [对话报告](references/conversation-report.md) 在对话中交付文字、数字表格和结论。Excel为补充附件。partial_success 只交付完整成功的方法，并说明另一方法失败原因。

缺少 query_stock_data 或 query_barra_data 时，说明相应数据入口尚不可用，不改用已撤下的服务器归因工具，也不使用本地旧样本或模型口算代替。

## 净值分支与原计算口径

- 有实际复权净值：按同一期初、期末净值计算区间收益。原有持仓风格、行业贡献照算，独立列示“实际净值与持仓重建差额”，用原累计连接公式核对实际超额。
- 无实际净值：沿用原持仓重建收益和净值计算。已提供但无效的净值报具体缺口，不静默降级。

保留原来的股票权重归一化、现金剔除、期初静态持有、股票日收益复利、历史行业按股票向前填充及完整周期判断。月末持仓用于次月，周末持仓用于下一交易周；净值频率更高不代表有更高频持仓。不用未来持仓倒推或补全缺失整期持仓。

十风格贡献仍为期初相对暴露乘本期因子收益。Brinson 配置为 (wp-wb)*(rb-Rb)，选股含交互为 wp*(rp-rb)。现成日、周、月因子收益直接读取对应期末行，不重新回归，不把周/月表逐交易日记录再次累乘。

累计连接仍为 C_t=(1+Rb_t)*C_previous+Vp_previous*c_t。有实际净值时 Vp 使用实际净值分支，独立差额同样连接。两套归因贡献不能相加，未解释收益和净值差额均不能直接称为纯Alpha。

字段和数据包格式见 [数据说明](references/data-contract.md)。

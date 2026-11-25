---
name: portfolio-attribution
description: 用 inputs/datasets 的基础数据、MCP 或自带的持仓权重，在本地做 Barra 和 Brinson 组合归因；Barra 全市场拟合不依赖 eligible。
---

# 组合收益归因

**持仓之外的输入只从 `<项目>/xyquant-data/inputs/datasets/` 读，不回退 MCP 原包内的其他文件。** 基础数据包括交易日历、年度市场日收益和市值，及适用的十个风格暴露、月度行业。缺少或过期时按[基础数据下载](../../references/datasets.md)通过 `query_stock_data` 分包下载并校验。组合、基准权重和快照可来自 MCP 或用户本地。缺数据时按实际状态说明，不换来源。

## 选择持仓入口

- 用户明确使用自己的组合并提供持仓：将经用户确认的本地文件放入 `xyquant-data/inputs/holdings/`，再传 `--source user --holdings <文件绝对路径>`；自带基准权重也放在同一目录。缺持仓先补充，不改用同名预置策略。复用本次对话已经提供的信息和文件。
- 用户选择300增强、xgb500增强、xgb1000增强或xgb2000增强：`--source preset`。300增强默认对应沪深300、月频；三个XGB分别对应中证500、1000、2000、周频。区间和方法沿用用户要求。
- 对象不明确时询问使用自有组合还是预置策略，一次补问缺失的名称、基准、日期、频率和持仓。默认两种方法；用户明确方法时只运行对应方法。

持仓支持Parquet、CSV、XLSX长表：`date, stock_code, weight`，也接受日期、股票代码、权重等对应列名；日期是快照日期，股票带交易所后缀，权重用小数。现金仅支持CNY、CASH、现金。入口不归一化权重、不补日期和收益。基准可选原包四个指数；客户另有基准权重时增加 `--benchmark-holdings <文件绝对路径>`，格式同持仓。其他基准需要客户提供。

## 服务器取数与本地缓存

1. 调用 `query_stock_data(dataset="catalog")`，读取返回的 `original_attribution`。它列出原版数据版本、策略与基准、频率和覆盖日期。策略展示工作簿不是持仓来源。若没有该数据类别，说明需刷新MCP连接或服务器尚未提供，不能换用现成因子收益率凑数。
2. 先检查项目已有缓存，版本和清单校验值使用目录实际返回值：
   ```text
   python3 <Skill绝对路径>/scripts/download_original.py status --project <已有项目绝对路径> --data-version <data_version> --manifest-sha256 <manifest_sha256>
   ```
    返回 ready 就把 `data_root` 作为持仓输入，不重复下载。缺缓存时，以 UUID `request_id` 和目录版本调用 `query_stock_data(dataset="attribution_bundle", data_version=..., request_id=...)`；该包只提供持仓、基准权重及快照，不传日期或股票筛选；市场基础数据按上述独立入口获取。网络重试复用原编号；只传 `job_id` 查询就绪状态。
3. 把ready任务JSON作为数据通过不回显的进程标准输入传给下载脚本：
   ```text
   python3 <Skill绝对路径>/scripts/download_original.py save --project <已有项目绝对路径>
   ```
    沿用MCP授权，无需再次网页登录。下载凭据不写入文件、日志、聊天或命令行参数。凭据到期时查询同一任务后重试，已过期任务用新请求编号。脚本校验压缩包、清单和逐文件SHA-256，返回 `xyquant-data/mcp-cache/attribution/original/` 下可复用的绝对 `data_root`。

即使下载包含市场数据，也不要拿它当作 `inputs/datasets/` 缺失时的替代品。客户持仓和权重留在本地，不上传；全市场横截面回归及归因全部在本地运行，不读取现成因子收益率。

## 本地执行与交付

先查看已下载数据的清单：
```text
python3 <Skill绝对路径>/scripts/entry.py inventory --project <项目绝对路径> --data-root <下载返回的data_root>
```

预置策略：
```text
python3 <Skill绝对路径>/scripts/entry.py run --project <项目绝对路径> --source preset --data-root <data_root> --portfolio 300增强 --benchmark 沪深300 --start 2025-01-01 --end 2025-02-28 --frequency monthly --method both
```

自有组合：
```text
python3 <Skill绝对路径>/scripts/entry.py run --project <项目绝对路径> --source user --holdings <用户持仓绝对路径> --data-root <data_root> --portfolio <组合名称> --benchmark 沪深300 --start YYYY-MM-DD --end YYYY-MM-DD --frequency monthly --method both
```

结果位于 `xyquant-data/results/portfolio-attribution/`；如需指定 `--output-root`，只能在该目录内选子目录。入口把自有持仓转换为权重和快照，保存在结果根 `_inputs/`，不复制基础数据。数据格式见 [数据约束](references/data-contract.md)。可运行范围取决于 `inputs/datasets/` 的基础数据和实际持仓覆盖，失败时不能填零、删除股票重配权重、缩短失败方法区间或切换来源。

完成后读取本次report.md、metadata.json及 [对话报告](references/conversation-report.md)，按原版格式在对话中交付分析和表格，Excel作为附件。partial_success只交付完整成功的方法，说明另一方法的缺口。

## 共用计算口径

- 一套数据供两个方法共用：股票日收益、日度基准、原频率组合持仓、按方法使用的风格暴露/市值或月度行业，以及交易日历。周/月收益从日简单收益连乘减1；缺一个日收益则该股票区间收益缺失，不能当作0。
- 期初股票权重分别归一化；剔除显式现金并报告；不支持负权重。只支持实际持仓快照具有的频率，不用未来持仓倒推、不给组合或基准任意 ffill。
- 行业逐股票按日期 ffill 到期初，只使用此前最近已知的分类；不能 bfill。初次历史分类之前仍缺失并报错。十风格暴露不采用行业填充规则。
- 两个方法使用同一份交易日历、相同的期初/期末和重建收益。周为周一至周日所在交易周，月为自然月；完整周期按该周期实际交易日判断。请求范围内不完整的周期排除，并说明有效交易区间。日历需额外覆盖请求前后周期以确认边界。
- 重建收益是假设期初股票静态持有一期的结果，未包含期中交易、现金收益和费用，不等于实际产品净值。累计均使用 `C_t=(1+Rb_t)*C_previous+Vp_previous*c_t` 连接，合计对应两者复利收益之差，不是净值比的几何超额。两种归因的贡献不能相加。

## 方法与解读

Barra：先从全市场选**期初十个风格均有效且流通市值为正**的股票，不读取 `eligible`。再剔除当期收益缺失的股票并报告剔除数，按流通市值平方根做带截距的横截面 WLS。不按持仓筛回归样本，不重新标准化暴露，不累加日因子收益替代周/月回归。贡献为期初相对暴露乘当期回归系数，未解释收益与重建超额核对。新旧回归样本定义不同，结果可能不同。

Brinson：配置为 `(wp-wb)*(rb-Rb)`；选股（含交互）为 `wp*(rp-rb)`。纯选股与交互另列供核对，不再加到两项合计上。基准未持有的行业以基准整体收益作为参考，配置为0，贡献计入选股（含交互），明确该约定；组合未持有的行业不归入选股。行业内选股仍可能包含市值、价值、动量等风格收益，不直接称为纯Alpha。

## 对话框报告

**固定交付格式见 [references/conversation-report.md](references/conversation-report.md)，每次归因完成后必须读取并使用。** 以本次 `report.md`、`metadata.json` 和Excel为数值依据，按照该格式直接在对话框完整呈现，不让客户另行打开文件才能看到报告，不只回复完成状态、几句摘要或下载链接。措辞随实际数据变化，结构、表格和单位保持一致；只有客户明确要求简版时再缩短。方法单独运行时仅展示该方法；失败方法说明原因，不编造结果。Excel仅为补充明细，默认不要求PDF或图像。

- 总体表现：组合、基准、频率、请求及有效区间；累计收益和超额；分期收益、正超额期数、最好/最差期。期数多于12时仅列最近12期并注明，统计仍用全部样本。
- Brinson：配置、选股（含交互）的累计贡献表和正贡献期数；主要正负行业的配置/选股拆分；解释最大贡献行业的来源和主要拖累。不要仅看行业合计就推断超低配正确。
- Barra：十风格、未解释项和总超额的核对表；十个风格的平均期初相对暴露、因子收益算术累计、累计连接贡献。用关键期的“暴露×当期因子收益”解释主要正负来源。相对暴露不是持仓比例；不以平均暴露乘累计因子收益替代真实贡献。
- 结论：明确主要收益来源和拖累，以及两种视角的一致或不同之处。结论限定在本次数据区间，不编造宏观行情原因、交易行为或因果关系；不足12期明确样本短，不能将正贡献期数外推为长期胜率。
- 明确累计贡献与单期算术求和的区别、两种归因不可相加、重建收益与实际净值的差异及失败方法。参考报告的“残差=Alpha”说法不适用于本模型，不照搬。

数字默认保留三位小数；收益率用%，贡献用百分点，暴露保留模型单位。不照搬参考报告中的年份、机构署名、数据来源或结论。参考材料仅提供分析结构，所有数字必须来自本次结果。

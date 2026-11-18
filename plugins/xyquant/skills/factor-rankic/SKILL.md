---
name: factor-rankic
description: 通过XYQuant MCP下载RankIC基础数据，在本地测试用户因子或因子库因子的预测能力，支持日、周、月、季频，交付RankIC统计和Excel报告。
---

# 因子 RankIC 测试

MCP只提供数据，相关性、统计和报告由本地脚本计算。用户因子文件留在本地，不上传。沿用原引擎的未来收盘到收盘收益和横截面Spearman计算。

## 确认任务与来源

沿用已给出的因子、起止日期、频率（日/周/月/季）和股票池（全A或自定义代码），只合并补问缺项。最低有效股票数默认20，用户明确要求小池测试才降低，最低3。

用户提供文件时使用该文件。仅给出名称时，用 `query_factor_catalog` 唯一识别并确认授权与覆盖，有歧义先让用户选。只有用户明确要求随附因子，才使用 `query_rankic_data` 目录中的随附FloatCap；不能用它替代用户因子或因子库的同名数据。

Python 3.10+，依赖见 `requirements.txt`；已有兼容环境直接复用。所有脚本通过可信的隔离入口运行，Windows可使用 `python` 或虚拟环境解释器：

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py <目标> <参数>
```

依赖需安装在所选解释器自身的环境中。推荐使用虚拟环境并以该环境的Python运行 `-m pip install -r <Skill绝对路径>/requirements.txt`；隔离入口不会加载 `pip --user` 安装的用户目录，不能通过取消 `-I` 来绕过。

## 服务器取数与缓存

1. 每次新任务先调用 `query_rankic_data(dataset="catalog")`，读取本次版本、清单SHA和覆盖。包按需生成，所有已授权用户共享1小时，访问不续期；到期自动清理，下次从当前数据重新打包。不能用股票日收益、Barra合格标记或当前存活股票替代本包字段。
2. 检查缓存：
   ```text
   python3 -I -B <Skill绝对路径>/rankic_entry.py data status --project <已有项目绝对路径> --data-version <目录版本> --manifest-sha256 <目录清单SHA>
   ```
   ready且未到期时复用data_root。missing/expired时调用 `query_rankic_data(dataset="bundle", data_version=..., request_id=<UUID>)`；网络重试复用UUID。仅传job_id查询同一工具直到ready/failed。
3. 将ready任务JSON通过不回显的进程stdin传入：
   ```text
   python3 -I -B <Skill绝对路径>/rankic_entry.py data save --project <已有项目绝对路径>
   ```
   下载凭据不写入聊天、argv、环境变量、日志或明文文件。凭据过期则查询同一任务；包到期或版本更新则重新查目录、用新UUID取数。脚本校验大小、SHA、ZIP成员和逐文件内容，返回data_root。沿用MCP授权，无需另配Key。

## 接入因子

用户文件支持CSV、TSV、XLSX、Parquet长表，列为 `date, stock_code, value`，也接受 `日期, 股票代码, 因子值`。代码保留前导零，日期为自然日；格式不匹配先确认映射，不猜列、不填缺值。

因子库数据先确认基础数据覆盖，再通过现有 `create_export` / `get_export` 导出一个准确因子，日期取用户的观察区间。ready任务用隔离入口 `download save --project ...` 经安全stdin保存，得到实际Parquet文件；支持 `alpha, date, stock_cd` 及其Pandas索引元数据。多因子ZIP应选择一个因子重新导出，不能混为一个测试。

两种输入都在本地适配：

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py factor prepare --data-root <基础包data_root> --factor-file <因子文件绝对路径> --factor-name <准确名称> --source user --project <已有项目绝对路径>
```

因子库文件改为 `--source factor_library`。使用返回的新data_root，保留原始因子SHA和基础市场快照。随附因子直接使用基础包data_root和目录返回的名称，不运行适配器。

## 计算与交付

生成只含parameters和data_root的任务JSON，结构见 [数据契约](references/data-contract.md)，然后实际执行：

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py rankic run --config <任务JSON绝对路径> --project <已有项目绝对路径>
```

日频为T到下一交易日，周/月/季为自然周期末到下一同频周期末；收益终点可超过观察截止日。按日期/代码对齐，收益为 `adj_close(exit)/adj_close(T)-1`，Spearman用平均并列秩。不前填、不去极值、不做中性化或方向反转。全A按历史T在市取池，未知状态保留计数；自定义池保持用户指定的静态集合。

读取本次report.md和metadata.json，在对话中交付参数、实际收益区间、有效/无效/未完成期、RankIC均值、样本标准差、未年化ICIR、正值比例与有效股票数，并链接rankic.xlsx。不足两期或零标准差的指标为空。partial_success说明实际缺口，no_result/error如实报告，不自动替换因子、数据源、区间或股票池。

结果位于项目 `results/rankic/<UUID>/`，数值均来自本次运行；Excel为明细附件，不让客户自行执行脚本才能得到分析。

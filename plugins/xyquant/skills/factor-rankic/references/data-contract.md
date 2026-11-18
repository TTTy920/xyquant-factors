# RankIC 数据契约

## 基础包

query_rankic_data提供服务器维护的数据和授权下载任务。包内包含catalog.json、provenance.json、calendar.pqt、market/YYYY.pqt、随附factors/FloatCap/YYYY.pqt和自动生成的rankic-data.json。GitHub只附代码和说明。

catalog的schema_version为1，服务器交付使用origin=xyquant_server、server_contract=rankic-data-v1和scope.kind=server_public_data。授权由MCP服务端验证，客户端标记本身不是授权凭证。下载清单含全文件大小/SHA、版本和有效期；一次计算只用同一基础快照。

| 表 | 字段 |
|---|---|
| 日历 | date |
| 因子 | date, stock_code, value |
| 市场 | date, stock_code, adj_close, listed, price_present, listed_present |

date为无时区午夜timestamp[ns]，代码为保留前导零的字符串，因子/价格为float64，listed为可空int8，presence为布尔。市场表保留价格与在市记录的外连接；presence=false表示原表缺键，true且值空表示原表记录未知，两者不能互换。

新增年份文件需在catalog登记；服务端每次重新打包重算文件大小、SHA和覆盖。日历complete_from/complete_through是自然日期完整边界的维护声明，随数据同步维护，不能直接把最后行情日当完整月末。

现有包保留供应文件的复权价格，复权算法定义尚待补充；日历来自价格日期轴，历史在市有空值和缺键。原始来源见provenance，当前覆盖看本次目录，不把上传和校验当作完整全市场历史或复权语义的证明。

## 任务

```json
{
  "parameters": {
    "factor_name": "用户确认的名称",
    "start": "2025-03-01",
    "end": "2025-03-31",
    "frequency": "daily",
    "pool": "all_a",
    "min_stocks": 20
  },
  "data_root": "/用户项目/downloads/rankic/本次版本/data"
}
```

frequency为daily/weekly/monthly/quarterly。自定义池为pool=custom并提供codes字符串数组；all_a不能暗中加入静态缩池。用户因子经本地适配后使用返回的data_root；基础包不被改写，客户数据不上传。

任务目录的日历、市场表及来源说明优先通过硬链接共用下载文件；跨磁盘或文件系统限制导致链接失败时使用文件复制。用户因子与任务catalog独立生成。链接增减引起的文件元信息变化会重新核验内容，实际内容变化仍会报错。

重复日期/股票键报错；非有限因子、非正/非有限价格按原引擎剔除并计数，不填零。完整文件哈希校验与所读日期的行级校验分别记录。

## 输出

report.md、rankic.xlsx和metadata.json每次独立生成。success/partial_success退出码0，no_result/error为2。partial_success有有效结果，同时存在无效/未完成期或在市未知；不能称为完整全A通过。统计仅用有效期，ICIR不年化，Excel公式缓存来自Python结果。

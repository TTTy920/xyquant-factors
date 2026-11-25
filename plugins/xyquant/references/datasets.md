# 基础数据下载

基础数据由现有 MCP 工具提供，计算仍由本地 Skill 执行。使用 Python 3.10+。不要从服务器路径、开发机目录或旧行情缓存自行替换来源。

| 用途 | 查询目录 | 创建下载 |
| --- | --- | --- |
| RankIC、合成的历史 RankIC 定权 | `query_rankic_data(dataset="catalog")` | `query_rankic_data(dataset="bundle", data_version=目录版本, request_id=UUID)` |
| 归因市场收益与市值 | `query_stock_data(dataset="catalog")` 的 `attribution_inputs.attribution_market` | 同工具 `dataset="attribution_market"` |
| 归因十风格暴露 | 同上 `attribution_inputs.barra_exposures` | 同工具 `dataset="barra_exposures"` |
| 归因交易日历 | 同上 `attribution_inputs.trading_calendar` | 同工具 `dataset="trading_calendar"` |
| 归因月度行业 | 同上 `attribution_inputs.industry_classification` | 同工具 `dataset="industry_classification"` |

归因四类下载也传各自目录的 `data_version` 与 UUID `request_id`。不传客户持仓，不按股票裁剪回归样本，不猜版本。各包独立生成、每包不超过 1 GiB；有效包由已授权用户共享一小时，访问不延长有效期。目录查询只读清单；有导出请求才打包。过期或更新后用新请求编号，网络重试沿用同一编号。

1. 先检查已有数据，`<插件根目录>` 是包含 `plugin.json` 和 `shared/` 的安装目录：

   ```text
   python <插件根目录>/shared/dataset_transfer.py status --project <项目绝对路径> --dataset adjusted-prices --dataset trading-calendar --dataset listed-history
   ```

   归因按本次方法检查 `attribution-market`、`trading-calendar`，以及适用的 `barra-exposures`、`industry-classification`。已有 `ready` 数据直接使用；`refresh_required` 才重新取对应包。共享的交易日历无需因换 Skill 再复制一份。

2. 创建任务后，用**同一个 MCP 工具**仅传 `job_id` 查询。等实际 `ready`，取得本次授权下载任务。不要把目录覆盖、排队成功或 HTTP 202 当成文件已下载。
3. 将完整 ready JSON 作为数据通过可信、不回显的 stdin 交给：

   ```text
   python <插件根目录>/shared/dataset_transfer.py save --project <项目绝对路径> --job <任务ID>
   ```

   不把凭据写到聊天、文件、日志、命令行参数或代码文本。支持既有单次回环交接的宿主可用 `receive --project ... --job ...`；同一次工具调用中将任务交给接收器，不输出票据。等待进程返回 `ready`。
4. 下载器验证授权任务、总清单、每套清单、每个文件的大小和 SHA-256 后，放入 `xyquant-data/inputs/datasets/<数据集>/`。保留不含凭据的来源记录。下载临时包和替换备份在完成或可处理的失败后清理；已有正式数据不会因下载失败被删掉。

RankIC 配置使用 `data.origin="xyquant_server"` 时，代码从已校验的 RankIC 包取得股票范围，不接受配置中的 `verified=true` 代替来源验证。本地离线基础数据仍使用 `local_reference` 并明确填写参考代码范围。服务器来源验证只确认文件来源和完整性；各 `manifest.json` 中的覆盖、复权说明和日历缺口继续适用。

用户自带因子仍放在 `xyquant-data/inputs/rankic/`，自带持仓仍放在 `xyquant-data/inputs/holdings/`。预置持仓、基准权重及快照通过 `query_stock_data(dataset="attribution_bundle")` 与原有持仓下载器接收；不混入六套基础数据目录。

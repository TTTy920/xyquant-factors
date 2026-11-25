# IPO 数据获取

使用当前宿主已授权的 XYQuant MCP。未连接时按 [xyquant-login](../../xyquant-login/SKILL.md) 使用宿主授权入口，所需权限为 `research:read`。不要通过 SSH、开发机目录或其他账号的缓存替代本次授权。

1. 调用 `query_ipo_data(dataset="catalog")`，读取本次数据版本、上市日期范围、记录数及缺失字段。不要把缺失值解释为零，也不要把数据覆盖日期当作今天。
2. 调用 `query_ipo_data(dataset="bundle", data_version=<目录返回的版本>, request_id=<UUID>)`。网络重试沿用同一请求编号；版本变化或包到期后重新查询目录并生成新编号。
3. 用同一个工具仅传 `job_id` 查询，直到实际返回 `status="ready"`。目录查询不生成包；已授权用户共享固定一小时的包，访问不续期，到期自动清理，下次请求再读取当前工作簿。
4. 将完整 ready JSON 作为数据，通过可信、不回显的 stdin 交给下载器：

   ```text
   python <本Skill>/scripts/ipo_transfer.py save --project <已有项目绝对路径> --job <任务ID>
   ```

   不把下载票据写入聊天、命令行参数、日志、脚本字符串或文件。支持插件现有单次回环交接的宿主可用 `receive --project ... --job ...`，协议见 [交接说明](../../stock-holding-diagnosis/references/host-transfer.md)，命令换成本 Skill 的 `ipo_transfer.py receive`，不带 `--kind`。等待下载器实际返回 `ready`，HTTP 202 仅表示接受交接。
5. 下载器校验包和 Excel 的大小、SHA-256、版本与有效期，返回本地 `path`。原始 Excel 保存在项目 `xyquant-data/inputs/ipo/IPO发行数据.xlsx`，同目录保留不含凭据的来源记录。更新覆盖该受管理的数据文件，不按任务累积副本；临时 ZIP 完成后清理。用户明确指定的其他本地 Excel 按原工作流直接读取。
6. 将返回的 `path` 交给原始 `scripts/calculate.py --input`；资金规模、入围率、年度与板块筛选、输出规则仍按 Skill 正文执行。MCP 只提供数据，不计算收益。缺失记录数等数量以本次目录/脚本实际返回为准，不写死在结论中。

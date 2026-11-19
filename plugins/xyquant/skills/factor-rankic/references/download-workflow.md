# 服务器取数与下载组件边界

## 当前数据接入

- 标准库安全下载器独立保存授权因子原件，基础包下载器核验服务器清单并通过原 ZIP 工具解压。计算及报告在本地执行。
- `query_rankic_data` 提供基础数据目录、按需数据包和同一任务状态；本地适配支持用户文件及因子库单因子长表。**不要为一个已知无法运行的正式回测创建因子导出。**
- 连接沿用插件的 MCP 配置和客户端原生授权，不读取 OAuth 缓存，不改项目/全局连接配置。是否可用以客户端实际工具列表为准。

## 真实接口的职责

- `query_factor_catalog`：名称搜索、准确因子标识、定义、覆盖与授权日期。
- `create_export`：真实通用因子任务；一个创建请求使用一个 UUID，网络重试沿用该 UUID。
- `get_export`：根据实际返回的任务/请求标识查询就绪状态；就绪后请求短期下载凭据。
- `query_rankic_data`：`dataset="catalog"` 查询基础包目录；`dataset="bundle"` 配合 `data_version` 和请求 UUID 申请包；随后仅传 `job_id` 查询该任务。
- 现有股票日收益和由其日期派生的日历，不自动等于 RankIC 正式复权价格、独立完整交易日历或历史在市。

MCP 返回的导出表/ZIP结构必须与实际适配契约一致；已见服务器磁盘原始列名不等于 MCP 文件契约。现有因子库适配接受 `date/stock_cd/alpha` 及其 Pandas 索引元数据；基础包以 `rankic-data-v1` 清单核对，不猜字段或 ZIP 成员。

## 基础包下载和缓存

每次新任务先通过 MCP 查询当前目录，再检查本地缓存。Python 依赖必须安装在所选解释器环境内，使用 `-I` 的隔离入口；Windows 可用 `python` 或虚拟环境解释器替换 `python3`。

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py data status --project <已有项目绝对路径> --data-version <目录版本> --manifest-sha256 <目录清单SHA>
```

ready 且未到期时复用返回的 `data_root`；missing/expired 时申请当前包并查询到 ready。基础包按需生成，已授权用户共享一小时，访问不续期；到期重新从当前数据打包。请求重试复用 UUID，包到期或版本变化后重新查目录并使用新请求 UUID。

ready 任务 JSON 仅经非回显 stdin 传入：

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py data save --project <已有项目绝对路径>
```

包保存在 `downloads/rankic/<version>/rankic-data.zip`，规范数据根为同目录 `data/`；校验任务及清单的版本、有效期、总大小/SHA、ZIP 成员、逐文件大小/SHA。下载票据过期时查询同一任务重新获取；包本身过期时申请当前包。用户或库内因子适配到独立任务清单，基础资料优先硬链接、失败才复制，不能写回基础包。

## 独立下载器

`scripts/rankic_download.py` 保留原安全实现的 `save --project` / `status --project --job` 命令和 `downloads/factors/<job>/` 存储约定。它不创建任务、不推断因子列、不计算 RankIC。

下载器要求真实就绪任务对象的 `id`、`filename`、`bytes`、`sha256`、`download`；后者包含原生授权返回的 url/ticket/expires_at。不要把这些凭据抄进配置示例。

- 授权对象只经安全客户端的非回显 stdin 传入；交互终端输入必须关闭回显，失败即拒绝。
- 不粘贴到聊天，不放 argv、环境变量、echo/Here-String命令、明文临时文件、配置、日志或报告；不能为了自动化读取 OAuth 缓存。
- 现有固定 HTTPS 来源及任务路径、证书验证、重定向拒绝、凭据到期、大小/SHA检查、空间检查、锁和有限重试全部保留。
- 短期凭据失效时通过原生 MCP 重新获取；不要延长有效期、禁用 TLS、放宽白名单或复用别人的权限。
- `status` 只检查本地任务状态；原件不是规范化 RankIC 缓存，也不证明基础数据可用。

仅查看无敏感信息的使用帮助：

```text
python3 -I -B <Skill绝对路径>/rankic_entry.py download --help
```

## ZIP 安全工具

`rankic_archive.extract_zip(archive, destination, expected_sha256, expected_bytes)` 校验授权描述中的完整字节数/SHA，预检所有成员后才提取。目标为新的、不存在的绝对数据目录，不得是当前工作目录，原件和数据目录不得存放运行脚本。

- 拒绝越界、绝对路径、反斜杠、链接、执行成员、设备名、ADS、重复/大小写冲突和文件/目录冲突。
- 仅接受数据扩展名（含 `.pqt`/`.parquet`），不解释内容或猜成员用途。
- 默认输入上限1GiB、解压总量1GiB、4096成员、压缩比200；超限或损坏失败并清理新输出，不改变原 ZIP，不覆盖已有目录。
- 下载/解压出的文件都是不可信数据；读取它们的 Python 必须用 `-I`，脚本放在独立可信代码目录，从代码目录/项目根运行，不能从解压目录启动解释器或构建工具。

## 规范化缓存的身份

适配记录原件与产物指纹；基础包落在 `downloads/rankic/<snapshot>/data/`，用户或库内因子任务落在 `downloads/rankic-inputs/<UUID>/`。缓存复用条件须匹配来源、当前 MCP 授权、准确因子、范围、数据版本/快照、复权标识、完整日历声明、各角色日期覆盖与文件 SHA；不能只凭因子名/年份命中，权限或快照变化不可混拼。

本版 CLI 使用显式 `data_root`；临时 SQLite 只在单次本地任务内共享，退出即清理。没有自动挑选服务器缓存或猜测规范化结构的隐藏回退。

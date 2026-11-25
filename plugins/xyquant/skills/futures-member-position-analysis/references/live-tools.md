# 实时计算与 MCP 接入

## 可调用的两个工具

- `compute_futures_crowding(symbol, date?, windows?)`：原始指标1，默认回看60、120、250个交易日的经验分位。中文名或品种代码均可；不对原始值平滑、延续或取反。每个窗口返回有效样本数和缺失数；分位数定义为小于等于当前值的有效样本比例，含当日。
- `get_futures_member_heatmap(symbol, date?, lookback=20, top_n=5, scope="dominant", contract?)`：直接返回图片与元数据。商品默认固定目标日的主力单合约，回看同一合约，沿用原“商品期货热力图更新_中欧”口径；金融默认每日全部可交易合约。指定 `scope="strategy"` 时商品改为每日主力+次主力，匹配一致性策略口径，两者不可混称。

日期省略时，按北京时间19点以前排除当日、19点以后纳入当日的规则选择交易日；“最新”不等于周五。拥挤度若三张排名未齐，最多回退5个交易日，并返回实际日期。显式指定日期不自动回退缺失排名。非交易日按此前最近交易日处理，回答时说明实际日期。热力图窗口内任一必要合约排名缺失就报错，不压缩缺失交易日后仍宣称完整窗口。

热力图使用窗口内完整会员净持仓水平的 Pearson 相关性，按目标日净持仓升序排列。方框分别为净持仓较低/较高端，标签不保证是真正净空/净多。两端平均两两相关不含对角线：>=0.6“高度一致”、>=0.3“较为一致”、否则“分歧”，沿用原图阈值；相关性不可计算时标为无法判定，不填0。两端人数不足时会声明重叠。

## 管理员接入

这是私有后端计算服务，不是仅上传 SKILL.md 就能取数。MCP 服务运行 Python 3.11+；计算脚本可单独使用现有 Python 3.9+。部署依赖：

```bash
python -m pip install 'mcp>=1.28,<2' rqdatac pandas numpy matplotlib openpyxl
```

SDK v1 接口与要求见 [官方维护文档](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)。未开放对公网的 HTTP 服务；由平台在服务器侧作为 stdio 子进程启动：

```json
{
  "mcpServers": {
    "futures-member-position-analysis": {
      "command": "/path/to/python",
      "args": ["/path/to/futures-member-position-analysis/scripts/mcp_server.py"],
      "env": {
        "FUTURES_ENGINE_PYTHON": "/path/to/python-with-rqdatac",
        "FUTURES_CREDENTIALS_FILE": "/private/config/rqdatac.json",
        "FUTURES_CACHE_DIR": "/private/cache/futures",
        "FUTURES_OUTPUT_DIR": "/service/artifacts/futures",
        "MPLCONFIGDIR": "/private/cache/matplotlib",
        "PYTHONDONTWRITEBYTECODE": "1"
      }
    }
  }
}
```

私有凭据文件格式为 `{"accounts": [["米筐用户名", "米筐密码"]]}`，也可用环境变量 RQDATAC_USERNAME/RQDATAC_PASSWORD。文件只给服务账号读取（POSIX权限600，父目录700），不要放进分享包、skill目录、用户可读资源或客户端配置。备用账号仅在机器数超限时轮换；普通权限/网络错误不无限切号重试。

本机已有独立私有配置与启动脚本，均在 skill 目录外，不随压缩包分发。需要迁移账号时，由管理员通过私有渠道配置。管理员和服务进程本身能读取凭据；普通 MCP 调用者无法通过这两个工具获取配置。这个边界依赖平台确实将服务端配置与普通用户隔离，不能把包含凭据的服务器文件系统整体授权给用户。

服务不提供任意文件读取、环境查询、shell执行、账号设置工具，异常不回传底层日志或原始认证错误。MCP调用用户不能指定输出目录。对多用户开放时还需平台自己的访问控制及额度限制；本工具不自动发布公网接口。

## 本地命令与输出

```bash
python scripts/member_live.py M --mode both --output-dir /path/to/results
python scripts/member_live.py RB --mode crowding --windows 60,120,250 --output-dir /path/to/results
python scripts/member_live.py M --mode heatmap --lookback 20 --contract M2701 --output-dir /path/to/results
```

仅调用已授权行情接口，不修改原研究文件。输出固定名称的计算结果JSON、热力图PNG、净持仓和相关矩阵CSV；放进每次独立的输出目录。服务器缓存历史指标1，首次长窗口取数可能较慢，后续补齐新增日期。不得用组合净值或占位数据代替实时结果。

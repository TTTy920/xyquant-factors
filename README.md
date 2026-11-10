# XYQuant 因子与研究资料

XYQuant 为 Codex 提供因子数据和本地分析技能。请按电脑系统选择：

WorkBuddy 用户请看 [WorkBuddy 安装说明](docs/workbuddy.md)。两个客户端复用相同取数程序和 Skills，市场与启动配置分别提供兼容入口。

| 电脑 | 插件 | 下载与计算 |
| --- | --- | --- |
| macOS（Apple 芯片 / Intel） | [Mac 使用说明](plugins/xyquant-factors-macos/README.md) · [Mac 发布包](https://github.com/TTTy920/xyquant-factors/releases/tag/macos-v0.3.0-rc.6) | 随包运行本地归因，登录授权保存在系统钥匙串。 |
| Windows x64 | [Windows 使用说明](docs/customer-guide.md) · [Windows 发布包](https://github.com/TTTy920/xyquant-factors/releases/tag/v0.3.0-rc.6) | 随包运行本地归因，用系统 DPAPI 保护登录授权。 |

`0.3.0-rc.6` 为两端同步的预发布版本。两个完整插件提供相同的三个 Skill（因子查询与下载、Barra 归因、策略选股归因）和 16 个客户端工具。归因输入为本地持仓或评分资料，两个系统都可直接运行随包脚本。服务端逐次根据账号授权决定可用数据。[验证范围](docs/acceptance-0.3.0-rc.6.md)

## 访问入口

| 入口 | 用途 |
|---|---|
| [因子网站](https://47.103.215.251/factors/) | 登录、查看因子、预览数值及管理导出 |
| [研究中心](https://47.103.215.251/factors/research) | 策略介绍、原表表现、净值图表、团队介绍及受保护下载 |
| [注册](https://47.103.215.251/factors/apply) · [连接管理](https://47.103.215.251/factors/connect) | 注册账号、管理客户端授权 |
| [MCP 地址](https://47.103.215.251/mcp) | 插件连接的认证服务；浏览器匿名访问返回 401 属于预期行为 |

网站可打开不代表资料公开；实际数据请求需要有效客户身份和对应客户端授权。

## 安装与开始使用

支持 Windows 10/11 x64 的本地 Codex。插件自带运行程序，无需另装 Python、复制 API Key 或单独打开连接助手。

1. 在 Codex 插件市场添加本项目 GitHub 来源，选择 Windows 固定版本 `v0.3.0-rc.6`，安装 **XYQuant 因子**。使用已配置 Codex CLI 时：

   ```powershell
   codex plugin marketplace add https://github.com/TTTy920/xyquant-factors.git --ref v0.3.0-rc.6
   codex plugin add xyquant-factors@xyquant
   ```

2. 按 Codex 提示重新打开会话，在研究项目中说“连接我的 XYQuant 账号”。
3. **在运行插件的同一台 Windows 电脑浏览器中**打开授权页，登录自己的账号并确认范围。密码只输入网站；不要发到聊天。链接约五分钟有效。
4. 返回 Codex，提出需要的查询或下载，例如：

   > 搜索波动率相关因子，查看其中一个因子的说明和可查询日期。

   > 查询 vol_entropy 在 2026-09-11 的 000001.SZ 因子值，并将同条件完整数据下载到当前项目。

   > 搜索已发布策略，查看一个策略的说明、原表表现和净值，并介绍研究团队。

**登录和下载完成前保持当前 Codex 对话运行。** 已验证环境中的后台任务受宿主生命周期限制；退出可能中断未完成任务。不要用执行一次就退出的 `codex exec` 等待跨退出登录或下载。

## 功能

| 能力 | 工具或入口 |
|---|---|
| 登录、状态和断开连接 | `get_connection_status`、`start_login`、`get_login_status`、`disconnect` |
| 因子目录、说明和数值预览 | `list_factors`、`get_factor_info`、`preview_factor` |
| 因子完整导出、任务查询和本地校验下载 | `create_export`、`get_export`、`list_exports`、`download_export`、`get_download` |
| 策略目录、介绍、原表表现与净值分页 | `query_strategy_research`；不提供团队成员查询 |
| 历史统一搜索入口（按服务端开关） | `search_research` |
| 软件版本及研究发布事件 | `get_research_updates` |
| 完整净值 CSV、原始 Excel 及图表 | 受保护研究中心；研究工具返回网页入口 |

策略对比已下线：网页入口和专属 API 停止提供服务，远端 MCP 不再发现 `compare_strategies`。既有 `0.3.0-rc.1` 本地插件仍列出 20 个工具，其中对比入口只返回已下线说明，不返回对比数据；该记录对应当时的远端配置；实际开放工具以当前服务器设置为准。无需重新登录。本次没有更新客户端 EXE、标签或发布资产。登录管理和项目文件下载由各平台的本地插件完成。

因子预览最多 50 行，完整数据下载到 `<项目目录>/downloads/factors/<任务编号>/`，校验大小和 SHA-256 后才算完成。每个导出最多 10 个因子、200 个指定股票代码、5,000 万行、1 GiB、30 分钟；完成文件保留一小时。

`query_strategy_research` 不传 `strategy_id` 时搜索目录；指定目录中的 ID 后默认同时返回介绍和表现。`sections` 支持 `info`、`performance`、`nav` 及其组合，所有内容固定在同一已发布工作簿版本。后续净值页携带返回的 `version` 与 `next_offset`，每页最多 50 行。四个旧的独立查询入口及团队入口在远端保持关闭。

研究结果保留原表指标名称、统计期间、空值和警告，不重算回测。本版两个平台都使用统一策略查询入口 `query_strategy_research`，在 Skill 内计算选股归因。

统一搜索只显示现有授权可见的类型，翻页固定研究 `version` 和因子 `catalog_version`。发布更新区分软件版本、资料发布时间与原始净值截止日，翻页固定 `publication_cursor`；不会自动升级软件或申请新授权。

## 研究网站 A+ 界面

研究中心采用浅蓝工作台、侧边导航与响应式白色面板；登录、因子、连接和管理页面沿用统一视觉。首页年度图来自单个策略的原始年度表，保留完整年度／部分年度标记、来源和统计期间；研究方向分布统计当前目录数量。策略详情继续提供完整原表，空值不会显示为零。

策略和因子详情支持桌面侧栏与手机可展开面板；净值图增加触摸长按、时间范围和全屏。搜索、筛选与位置按账号记忆，退出时清除。手机快捷操作栏显示实际下载阶段并复用原任务。交互范围与限制见 [网页交互说明](docs/website-interactions.md)。

这是服务端网页资源更新，客户端仍为 `0.3.0-rc.1`，无需重新安装插件，也不会增加 OAuth 授权。图表展示规则、发布和验收范围见 [A+ 网页更新说明](docs/website-a-plus.md)。

## 账号与 OAuth

正常启用、未过期的新旧普通客户默认可查看和下载全部已发布因子、策略和团队资料，无需管理员逐项开通；后台后续调整、账号停用／到期和连接撤销仍会生效。管理后台权限独立，不向普通客户开放。

| OAuth scope | 客户端获准执行的操作 |
|---|---|
| `factors:read` | 因子目录、说明、预览及导出任务查询 |
| `factors:export` | 创建因子导出任务 |
| `factors:download` | 下载自己获准访问的导出文件 |
| `research:read` | 已发布策略、表现、净值和团队介绍（含联系方式） |

账号的数据权限与客户端 OAuth 授权分别检查。**升级插件或刷新旧令牌不会自动增加 `research:read`。** 遇到研究授权不足时，先明确同意替换当前连接，再断开并重新登录，在网页确认新增范围。断开影响同一 Windows 用户共享的连接，不删除已下载文件。

插件授权跟随账号有效期，续期后客户端向服务器核对并更新连接日期，短期访问令牌自动刷新。凭据由 Windows DPAPI 加密，默认保存在 `%LOCALAPPDATA%/XYQuant/FactorConnect`，不写入项目仓库或插件安装目录。

## 从旧版本升级

- 固定在旧标签的客户需在确认新 Release 已发布后，将 Windows 市场来源选择为 `v0.3.0-rc.6`，再按 Codex 提示更新或重新安装插件；只刷新旧固定标签不会变成新版。既有旧独立 MCP 入口保留，只有明确要求迁移时才移除。
- 新增工具需要客户端刷新工具列表，按界面提示重新打开会话。仅更新同一 Windows 用户的默认连接时，插件下一次调用会读取新凭据；已验证同一 `0.2.0-rc.1` 进程无需重启即可读取新连接。
- 服务端上线、GitHub 文档更新和本机连接切换是不同操作。网站更新不会替客户升级插件或同意 OAuth 授权。

## 文档与发布范围

- [客户使用说明](docs/customer-guide.md)：安装、网页登录、升级和常见问题。
- [研究工具说明](docs/research-mcp.md)：数据版本、统计口径和权限边界。
- [管理员说明](docs/admin-guide.md) · [旧独立连接迁移](docs/migration.md)。
- [本版候选验收](docs/acceptance-0.3.0-rc.6.md) · [早期验收记录](docs/acceptance.md)。
- [构建与发布](docs/releasing.md) · [更新记录](CHANGELOG.md)。
- [Mac 打包说明](scripts/macos/README.md)：Mac 包含三个 Skills，适配系统钥匙串、文件和进程；同一份 ZIP 包含两种芯片的运行环境。

公开仓库包含客户端源码、插件、合成测试和 Windows 构建产物。服务端源码、真实研究资料、客户身份和凭据留在私有环境。本分支提供 `.github/workflows/verify-windows.yml`：在干净 Windows 运行器测试、构建并上传候选制品，仅读仓库，不自动发布。是否已启用和通过以对应提交的 Actions 记录为准；没有运行的 CI 仍标为未运行。

`main` 上的说明会随验收补充更新；固定标签和发布包保留发布时快照。历史 `v0.2.0-rc.1` 的程序、标签或资产保持不变。客户端代码使用 [MIT 许可证](LICENSE)，不授予因子数据、账号或研究资料的再分发权。

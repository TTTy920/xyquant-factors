# XYQuant 因子 · macOS

Mac `0.3.0-rc.6` 包含三个 Skill：因子查询与下载、Barra 风格归因、策略选股归因。可以在对话里授权自己的 XYQuant 账号，把完整因子保存到研究项目，查询已发布策略，并解释某月股票入选或落选的评分依据。一个包包含 Apple 芯片和 Intel Mac 的运行环境（macOS 11 或以上；Codex 本身的系统要求另计）。不需要管理员权限、独立安装程序或系统 Python。

## 安装与首次使用

在 Codex 插件市场添加 GitHub 来源 `TTTy920/xyquant-factors`，选择版本 **macos-v0.3.0-rc.6**，安装 **XYQuant 因子 · macOS**。已添加过市场的用户请更新市场来源，再更新 Mac 插件。也可从 [GitHub Release](https://github.com/TTTy920/xyquant-factors/releases/tag/macos-v0.3.0-rc.6) 下载完整 ZIP 和校验文件。使用 Codex CLI 时：

```sh
codex plugin marketplace add TTTy920/xyquant-factors --ref macos-v0.3.0-rc.6
codex plugin add xyquant-factors-macos@xyquant
```

重开项目对话，说“连接我的 XYQuant 账号”。在同一台 Mac 的浏览器中打开插件给出的链接，用自己的网站账号登录并授权。授权保存在 macOS 登录钥匙串，同一系统用户下的项目共享。由旧浏览器下载版升级时，先通过新插件授权一次；无需复制密码、API Key 或旧令牌。首次启动会把随包运行文件解压到当前用户的“资源库/Application Support/XYQuant/FactorConnect”。登录或下载完成前保持当前对话打开。

| 需求 | Mac 版的行为 |
| --- | --- |
| 查询因子与研究资料 | 提供 16 个客户端工具，包括账号和下载管理。策略目录、介绍、原表表现和净值合并为 `query_strategy_research`，不再单列四个查询工具及团队查询。可用数据以服务端开放情况和账号权限为准。 |
| 将完整因子保存到当前项目 | 可说“把这些条件的全量数据下载到当前项目”。插件创建或复用导出、查询进度、按需重试，校验字节数和 SHA-256 后才报告成功，返回 `downloads/factors/<任务编号>/` 下的实际路径。也可在[因子网站](https://47.103.215.251/factors/)用同一账号打开“最近导出”，手动下载未过期的文件。 |
| 计算月度 Barra 归因 | 两端使用同一份 Skill 脚本。提供月末逐股持仓、月末股票风格暴露和月度因子收益，得到逐月组合暴露和次月各风格的收益贡献。使用随包解释器，不会把持仓发给服务器。自动获取 Barra 原始模型数据另行接入。 |
| 解释策略月度选股 | 提供本地完整候选组评分明细、名额、排序方向和评分规则，用 Skill 自带脚本在本机计算组内排名、名额线及主要驱动。保留并列待定、缺失分组及无记录情况；评分差不代表收益贡献。使用 `factor-bridge --selection 本地输入.json`，本版不自动下载评分原始明细。 |
| 管理账号和任务 | 可查看当前连接、重新授权、断开、找回自己的云端导出并跟踪本机下载。断开连接不删除已下载的文件。 |

如果宿主已有独立远程 MCP，也可以复制所需的完整 `skills/` 子目录到项目的 `.agents/skills/` 中。只复制 Skills 不会安装本机下载工具或解释器；这种用法继续通过浏览器下载，归因使用已有的 Python。要获得完整功能，请安装整个 Mac 插件，并避免重复加载同名工具。

本版为预发布版。验证范围见[验收记录](https://github.com/TTTy920/xyquant-factors/blob/main/docs/acceptance-0.3.0-rc.6.md)。源码和随包运行环境的许可见 [LICENSE](LICENSE) 和[第三方说明](THIRD_PARTY_NOTICES.md)。

插件授权有效期与账号有效期一致，账号续期后自动核对并更新本地记录。已经撤销或实际过期的连接需要重新登录；内部访问令牌继续自动刷新。

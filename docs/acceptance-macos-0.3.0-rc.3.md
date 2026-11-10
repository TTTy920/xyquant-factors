# Mac 0.3.0-rc.3 预发布验证

日期：2026-11-10。插件 `xyquant-factors-macos`，固定版本 `macos-v0.3.0-rc.3`。

本次仅移除测试用 `factor-count` Skill 及其打包引用，保留 `factor-download`、`barra-attribution`、`strategy-attribution` 三个 Skill。`list_factors` 和其他 MCP 工具保留。

验证已完成：

- 插件目录与完整 ZIP 都恰好包含上述三个 Skill，不包含 `factor-count`。
- ZIP 的 23 个成员通过完整性检查，逐一与插件目录匹配；四种清单版本均为 `0.3.0-rc.3`。
- 在 Apple 芯片 Mac 上用实际随包启动器验证 CLI 版本及 MCP 初始化，工具发现仍为 17 个，未调用远端数据工具。
- 桥接与上版逐模块比较，仅版本声明变化；业务源码及其余 Skill 无变化。
- 依赖锁未变，两种架构的 Python、依赖归档沿用已校验文件；WorkBuddy 配置一致性检查通过。

本次不重复运行与 Skill 删除无关的客户端或服务端回归；前版验证范围见 [0.3.0-rc.2 记录](acceptance-macos-0.3.0-rc.2.md)。未重测 Intel 实机、首次登录或真实下载，未构建 Windows，也未重部署服务器。

安装包：`xyquant-factors-macos-0.3.0-rc.3.zip`，66,984,855 字节。

SHA-256：`181973e4b2ef90f2a83d6220b17f61545d2ee1b58cf9ef2ad11c312fab153fa3`。

旧标签及旧发布资产保持原样。已安装旧版的客户端需要更新插件并重新打开会话，才能刷新 Skill 列表。

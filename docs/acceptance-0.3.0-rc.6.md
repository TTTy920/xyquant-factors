# Windows 与 macOS 插件验收 · 0.3.0-rc.6

两个发布包使用相同的因子查询下载、Barra 归因与策略选股归因 Skill。客户端声明 16 个工具。实际查询根据服务端开放状态和当前账号权限执行。

验证日期为 2026-11-10。

- Windows x64 的[原生构建与检查](https://github.com/TTTy920/xyquant-factors/actions/runs/42802991627)通过。实际 EXE 完成版本检查、MCP 握手和 16 个工具的发现，WorkBuddy 入口通过。构建来源为干净的候选源码树。
- Mac 包包含 Apple 芯片和 Intel 的运行环境。在 Apple 芯片 Mac 上从 ZIP 解压到新目录，实际启动器版本和 MCP 版本均为 0.3.0-rc.6，并发现相同的 16 个工具。
- 两端分别用合成的年末持仓跨年计算，十个因子的次月贡献均为 0.01。对同一份合成评分表，两端均返回指定股票排名第二、按规则未入选。
- 对两个完整 ZIP 核对了四份插件清单。三个 Skill 的七个文件逐字节相同，Windows 包中的 EXE 与原生构建产物的 SHA-256 一致。

| 文件 | SHA-256 |
| --- | --- |
| Windows 完整 ZIP | `83856ed4efc6e8311df5374ff4ef23a620267200661fef133230bd6b25ea8fa1` |
| Windows FactorBridge.exe | `cab23dbf0d996b99bd8c5c0d50489c0ef6b905a6c3a8487cb7d4c37031bf4380` |
| macOS 完整 ZIP | `68e48edfab571856568250000ddcba08234fadf8fb3bd08606fbf0682a2a1d3d` |

本轮在隔离目录中测试，未执行 Windows 桌面端首次网页授权、真实客户下载或 Intel Mac 实机验收。本版按预发布版分发。

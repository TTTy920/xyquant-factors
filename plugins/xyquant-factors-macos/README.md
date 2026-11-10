# XYQuant 因子 · macOS

这个包给 Mac 上的 Codex 添加两个 Skill。没有需要双击的安装程序，也不需要管理员权限。

| Skill | 使用方式 |
| --- | --- |
| 因子查询与下载 | 用自己的 XYQuant 账号连接 MCP，在对话里选因子、日期和股票。完整文件在[因子网站](https://47.103.215.251/factors/)用同一账号登录后，从“最近导出”下载。 |
| Barra 风格归因 | 提供本地月末持仓、个股月末暴露和月度因子收益。随包脚本在 Mac 上计算组合暴露和次月风格收益贡献；持仓不发送给 XYQuant。逐股暴露和因子收益的自动取数还未接入。 |

## 在 Codex 中启用

在 Codex 的插件市场添加来源 `https://github.com/TTTy920/xyquant-factors.git`，选择 **XYQuant 因子 · macOS**。如果已经添加过 XYQuant 市场，刷新它后选择 Mac 版。首次取数时通过 Codex 连接 XYQuant，在打开的网页用自己的账号授权。只做已有数据的本地归因时不用登录。

已经安装 Codex CLI 的用户也可以运行：

```sh
codex plugin marketplace add TTTy920/xyquant-factors --ref macos-v0.1.0-rc.1
codex plugin add xyquant-factors-macos@xyquant
```

重新打开研究项目的对话即可选择 Skill。查询和浏览器下载不需要 Python；运行归因时需要本机或客户端提供 Python 3.7 以上（只用标准库）。Apple 芯片与 Intel 芯片共用这一份包。

## 已有 MCP 连接，只想使用 Skills

也可以直接解压 ZIP，把 `skills/factor-download/` 和 `skills/barra-attribution/` 两个完整目录放进研究项目的 `.agents/skills/`。重开该项目的 Codex 对话即可从中选择；这种方式不会替你新增 MCP 连接。不要同时用两种方式加载同一技能。

拿到数据后，例如可以说：“用这三张本地表做 Barra 归因，告诉我哪几项风格对次月收益贡献最大。”浏览器下载的文件保存到浏览器所选的位置；本版不会在后台写入研究项目。

此版为候选包，尚未进行 macOS 安装、登录或运行验收。插件源码按 MIT 许可分发，数据访问仍使用各自账号的授权。

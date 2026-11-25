# RankIC 环境与数据迁移说明

插件分发代码、依赖声明、测试、四频示例参数和原验证资料。基础面板通过 MCP 按需下载，用户因子留在本地；虚拟环境、下载任务、已生成报告和授权配置不随插件分发。

## 数据和任务

- 基础数据、因子库原件分别按 SKILL.md 的 MCP 流程取得；用户已有本地因子直接适配。实际数据根由下载器或适配器返回，不依赖开发者路径。
- `examples/floatcap-*.json` 保留原始四频参数和离线路径模板。助手在用户项目生成本次配置并替换 data_root，不要求客户手写配置。
- 明确的本地离线测试可使用原参考包 `data/local-reference/market-202001-202610/`；原包数据不随插件安装。不能把服务器包改标 local_reference，也不把离线样例作为正式任务失败后的替代。
- 原 FloatCap 样例只有2025年3月；源价格和历史在市覆盖2020-01-02至2026-10-30。原四频各期348个历史在市未知，预期 partial_success；未来数据更新后的覆盖以当前目录为准。
- 普通计算不读原 HDF5。重新导出基础数据的开发工具需另备合法源 HDF5 及 h5py，不属于客户运行依赖。

## 新电脑运行

使用 Python 3.10+ 的兼容环境；首次安装依赖可能需要网络。不要跨电脑搬运虚拟环境。创建虚拟环境后，用其解释器安装 requirements.txt，依赖不能只装在 pip --user 目录。

```text
python3 -m venv .venv
.venv/bin/python -m pip install -r <Skill绝对路径>/requirements.txt
.venv/bin/python -I -B <Skill绝对路径>/rankic_entry.py rankic run --config <本次任务配置绝对路径> --project <已有项目绝对路径>
```

Windows 对应解释器为 `.venv\Scripts\python.exe`，创建环境可用 `python -m venv .venv`。服务器取数沿用客户端原生 MCP 授权，不复制其他电脑的票据。

日、周、月、季样例的原有效期数为21、4、1、1。每次输出 report.md、rankic.xlsx、metadata.json，并在对话中给出摘要和三份链接。具体交付规则见 [SKILL.md](SKILL.md)。

## 开发验证资料

开发回归依赖 requirements-dev.txt，执行方法见 [README](README.md#测试与输出)。原始基线、导出记录和迁移重放资料位于 verification。原八次重放工具需要独立工作副本中的本地参考数据；它不下载数据，也不用于修改服务器来源标签。

MIGRATION-MANIFEST.json 保存输入迁移包的原始文件清单和摘要，供追溯使用；它不是当前插件包的完整性清单。当前插件包完整性使用发布 ZIP 对应的 SHA-256 文件。历史记录里的开发目录和旧输出路径不是当前运行依赖。

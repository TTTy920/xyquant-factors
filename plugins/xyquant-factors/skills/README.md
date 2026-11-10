# XYQuant 插件技能

MCP 为插件提供数据。分析计算由各 Skill 自带的代码在用户本机完成。

- [因子查询与下载](./factor-download/SKILL.md) 支持按因子、日期和股票代码查询数据，也可以将因子数据下载到研究项目。
- [Barra 组合风格归因](./barra-attribution/SKILL.md) 在本地用逐股持仓、股票风格暴露和月度因子收益计算两张归因表。原始因子的取数接口待接入。

新增技能时，在这里建立与 `factor-download/` 同级的目录，并在其中编写 `SKILL.md`。

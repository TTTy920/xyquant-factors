# 插件接入客户端前置能力：审查与验证记录

日期：2026-11-18。工作目录：独立 RankIC 目录；不是 Git 仓库，不存在用于本轮比较的 base/head 提交。原插件只读。本记录仅覆盖客户端前置能力，不是正式服务器接入验收。

## 审查范围

- 新增 `scripts/rankic_workflow.py` 与 `tests/test_rankic_workflow.py`。
- 新增 `rankic_entry.py` 与 `tests/test_rankic_entry.py`。
- `verification/verify_catalog.py` 的新入口调用与搬迁复制改动。
- README、接入状态文档及参数样例的实际能力边界。

未修改计算引擎、Store/来源准入、下载器、数据、四份本地样例配置或历史数值基线。未实施真实文件适配、正式缓存/授权准入、宿主安全下载桥、真实服务器联调或插件发布。

## 规格审查

### 已确认并修复：URI 因子名准入不完整

原检查只拒绝包含 `://` 的名称，`data:`、`file:/`、`mailto:`、协议相对地址和 UNC 引用仍能作为成功参数保存及回显；预检虽然始终阻塞，但名称与无凭据任务边界不符合要求。

TDD 过程：

1. 新增 prepare 拒绝五类地址、以及 preflight 拒绝旧任务内非法 URI 的两项回归。
2. 修复前实际运行21项测试，出现6个预期 assertion failures：5个 prepare 子用例错误返回0，1个 preflight 用例错误返回 blocked 而非 error。
3. 增加 trim 后 URI scheme、协议相对地址和 UNC 前缀拒绝，保留原控制字符和 `://` 检查；没有更改服务器闸门。
4. 修复后21项全部通过、0跳过。独立规格复核再次运行21项，全部通过，确认该问题闭环。

独立规格审查对隔离入口也给出 PASS：实际 -I、非隔离时在非内建导入前固定拒绝、词法路径检查、固定目标、可信 scripts 导入、UTF-8、业务退出码、污染和搬迁均符合本轮要求。该审查独立运行入口23项：21通过、2项文件 symlink 因权限跳过。

## 代码质量审查

独立静态质量审查已完成，在本轮五个运行/测试文件中未确认到需要修复的问题。重点核对了部分参数、任务身份与持久化、错误不回显、固定阻塞、隔离导入和业务状态透传。该结论仅针对所审文件快照；动态验证以主会话的测试日志和重放证据为准。

## 主会话最终验证

- [最终完整测试日志](unit-tests-plugin-client-foundation-final.txt)：191项，187通过、4跳过，58.687秒，退出码0；新增 URI 两项均通过。
- 4项跳过：此前被阻止创建的项目 MCP 配置检查，以及入口文件、目标模块文件、Parquet 面板的3项 symlink 测试。目录 scripts/代码根/祖先的 junction 拒绝测试实际通过；不以静态检查冒充跳过项动态通过。
- [新入口真实重放](plugin-integration-client-replay.json)：原目录日/周/月/季及搬迁后四频共8次，status/summary/periods/return_range 精确匹配历史基线；所有 HDF 导入、禁止目录访问及网络计数为0。
- 原目录实际生成4组三份报告，期数21/4/1/1；各期348个 unknown_universe，均保持 local_reference / partial_success，不降低阈值或缩池掩盖。
- 当前清单与计算引擎、来源准入、Store、历史基线及 FloatCap 文件 SHA 均与实施前指纹一致。清单所列全部10个文件（7个市场年分片、日历、因子、provenance），共113,570,202字节，逐文件大小与完整 SHA 全部匹配未变化的 catalog。
- 实际调用 validate_origin 拒绝 xyquant_server；在临时合成 catalog 中填写服务器 scope 与 verified 也被 Store 拒绝。没有放宽来源准入。
- 原插件工作树干净，仍为 czx / 99cb2ba，较 origin/czx ahead 1；本轮没有提交、推送、打包或部署。

## 不属于已验证结论的事项

- -I 防护不能补救非隔离解释器在入口执行前可能执行的 sitecustomize；可信安装代码和解释器是信任前提，不承诺抵御可信代码根被并发恶意改写。
- 普通因子名称参数必须无凭据；URI 拒绝不是任意秘密识别器。没有传递或使用真实票据。
- Excel 仅核对 Python 缓存，没有独立表格引擎重算。
- 当前会话没有可用的 XYQuant 查询/导出工具和可读取的 schema 资源，不等于已证明服务器缺工具或账号未授权。
- 真实服务器契约、基础数据出口、授权版本关系和宿主安全下载交接仍未核验；本轮没有通过 SSH、OAuth 缓存或自填标记绕过。

恢复材料及边界见 [正式接入状态](../references/server-integration-status.md)。

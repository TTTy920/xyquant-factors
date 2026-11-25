# RankIC 插件接入：客户端前置能力 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 在当前会话执行，不创建新的会话或 worktree，不提交 Git。

**Goal:** 交付缺参数整理、不可绕过的服务器预检和受信任隔离启动入口；为后续真实接口适配提供可测试的客户端起点，不冒充完成正式服务器接入。

**Architecture:** 保留原计算引擎、schema v1、本地参考数据和服务器固定拒绝。新增 workflow 只整理已给参数并生成无凭据任务；preflight 从白名单任务字段重新计算阻塞状态，永不接受用户填写的 verified/ready/capabilities。根目录启动器必须在 Python -I 下使用，只将本发行包自己的 scripts 加入导入路径。

**Tech Stack:** Python 3.10+，stdlib，现有 Parameters/SourceFiles 与 unittest；不增加依赖，不读 OAuth/私钥，不访问网络。

---

## 已确认边界和未确认事项

2026-11-18 用户已批准开始实施接入设计。本会话实际工具列表未提供 XYQuant 查询/导出工具，MCP 资源列表没有返回 schema 资源；这不证明服务不存在，只说明本会话不能核实其当前契约。项目中未发现服务端源码或可证明已实现的宿主安全交接器。

因此完整设计的 A 阶段真实 schema、基础数据出口、权限链和安全交接核验仍阻塞；B（若需补服务）、D（真实联调）、E（同步正式插件与启用）不执行。C 中真实文件适配、正式 scope/schema v2、正式缓存匹配依赖上述资料，也不猜测实现。当前仅实施与外部契约无关的参数准备、固定拒绝预检、隔离启动和其回归验证；不代表 A 或 C 整体完成。

原插件 `D:/XYQuantPlugin` 保持只读。独立目录不是 Git 仓库，不初始化仓库、不提交或推送，也不复制尚未完成的接入能力到插件。

## 文件结构与职责

| 文件 | 操作与责任 |
|---|---|
| `scripts/rankic_workflow.py` | 新建：参数白名单、任务创建/读取、固定拒绝 preflight CLI |
| `rankic_entry.py` | 新建：检查 -I 和受信任代码路径，固定分发到 rankic/workflow；不接收任意模块名 |
| `tests/test_rankic_workflow.py` | 新建：参数、任务、阻塞、凭据拒绝、读写故障和网络/导出不触发 |
| `tests/test_rankic_entry.py` | 新建：真实子进程、导入污染、环境污染、搬迁及状态码 |
| `verification/verify_catalog.py` | 修改：八次真实样例重放改走新隔离入口，搬迁复制该入口 |
| `examples/server-request-name-only.json` | 新建：只有因子名称的无凭据参数样例，不是服务器因子数据 |
| `README.md` | 修改：实际新命令、仅参数准备与 blocked 的含义 |
| `references/server-integration-status.md` | 新建：本轮真实核验状态、已交付能力、恢复正式适配所需材料 |
| `verification/plugin-integration-client-check.json` | 新建：本轮测试、重放与来源闸门证据，不覆盖旧证据 |

## Task 1: 参数白名单与固定拒绝预检

**Files:** Create `scripts/rankic_workflow.py`; Test `tests/test_rankic_workflow.py`。

- [x] 写测试，先以模块不存在的明确 assertion 观察 RED，不因缺模块让整个测试发现中断。
- [x] 固定任务输入为 `{"parameters": {...}}`。允许的参数仅为 Parameters 的 factor_name/start/end/frequency/pool/codes/min_stocks；未知根字段和未知参数字段拒绝，错误不得回显字段值或输入内容。
- [x] 复用 Parameters 做完整及部分参数校验：部分参数只在校验内部补临时值，输出不含这些临时值；默认 min_stocks=20；自定义代码规范化排序去重，不推断缺失的 pool；自定义池缺 codes 列为缺项。
- [x] 公开函数 `normalize_parameters(value)` 返回 `(parameters, missing)`。预期核心用例：

```python
parameters, missing = normalize_parameters({'factor_name': '流通市值'})
assert parameters == {'factor_name': '流通市值', 'min_stocks': 20}
assert missing == ['start', 'end', 'frequency', 'pool']
```

- [x] 固定预检函数 `assessment(parameters)` 返回由代码计算的状态，不读取外部 verified。完整参数也必须 blocked，且 `can_create_export=False`、`can_run=False`。阻塞代码固定为 `server_adapter_not_released`、`factor_not_resolved`、`authorization_not_checked`、`secure_handoff_not_verified`；缺参数时另含 `parameters_incomplete`。
- [x] 拒绝 URL/控制字符等明显不是因子名称的输入；普通中文名称、空白修剪可接受。不把因子名称整理当作准确服务器 ID 解析。
- [x] 运行单项测试 GREEN；日期、股票类型与 min_stocks 的语义沿用现有 Parameters，不修改引擎。

运行：

```powershell
.\.venv\Scripts\python.exe -I -B -X utf8 -m unittest discover -s .\tests -p test_rankic_workflow.py
```

## Task 2: 无凭据任务持久化与 CLI

**Files:** Modify `scripts/rankic_workflow.py`; Test `tests/test_rankic_workflow.py`。

- [x] 先写 RED：准备任务隔离、原请求不变、preflight 只读、任务篡改/损坏/超限拒绝、写失败清理、不能覆盖已有任务。
- [x] `prepare(project, request)` 严格检查 root 只有 parameters；仅写入 `downloads/rankic-tasks/<UUID>/request.json`。项目必须已存在、绝对、本地、无 .. 或链接/重解析点；目录逐级创建并检查，不跳过已存在目录的类型检查。
- [x] 保存内容只含 schema_version=1、UUID、origin=xyquant_server、清洗后的 parameters；不保存运行资格、下载描述、授权标记、凭据或能力声明。新任务 UUID 目录 exclusive 创建；写失败仅清理本次新任务，不改已有任务。
- [x] `preflight(project, task_id)` 验证 UUID/文件位置、文件上限256KiB、UTF-8、无重复JSON键或非有限JSON数值；使用现有 SourceFiles 检查读取前后文件状态。重新校验任务白名单与参数并调用 assessment，不信任文件里的额外通过标记。
- [x] CLI：`prepare --request <JSON路径> --project <绝对目录>` 与 `preflight --task <UUID> --project <绝对目录>`。prepare 成功退出0（只表示准备完成）；preflight blocked 退出2；所有错误返回无输入回显的固定 JSON，且无 traceback。普通参数错误由自定义 argparse.error 转为固定安全错误。
- [x] 运行测试 GREEN；使用 socket 和下载函数断言确认没有网络、导出/下载或报告创建；不创建 .mcp.json。

## Task 3: 受信任 -I 入口

**Files:** Create `rankic_entry.py`; Test `tests/test_rankic_entry.py`。

- [x] 写子进程测试 RED：`python -I ... rankic_entry.py rankic inventory/run`、workflow prepare/preflight；无 -I 拒绝；不允许任意模块；项目/PYTHONPATH 中的 json.py、sitecustomize.py、rankic_engine.py 均不执行。
- [x] 启动器只依赖 stdlib。检查 sys.flags.isolated；检查自身、scripts 及其所有已有父路径不是 symlink/reparse point；拒绝不安全代码根。只用固定映射加载模块：

```python
TARGETS = {'rankic': 'rankic', 'workflow': 'rankic_workflow'}
```

- [x] 仅把本包 scripts 的绝对路径放入 sys.path，设 dont_write_bytecode；从固定目标调用 main(argv)。绝不把客户项目、data_root 或下载目录加进导入路径。入口自身错误为固定 JSON 和退出2，不回显用户目标名或异常。
- [x] 小型合成数据真实运行后比较旧 CLI 与新入口的 status/summary/periods/return_range；success/no_result/blocked 退出码保持各自语义。
- [x] 搬迁代码到新目录再运行；不硬编码原独立项目路径。原 CLI 保留兼容，不直接为其加 -I 后假装同目录 imports 自动可用。

## Task 4: 文档、真实重放与记录

**Files:** Modify `verification/verify_catalog.py`, `README.md`; Create `references/server-integration-status.md`, `verification/plugin-integration-client-check.json`。

- [x] verify_catalog 的 CHILD 去掉 scripts 路径注入，改调用 rankic_entry.py；搬迁时复制该文件。原八次比较及 HDF/原目录/网络 guard 保留，不改基线。
- [x] README 展示实际 prepare/preflight 命令，并标明准备状态不是服务已接入；普通样例也给出 -I 入口命令。SKILL.md 本轮不改成已支持正式接入，不新增不存在的 MCP 名称或参数。
- [x] 状态文档区分“本会话未能核验”和“已证明服务缺失”；列出需要原生 MCP 当前 schema、正式基础数据契约、真实导出样本、授权版本关系和宿主安全交接的证据。不得通过 SSH 或 OAuth 缓存补齐。
- [x] 完整回归：

```powershell
.\.venv\Scripts\python.exe -I -B -X utf8 -m unittest discover -s .\tests -p "test_rankic*.py"
```

- [x] 八次真实隔离重放：

```powershell
.\.venv\Scripts\python.exe -I -B -X utf8 .\verification\verify_catalog.py --evidence .\verification\plugin-integration-client-replay.json
```

- [x] 记录真实测试数、跳过、来源闸门仍拒绝、新文件SHA、原插件工作树未改。不得用旧测试数字替代本轮结果。
- [x] 使用 requesting-code-review 进行本轮代码只读审查，确认发现后逐项复现修复并重跑；不合并、不提交、不推送。

## 完成与恢复条件

当前交付必须可单独运行和验证，但只标为“客户端前置能力完成”。不能标为正式服务器接入、完整阶段 C 或整份接入方案已完成。

正式工作恢复须由原生客户端实际提供 XYQuant 工具与 schema，并能取得经授权的契约和样本；确认安全交接后，另写针对真实结构的适配、正式缓存/权限准入和联调任务。缺正式出口或宿主能力时，先按设计阶段 B 申请具体实现/部署授权。本轮不为解除阻塞而虚构这些能力。

# 咕嘎内核套壳评估：DSH / pi / 排除项

日期：2026-11-12
范围：只做调研，不改 core/、ui/、tools/ 任何代码，不重启咕嘎。
结论先行，证据在后。所有版本号、日期、URL 均为本次实际抓取或本机实测；无法核实的一律标注"未核实"。

---

## 0. 结论（先读这段）

**要不要套壳：要，但只套 DSH，不套 pi。**

| 问题 | 答案 |
|---|---|
| 现在该不该把内核换成开源 harness | 该换，但换的是"多步任务由谁规划"，不是"整个 self_agent.py 删掉重写" |
| 套哪个 | **DSH**（本机已装 0.2.1-rc.5，MIT，三条接缝已在仓库里跑着） |
| pi 呢 | **不现在做**。pi 没有 Python 绑定、也没有自带控制面，引入它等于在同一台机器上再养一个 Node 运行时，收益是"设计更干净"，成本是"多一层进程和协议" |
| OpenHands / SWE-agent / Aider | 排除，理由见 §4（世界模型是代码仓库，不是 Windows 桌面） |
| 最该先做的一件事 | 不是套壳，是**先删 `_local_model_vote` 死代码 + 把快路径从"能吞就吞"改成"默认不接"**。这两件事不改架构、当天能做完，且是套壳的前提 |

一句话理由：咕嘎今天的问题不是"缺一个 agent 内核"，而是**三条执行路径（快路径 / 本地 agent / DSH）之间的判定权不清**。DSH 已经是这三者里唯一具备"规划-执行-事件流-会话持久化"完整闭环的那个，且接缝已经存在；pi 是一条更干净但要重建接缝的新路。先修判定权，再谈换芯。

---

## 1. 咕嘎现状：本机核实的真实形态

这一节是后面所有成本估计的基准。全部来自本机代码，行号可查。

| 模块 | 事实 | 证据位置 |
|---|---|---|
| `core/self_agent.py` | 700 行；工具表 `TOOLS = [...]` 在第 92 行，是 **OpenAI function-calling 形状**（`payload.update(tools=TOOLS, tool_choice='auto')`）；分派在 617 行 `elif name in {t['function']['name'] for t in TOOLS}` | self_agent.py:92、347-348、617 |
| `core/task_router.py` | `choose_task_route()` 是纯正则 + 固定优先级：显式 dsh > 工作流 > `_is_code_task()` > 本地；**不依赖任何模型连接** | task_router.py:79-115 |
| `core/task_router.py` | `_local_model_vote()` 定义在 38 行，**全仓库零调用点**（唯一命中是它自己的 def，以及 `runtime/public-source-*-smoke-*` 下的整仓快照副本） | task_router.py:38；递归 grep 结果仅 1 处定义 |
| `core/fast_commands.py` | `match()` 在 111 行；`_needs_agent()` 在 101 行，**唯一调用点 205 行**；另有一条复合任务正则守卫在 138 行 | fast_commands.py:101、111、138、205 |
| `core/dsh_worker.py` | `DshManager(QObject)`，进程模型 = `powershell.exe -NoProfile -Command "& dsh --profile <p> --json -"`，任务写 stdin，stdout 逐行 JSON，两个线程 + `queue` 读，`seconds` 超时，`CREATE_NO_WINDOW`；冻结构建改走 `vendor/node.exe + dsh/lib/bin.js`；派单时强制 `DSH_PERMISSION_MODE=danger-full-access` | dsh_worker.py:84、269-330 |
| `core/super_agent/dsh_gateway.py` | HTTP 面：`POST /api/<method>`，operator cookie 由 `GET /connect?token=<launchToken>` 换得；已封装 `session/create`、`commands/execute`（用来跑 `/permission <preset>`）、`prompt`、`ensure_full_access`、`health` | dsh_gateway.py:8-10、43-44、428-437、478 |
| `core/mcp_contract.py` | 咕嘎**自己就是一个 MCP server**：`pipe_name()` 生成 `guga-self-<sha256[:20]>`；`extension_tools()` 用标准 `inputSchema` 暴露 `screen_context` / `desktop_command` / `lingshu_memory` / `dsh_gateway` | mcp_contract.py:8-14、17-40 |

**关于"快路径会吞掉复合任务"的准确表述**：代码里**已经有**守卫，不是完全没有防护。第 138 行专门拦"搜索/打开前缀 + 后续写入/发送/保存"这类复合句，第 205 行用 `_needs_agent()` 判断"第二段是不是真活"，注释也写明了 `"打开微信，找到小军" is one task with two steps`。问题是这套守卫是**逐条正则打补丁**：每出现一种新的复合说法就得加一条规则，而且 `_needs_agent` 只有一个调用点，其余分支（如 `dsh_link` 状态查询、`daily_plan`、`browser_step`、`desktop_step`）都是**直接 return，不过守卫**。所以准确说法是：**结构性风险在"前缀匹配 + 尾部白名单"这个设计本身，不在某一条漏网的正则**。

---

## 2. 路线 A：DSH 作为内核（推荐）

### 2.1 它到底是什么（本机 + 上游双向核实）

| 项 | 事实 | 来源 |
|---|---|---|
| 版本 | `0.2.1-rc.5` | 本机 `dsh --version` 实测；package.json |
| 许可证 | MIT | package.json；GitHub API `license.spdx_id = MIT` |
| 上游仓库 | `deepseek-ai/deepseek-harness`，默认分支 **master**（不是 main），TypeScript | GitHub API |
| 活跃度 | `pushed_at` 2026-11-11T11:32:04Z；创建于 2026-08-13；GitHub API 读数 278,865 stars / 34,305 forks | GitHub API，2026-11-12 抓取 |
| 入口模式 | `dsh <profile>` / `--profile headless "job"`（跑一个持久化 session、打印最终答案、退出）/ `--profile sdk`（JSON-RPC stdio 常驻）/ `--profile acp`（ACP stdio）/ `dsh web` | 本机 `README.md` |
| headless 包自述 | "the dsh one-shot bundle: a direct core Agent/Session runner over dsh-base with **no Host, HTTP, or browser layer**" | dsh-headless/package.json |
| SDK 包自述 | "the dsh SDK profile bundle: **stdio JSON-RPC serving** and process lifecycle over dsh-base" | dsh-sdk-app/package.json |
| MCP 面 | `dsh-mcp-client`："**MCP client bridge: connects to MCP servers and registers their tools on `ctx.tools`**" | dsh-mcp-client/package.json |
| pi 关联 | `dsh-llm-pi-ai`："pi-ai-backed DeepSeek adapter for the DeepSeek Harness LLM seam (design-verification twin of dsh-llm-deepseek)" → **DSH 在 LLM 层已经吃过 pi 的 ai 包** | dsh-llm-pi-ai/package.json |

**SDK 协议面（这是"能不能当内核"的关键）**，来自本机 `dsh-sdk-protocol/README.md`：

- 传输：**newline-delimited JSON-RPC 2.0 over byte streams**；`id + method` = 请求，`id` = 响应，`method` = 通知；未注册方法回 `-32601`，handler 异常回 `-32603`
- 方法集：**7 个 client→server 请求 + 4 个 server→client 通知**；已列出的是 `initialize`、`session/prompt`、`session/resume`、`session/wait`、`session/working-directory/get`、`session/working-directory/set`（第 7 个未逐条列出，**未核实**）
- `dsh-sdk-jsonrpc-server`：**每个 `sessionId` 开一个 agent**，把用户 prompt 排队，**把所有 session 事件和 agent 状态变化流式推回客户端**；`stdout` 只走 JSON-RPC 帧，所以部署时**不能挂 stdout logger**

这条协议对咕嘎的意义：**Qt 侧需要的"发一句 → 收事件流 → 中途插话"三件事，协议层原生支持**（`session/prompt` + 通知流 + `session/resume`）。而且仓库里有官方 **Python SDK**（本机多个 README 都指向 `../../../python/README.md`，并提到 Python wheel 会复制 `ripgrep` 原生二进制）——**但 pip 包名与安装方式本次未核实**。

### 2.2 接入成本

**进程模型（三选一，可并存）**

1. **一次性管道（现状，零新增）**：`dsh --profile <p> --json -`，stdin 喂任务、stdout 读 NDJSON。已跑在 `dsh_worker.py` 里。缺点：一问一答，中途不能插话，会话续接靠 `--session-id`。
2. **常驻 stdio JSON-RPC（推荐中期目标）**：`dsh --profile sdk`，一个 sessionId 常驻一个 agent，事件流天然可映射到 `chat_render` / `task_report`。缺点：要新写一个 Python 客户端（或接官方 Python SDK）。
3. **HTTP 3080（现状，零新增）**：`POST /api/session/create` + `POST /api/commands/execute`。已跑在 `dsh_gateway.py` 里。缺点：依赖 web 进程在跑，且要处理 operator cookie。

**改哪些文件（按侵入度排序）**

| 文件 | 改动性质 | 说明 |
|---|---|---|
| `core/task_router.py` | 改判定权 | 从"本地 / DSH 二选一"改成"能力分级"：快路径 → 本地 agent → DSH。同时删掉 `_local_model_vote` |
| `core/fast_commands.py` | 改默认值 | `match()` 从"能匹配就 claim"改成"证明能做完才 claim"，`_needs_agent` 提为所有分支的统一出口 |
| `core/self_agent.py` | 收缩职责 | 不再承担"多步工程任务"，只留桌面快路径与失败升级；工具表若迁到 MCP 则 `TOOLS` 改为从 `mcp_contract` 派生 |
| `core/dsh_worker.py` | 复用为主 | 已有 `--json -` 管道可直接作为 P1 的执行器；若切 sdk profile 需加常驻进程管理与重连 |
| `core/super_agent/dsh_gateway.py` | 复用为主 | HTTP 面已封装 `session/create` / `commands/execute` / `prompt` / `health`，无需新写 |
| `core/mcp_contract.py` | 复用为主 | 咕嘎工具面已经是标准 MCP server，DSH 侧用 `dsh-mcp-client` 直接挂 |
| `core/dsh_sdk_client.py` | **新增**（仅当走 stdio SDK） | JSON-RPC 客户端 + 事件→Qt 信号映射 |

**工作量估计（人工日，属估算不是实测）**：P1 路线（沿用一次性管道 + 改判定权）约 3-5 人日；切常驻 stdio SDK 再加 5-8 人日；工具面全量迁 MCP 再加 5-10 人日（取决于 `self_agent.py` 那 700 行里有多少工具是 UI 耦合的）。

### 2.3 风险

1. **RC 版本**：`0.2.1-rc.5` 是预发布。升级时 `engines.dsh` 范围检查会卡住不兼容插件（本机 README 明确写了这条），需要显式版本豁免。
2. **上游不接受外部反馈**：GitHub API 显示 `has_issues: false`、`has_pull_requests: false`、`open_issues_count: 0`。**出问题不能提 issue/PR，只能自己 patch 或绕路**。这是选它当内核最大的单点风险。
3. **权限面已经是最宽的**：`dsh_worker.py` 派单时硬编码 `DSH_PERMISSION_MODE=danger-full-access`，等于"全盘可写 + 永不询问"。当执行器时这是对的；当**常驻内核**时，任何一次误判都会直接落到文件系统上。套壳后必须把这个权限从"全局默认"改成"按任务授予"。
4. **双运行时体积**：冻结构建要带 `vendor/node.exe` + `runtime/dsh-home` 模板，安装包会显著变大。
5. **stdout 协议纯洁性**：SDK 模式要求 stdout 只有 JSON-RPC 帧，任何 `print` 都会污染协议。咕嘎的 Python 侧不会碰 DSH 的 stdout，但如果未来给 DSH 挂插件，这条要守住。

### 2.4 最小验证步骤（30 分钟内可做，不重启咕嘎）

- **V1（协议通不通）**：起一个 `dsh --profile sdk` 子进程，发 `initialize` + `session/prompt`，确认能收到 session 事件通知且 stdout 无杂质。**判据**：收到 ≥1 条事件通知、进程干净退出。
- **V2（能不能替代快路径 + 本地 agent 的失败升级）**：用现成 `dsh_gateway.py` 的 `health()` → `create_session()` → `prompt()`，对 3080 投一句已知会被快路径吞掉的复合任务（例："打开微信，找到小军，告诉他我晚十分钟"），观察它是否**一次规划完成**而不是只做第一步。**判据**：整句被作为一个任务处理，且中途有进度事件。
- **V3（工具面能不能挂上）**：把 `mcp_contract` 的 `guga-self-<hash>` pipe 作为 MCP server 配进 DSH 的 `dsh-mcp-client`，确认 `screen_context` / `desktop_command` 出现在 DSH 的工具列表并可调用。**判据**：DSH 侧能 list 到咕嘎工具并成功 call 一次。

---

## 3. 路线 B：pi（可嵌入，但不建议现在做）

### 3.1 真实形态

| 项 | 事实 | 来源 |
|---|---|---|
| 仓库 | `badlogic/pi-mono` **已迁移**到 `earendil-works/pi`（fetch 旧地址 200 重定向到新仓库） | web_fetch 实测 |
| 自述 | "AI agent toolkit: unified LLM API, agent loop, TUI, coding agent CLI" | GitHub API |
| 许可证 | **MIT** | GitHub API `license.spdx_id = MIT` |
| 语言 / 活跃度 | TypeScript；创建 2025-08-09；`pushed_at` 2026-11-11T16:19:50Z；121,423 stars / 15,650 forks | GitHub API，2026-11-12 抓取 |
| 最新版本 | **v1.5.2**，`published_at` **2026-11-10T12:25:43Z** | GitHub releases API |
| npm | `@earendil-works/pi-agent-core@1.5.2`，MIT，`engines.node >= 22.19.0`，unpackedSize 274,063，maintainers 含 `badlogic` / `mitsuhiko` / `rwachtler` | registry.npmjs.org |
| 包布局 | `agent`（核心）/ `ai`（LLM）/ `mcp` / `codemode` / `coding-agent`（CLI）/ `tui` / `durable` / `schema` / `telemetry` / `evals` / `env` / `chord` | GitHub contents API |

**可嵌入性（这是本次最关键的核实项）**，来自 `packages/agent/README.md` @ v1.5.2：

- 库入口：`import { Agent } from "@earendil-works/pi-agent-core"`，`new Agent({ initialState: { systemPrompt, model, tools, messages }, streamFn })`
- 方法：`prompt()` / `continue()` / `abort()` / `waitForIdle()` / `steer()`（运行中插话）/ `followUp()`（跑完后追加）/ `subscribe()` / `reset()`
- **事件流（10 类）**：`agent_start`、`agent_end`、`turn_start`、`turn_end`、`message_start`、`message_update`（仅 assistant，带 delta）、`message_end`、`tool_execution_start`、`tool_execution_update`、`tool_execution_end`
- **工具协议**：`AgentTool { name, label, description, parameters /* 任意 JSON Schema */, execute(toolCallId, params, signal, onUpdate), executionMode }`；**工具失败必须 throw**（不要返回错误字符串当 content）；返回 `terminate: true` 可跳过后续自动 LLM 回合
- **钩子**：`beforeToolCall`（可 `{block: true, terminate: true}` 拦截）、`afterToolCall`（可改写 result）、`prepareRequest`（每次请求前重建上下文）、`finishTurn`（`{action:'end'|'continue'}`）
- **MCP**：`@earendil-works/pi-mcp` 连 MCP server；`@earendil-works/pi-codemode` 让模型写 JS 调工具，两者都能包成 `AgentTool`
- **低层**：`agentLoop()` / `agentLoopContinue()` 是"观察性"流，不等待异步处理完成；需要 barrier 语义时用 `Agent` 类
- **未核实**：① `pi-coding-agent` 是否提供 headless / JSON 服务模式（本次只核实了 `packages/agent` 的库 API）；② pi 是否有 Python 绑定（**无证据表明有**，npm 包是纯 ESM/TypeScript）。

### 3.2 接入成本

**进程模型**：pi **没有**自带控制面（无 stdio server、无 HTTP server 的公开说明），所以接入咕嘎必须**自己写一个 Node sidecar**：Qt/Python 侧 → (stdio 或 HTTP) → sidecar（持有 `Agent` 实例）→ pi 事件流。这等于在已有的 DSH Node 运行时之外**再养第二个 Node 运行时**。

**改哪些文件**：

| 文件 | 改动性质 |
|---|---|
| `tools/pi_sidecar/`（新增） | 全新 TS/JS 程序：实例化 `Agent`、把 10 类事件转成 JSON lines、把咕嘎工具包成 `AgentTool` |
| `core/pi_bridge.py`（新增） | Python 侧进程管理 + 事件→Qt 信号映射 |
| `core/self_agent.py` | `TOOLS` 是 OpenAI function 形状，pi 要 `parameters` 是 JSON Schema——**字段本身兼容，但分派层（617 行那套）要整体重写** |
| `core/mcp_contract.py` | 复用：pi-mcp 可直接吃咕嘎的 MCP server |

**工作量估计**：sidecar + 桥 + 事件映射约 8-12 人日；把 700 行 `self_agent.py` 的桌面语义迁到 pi 的工具模型再加 10-15 人日。**比 DSH 路线贵，且贵在"重建接缝"上。**

### 3.3 风险

1. **重复运行时**：DSH 已经是 Node，再引 pi 就是 Python + Node×2。桌宠的启动时间和内存都要为此付账。
2. **Node >= 22.19.0 硬要求**：与 DSH 的 Node 版本约束叠加，冻结包要同时满足两者。
3. **pi 的主线是编码 agent**（`coding-agent` + `tui`），桌面/管家语义（窗口、UIA、日程、语音播报）**完全没有**，这些全要自己造——也就是说，pi 只能替代"工具调用循环"这一层，而这一层恰好是咕嘎**已经写出来且不算最烂**的部分。
4. **版本节奏快**：v1.5.2 于 2026-11-10 发布，仓库次日仍在推。追版本有成本。

### 3.4 最小验证步骤

- **V1（库可用性）**：临时目录 `npm i @earendil-works/pi-agent-core@1.5.2`，跑 README Quick Start + 自定义一个 `AgentTool`（mock 掉 `screen_context`），确认事件顺序符合 `agent_start → turn_start → message_* → tool_execution_* → turn_end → agent_end`。
- **V2（工具面打通）**：`@earendil-works/pi-mcp` 指向 `mcp_contract.pipe_name()` 的 pipe，确认能 list/call 咕嘎工具。
- **判据**：V1 事件顺序正确 **且** V2 能调通，才值得进入"是否替换"的讨论。**本次建议不做到这一步**。

---

## 4. 排除性说明（只排除，不展开）

这三个都是成熟项目，但它们的**世界模型是代码仓库**，不是 Windows 桌面，接缝根本不匹配：

- **OpenHands**：为"沙箱内自主写代码 / 跑容器"设计，核心假设是容器 + 仓库快照。桌面 GUI 操作不是它的输入面，套到桌宠上要重写整个执行层。
- **SWE-agent**：面向 issue→patch 的单点修复，工具面是 shell + 文件编辑，没有屏幕、窗口、UIA、日程的概念。
- **Aider**：单仓库配对编辑的 CLI，没有 agent 服务面、没有多步任务与会话管理。作为"内核"等于没有内核。

一句话：咕嘎的世界是"用户的 Windows 桌面 + 他的日程 + 他的聊天记录"，不是 git repo。选型要看**谁的工具面里已经有屏幕和窗口**——目前只有 DSH（通过 `dsh-mcp-client` 挂咕嘎自己的 MCP server）满足。

---

## 5. 建议实施顺序

**P0（本周，不改架构，当天可完成）**

1. 删 `task_router.py:38-76` 的 `_local_model_vote`——零调用点的死代码，留着会误导后来人以为"路由有模型参与"。
2. `fast_commands.py`：把 `match()` 的默认值从"能匹配就 claim"改成"证明能做完才 claim"，`_needs_agent()` 提为**所有分支的统一出口**（现在只有 205 行一个调用点）。
3. 把已知被吞的复合句固化成回归用例（放测试目录，**不动 core/**）。

**P1（两周内）**

4. `task_router` 从"二选一"改成"能力分级"：快路径 → 本地 agent → DSH；本地 agent 失败后**自动升级**到 DSH，而不是现在这样靠正则预先判断。
5. `self_agent.py` 收缩为"桌面快路径 + 失败升级"，多步规划整体交给 DSH。

**P2（P1 之后评估）**

6. DSH 从一次性管道切到 `--profile sdk` 常驻 stdio JSON-RPC（事件流才能映射到 Qt 的实时播报）。
7. pi 只作为 `self_agent.py` 重写的**设计参照**（事件流分层、工具协议、钩子设计都很干净），**不作为依赖引入**。

---

## 6. 明确结论

- **现在做不做**：做，但做的是**收口判定权**，不是**换芯**。
- **套哪个**：**DSH**。理由：本机已装、MIT、三条接缝（`--json -` 管道 / 3080 HTTP / stdio SDK）已在仓库里跑着、`dsh-mcp-client` 能直接挂咕嘎现成的 MCP server。
- **怎么接**：短期沿用 `dsh_worker.py` 的 `--json -`；中期切 `--profile sdk` 的 stdio JSON-RPC（一个 sessionId 常驻一个 agent，事件流可映射 Qt）；长期把工具面收敛到 `mcp_contract.py` 的 MCP server。
- **不做什么**：不为"内核现代化"引入 pi 的第二个运行时；不把 OpenHands / SWE-agent / Aider 纳入候选；不在 P0 完成前动 `self_agent.py` 的架构。

---

## 7. 未核实 / 待办清单

| 项 | 状态 |
|---|---|
| DSH Python SDK 的 pip 包名、版本、安装方式 | **未核实**（仓库有 `python/`，本机 README 多次引用 `../../../python/README.md`，但未读其内容） |
| DSH SDK 协议第 7 个 client→server 方法 | **未核实**（README 只逐条列了 6 个） |
| `dsh --json -` 这个 flag | **本机代码在用**（dsh_worker.py:311），但官方 README 只写 `dsh --profile headless "job"`，flag 语义**未在文档核实** |
| `commands/execute` 的完整参数面 | 只从咕嘎代码看到 `{...}` 跑 `/permission <preset>` 的用法，**完整 schema 未核实** |
| pi `coding-agent` 是否提供 headless/JSON 服务模式 | **未核实** |
| pi 是否有 Python 绑定 | **无证据表明有**（npm 包为纯 ESM/TypeScript） |
| pi 各子包（mcp/codemode）的独立版本号 | 未逐包核实（发布节奏与核心包一致，但未验证） |
| web_search | **不可用**（DeepSeek 搜索端点 401，API key 失效）；本文档所有外部信息均经 `web_fetch` 直取 GitHub API / npm registry，属联网核实，非搜索摘要 |

---

## 8. 证据附录（可复现）

**外部（web_fetch，2026-11-12 抓取）**

- `https://api.github.com/repos/earendil-works/pi` → MIT、TypeScript、created 2025-08-09、pushed_at 2026-11-11T16:19:50Z、121,423 stars
- `https://api.github.com/repos/earendil-works/pi/releases/latest` → `v1.5.2`，published 2026-11-10T12:25:43Z
- `https://api.github.com/repos/earendil-works/pi/contents/packages` → agent / ai / mcp / codemode / coding-agent / tui / durable / schema / telemetry / evals / env / chord
- `https://api.github.com/repos/earendil-works/pi/contents/packages/agent/package.json?ref=v1.5.2` → `@earendil-works/pi-agent-core` 1.5.2、MIT、`node >=22.19.0`
- `https://api.github.com/repos/earendil-works/pi/contents/packages/agent/README.md?ref=v1.5.2` → Agent / 10 类事件 / AgentTool / 4 个钩子 / pi-mcp / pi-codemode / agentLoop
- `https://registry.npmjs.org/@earendil-works%2Fpi-agent-core/latest` → 1.5.2 已发布、maintainers `badlogic`/`mitsuhiko`/`rwachtler`、unpackedSize 274,063
- `https://api.github.com/repos/deepseek-ai/deepseek-harness` → MIT、TypeScript、默认分支 `master`、pushed_at 2026-11-11T11:32:04Z、278,865 stars、`has_issues:false`、`has_pull_requests:false`
- `https://github.com/badlogic/pi-mono` → 200 重定向至 `earendil-works/pi`

**本机（可复跑）**

- `dsh --version` → `0.2.1-rc.5`
- `...\node_modules\@deepseek-ai\dsh\README.md` → 入口模式表（headless / sdk / acp / web）、`engines.dsh` 兼容检查
- `...\@deepseek-ai\dsh-sdk-protocol\README.md` → NDJSON JSON-RPC 2.0、方法表、错误码
- `...\@deepseek-ai\dsh-sdk-jsonrpc-server\README.md` → 一 sessionId 一 agent、事件流回推、stdout 只走帧
- `...\@deepseek-ai\dsh-headless\package.json` → "no Host, HTTP, or browser layer"
- `...\@deepseek-ai\dsh-mcp-client\package.json` → "registers their tools on `ctx.tools`"
- `...\@deepseek-ai\dsh-llm-pi-ai\package.json` → "pi-ai-backed DeepSeek adapter"
- 咕嘎仓库：`core/task_router.py:38,79-115`、`core/fast_commands.py:101,111,138,205`、`core/self_agent.py:92,347,617`、`core/dsh_worker.py:84,269-330`、`core/super_agent/dsh_gateway.py:8-10,43-44,428-437,478`、`core/mcp_contract.py:8-14,17-40`

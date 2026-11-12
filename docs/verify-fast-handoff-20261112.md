# 独立验收报告：快速路由交棒 + 浏览器窗口激活 + 字段名归一化

- 验收人：verifier（独立验收，不采信实现者自述）
- 时间：2026-11-12 00:20—00:55（Asia/Shanghai）
- 仓库：`C:\Users\Admin（无密码）\.zcode\workspace\default\guga-penguin-pet`
- 解释器：`C:\Users\Admin（无密码）\AppData\Local\Programs\Python\Python312\python.exe`（未使用 3.13）
- 限制遵守：未重启咕嘎；只写了 `docs/verify-fast-handoff-20261112.md` 与 `tools/test_verify_handoff.py`；未改 `core/`、`ui/`、其他 `tools/` 文件。

> 说明：验收期间 lead 根据本报告的 §5.1、§5.3 证据继续修了两个同类残留（见 §7 addendum）。本报告已按**最终代码**更新，所有最终数字来自最后一次复跑。

## 结论摘要

| 验收项 | 结果 |
|---|---|
| 指定 6 模块基线（最终状态） | **69 tests，OK，退出码 0** |
| lead 指定复跑命令（5 模块） | **46 tests，OK，退出码 0** |
| 独立对抗用例（新增 23 条） | **23/23 OK，退出码 0** |
| 补充验收：self_agent 预算/超时 | **通过**（needs_full_budget 5 条 + timeout 2 条 + 既有 7 tests OK） |
| 回归：`remaining` 依赖 | 无测试依赖；`ui/agent_chat.py:1859` 残留死代码（不崩溃，见 §5.4） |
| 回归：test_window_control 旧断言 | 更新合理，与新契约一致 |
| test_computer_fill 的 `0 != 4` | **既有失败，与本次无关**（该测试与 `core/computer_control.py` 对 HEAD 零 diff） |
| 依赖模块回归扫描（188 tests） | 1 条失败在 `test_screen_share` 主动交流路径，**不在本次六个文件的调用路径上**（见 §4.4） |
| 本次改动导致的失败 | **0 条** |

**判定：本次改动通过指定验收标准。** §5.1、§5.3 两条同类残留已在验收期间修复并复验通过；剩余可疑点见 §5。

## 0. 验收范围与前提

本次改动文件（`git diff --stat`，最终状态；另 `tools/test_fast_handoff.py` 为新增未跟踪文件）：

```
 core/browser_interaction.py  | 15 ++++++-
 core/desktop_interaction.py  | 42 +++++++++++++++++++
 core/fast_commands.py        | 34 ++++++++++++---
 core/self_agent.py           | 98 +++++++++++++++++++++++++++++++++++++++++-----
 tools/test_window_control.py |  6 +--
 5 files changed, 174 insertions(+), 21 deletions(-)
```

**重要前提（如实声明）**：该 worktree 在本次任务前已有 **108 项未提交改动**，且 `core/self_agent.py` 的 diff 中包含大量既有改动（agent_harness、dsh_gateway 等）。因此无法用 git 单独切出"本次改动"的完整补丁；本报告对**行为**负责，回归归因用 import 关系与调用路径证据，而不是作者归属。lead 在验收期间对 `core/self_agent.py`、`core/browser_interaction.py` 的追加修改已单独复验（§7）。

## 1. 指定命令（最终状态）

命令（在仓库根目录执行）：

```
C:\Users\Admin（无密码）\AppData\Local\Programs\Python\Python312\python.exe -m unittest tools.test_fast_handoff tools.test_window_control tools.test_agent_handoff tools.test_command_intent_repair tools.test_browser_responsiveness tools.test_task_router -q
```

真实输出：

```
----------------------------------------------------------------------
Ran 69 tests in 0.515s

OK
EXITCODE=0
```

说明：验收过程中该命令的测试数随 lead 追加测试而变化——最初 `Ran 67 tests / OK`；lead 第一次补充 self_agent 改动后 `Ran 68 tests / OK`；最终（追加搜索框归一化断言后）`Ran 69 tests / OK`。三次退出码均为 0。

## 2. 独立对抗用例

新增 `tools/test_verify_handoff.py`（仅此一个测试文件，23 条）。运行：

```
C:\Users\Admin（无密码）\AppData\Local\Programs\Python\Python312\python.exe -m unittest tools.test_verify_handoff -q
```

真实输出：

```
----------------------------------------------------------------------
Ran 23 tests in 0.034s

OK
EXITCODE=0
```

覆盖内容与结论：

| # | 用例 | 断言 | 结果 |
|---|---|---|---|
| 1 | `'打开浏览器，在输入框中输入 咕咕嘎嘎'` | `matched=False`、`action='none'`、无 `remaining` 键、`requires_execution=True`、`choose_task_route().executor='local_agent'`、`category='local_task'` | PASS |
| 2 | `'打开记事本，好吗'` / `'打开浏览器，谢谢'` | `matched=True`、`action='open_app'`；记事本 `target='记事本'` | PASS |
| 3 | `'打开微信，找到小军'` | `matched=False`、`requires_execution=True`、路由 `local_agent` | PASS |
| 4 | `'帮我打开浏览器'` | `matched=True`、`action='open_app'`、`target='浏览器'` | PASS |
| 5 | `'打开微信，帮我发消息给小军'` / `'打开浏览器，在输入框中输入咕咕嘎嘎'`（无空格）/ `'打开记事本，写一段文章'` | 均 `matched=False` 且 `requires_execution=True`（不再只做一半） | PASS |
| 6 | `parse_step('在浏览器的输入框中输入 咕咕嘎嘎')` | `fill_named`、`target='输入框'`、`text='咕咕嘎嘎'` | PASS |
| 7 | `parse_step('在浏览器的输入框中输入咕咕嘎嘎')`（无空格） | `target='输入框'`、`text='咕咕嘎嘎'` | PASS |
| 8 | `parse_step('在浏览器的搜索框中输入 咕嘎')` | 前置窗口词被剥离，`target='搜索'`、`text='咕嘎'` | PASS |
| 9 | `parse_step('在用户名输入框中输入 咕嘎')` / `('在密码框中填写 abc123')` | 显式字段名保留：`用户名` / `密码` | PASS |
| 10 | `needs_full_budget('打开浏览器，在输入框中输入 咕咕嘎嘎')`、`('打开微信，找到小军')`、`('打开微信，帮我发消息给小军')`、`('打开记事本，写一段文章')` | 均为 `True` | PASS |
| 11 | `needs_full_budget('帮我打开浏览器')` | `False` | PASS |
| 12 | mock `SelfAgent._request`，`_routine_request=False` | `requests.post(timeout=(6,60))` | PASS |
| 13 | mock `SelfAgent._request`，`_routine_request=True` | `requests.post(timeout=(6,30))` | PASS |
| 14 | mock `_activate_browser`：前台是自身 pid，浏览器窗口存在且最小化 | 返回浏览器 hwnd；`ShowWindow(hwnd, SW_RESTORE)` 与 `SetForegroundWindow(hwnd)` 都被调用 | PASS |
| 15 | mock `_activate_browser`：前台是**其他 pid** | 返回 `None`；`SetForegroundWindow` 零调用；`list_windows` 零调用（不抢焦点） | PASS |
| 16 | mock `_activate_browser`：自身 pid 前台但无浏览器窗口 | 返回 `None`，不调 `SetForegroundWindow` | PASS |
| 17 | mock `_activate_browser`：窗口列表里只有自身 pid 的窗口 | 自身进程窗口被过滤，返回 `None`，不抢焦点 | PASS |
| 18 | mock `_activate_browser`：前台 hwnd 为 0 | 返回 `None`，不调 `list_windows` | PASS |
| 19 | mock `_activate_browser`：`SetForegroundWindow` 抛异常 | 返回 `None`，不向外抛异常 | PASS |
| 20 | mock `_activate_browser`：`SetForegroundWindow` 调了但焦点始终没落上 | 有界轮询后返回 `None`（monotonic 打桩，未真实等待 1.5s） | PASS |
| 21 | mock `DesktopInteraction.execute`：`kind='browser'` | `_activate_browser` 被调用一次，返回的 hwnd 传给 `_guard(hwnd=4242)` | PASS |

（第 8、10 条中的搜索框与"发消息/写一段"断言，是 lead 按本报告 §5.1/§5.3 修复后追加的复验用例，见 §7。）

mock 方式：`patch.dict(sys.modules, {'win32gui': ..., 'win32process': ..., 'win32con': ...})` + `patch('core.window_inventory.list_windows')`，不触碰真实桌面。

## 3. 补充验收：self_agent 预算与超时（lead 第二轮改动）

代码位置（最终工作区）：

- `core/self_agent.py:268-273`：`_MULTI_STEP` 正则 + `needs_full_budget()`；最终正则含 `输入|键入|发消息|发条消息|发个消息|写一段|写一篇|写个`。
- `core/self_agent.py:385-389`：`multi_step=needs_full_budget(prompt)`；`_routine_request=route_category=='local_task' and len(prompt)<100 and not multi_step`；routine 步数上限 4、操作上限 6。
- `core/self_agent.py:430-435`：routine 预算 `min(budget, 45)`（`elif self._guided_local` 封顶 180）。
- `core/self_agent.py:353`：`timeout=(6, 30) if routine else (6, 60)`。

与 HEAD 对比可复核 lead 所述"改动前"的值（HEAD 版本）：

```
timeout=(4, 12) if routine else (6, 60)
multi_step=bool(re.search(r'然后|接着|之后再|再帮|再发|找到|搜索|查找|发送|回复|填写|上传|下载|选择|切换|先.{1,12}再',prompt))
budget=min(budget,20)
```

即：读超时 `(4,12)→(6,30)`、routine 预算 `20→45` 秒、正则补 `输入|键入`（后又补 `发消息/写一段` 等），均与 lead 自述一致。

最终复跑（lead 指定命令）：

```
命令：...python.exe -m unittest tools.test_verify_handoff tools.test_fast_handoff tools.test_window_control tools.test_self_agent_payload tools.test_self_agent_loop -q
Ran 46 tests in 0.055s
OK
EXITCODE=0
```

其中单独跑 `tools.test_self_agent_payload tools.test_self_agent_loop`：

```
Ran 7 tests in 0.015s
OK
EXITCODE=0
```

（lead 曾自述"33 tests OK"；我实测 payload+loop 两个模块合计 7 条。数字口径不同，但"全绿"结论一致。）

**未验证项（截至本报告）**：我没有独立复现 `_run()` 里 `_routine_request` 分支的真实模型端到端行为（需要运行中的咕嘎与真实 API，且被限制不得重启）。lead 在报告之后补做了真实 E2E，属 lead 自述证据（§5.6）。

## 4. 回归检查

### 4.1 是否还有代码/测试依赖 `command['remaining']`

grep 范围：`core/`、`ui/`、`tools/` 下全部 `.py`。与快速命令相关的命中只有：

```
ui\agent_chat.py:1859: if result.get('ok') and command.get('remaining'):
ui\agent_chat.py:1861: message=...+str(command['remaining'])[:24]+...
```

结论：

- `core/fast_commands.py` 已不再向返回 dict 写入 `remaining`（新逻辑里 `remaining` 只是 `_needs_agent` 的局部变量）；
- 除 `ui/agent_chat.py` 外，没有其他代码或测试读取该键；`test_window_control.py` 的旧断言已删除；
- 因此 `ui/agent_chat.py:1859-1862` 现在是一条**不可达的"只做一半"提示分支**（无生产者）。它不会崩溃，但属于未清理死代码（§5.4）。

### 4.2 test_window_control 旧断言改动是否合理

`git diff -- tools/test_window_control.py`：

```
-        compound=match('打开微信，找到小军')
-        self.assertEqual(compound['action'],'open_app')
-        self.assertEqual(compound['target'],'微信')
-        self.assertEqual(compound['remaining'],'找到小军')
+        # 有真实第二动作的复合句现在整句交给 agent，不再只做一半。
+        self.assertFalse(match('打开微信，找到小军')['matched'])
```

结论：**合理**。旧断言编码的是被替换掉的旧契约（只做一半 + `remaining`），新断言与新契约一致，且与 `tools/test_fast_handoff.py` 的独立断言互相印证。唯一小瑕疵：该测试方法名仍叫 `test_inverted_and_compound_app_requests_stay_local`，而现在其中一条复合句断言的是"交给 agent"，方法名与语义不再匹配（纯命名问题，不影响功能）。

### 4.3 test_computer_fill 的 `0 != 4` 是否与本次改动无关

复现（单独运行）：

```
...python.exe -m unittest tools.test_computer_fill -q
FAIL: test_click_and_unicode_type_consume_one_observation (tools.test_computer_fill.FillTests.test_click_and_unicode_type_consume_one_observation)
  File "...\tools\test_computer_fill.py", line 37, in test_click_and_unicode_type_consume_one_observation
    self.assertEqual(self.mocks[2].call_count,4) # UTF-16 units, including surrogate pair
AssertionError: 0 != 4

Ran 9 tests in 0.369s
FAILED (failures=1)
EXITCODE=1
```

归因证据：

- `git diff --stat -- core/computer_control.py tools/test_computer_fill.py` 为空，`git status --porcelain` 对这两个文件也为空 → **两者与 HEAD 完全一致**，失败在 HEAD 上即存在；
- 失败测试走 `ComputerControl._execute(action='fill')` 的 SendInput 计数路径，不 import/不调用本次六个文件（同文件里 import `fast_commands.match` 的是另一个测试方法，且按字母序在该失败用例之后运行）。

结论：**既有失败，与本次改动无关**，按要求只记录不修。

### 4.4 依赖模块回归扫描（额外做的）

对 `tools/` 下其余 import 了 `core.fast_commands` / `core.browser_interaction` / `core.desktop_interaction` 的测试做了一轮扫描：

```
命令：...python.exe -m unittest tools.test_daily_plan tools.test_demo_capabilities tools.test_desktop_interaction tools.test_fast_and_speaker tools.test_music_performance tools.test_personal_harness tools.test_screen_share tools.test_voice_assistant tools.test_voice_conversation_controls tools.test_window_discovery -q
Ran 188 tests in 16.199s
FAILED (failures=1)
EXITCODE=1
```

唯一失败：`tools.test_screen_share.ProactiveTests.test_offline_memory_initiative_uses_memory_and_speaks_when_idle`，断言主动交流提示词里应包含记忆内容 `'AI 相关的学习资料'`，实际提示词里没有。可单独稳定复现（`Ran 1 test ... FAILED`）。

归因证据（**不是本次改动引入**）：

- 失败路径是 `core/proactive_conversation.py` 的 `tick()` → `memory.proactive_topics(...)`；该模块只 import stdlib/requests/PyQt5，**不 import 本次六个文件中的任何一个**；
- `core/proactive_conversation.py` 与 `tools/test_screen_share.py` 相对 HEAD 均无改动；
- 该测试方法不调用 `match()`（`test_screen_share.py` 第 13 行 import `match` 只用于别的测试类）。

无法在含 108 项未提交改动的 worktree 上取得"改动前"干净基线，因此不能断言它是改动前就红；但可以断言：**本次六个文件不在这条失败路径上，归因本次改动不成立**。疑似与既有未提交改动（如 `core/agent_memory.py`）或该测试本身的历史漂移有关，建议另行 triage。

## 5. 可疑点与残留风险（如实记录）

### 5.1 `needs_full_budget` 的同类漏判 —— 已修复并复验

初验时发现：

| 句子 | 当时 `needs_full_budget` | 后果 |
|---|---|---|
| `打开微信，帮我发消息给小军` | **False**（"发消息"不在正则） | 误走 routine：4 步上限 / 45 秒预算 |
| `打开记事本，写一段文章` | **False**（"写一段"不在正则） | 同上 |

lead 按此证据把 `发消息|发条消息|发个消息|写一段|写一篇|写个` 补进 `_MULTI_STEP`。我复验（最终代码）：

```
needs_full_budget('打开微信，帮我发消息给小军') = True
needs_full_budget('打开记事本，写一段文章')     = True
needs_full_budget('打开记事本，写一篇报告')     = True
needs_full_budget('打开记事本，写个便签')       = True
needs_full_budget('帮我打开浏览器')             = False
```

结论：**已修复，复验通过**。剩余同类风险：不在关键词表里的其他第二动作仍可能漏判（启发式固有边界），建议后续用真实失败样本继续补词。

### 5.2 `needs_full_budget` 过触发（低风险）

`needs_full_budget('打开浏览器，然后呢')` 为 `True`（正则命中"然后"），而 `match('打开浏览器，然后呢')` 为 `matched=True` 走快路径，因此当前无功能影响；只是该函数把语气尾巴也算作多步，属于保守误报。

### 5.3 字段名归一化只覆盖通用"输入框" —— 已修复并复验

初验时发现 `parse_step('在浏览器的搜索框中输入 咕嘎')` 得到 `target='浏览器的搜索'`，未被归一化。lead 按此证据在 `_field_name` 中增加"剥离前置窗口限定词"（`浏览器|窗口|页面|网页|当前页面|当前窗口` + 可选 `的/里的/中的`）。我复验（最终代码）：

```
parse_step('在浏览器的搜索框中输入 咕嘎')  -> target='搜索'
parse_step('在浏览器的输入框中输入 咕嘎')  -> target='输入框'
parse_step('在窗口的输入框中输入 你好')    -> target='输入框'
parse_step('在页面的搜索框中输入 咕嘎')    -> target='搜索'
parse_step('在用户名输入框中输入 咕嘎')    -> target='用户名'（显式字段名保留）
```

结论：**已修复，复验通过**。`'搜索'` 在 `BrowserInteraction.execute` 的 fill_named 通用输入集合内，会走 `page_input(search=True)`，不会再按错误名称找控件。

### 5.4 `remaining` 消费分支成为死代码

见 §4.1：`ui/agent_chat.py:1859-1862` 已无生产者。建议清理（不在本次允许改动范围内，未动）。

### 5.5 测试方法命名

`tools/test_window_control.py:46` 方法名 `test_inverted_and_compound_app_requests_stay_local` 与其内部"复合句交给 agent"的新断言语义不符，纯命名问题。

### 5.6 端到端覆盖

本报告独立验证到"路由层交棒"（`match` 未命中 → `uses_task`/`requires_execution` 为 True → `choose_task_route=local_agent`），并用 mock 验证了窗口激活与超时参数；`_activate_browser` 只在 mock 层验证。

lead 在本报告之后补做了真实 E2E（**lead 自述，我未独立复现**，证据见 lead 最终回复）：通过 UIA 向咕嘎聊天框真实发送"打开浏览器，在输入框中输入 咕咕嘎嘎"；`route=local_agent`、`status=completed`、9 次工具调用、9 次模型请求，中途一次 fill 失败后自行恢复；硬核验为豆包浏览器"地址和搜索栏"控件 value 实测为"咕咕嘎嘎"。按"不重启咕嘎"的限制，本次验收未重复该 E2E。

## 6. 产出与未改动声明

- 本报告：`docs/verify-fast-handoff-20261112.md`
- 对抗测试：`tools/test_verify_handoff.py`（23 条，全绿）
- 未修改 `core/`、`ui/`、其他 `tools/` 文件；未重启咕嘎。
- 复跑建议：

```
python -m unittest tools.test_verify_handoff tools.test_fast_handoff tools.test_window_control tools.test_self_agent_payload tools.test_self_agent_loop -q
```

## 7. Addendum：lead 修复后的复验（最终状态）

lead 在验收期间按 §5.1、§5.3 的证据修了 `core/self_agent.py` 的 `_MULTI_STEP` 与 `core/browser_interaction.py` 的 `_field_name`，并在 `tools/test_fast_handoff.py` 增加两条断言。我在最终代码上复跑：

```
命令：...python.exe -m unittest tools.test_verify_handoff tools.test_fast_handoff tools.test_window_control tools.test_self_agent_payload tools.test_self_agent_loop -q
----------------------------------------------------------------------
Ran 46 tests in 0.055s

OK
EXITCODE=0
```

```
命令：...python.exe -m unittest tools.test_verify_handoff -q
----------------------------------------------------------------------
Ran 23 tests in 0.034s

OK
EXITCODE=0
```

```
命令：...python.exe -m unittest tools.test_fast_handoff tools.test_window_control tools.test_agent_handoff tools.test_command_intent_repair tools.test_browser_responsiveness tools.test_task_router -q
----------------------------------------------------------------------
Ran 69 tests in 0.515s

OK
EXITCODE=0
```

最终 diff stat：

```
 core/browser_interaction.py  | 15 ++++++-
 core/desktop_interaction.py  | 42 +++++++++++++++++++
 core/fast_commands.py        | 34 ++++++++++++---
 core/self_agent.py           | 98 +++++++++++++++++++++++++++++++++++++++++-----
 tools/test_window_control.py |  6 +--
 5 files changed, 174 insertions(+), 21 deletions(-)
```

复验结论：§5.1、§5.3 两条残留已修复，无新增失败；本次改动导致的失败仍为 0 条。

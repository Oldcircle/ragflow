# RAGFlow Agent 二改 — 进度快照

> **STATUS.md 是会话交接文档**，每次有实质进展时更新，只记录"当前状态 + 下一步入口"，不是历史日志。
> 老条目归档到 `archive/HISTORY-phase-2.x.md`。STATUS 保持 ≤ 5 个 "最近更新" 段。

---

## 最近更新：2026-04-26（Phase 2.8.3 — Interactive Tool Pause Framework + 前端滚动锁修复）

**触发**：用户实测踩坑两条 ——
1. supervisor 调 `ask_user_question` 后没等用户点选项就自己往下答了——交互卡片
   和完整答案并列出现（旧实现是 fire-and-forget + 一行 prompt 嘱托 STOP，
   软约束扛不住模型漂移）
2. 流式输出期间右侧滚轮被无条件 `scrollIntoView` 锁在最底，用户无法滚上去
   看历史

### Modify A — Interactive Tool Pause Framework（参照 Claude Code）

参照 `~/Opensource/vendor/claude-code-ref/packages/builtin-tools/src/tools/AskUserQuestionTool/AskUserQuestionTool.tsx`
的 `shouldDefer=true` + `checkPermissions: 'ask'` 模式 —— 它通过 SDK 的
permission flow 真正阻塞 agent loop，模型根本没机会在 `call()` 之后继续生成。
我们用 HTTP-SSE，没法挂请求等用户点击；落地为 cancel + resume：
- 工具 emit 事件后**持久化 pending 状态 + 在响应里设 `pause_loop=true` 标记**
- runner 在 `tool_call_end` 处观察标记，**force-stop 整个 turn**（与 Phase
  2.6 v0.8.3 的 `consecutive_empty_rag` force_stopped 同模式）
- 用户在前端点选项 → 下一个 user message 带 `[answer: <label>]` 前缀
- 后端 `parse_question_answer` 剥前缀 + `augment_for_question_answer` 注入
  `[question system]` directive，让 supervisor 知道这是 resume 信号

落地清单：

| 层 | 改动 |
|---|---|
| **Schema** (`db_models.py`) | `AgentV2Session` 加 `pending_question_id/status/submitted_at/body` 4 列 + 迁移；与 `pending_plan_*` 平行设计 |
| **Service** (`agent_v2_service.py`) | `AgentV2SessionService.{set,transition,clear,get}_pending_question` 4 个方法，状态机 `NULL → waiting → answered → NULL` |
| **Decision parser** (`plan_decision.py`) | 新增 `parse_question_answer(message) -> (cleaned, {labels, raw_payload})` + `augment_for_question_answer`；支持 `[answer:]` / `[user answer:]` / `[选择:]` / `[回答:]` 等中英文前缀；只匹配带 `[]` 形式（避免误吃普通 user message） |
| **Tool helper** (`tools/base.py`) | 新增 `mcp_pause_response(payload)` + `PAUSE_LOOP_KEY` 常量，统一两个交互工具的标记 |
| **`ask_user_question`** | emit_event 后调 `set_pending_question(body=...)` 持久化整个 payload，返回 `mcp_pause_response(...)` |
| **`submit_plan`** | 同 pause_loop 标记（顺手修第 2 个 bug —— supervisor 提交 plan 后也会继续生成） |
| **Runner** (`runner.py`) | `_has_pause_loop_marker` helper + `tool_call_end` 处的早期短路：observe pause_loop → yield event → emit end → return；不跑 citation validator（pause turn 没产文本） |
| **HTTP** (`agent_v2_app.py::send_message`) | `parse_question_answer` 与 `parse_plan_decision` 并列，命中后 `transition_question_status('answered')` + `augment_for_question_answer` 注入 directive + `clear_pending_question`。互斥保护（同时命中两个前缀时只跑 plan，不堆叠 directive） |
| **前端** | `pending-question-card` 改输出结构化 `{labels, notes}`，`message-list` 包成 `[answer: <labels>]` 前缀 + 用户自定义 notes 作残余 —— 与 plan_card 的 `[plan approved]` 模式对齐 |

### Modify B — 滚动锁修复

`web/src/pages/agent-chat/components/message-list.tsx` 旧实现 `useEffect` 监听
`streaming?.text` 无条件 `scrollIntoView({behavior: 'smooth'})`，每秒数十次
delta 把用户拽回最底。改为：
- `containerRef` 监听 `onScroll`，`stickToBottomRef` 跟踪用户是否在 80px 容
  差内（"贴底跟随"模式）
- `useLayoutEffect` 替代 `useEffect`，避免 paint 后再滚视觉跳一下
- 流式中用 `behavior: 'auto'`（瞬时跳）而非 'smooth'，避免高频触发抖动；非
  流式才用 smooth

模式参照所有现代聊天 UI（Claude.ai / ChatGPT / iMessage）的"pin-to-bottom
only when at bottom"。

### 测试

- 后端 +25 case（test_question_answer.py：parse 11 / augment 4 / pause marker
  helper 7 / ask_user_question pause 注入 2 / submit_plan pause 注入 1）
- 总计 **650 passed / 8 skipped**（v1.0.1 → 615 → 625 → 650）
- ruff clean / tsc clean（agent-chat/ 0 错）

### 真机 smoke 入口

1. 让 supervisor 触发 `ask_user_question` —— 验证：
   (a) SSE 流在 tool_call_end 后立即 emit `end`，supervisor 没机会再生成文本
   (b) 浏览器卡片渲染后用户上滚能滚动，下一个 delta 不会拽回去
   (c) 用户点选项后下条 message 是 `[answer: <label>]` 前缀，supervisor
       resume 后 `[question system]` directive 被消费
2. 同样流程跑 `submit_plan`：plan 卡片下方应该**没有**多余 supervisor 文本
3. SQL 看 `agent_v2_session` 表：`pending_question_id` 在 turn 内 = uuid，
   下一轮被 `clear_pending_question` 设回 NULL

### 下一步候选

- v0.16: pending_question TTL 清扫（与 attachment_sweeper 同模式，避免遗弃
  会话的 stale waiting state 永久占着 UI）
- v0.17: 前端检测 SSE end + 仍处于 pending_question 时自动 focus 卡片，提升
  键盘可达性

---

## 最近更新：2026-04-26（Phase 2.8.2 — citation_without_evidence harness + spawn_subagent 子代理可见性）

**触发**：用户实测踩坑两条 ——
1. supervisor 没调 rag_* 工具时仍带 [N] 引用（凭训练知识/历史答题，给 [N] 涂上"权威感"）
2. 模型自述"子代理共享我的工具集"——supervisor 不知道每个 subagent 的真实工具白名单

参照 `~/Opensource/vendor/claude-code-ref/` 的几个设计模式做：

### Modify A — spawn_subagent 注入子代理工具清单（claude-code 的 `formatAgentLine` 模式）

`api/agent_v2/registry.py`：新增 `_format_subagent_line()` + `_build_subagent_listing()`，
`_decorate_for_mcp` 在 `tool.name == SPAWN_SUBAGENT` 时把 listing 拼到 description
末尾（带 `_AGENT_LISTING_MARKER` 哨兵保证幂等）。supervisor 在工具描述里直接看到：

```
Available subagent types and the tools they have access to:
- sub_archivist: <whenToUse> (Tools: doc_tag, doc_rename, ...)
- sub_librarian: ... (Tools: kb_stats, kb_audit, ..., doc_create_note, ...)
- sub_policy_researcher: ... (Tools: rag_retrieve, rag_read_doc)
- sub_evidence_checker: ... (Tools: rag_retrieve, rag_read_doc)
```

### Modify B — citation_without_evidence 校验规则 + 专用重写路径

`api/agent_v2/validators/citation.py`：
- `CitationIssueKind` Literal 加 `citation_without_evidence`
- `validate_citations` 新增**短路前置规则**：cites 非空 + index 为空 → 聚合一条
  issue 立即返回，不再跑 missing_chunk / number_unsupported / no_citation_for_numeric
  （它们都是同一根因的 N+M 条噪音）
- 设计参照 verificationAgent.ts 的 distinct-VERDICT-per-distinct-cause 模式

`api/agent_v2/validators/rewrite.py`：
- 新增 `rewrite_to_no_basis()` 函数，与 `rewrite_answer_strict` 互斥（前者要 evidence==0
  时把答复降级为免责声明，后者要 evidence≥1 时把引用修对）—— 不重载一个函数吃两种状态
- system prompt 用 claude-code-ref/verificationAgent.ts 的对抗式风格：
  `Your job is not to polish — it's to STRIP` / `FAILURE MODES YOU WILL REACH FOR`
  列 4 条 LLM 真实会用的"理性化跳过"借口逐条反驳 / `HARD RULES` A-E /
  `Forbidden openings` 禁掉道歉式开场 / `OUTPUT` 数字化字数锚点

`api/agent_v2/runner.py`：
- `_handle_citation_issues` 顶部加 phantom-issue 分支，命中走 `_handle_phantom_citations`
  专用通路（warn / strict 各自分支），不走通用 strict rewrite
- `_audit_citation_phantom` 写 `access_audit_log(action=agent_v2.citation_phantom,
  result=deny)`，便于运维 dashboard 聚合"% turn 无检索却带引用"指标
- 新增模块级 `_count_citations` helper

**前端**：`web/src/pages/agent-chat/hooks/use-agent-stream.ts` 的 `CitationIssueKind`
union + `message-list.tsx::issueKindLabel` switch + `web/src/locales/{zh,en}.ts` 各
新增 `citationKindPhantom` —— 三处一并加（claude-code 的 single-source-of-truth 纪律）。

### 测试

- 新增 `TestCitationWithoutEvidence` (4) + `TestRewriteToNoBasis` (6) +
  `_format_subagent_line` / `_build_subagent_listing` / spawn_subagent description
  注入 (4) — 共 14 case
- **后端**：625 passed / 8 skipped（v1.0.1 → 615 → 625）
- **前端**：tsc 在 agent-chat/ 内 0 错；ruff clean

### 下一步入口

1. 真机 smoke：故意问知识库范围外问题，观察是否：
   (a) supervisor 不再带 [N]（prompt 加固）
   (b) 若仍带 [N]，warn 模式前端出"未检索却带引用"红色标签 + audit log 多一条
   (c) strict 模式自动重写为免责文案
2. 30 天后看 `access_audit_log WHERE action='agent_v2.citation_phantom'` 命中率，
   决定是否把默认 `citation_enforce_level` 升到 strict
3. v0.15 候选：runtime system_prompt 求值（让 pending_plan_section / kb_scope_section
   / tool_names 真起作用、enabledTools 改后 cached prompt 重渲）

---

## 最近更新：2026-04-26（前端修复 — 右侧工具调用边栏乱序）

**触发**：用户实测反馈 agent 页面右侧"工具调用"列表顺序乱。

**根因**：`web/src/pages/agent-chat/components/tool-calls-sidebar.tsx` 旧实现
`[...streamingToolCalls, ...historyToolCalls]` + `Set` 首位保留，把当前轮调用
强行顶到列表最前，多轮会话时上面是当前轮 #1/#2/#3、下面才接上一轮的 #1/#2/#3。
后端 `agent_v2_service.AgentV2ToolCallService.list_by_session`（service.py:450）
本身按 `start_time asc` 已排好，不是后端的锅。

**修复**：改用 Map 合并，`history` 覆盖同 id 的 `streaming`（history 是 canonical
真值 + 服务端时钟一致），用 `...(existing ?? {})` 保留 streaming 上
`subagent_start/end` 注入的内联 trace（history 没有这字段），最后按 `startTs`
升序排，与左侧消息流"老→新 自上而下"对齐。

**验证**：`npx tsc --noEmit` 在 `agent-chat/` 内 0 错。

**下一步入口**：真机 smoke —— 同一 session 连发 2 轮带工具的问题，确认右侧
编号 #1→#N 严格按时间从上到下递增、跨轮无穿插。

---

## 最近更新：2026-04-25（Phase 2.8.1 — Session 可编辑 + kb_create 自动入会话作用域）

**触发**：用户实测踩坑——"聊了一会让 agent 新建 KB，发现新 KB 不能被检索"。
原因是 session 创建后 kb_ids 锁死、新 KB 不会自动加入；同时 supervisor 把
"我没 shell" 误当作 "我不能触发解析"（v1.0.1 已修）。本批做两件互补的事：

### Modify A — `kb_create` 自动追加新 KB 到当前 session.kb_ids

`api/agent_v2/tools/doc_ops/kb_create.py`：成功路径读 `ctx.session_id`（在
sub_archivist 子 runner 里就是父 session id，见 spawn_subagent.py:306-307），
通过 `AgentV2SessionService.update_fields(kb_ids=...)` append 新 kb_id。
失败 / 无 session 时静默回退（KB 创建仍成功，只是没自动 attach）。

新参数 `add_to_session: bool = True` 让 supervisor 显式 opt-out（如想先填充
KB 再 attach）。响应里多一个 `attached_to_session: bool` + `next_steps` 多一行
确认信息。

### Modify B — `PATCH /v1/agent_v2/session/<id>` + 前端 settings drawer

后端：
- `api/agent_v2/session_patch.py`（新）— 纯函数 `validate_patch_body`，
  把请求体校验从 Quart 路由解耦出来好测试
- `PATCHABLE_FIELDS` 7 字段白名单：`name / kb_ids / tool_names / max_turns /
  max_budget_usd / citation_enforce_level / citation_numeric_strict /
  history_turn_limit`
- **故意锁死** `model_config_json` / `system_prompt` —— cross-provider tool_use
  格式不兼容、改 system_prompt 等于换 agent 身份；想换走"新建会话"路径，
  和 Anthropic / OpenAI 行业惯例一致
- 校验：kb_ids 走 `DatasetAccessService.filter_accessible_kb_ids` RBAC；
  tool_names 必须 ∈ `ALL_TOOLS`；citation_enforce_level ∈ off/warn/strict；
  numeric 字段范围保护
- tool_names 改时 response 加 warning：cached system_prompt 仍引用旧工具集
  （v0.15 接通 runtime re-eval 后此 warning 消失）

前端：
- `web/src/pages/agent-chat/components/session-settings-drawer.tsx`（新，
  330+ 行）— shadcn Sheet 抽屉；7 字段表单（KB 多选 chip / Tool 多选 chip /
  数字 input / citation enforce select / numeric strict checkbox）；diff
  patch 计算只发生变更字段；保存后 toast + 失效 react-query 缓存
- `session-sidebar.tsx` 每个会话条目加齿轮图标（hover 显出，与垃圾桶并列）
- `api.ts` + `use-sessions.ts` 加 `updateSession` / `useUpdateSession`
- i18n zh + en 各 +18 keys

### 测试

- **后端**：`test/agent_v2/test_session_patch.py`（新，47 case）覆盖 7 字段
  white/blacklist contract + 边界 + 类型强制 + happy path；
  `test_doc_ops_tools.py::TestKbCreate` +3 case（auto-attach default-on /
  opt-out / idempotent when already in scope）
- **后端总计**：611 passed / 8 skipped（v1.0 时 561，新增 50）
- **前端**：tsc 在 agent-chat/ 内 0 错；ESLint clean
- **ruff**：0 errors

### 下一步入口

1. 真机 smoke：浏览器登录手机/桌面 → 点会话齿轮 → 改 kb_ids 看下一轮 retrieve
   能不能拿到新 KB；改 tool_names 看 warning 是否触发；改 budget 看下一轮
   是否生效
2. v0.15 候选：`AgentRunner.run()` 把 system_prompt 求值挪到 runtime，
   pending_plan_section / kb_scope_section 真起作用、tool_names 改后真重渲

---

## 最近更新：2026-04-25（Phase 2.8 **v1.0 落地** — Prompt 系统架构重写）

**S1-S7 全部完成**。`AUDIT-claude-code-alignment.md` §五-e 列的 6 偏差 D1-D6 全部
得到结构性修正。

### 落地总览

| Stage | 内容 | 状态 |
|---|---|---|
| S1 | `api/agent_v2/tools/_names.py` 创建 + registry / annotations / SEARCH_HINT_BY_TOOL 切到常量；3 个 dict 22 项全部 parity | ✅ |
| S2 | `prompting/builder.py` 重写：`PromptSection` / `PromptCtx` / `PromptCache` / `assemble_prompt` / `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` / `prepend_bullets`；旧 `build_supervisor_prompt` / `build_subagent_prompt` 保留作 backward-compat | ✅ |
| S3 | `prompting/sections.py` 共享段库（590 行）：identity / mission / domain_context / strict_rag_constraints / read_only_block / plan_gate_block / retrieval_output_rules / no_citation_for_operators / step_marker_rules / tone_and_style / numeric_length_anchors / actions_risk / using_your_tools / supervisor_workflow / supervisor_delegation / clarify_vs_act + 3 个 dynamic 段（pending_plan / kb_scope，v0.15 真接通时启用）+ 3 个 preset assembler（supervisor / operator / reflective） | ✅ |
| S4 | 4 subagent 重构：sub_archivist 192→91 行 / sub_librarian 145→153 行 / sub_evidence_checker 98→118 行 / sub_policy_researcher 94→112 行；全部 v2.0.0 | ✅ |
| S5 | `_common.py::strict_rag_prompt` 改为 callable + 6 supervisor 全部走新路径；版本号 v1.0.0 → v2.0.0 | ✅ |
| S6 | 测试：新 `test_prompting_sections.py`（27 case）+ `test_sub_archivist.py` 适配 callable system_prompt；560 passed / 8 skipped；ruff 0 错 | ✅ |
| S7 | 静态 prompt 长度测量 + 文档收尾 | ✅ |

### 量化结果

**prompt 大小（resolve_system_prompt 渲染后）**：

| Agent | v1.x → v2.0 | Δ |
|---|---:|---:|
| sub_archivist | 7804 → **3248** | **-58%** |
| supervisor_baozhang | 6962 → **6080** | -13% |
| sub_librarian | 4677 → 5398 | +15%（+ READ_ONLY 块、actions、长度锚点）|
| sub_evidence_checker | ~3000 → 4670 | +56%（同上）|
| sub_policy_researcher | ~3000 → 4611 | +54%（同上）|
| 6 supervisor 平均 | ~6000 → ~5900 | 持平 |

read-flavored agent 的 prompt 略胖是因为加进了之前没有的共享段（`READ_ONLY_BLOCK`、
`actions_risk`、`tone_and_style`、`numeric_length_anchors`）——内容是真增益（行为
更一致、可缓存），且 v0.15 真接通 cache 后跨 agent 共享。**sub_archivist 一项的
58% 缩减就证明了去重的价值**。

**代码大小**：

| 文件 | 之前 | 之后 |
|---|---:|---:|
| sub_archivist.py | 192 行 | 91 行 |
| 4 subagent 总行数 | 529 行 | 474 行 |
| 6 supervisor 平均 | ~50 行 | ~48 行（基本不变，behaviour 由 _common 改 callable 实现）|
| **新模块 _names.py** | — | 135 行 |
| **新模块 sections.py** | — | 590 行 |
| 增量净行数 | — | +540 |

### 6 偏差修正状态

| # | 偏差 | 修正 |
|---|---|---|
| **D1** 静态/动态分界 + 节级缓存 | `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 常量 + `PromptCache` 实现；assemble_prompt 在 boundary 标记前后切分。**v1.0 暂未真接通 SDK cache_control**（DeepSeek server-side 自动检测前缀），架构层已就位 | ✅ 架构 / ⏳ 真接通 v0.15 |
| **D2** enabledTools 过滤 | `PromptSection.compute(enabled_tools, ctx)` 标准签名；`make_plan_gate_block` / `make_supervisor_delegation_section` / `make_clarify_vs_act_section` / `make_step_marker_rules` 都按 `enabled_tools` 决定是否 emit；段返 None 自动消失 | ✅ |
| **D3** 工具名常量化 | `tools/_names.py` 22 工具 + 5 group tuple；registry / annotations / SEARCH_HINT_BY_TOOL / runner._EMPTY_RAG_TOOLS / 6 supervisor / 4 subagent 全部 import；裸字面量 grep 命中数从 50+ 降到 0（@tool 装饰器自身 name 字段除外，那是工具的 canonical 自定义） | ✅ |
| **D4** 段函数化 + null 自动消失 | `PromptCache.resolve` 自动过滤 None；`make_identity_section` / `make_mission_section` / `make_domain_context_section` 等多个段都会在条件不满足时 return None | ✅ |
| **D5** Tool 信息单一来源 | `_SUBAGENT_SKELETON` / `_SUPERVISOR_SKELETON` 模板里的 "Tool cost hints" 段已被新 preset 移除（preset 不再包含 cost hints）。MCP annotations + searchHint 是唯一来源 | ✅ |
| **D6** 数字化长度锚点 | `make_numeric_length_anchors`：≤25 words inter-tool / ≤100 words final / ≤30 words per [step K/N done] line。supervisor / operator / reflective 三个 preset 全包含 | ✅ |

### 测试

- `pytest test/agent_v2/`：**560 passed / 8 skipped**（v0.13 时 478，新增 82：27 个新 prompt section + 55 来自 parametrized test_runner_registry / test_subagent_event_bubble 重新计数）
- `uvx ruff check api/agent_v2/ test/agent_v2/`：**0 errors**
- 工具名字面量 grep：仅 `@tool(name="..."` 装饰器自身 ~6 处（canonical 工具自定义，等价于 claude-code-ref 每个工具的 `*Tool/toolName.ts`）

### 关键决策记录

1. **保留 backward-compat**：`build_supervisor_prompt` / `build_subagent_prompt` 旧函数没删，下个 phase（v0.15）再清。
2. **dynamic 段在 v1.0 暂为空**：`pending_plan_section` / `kb_scope_section` 已写好但 `supervisor_dynamic_sections()` 返 `[]`。原因是 `AgentDefinition.system_prompt` 现在仍是 module-load-time 一次性求值；要 per-turn 真动态需要把求值挪到 runner.run() 里，留 v0.15 做。boundary marker 仍存在，让未来真启用时无需改 caller。
3. **subagent 文件不再放 inline 的 HARD_RULES / WORKFLOW / TOOL_RULES / OUTPUT_RULES list**：sub_archivist 现在只有 `mission` 字符串 + `_build_*_system_prompt` callable。复杂的 librarian / evidence_checker / policy_researcher 用 `make_*_workflow_section()` inline 加一段定制 workflow，其余共享。
4. **callable system_prompt**：schema 早就支持 `Callable[[dict], str]`，本次首次真用上。`AgentDefinition.system_prompt = _build_archivist_system_prompt`，不再是字符串字面量。

### 下一步入口

1. **v0.14 commits 拆分**（4 commits）：docs / 工具名常量 + builder + sections / 10 definition / 测试 + STATUS。git status 确认无脏工作树
2. **真机 smoke**：浏览器里跑一次 sz-baojian-house supervisor 让 LLM 选工具，对照 v0.13 行为
3. **v0.15 候选**：
   - 把 system_prompt 求值挪到 `AgentRunner.run()` 让 dynamic 段真起作用
   - `pending_plan_section` / `kb_scope_section` 真动起来
   - 接通 SDK cache_control / DeepSeek prompt_cache 让 boundary 真省 token

---

## 最近更新：2026-04-25（Phase 2.8 **立项** — Prompt 系统架构重写）

**触发**：v0.13 落地后用户审视 Agent 系统提示词，发现 v0.3 立的 "8 段式 prompt"
只是抄了节标题没抄到 claude-code-ref 真正核心机制。深读
`vendor/claude-code-ref/src/utils/systemPrompt.ts` +
`src/constants/prompts.ts:448-581` + `src/constants/systemPromptSections.ts` +
`packages/builtin-tools/src/tools/AgentTool/built-in/exploreAgent.ts`，锁定 6
个偏差（D1-D6）。

**6 个偏差**（详见 `AUDIT-claude-code-alignment.md` §五-e）：

1. **D1 静态/动态分界 + 节级缓存**：claude-code-ref 用
   `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 把可缓存静态段和易变动态段切开 +
   `systemPromptSection(name, compute)` memoize；我们整 prompt 字符串拼接，
   DeepSeek prompt_cache 命中率 0%
2. **D2 enabledTools 过滤**：ref 用 `Set<string>` 决定段是否引用某工具；
   我们硬编码 `"get_pending_plan"` 等字面量
3. **D3 工具名常量化**：ref 全部 `BASH_TOOL_NAME` import；我们 22 工具裸字面量
   散落 50+ 处
4. **D4 段函数化 + null 自动消失**：ref 段返 `string | null`，filter 掉；
   我们大字符串模板，每个 slot 必须有内容
5. **D5 Tool 信息单一来源**：ref 工具描述 + system 段职责不重；我们 prompt 段
   重述 tool description，且加了第三处 "Tool cost hints" 表
6. **D6 数字化长度锚点**：ref `"≤25 words / ≤100 words"` 实测 1.2% token 减少；
   我们全定性 "be concise"

**核心产物**（待落地）：
- `api/agent_v2/tools/_names.py` — 22 工具名常量集中
- `api/agent_v2/prompting/builder.py` 重写 — `PromptSection` / `PromptCtx` /
  `PromptCache` + `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`
- `api/agent_v2/prompting/sections.py` — 共享段库（IDENTITY / READ_ONLY_BLOCK /
  PLAN_GATE_BLOCK / CITATION_RULES / TONE_AND_STYLE / NUMERIC_LENGTH_ANCHORS /
  PENDING_PLAN 等）
- 10 个 AgentDefinition 切到 `static_sections=[...] + dynamic_sections=[...]`
- 测试 `test_prompting_sections.py` + 现有断言适配

**预期改变**：

| 指标 | 当前 | 预期 |
|---|---:|---:|
| sub_archivist.py | 192 行 | ~80 行 |
| sub_archivist 渲染后 prompt | ~3.4K char | ~2.0K char |
| 静态段缓存命中率（同 session）| 0% | 70-80% |
| 工具名 grep 命中数 | 50+ | 1（`_names.py`）|
| pytest | 478 passed | 478 + ~25 新增 |

**Stage 拆解**（11–14h）：S1 _names.py（1h）→ S2 builder 重写（3h）→
S3 sections.py（3h）→ S4 4 subagent（2h）→ S5 6 supervisor（1.5h）→
S6 测试（2h）→ S7 smoke + 文档（1h）→ S8 commit 拆分（0.5h）

**已落地**：
- `AUDIT-claude-code-alignment.md` §五-e — 6 偏差 D1-D6 + T19-T27 整改清单
- `PLAN-prompt-architecture.md` — 完整设计 + ref 引用精确到行号 +
  backward-compat 策略 + 风险 / 缓解
- `PLAN.md` 注册 Phase 2.8 + v1.0 版本记录
- 本 STATUS 顶部条目；老条目（04-23 v0.4-v0.6 / v0.5 / v0.4 / v0.3 / v0.2 /
  v0.1 / 上架硬化 / P2.5-hardening / Phase 2.5 / P3.1 / P2.3 / P2.2 / P2.1）
  搬到 `archive/HISTORY-phase-2.x.md`，STATUS.md 从 1247 行降到 ~200 行

**下一步入口**：
1. S1 开工：建 `api/agent_v2/tools/_names.py` 集中 22 工具名
2. S2 重写 `prompting/builder.py`：`PromptSection` 抽象 + cache +
   `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`；旧 `build_supervisor_prompt` /
   `build_subagent_prompt` 内部转调新 API（保 backward compat）
3. S3 起步：`prompting/sections.py` 共享段库

---

## 最近更新：2026-04-25（Phase 2.7 **v0.10 完整落地** — 附件协议 + 下载-验证-归档）

**当前阶段**：Phase 2.7 全 5 stage 闭环，两个核心用户场景端到端可用。

### 落地总览

| Stage | 内容 | 关键文件 |
|---|---|---|
| 1 | DB + HTTP + ToolContext 附件基础设施 | `db_models.AgentV2Attachment` / `AgentV2AttachmentService` / `POST-GET-DELETE /v1/agent_v2/session/<id>/attachments` / `api/agent_v2/attachments.py` |
| 2 | 2 新 MCP 工具 | `doc_archive_attachment` (sub_archivist, plan_gated) + `web_fetch_to_attachment` (sub_archivist, openWorld) |
| 3 | `submit_plan.preview` 扩展 | 8KB markdown_excerpt + 前端 `pending-plan-card` 折叠渲染 |
| 4 | 前端 composer 附件 UI | 📎 按钮 + drag-drop + `AttachmentChip` + `useAttachments` hook |
| 5 | 真机 smoke + 文档 | 两 smoke 脚本 + G13 8 个人工用例 + FORK.md v0.10 |

### 两个场景端到端

**场景 1 — 用户上传 → Agent 归档**：
```
composer 拖 PDF → POST /attachments（MinIO + DB staged 行 + 8KB preview）
→ 用户："归档到 XX KB" → POST /conversation（send_message 自动扫 staged 附件注入 ctx）
→ supervisor 看到 "# 会话附件" 段 → spawn_subagent(sub_archivist)
→ archivist: submit_plan(preview=attachment.preview_text[:2000])
→ 前端 plan card 弹可折叠预览
→ 用户批准 → get_pending_plan → doc_archive_attachment → KB
```

**场景 2 — URL 下载 → 用户验证 → 归档**（与场景 1 共用最后一步）：
```
用户："把 https://... 归档" → supervisor spawn sub_archivist
→ archivist: web_fetch_to_attachment(url)（SSRF 三段防御 + redirect 白名单 + markdownify）
→ submit_plan(preview={kind:"markdown_excerpt", excerpt, source_ref: url})
→ 用户批准 → doc_archive_attachment（复用）
```

### 关键设计决策（对齐 claude-code-ref）

1. **Attachment 作独立 message type** — 对齐 `attachments.ts:3675` 的 60+ 子类型
2. **复用 submit_plan + plan_gate 作 staging** — ref 全库无 PendingQueue 抽象
3. **场景 1 / 2 共用 `doc_archive_attachment`** — 两场景最后一步相同
4. **图片走 OCR 路径**（不走 Vision inline）— 持久可检索；`FileType.VISUAL` 交给上游 PaddleOCR/MinerU
5. **MinIO bucket 重命名** `agent_v2_attachments` → `agent-v2-attachments`（S3 DNS-compliant naming）

### 验证

- **自动化测试**：`pytest test/agent_v2/` → **452 pass / 8 skip**（376 → +76）
  - `test_attachments.py` 36 / `test_doc_archive_attachment.py` 17 / `test_web_fetch_to_attachment.py` 11 / `test_submit_plan_preview.py` 12
- **lint**：`uvx ruff check api/ test/ scripts/` clean
- **前端**：`npm run lint` clean + `npm run build` 51.6s OK
- **真机 smoke（DeepSeek）**：
  - `scripts/smoke_attachment_archive.py` → **PASS ✓**（上传 → staged → supervisor 看到 → spawn archivist）
  - `scripts/smoke_web_download_archive.py` → **PASS ✓**（URL 归档指令 → supervisor clarify → confirm → spawn archivist）
- **人工用例**：TEST-MANUAL-v0.4.md 加 G13（8 个用例，P0-P2）

### 工具总数

20 → **22**（新 `doc_archive_attachment` + `web_fetch_to_attachment`）

### 下一步入口

1. （可选）真机手工跑 G13-004 / G13-005 端到端，拿到一份 archive 成功的截图
2. （可选）子 agent 事件 bubble up 机制完善——smoke 2 无法看到子 archivist 的 `plan_submitted` 事件（spawn_subagent 只 merge text_delta / subagent_start/end，不 merge 所有 tool_call 事件），不影响生产但 smoke 观测度有限
3. （可选）OCR pipeline 深度集成——当前图片附件走 `FileType.VISUAL` 依赖 tenant 的 PaddleOCR/MinerU 配置；v0.11 可考虑 `doc_archive_attachment` 里同步拉 OCR 并写 markdown 子文档

---

## 最近更新：2026-04-25（Phase 2.7 立项：附件协议 + 下载-验证-归档，设计定版）

**触发**：用户提了两个场景——
1. 对话里直接上传文件，Agent 自动归档到 KB
2. Agent 下载 URL 内容，用户 preview 审核，批准后归档

按用户要求**先深扒 `vendor/claude-code-ref` 同类机制再动手**。Explore 过一遍
`src/utils/attachments.ts` / `bridge/inboundAttachments.ts` /
`EnterPlanModeTool.ts` / `ExitPlanModeV2Tool.ts`，拿到 5 节核心发现：

**关键设计决策（3 条）**：
1. **Attachment 作独立 message type**（不塞 UserMessage 的 content block）
   —— ref `attachments.ts:3675` 的 60+ 子类型证明架构扩展性
2. **不搞通用 pending queue** —— ref 全库 0 命中 `PendingEdit`；我们的
   `submit_plan + plan_gate runtime`（v0.4 做的）就是 ExitPlanMode 等价物
3. **场景 1 + 场景 2 共用** `doc_archive_attachment` 作最后一步；
   `web_fetch_to_attachment` 只材化成 staged attachment，不直接入库

**关键产物**：
- `PLAN-attachments.md`（**新**，完整设计 + ref 引用精确到行号 + 决策 log +
  11 节 11 章，覆盖 DB schema / HTTP 端点 / MCP 工具 / 前端组件 / safety
  limits / 分 Stage 工时拆解 / 拒绝的替代方案 log）
- `CLAUDE.md` / `PLAN.md` 活跃文档清单登记
- `PLAN.md` 新增 Phase 2.7 章节 + v0.9 版本记录

**数据/工具摘要**：
- 新表 `agent_v2_attachment`（session-scoped + 24h TTL + xxhash128 dedupe）
- 新工具 `web_fetch_to_attachment`（sub_librarian）+ `doc_archive_attachment`
  （sub_archivist，plan_gated）
- `submit_plan.preview` 字段扩展（markdown_excerpt，≤ 8KB，前端折叠渲染）
- 前端 composer 文件选择器 + attachment chip + plan card preview 展开

**Stage 分解（11-15h 合计）**：
1. DB + HTTP + ToolContext 附件基础设施（4-5h）
2. `doc_archive_attachment` + `web_fetch_to_attachment` 工具（2-3h）
3. `submit_plan.preview` + 前端 plan card 折叠预览（2h）
4. 前端 composer 附件上传（2-3h）
5. 真机 smoke + 文档更新（1-2h）

**下一步入口**：
1. 用户确认设计（PLAN-attachments.md）→ 开 Stage 1
2. Stage 1 开始：建 migration + `AgentV2AttachmentService` + 3 个 HTTP 端点

---


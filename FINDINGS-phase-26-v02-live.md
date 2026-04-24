# Phase 2.6 v0.2 活体验证发现

> 2026-04-24 · 基于 `scripts/test_phase_26_v02_live.py` 在真 DeepSeek 上跑出来的
> 结果（非 mock）。用于记录 unit test 过了但**实际跑 LLM 才暴露**的问题与修复。

---

## 摘要

三个 scenario 在真 LLM 上跑过两轮（修复前 / 修复后），总共烧了 ~$1.5。发现
3 个**unit test 抓不到**的真问题：

| # | 问题 | 发现于 | 当前状态 |
|---|---|:-:|:-:|
| F1 | Supervisor 直接调 `kb_audit` 绕过 sub_librarian（架构分离失效） | A1 | ✅ 修好 |
| F2 | `spawn_subagent` 把 subagent 工具集与父工具集做**交集**，导致 librarian 拿不到 `doc_create_note` / `kb_audit` | A2 | ✅ 修好 |
| F3 | Claude Code SDK 内建工具（Read / Bash / LS / Agent 等）没被 `disallowed_tools` 完全过滤 | A1/A2 | ⚠️ 减缓（黑名单扩到 16 个），**根治留下轮** |

修复后复跑 A2，真的写成了一份新文档到 DB（cleanup 确认删了 1 doc）。

---

## F1：Supervisor `tools="*"` 导致架构分离失效

### 症状

A1 prompt：`帮我看看这个保障房知识库现在是什么状态？`

**期望**：supervisor 识别"想了解 KB 形态"→ `spawn_subagent(subagent_type="sub_librarian")` → librarian 调 `kb_audit`。

**实测**：supervisor 直接调 `rag_list_docs` + `kb_audit`，**没派 librarian**。

### 根因

所有 supervisor definition 都用 `tools="*"`，resolve_tools 展开成全量 17 个工具，
包括 `kb_audit`（本来应该只给 librarian）。LLM 当然选一步到位，不会多此一举 spawn。

### 修复

`api/agent_v2/definitions/built_in/_common.py` 新增 `SUPERVISOR_TOOLS` 常量：

```python
SUPERVISOR_TOOLS = [
    "rag_retrieve", "rag_list_docs", "rag_read_doc", "rag_graph_query",  # 读
    "kb_stats",              # 轻量 sanity check
    "spawn_subagent",        # 派 librarian / archivist
    "ask_user_question",     # 直接澄清
    "submit_plan",           # 提交计划
]
```

6 个 supervisor 全部改成 `tools=SUPERVISOR_TOOLS`。**不**给 supervisor 任何写工具
或 `kb_audit`——这些只能通过 spawn 对应的 subagent 来访问。

### 验证

A1 复跑：工具调用从 `[rag_list_docs, kb_audit]` 变成
`[kb_stats, rag_list_docs, rag_read_doc × N]`——`kb_audit` 不再出现（被 CLI 层
`allowed_tools` 过滤）。回答质量仍然很好（甚至更深入，因为 Agent 改用
`rag_read_doc` 抽查了实际文档）。

---

## F2：`spawn_subagent` 父子工具集**交集**逻辑反了架构

### 症状

A2 修了 F1 之后：supervisor 确实派了 3 个 subagent，但最终文本说
**"由于当前会话环境中没有文件系统写入工具，报告内容直接输出如下"**——
`doc_create_note` **没被调用**，报告只是打印到 chat。

### 根因

`api/agent_v2/tools/spawn_subagent.py:169`：

```python
defn_tools = resolve_tools(definition, parent_tools=parent_tools)
allowed = [t for t in (defn_tools or []) if t in parent_tools]
```

做了交集。sub_librarian 定义工具集是：
`[kb_audit, kb_stats, doc_list_recent_changes, doc_create_note, ...]`

但父 supervisor（F1 修复后）的工具集是：
`[rag_retrieve, rag_list_docs, rag_read_doc, rag_graph_query, kb_stats,
spawn_subagent, ask_user_question, submit_plan]`

交集只有 `[rag_retrieve, rag_list_docs, rag_read_doc, kb_stats,
ask_user_question, submit_plan]`——**kb_audit / doc_create_note /
doc_list_recent_changes 全被过滤掉**。librarian 被派出来但拿不到自己应有的工具。

这个交集逻辑的初衷是**防止 subagent 越权**（子不能比父权限多）。但新架构
里 supervisor 故意**比 subagent 少权限**（不拿写/审计工具），交集逻辑反向
破坏了设计。

### 修复

改 `spawn_subagent.py`：**命名 subagent 路径**（`subagent_type=...` 时）
**不做父子交集**，只要求 subagent 声明的工具名在全局 `ALL_TOOLS` 里即可。
权限边界由 `ctx.allowed_subagent_types` 控制——父如果 whitelist 了这个
subagent_type，就等于认可了它声明的**完整工具集**。

**非命名路径**（直接传 `allowed_tools` 列表）仍保留父子交集（防止父
"偷"比自己更高权限的工具）。

### 验证

A2 复跑：cleanup 报告 `Deleted 1 newly-created documents`——librarian 真的
调了 `doc_create_note` 写了一份 Markdown 报告进 KB（doc_id
`0a5e6c6c3f8c11f18648478cfaf3a572`）。总耗时 144 秒 / $0.21。最终文本：
"✅ 体检完成 — 报告已入库 … 文档 ID: 0a5e6c6c..."。

---

## F3：Claude Code SDK 内建工具泄漏（未根治）

### 症状

三个 scenario 都能观察到 supervisor 的 tool 列表里混入：
- `Read`（Claude Code 的文件读工具）
- `Bash` / `BashOutput`
- `LS`
- `Agent`（SDK 自己的 agent delegation）

这些**不在**我们的 17-tool MCP 注册表里。

### 风险评估

**不是立即风险**——Claude Code CLI 有自己的 permission prompt，Agent 调
这些工具时不会自动执行（会报错 / 提示用户）。但：
- 工具清单**暴露**给 LLM，浪费 token + 干扰推理
- 如果 permission mode 设成 bypass，理论上可能真跑到宿主 FS

### 缓解

`api/agent_v2/runner.py::_build_options` 加上 `disallowed_tools` 显式黑名单：

```python
disallowed_tools=[
    "Read","Write","Edit","NotebookEdit",
    "LS",
    "Bash","BashOutput","KillShell","KillBash",
    "Glob","Grep",
    "WebFetch","WebSearch",
    "TodoWrite","Task","Agent",
    "ExitPlanMode","SlashCommand",
]
```

### 实测效果

部分生效：A1 修复后 `Read` 没了；但 A2 复跑仍见 `Bash`。说明 Claude Code
CLI 的 `--disallowedTools` 参数对某些内建工具不完全生效，或者名字不匹配。

### 根治方向（下轮做）

1. 实测到底 `disallowed_tools` 用什么字符串 key 才能拦住（可能要 `bash_tool` / `BashTool` / `shell_exec` 之类）
2. 或者：通过 `permission_mode="deny"` 让 CLI 默认拒绝所有未显式 allow 的工具，只保留 `mcp__ragflow__*` 白名单
3. 最彻底：改用 headless 模式直接调 Anthropic Messages API（绕过 Claude Code CLI），这样只有我们自己的 MCP 工具存在，不可能泄漏

---

## F4（观察，不修）：Agent 倾向于用 Markdown 问问题，不调 `ask_user_question`

### 症状

A3 prompt：`帮我把政策类文档归档一下。`（故意模糊——没说归到哪个库）

**期望**：Agent 调 `ask_user_question` emit 多选卡片。

**实测**：Agent 用普通 Markdown 列了 3 个选项让用户回答。`ask_user_question`
工具一次都没调用。

### 为什么不算 bug

这是 LLM 天性——倾向于**对话式**澄清，而不是**结构化卡片**。从用户体验角度
两者都能用：Markdown 澄清也能得到有效回答。只是前端看不到多选卡。

### 如果要强制调工具

可以在 supervisor system prompt 里加硬规则："遇到歧义时**必须**调
ask_user_question 工具，不许用 Markdown 问"。但这会削弱 LLM 的自然对话能力。
决策留给用户。

---

## 测试脚本本身的局限

`scripts/test_phase_26_v02_live.py` 只捕获**父 Agent** 的 tool_call_start
事件。subagent 内部的工具调用（librarian 调 `doc_create_note` 等）不冒泡到
父的 observation。所以 "Missing: doc_create_note" 的警告不一定是真的 miss——
要结合 `Cleanup: Deleted N documents` 反推是否真落盘。

后续要补一个**端到端**确认：调 `AuditLogService.query_logs` 筛 `action=
'kb.doc.note_create'` 确认 librarian 真的调了 doc_create_note。

---

## 相关 commit

- SUPERVISOR_TOOLS + disallowed_tools（F1 + F3 部分缓解）
- spawn_subagent 工具交集修正（F2 根治）
- FINDINGS 文档（本文件）

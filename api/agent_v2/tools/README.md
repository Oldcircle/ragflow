# Agent v2 工具集

此目录下的每个 `.py` 文件定义一个可以被 Claude Agent SDK 调用的 **MCP 工具**。
Agent 通过对工具 schema 的理解，自主决定何时以及以什么参数调用哪个工具。

## 架构速览

```
用户请求
   │
   ▼
AgentRunner.run(message, history=..., summary_text=...)  # api/agent_v2/runner.py
   │  set_ctx(ToolContext)                                # api/agent_v2/tools/base.py
   ▼
claude_agent_sdk.query(prompt=<history + user_msg>)       # 子进程 Claude Code CLI
   │  LLM 推理 → "我要调 rag_retrieve"
   │  MCP tool call via stdio
   ▼
SdkMcpTool.handler(args)            # 本进程内 MCP In-Process Server
   │  get_ctx() 读出 tenant/kb      # ← ContextVar（同事件循环传播）
   │  调 RAGFlow 服务：Dealer.retrieval() / DocumentService / ...
   ▼
返回 MCP tool-result（JSON 文本）
   │
   ▼
（收尾）AgentRunner 对最终答复跑 Citation Validator，有问题发 citation_warning 事件
```

## 为什么用 ContextVar 而不是 env vars

`ClaudeAgentOptions.env` 注入的环境变量只进入 **Claude Code CLI 子进程**，
但我们的 MCP 工具运行在 **SDK 调用方的 Python 进程**（In-Process Server）。
这是两个进程空间。用 `contextvars.ContextVar` 可以在同事件循环的异步任务里
自动传播，工具可以拿到 Runner 注入的 `ToolContext`。

## 工具清单（18 个，截至 Phase 2.6 v0.6）

| 分层 | 工具 | 归属 subagent | 受 plan gate |
|---|---|---|---|
| 读 | `rag_retrieve` / `rag_list_docs` / `rag_read_doc` / `rag_graph_query` | supervisor + 所有 subagent | — (只读) |
| 轻体检 | `kb_stats` | supervisor + sub_librarian | — (只读) |
| 委派 | `spawn_subagent` | supervisor | — |
| 交互 | `ask_user_question` / `submit_plan` | 所有 | — |
| 写 | `doc_tag` / `doc_rename` / `doc_archive` / `doc_reparse` / `doc_upload_from_url` / `kb_create` | **sub_archivist 独占** | ✅ 硬约束 |
| 自省 | `kb_audit` / `doc_list_recent_changes` | sub_librarian 独占 | — (只读) |
| 总结 | `doc_create_note` | sub_librarian 独占 | 显式 `plan_gated=False`（低风险可删） |
| plan 执行（v0.6） | `get_pending_plan` | sub_archivist 独占 | — (只读) |

每个工具的 schema / Agent 调用策略见 `README-agent-v2.md §4`（不在本文件重复）。
RAG 链路工具（retrieve / list_docs / read_doc / graph_query）会被
`api/agent_v2/validators/evidence.py::EvidenceIndex` 吸收，用于 Citation
Validator 的 [N] 脚注 / 数字断言校验（Phase 2.5.1）。

### `spawn_subagent` gate 链（按顺序）

1. `ctx.depth >= 1` → `nested_spawn_forbidden`
2. `ctx.subagent_count_this_turn >= 3` → `too_many_subagents`
3. `subagent_type` 给了但 registry 找不到 → `unknown_subagent_type`
4. `subagent_type` 找到但 kind != "subagent" → `wrong_definition_kind`
5. `ctx.allowed_subagent_types` 非 None 且不含此 type → `subagent_type_not_allowed`
6. definition 要求的工具在全局 `ALL_TOOLS` 找不到 → `definition_tools_unavailable`
   （v0.2 起**不做**父子工具交集——命名 subagent 可拿父都没有的工具）
7. 非命名派时父工具白名单不含请求工具 → `no_allowed_tools`
8. 空 prompt → `empty_prompt`

### `@require_kb_write` gate 链（doc_ops 写工具，Phase 2.6 + v0.4）

1. **RBAC**：`kb_id` + `user_id` 齐全时调
   `DatasetAccessService.require_at_least(kb_id, user_id, role)`，不足直接
   `error: no_access`
2. **Plan gate**（`plan_gated=True` 默认）：
   - 如果 `ctx.plan_submitted_this_turn == True` → `plan_submitted_same_turn`
   - session `pending_plan_status == waiting` → `plan_waiting_user_decision`
   - `rejected` → `plan_rejected`；`request_changes` → `plan_request_changes`
   - `approved` 或 `None` → 放行；`waiting` 超 1h 自动 TTL 清空
3. 执行工具本体；异常写 deny 审计并重抛；成功写 allow 审计（含 `extra_audit_metadata`）

## 通用约定

### 输入

所有工具通过 `input_schema` 声明 JSON Schema；Agent 调用时传 `args: dict`。
工具函数签名统一 `async def fn(args: dict) -> dict`（由 `@tool` 装饰）。

### 输出

统一 MCP tool-result 格式：

```python
{"content": [{"type": "text", "text": "<JSON or plain text>"}]}
```

用 `mcp_json_response(obj)` / `mcp_text_response(text)` 生成。

doc_ops 写工具统一走 `ok()` / `err()` helper（`api/agent_v2/tools/doc_ops/_common.py`）：

- `ok(**kw)` → `{"status": "ok", ...}`
- `ok(next_steps=[...], **kw)` → 同上 + `next_steps` 数组（v0.4，最多 3 条 × 160 字符）
- `err("code", "message", **kw)` → `{"error": "code", "message": "...", ...}`

### 上下文（ToolContext）

工具通过 `get_ctx()` 获取 `ToolContext`：

```python
from .base import get_ctx

async def my_tool(args: dict) -> dict:
    ctx = get_ctx()                # 默认要求 tenant_id + kb_ids
    # ctx.tenant_id / ctx.kb_ids / ctx.user_id
    # ctx.session_id / ctx.depth / ctx.current_tool_call_id       (P2.3)
    # ctx.event_emitter (callable → emit SSE event)                (P2.3)
    # ctx.allowed_subagent_types (tuple | None)                    (P2.5.3)
    # ctx.pending_plan_status / pending_plan_id                    (P2.6 v0.4)
    # ctx.plan_submitted_this_turn                                 (P2.6 v0.4)
```

要求字段可以显式传入 `get_ctx(require=["tenant_id"])`。

### 大小限制

`mcp_json_response()` 默认截断到 32KB（`MAX_TOOL_OUTPUT_BYTES`）以避免炸
Agent 上下文。对可能返回大量文本的工具（如 `rag_read_doc`），设计上应当
让 Agent 分页取。

### 错误处理

工具出错两种模式：

1. **业务错误**（如 KB 不存在、参数无效、plan gate 拒绝）：返回
   `{"error": "..."}` JSON，**不抛异常**。这让 Agent 知道错误原因并继续推理。
2. **系统级异常**（DB 断连、模型调用失败等）：直接 raise，Runner 会捕获
   并转成 `tool_call_end(error=...)` 事件。

## 新增工具步骤

1. 在本目录（或 `doc_ops/`）新建 `my_tool.py`
2. 用 `@tool(name, description, input_schema)` 装饰 async 函数。Description
   写英文，开头 `Use this tool when ...`（Claude Code 风格）
3. 写工具一定要加 `@require_kb_write(action="kb.xxx.yyy", ...)` 装饰器；低风险
   工具可带 `plan_gated=False`，但**必须有明确理由**
4. 在 `api/agent_v2/registry.py` 的 `ALL_TOOLS` 字典注册
5. 在 `api/agent_v2/annotations.py::ANNOTATIONS` 补一条 `ToolAnnotation`。
   `test/agent_v2/test_annotations.py` 有 parity 断言，漏掉就挂
6. 在某个 supervisor / subagent definition 的 `tools=[...]` 里加进去
   （supervisor 默认只有 `SUPERVISOR_TOOLS`；写/审计工具必须挂在 subagent 上）
7. 在 `scripts/test_tools_direct.py` 加一个 smoke 调用
8. 有需要在 `README-agent-v2.md §4` 表格里加一行

## 新增 subagent 定义步骤（P2.5.3）

1. 在 `api/agent_v2/definitions/built_in/` 建一个 `.py` 文件，export 一个
   `DEFINITION = AgentDefinition(kind="subagent", ...)`
2. system prompt 用 `build_subagent_prompt(...)` 组装；传 `tool_names_for_annotations`
   让 **Tool cost hints** 段只显示该 subagent 自己能用的工具
3. 在某个 supervisor definition 的 `allowed_subagent_types` tuple 里加这个 name
4. `api/agent_v2/definitions/registry.py` 会在下次访问时自动 pickup

## 测试

- 直接 smoke：`python scripts/test_tools_direct.py`（绕过 Agent，验证纯工具逻辑）
- Agent 端到端：`python scripts/test_agent_v2.py`（让 Agent 自主选工具）
- 单元测试：`pytest test/agent_v2/`（229 pass / 8 skip，截至 Phase 2.6 v0.4）

### 关键测试文件

- `test_doc_ops_common.py` — `@require_kb_write` 五条路径 + plan gate 七分支
  + plan 决策前缀解析
- `test_annotations.py` — 注册表 parity / `ok().next_steps` 裁剪
- `test_sub_archivist.py` / `test_doc_ops_reflect.py` — 两个命名 subagent 的
  工具集 / 白名单 / definition 结构
- `test_validators.py` — Citation Validator 全量规则
- `test_tool_schemas.py` — 每个工具的 `input_schema` 合法性

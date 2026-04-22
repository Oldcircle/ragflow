# P2.3 — Multi-Agent（Supervisor + Subagent）

> 让 Agent v2 能处理「列出所有相关文档 → 分头总结 → 综合结论」这类需要委派的复杂任务。参考 `vendor/claude-code-ref/packages/builtin-tools/src/tools/AgentTool/` 的子 Agent 模式，适配到我们的 Claude Agent SDK Python 栈。

---

## 一、为什么值得做

**当前限制**：Agent v2 只能单线程推理，遇到需要并行 / 分治的任务（对比多份文档、多步研究、综合多源信息）要么一次 tool call 干完（上下文炸）要么反复来回（成本高 + 质量差）。

**目标用例**：
- 「对比深圳和广州的保障房政策差异」→ 主 Agent 派 2 个子 Agent 分别研读、主 Agent 综合
- 「梳理 2023 年 3 家公司财报的增长策略异同」→ 每家一个子 Agent
- 「给这个政策库做一份 FAQ」→ 子 Agent 分主题生成，主 Agent 合并去重

---

## 二、参考设计分析（来自 claude-code-ref）

| 关键点 | claude-code-ref 做法 | 我们照搬 / 改写 |
|---|---|---|
| 主循环 | `queryLoop()` async generator + 可变 state dict | 已有 `AgentRunner.run()` async iterator，不改 |
| 工具调度 | partition(read-only 并发, write 串行) | 留作 Phase 3，v1 先按提交顺序串行 |
| 子 Agent | `AgentTool` + `runAgent()`，子跑独立 `query()` | 我们写 `spawn_subagent` 工具，内部再调一个 `AgentRunner` |
| 上下文隔离 | 子有自己的 messages + 可选 cwd / worktree + 转写录到独立目录 | 子的 messages 独立，tenant/kb/user 继承；不做 fs 隔离 |
| 结果回传 | 子完成后父拿到最后一条 assistant 文本 | 同样：子结束时把最终文本截断 32KB 当 tool result |
| 并行 | `AgentTool` 支持 `run_in_background=true` | v1 同步等待；v2 支持并发多个 subagent |
| 深度 | 无硬限（靠 maxTurns 自然收敛） | 显式限制 **depth=1**（子不能再派孙）避免爆炸 |
| 预算 | 子独立 token budget | 子从父的剩余预算里扣（父设 1 美金 = 父和所有子共用）|
| 失败处理 | 子抛错 → tool_result 返 error | 同样；父可以重试换 prompt |

---

## 三、数据模型

### 3.1 `agent_v2_subagent_trace`

```sql
CREATE TABLE agent_v2_subagent_trace (
  id                    VARCHAR(32) PRIMARY KEY,
  parent_session_id     VARCHAR(32) NOT NULL,
  parent_tool_call_id   VARCHAR(64) NOT NULL,        -- 父调 spawn_subagent 的 tool_use_id
  description           VARCHAR(255) NOT NULL,        -- 人类可读任务描述
  prompt                TEXT NOT NULL,                 -- 给子的 prompt
  allowed_tools         JSON NOT NULL DEFAULT '[]',   -- 子可用工具白名单，空 = 同父
  max_turns             INT NOT NULL DEFAULT 10,
  max_budget_usd        FLOAT NOT NULL DEFAULT 0.3,
  status                VARCHAR(16) NOT NULL,          -- running | success | error | cancelled | truncated
  result_preview        TEXT NULL,                     -- 最终 assistant 文本前 4KB 快照
  full_transcript_ref   VARCHAR(64) NULL,              -- 指向 agent_v2_message 表里的一串 message_id 快照
  token_usage_json      JSON NULL,                     -- {input_tokens, output_tokens, ...}
  cost_usd              FLOAT NULL,
  duration_ms           INT NULL,
  start_time            BIGINT NOT NULL,
  end_time              BIGINT NULL,
  INDEX idx_parent (parent_session_id, start_time)
);
```

**为什么不复用 `agent_v2_session`**：子 agent 跑完就消亡，不是给 IM 回复会再次召唤的长期对话；放单独表便于删、便于按父分组查询，避免 session 列表污染。

### 3.2 `agent_v2_subagent_message`（可选）

记录子 Agent 的内部对话：

```sql
CREATE TABLE agent_v2_subagent_message (
  id            VARCHAR(32) PRIMARY KEY,
  trace_id      VARCHAR(32) NOT NULL,
  seq           INT NOT NULL,
  role          VARCHAR(16) NOT NULL,
  content       LONGTEXT NULL,
  thinking      LONGTEXT NULL,
  tool_use_json JSON NULL,
  create_time   BIGINT NOT NULL,
  INDEX idx_trace (trace_id, seq)
);
```

可选（v1 可以先不持久化，存内存里按 `trace_id` 查完清掉，省 DB 开销）。

---

## 四、工具：`spawn_subagent`

### 4.1 Schema（暴露给主 Agent 的 LLM）

```python
spawn_subagent_schema = {
    "name": "spawn_subagent",
    "description": (
        "Dispatch a focused subagent to perform an isolated investigation "
        "or task. Use this when you need to:\n"
        "  - investigate multiple targets in parallel (one subagent each)\n"
        "  - delegate a deep read of specific documents\n"
        "  - run a long tool chain without cluttering your own context\n"
        "\n"
        "The subagent shares tenant, knowledge bases, and model with you, "
        "but has its own message history. It can NOT spawn further subagents.\n"
        "\n"
        "You get a single text response. Plan before you spawn: one "
        "subagent per clearly-scoped task."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short (3-8 words) task title. Shown in the UI.",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Self-contained brief for the subagent. Include: goal, "
                    "relevant background (NOT your own reasoning), output "
                    "format expected, constraints. Never write 'based on your "
                    "findings' — that forces synthesis onto the subagent."
                ),
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Restrict which tools the subagent can call. Leave empty "
                    "to inherit parent's tools. Typical scopes: "
                    "['rag_retrieve', 'rag_read_doc'] for read-only investigation."
                ),
                "default": [],
            },
            "max_turns": {
                "type": "integer",
                "description": "Upper bound on subagent LLM turns (default 10, max 20).",
                "default": 10,
                "maximum": 20,
                "minimum": 1,
            },
        },
        "required": ["description", "prompt"],
    },
}
```

### 4.2 实现骨架

`api/agent_v2/tools/spawn_subagent.py`：

```python
from ..runner import AgentRunner, ModelConfig
from .base import ToolContext, get_ctx

MAX_SUBAGENTS_PER_TURN = 3
MAX_RESULT_CHARS = 32_000


async def spawn_subagent(args: dict) -> dict:
    ctx = get_ctx()
    
    # 1. 深度检查：子不能再派
    if ctx.depth >= 1:
        return {"error": "subagents cannot spawn further subagents"}
    
    # 2. 本轮次数上限
    ctx.subagent_count_this_turn += 1
    if ctx.subagent_count_this_turn > MAX_SUBAGENTS_PER_TURN:
        return {"error": f"max {MAX_SUBAGENTS_PER_TURN} subagents per turn exceeded"}
    
    # 3. 预算检查
    remaining = (ctx.max_budget_usd or 0) - ctx.spent_usd
    child_budget = min(
        args.get("max_budget_usd") or ctx.child_default_budget,
        max(remaining * 0.5, 0.05),  # 留一半给父继续
    )
    
    # 4. 工具白名单
    allowed = args.get("allowed_tools") or []
    if not allowed:
        allowed = [t for t in ctx.tool_names if t != "spawn_subagent"]
    else:
        # 子工具必须是父工具的子集，且不能包含 spawn_subagent
        allowed = [t for t in allowed if t in ctx.tool_names and t != "spawn_subagent"]
    
    # 5. 创建 trace 记录
    trace_id = uuid4().hex
    SubagentTraceService.create(
        id=trace_id,
        parent_session_id=ctx.session_id,
        parent_tool_call_id=ctx.current_tool_call_id,
        description=args["description"],
        prompt=args["prompt"],
        allowed_tools=allowed,
        max_turns=args.get("max_turns", 10),
        max_budget_usd=child_budget,
        status="running",
        start_time=now_ms(),
    )
    
    # 6. emit start event（让前端实时渲染）
    await ctx.emit(SubagentStartEvent(trace_id=trace_id, description=args["description"]))
    
    # 7. 跑子 Agent
    try:
        child = AgentRunner(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            kb_ids=ctx.kb_ids,
            system_prompt=build_child_system_prompt(ctx, args),
            model=ctx.model_config,
            tool_names=allowed,
            max_turns=min(args.get("max_turns", 10), 20),
            max_budget_usd=child_budget,
            # 关键：depth+1 注入 ctx，子工具能看到自己已在子态
            _depth=ctx.depth + 1,
        )
        
        final_text_parts = []
        child_usage = {}
        async for ev in child.run(args["prompt"]):
            if isinstance(ev, AssistantTextEvent):
                final_text_parts.append(ev.text)
            elif isinstance(ev, ResultEvent):
                child_usage = ev.usage or {}
            # 可选：把子的 tool_call / thinking 也 emit 到父 SSE 流，带 parent_trace_id
            await ctx.emit(SubagentChildEvent(trace_id=trace_id, inner=ev))
        
        final_text = "".join(final_text_parts).strip()
        truncated = len(final_text) > MAX_RESULT_CHARS
        if truncated:
            final_text = final_text[:MAX_RESULT_CHARS] + "\n\n[truncated]"
        
        cost = estimate_cost(child_usage)
        ctx.spent_usd += cost  # 父继续扣
        
        SubagentTraceService.finish(
            trace_id,
            status="truncated" if truncated else "success",
            result_preview=final_text[:4096],
            token_usage_json=child_usage,
            cost_usd=cost,
        )
        
        await ctx.emit(SubagentEndEvent(
            trace_id=trace_id,
            status="success",
            result_preview=final_text[:512],
            cost_usd=cost,
        ))
        
        return {"result": final_text, "cost_usd": cost, "trace_id": trace_id}
    
    except Exception as e:
        logger.exception("subagent %s failed: %s", trace_id, e)
        SubagentTraceService.finish(trace_id, status="error", result_preview=str(e))
        await ctx.emit(SubagentEndEvent(
            trace_id=trace_id, status="error", error=str(e),
        ))
        return {"error": str(e), "trace_id": trace_id}
```

### 4.3 `ToolContext` 扩展

`api/agent_v2/tools/base.py` 的 `ToolContext`：

```python
@dataclass
class ToolContext:
    tenant_id: str
    user_id: str | None
    kb_ids: list[str]
    session_id: str
    tool_names: list[str]
    model_config: ModelConfig
    max_budget_usd: float | None = None
    spent_usd: float = 0.0
    
    # Phase 2.3 新增 ↓
    depth: int = 0                          # 0 = 父，≥ 1 = 子
    subagent_count_this_turn: int = 0       # 本 LLM turn 派了多少子
    child_default_budget: float = 0.3
    emit: Callable[[Any], Awaitable[None]] = None  # 往 SSE 流推事件
    current_tool_call_id: str | None = None
```

### 4.4 注册

`api/agent_v2/registry.py`：加一个 `register("spawn_subagent", spawn_subagent)` 注册点；并在 `SUPERVISOR_AVAILABLE_TOOLS` 白名单里加入。

---

## 五、子 Agent 的 System Prompt

关键：让子 Agent **知道**自己是被派的，产出格式要给父好吞咽。

```python
def build_child_system_prompt(ctx, args):
    parent_sp = ctx.system_prompt  # 父的 system prompt
    return (
        "You are a focused subagent dispatched by a parent agent to complete "
        "one isolated task.\n\n"
        "Your task:\n"
        f"{args['description']}\n\n"
        "Rules:\n"
        "1. Follow the parent's domain constraints (reproduced below).\n"
        "2. Execute the task independently — don't ask the parent for "
        "clarification; make reasonable assumptions and note them.\n"
        "3. Cite sources with [1][2] footnotes when basing claims on retrieval.\n"
        "4. Output only the final deliverable. No meta commentary like "
        "'Here is what I found'.\n"
        "5. If the task is impossible with the available tools, say so in "
        "one sentence and stop.\n"
        "6. You CANNOT spawn further subagents.\n\n"
        "Parent's domain constraints:\n"
        "---\n"
        f"{parent_sp}\n"
        "---\n"
    )
```

---

## 六、事件流 / 前端渲染

### 6.1 新事件类型（`api/agent_v2/event.py`）

```python
class SubagentStartEvent(Event):
    type: Literal["subagent_start"] = "subagent_start"
    trace_id: str
    description: str
    parent_tool_call_id: str

class SubagentChildEvent(Event):
    """把子 Agent 内部的事件包一层，前端按 trace_id 归集."""
    type: Literal["subagent_child"] = "subagent_child"
    trace_id: str
    inner: dict   # 原 event 的 to_dict() 结果

class SubagentEndEvent(Event):
    type: Literal["subagent_end"] = "subagent_end"
    trace_id: str
    status: Literal["success", "error", "truncated"]
    result_preview: str | None = None
    error: str | None = None
    cost_usd: float | None = None
```

### 6.2 前端 `tool-call-card.tsx` 升级

检测到 `tool_name === "spawn_subagent"` 时：
- 顶部显示任务描述 + 状态（running / success / error）+ 耗时 + 成本
- 可点开 → 展示完整子对话（复用 `MessageList` 组件 + `ThinkingBlock`）
- 子对话里嵌套的 tool call 也正常渲染

### 6.3 API 变更

`/v1/agent_v2/subagent/<trace_id>` 新端点：
- `GET` 返回 trace 详情 + 子消息列表
- 鉴权：只有父 session 的 owner 能读

---

## 七、安全 / 预算 / 限制

| 限制 | 值 | 原因 |
|---|---|---|
| 最大嵌套深度 | 1（子不能派孙） | 防 runaway |
| 每 LLM turn 最多子 agent 数 | 3 | 防 token 爆炸 |
| 每子 max_turns | 20 | 跟父对齐 |
| 子预算 | `min(req, remaining/2)` | 父能继续工作 |
| 结果字符上限 | 32 KB | 超过截断并标记 truncated |
| 子工具白名单 | 必须 ⊆ 父可用工具 且 ≠ `spawn_subagent` | 避免权限上升 |
| KB 访问 | 子继承父的 kb_ids，不可扩展 | P2.1 RBAC 的一致性 |

---

## 八、验收场景

### 场景 1：保障房对比

测试 prompt：
```
对比深圳保障房里"公共租赁住房"和"人才安居住房"的主要申请条件和资格要求，
给一个 2 列的对比表，并引用政策原文。
```

**期望行为**：主 Agent
1. 调 `rag_retrieve("公共租赁住房申请条件")`
2. 看到结果后派 `spawn_subagent(description="整理公租房条件", prompt="...")`
3. 同一 turn 里再派 `spawn_subagent(description="整理人才房条件", prompt="...")`（并行支持留 v2，v1 串行）
4. 两个子都返回后，主 Agent 合成对比表

**观察点**：两个 trace_id 出现在工具调用侧栏，每个可点开看子检索细节。

### 场景 2：预算耗尽

人为把父 max_budget_usd 设 0.1，发一个需要多次子 agent 的 prompt。期望：第 3 个 subagent 被预算拒绝，父收到 `{error: "budget_exceeded"}` 能优雅收尾。

### 场景 3：防止嵌套

构造一个 system prompt 诱导子 Agent 再调 spawn_subagent。期望：工具拒绝，`{error: "subagents cannot spawn further subagents"}`。

---

## 九、实现顺序

1. DB 表 `agent_v2_subagent_trace` + Service
2. `ToolContext` 扩字段 + 向下兼容
3. `spawn_subagent` 工具 + 注册
4. 事件类型 + SSE 透传
5. 单测：mock `AgentRunner` 验证 depth/budget/tool whitelist
6. 集成测试：真 DeepSeek 跑保障房对比场景
7. 前端：`ToolCallCard` 识别 subagent + 展开子对话
8. 前端：`SubagentDetailDrawer`（可选，v1 inline 展开就够）

---

## 十、延伸（Phase 3）

- **并行调度**：一个 turn 里派多个子时 `asyncio.gather` 并发跑
- **子可以启动 Monitor**：长任务用 background pattern（参考 claude-code-ref `MonitorTool`）
- **父干预子**：父能中途 abort 某个子
- **跨 session 复用子结果**：缓存 `(prompt_hash, kb_ids)` → 命中直接返旧结果（Langfuse 帮忙）

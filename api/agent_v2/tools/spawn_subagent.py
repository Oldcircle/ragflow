"""spawn_subagent — 派出聚焦子 Agent（Phase 2.3 Multi-Agent）。

父 Agent 用这个工具委派一个独立 context 的聚焦任务。参考 Claude Code 的
AgentTool 模式：
  - 子用父的 tenant / kb / model，但有独立 messages
  - 子工具白名单必须 ⊆ 父工具 且 不含 ``spawn_subagent``（禁止嵌套）
  - 子结束后父拿到最终文本（截断到 32 KB）
  - 所有派出事件都 emit 到父 SSE 流（前端能实时渲染子 trace）
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from api.db.services.subagent_trace_service import SubagentTraceService

from .base import (
    emit_event,
    get_ctx,
    mcp_json_response,
    tool,
)

logger = logging.getLogger("ragflow.agent_v2.spawn_subagent")

MAX_SUBAGENTS_PER_TURN = 3
MAX_RESULT_CHARS = 32_000
MAX_CHILD_TURNS = 20
DEFAULT_CHILD_TURNS = 10
DEFAULT_CHILD_BUDGET_USD = 0.3
MAX_DEPTH = 1  # 0 = 父；子不能再派


@tool(
    name="spawn_subagent",
    description=(
        "Dispatch a focused subagent to perform an isolated investigation "
        "or task. Use this when you need to:\n"
        "  - investigate multiple targets in parallel context (one per subagent)\n"
        "  - delegate a deep read of specific documents without cluttering your own context\n"
        "  - run a long tool chain and synthesize a summary\n\n"
        "The subagent shares the same tenant, knowledge bases, and model with you, "
        "but has its OWN message history. It CANNOT spawn further subagents.\n\n"
        "You receive a single synthesized text response. Plan carefully before spawning: "
        "one subagent per clearly-scoped task. Give a complete brief — the subagent "
        "cannot ask you clarifying questions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short (3-8 words) task title. Shown in the UI trace card.",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Self-contained brief for the subagent. Include: the goal, "
                    "relevant background (NOT your own reasoning), output format, "
                    "and constraints. Never write 'based on your findings, ...' — "
                    "that pushes synthesis onto the subagent without direction."
                ),
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Restrict which tools the subagent can call (short names, e.g. "
                    "['rag_retrieve', 'rag_read_doc']). Leave empty to inherit the "
                    "parent's full toolbox (minus spawn_subagent)."
                ),
                "default": [],
            },
            "max_turns": {
                "type": "integer",
                "description": "Upper bound on subagent LLM turns (default 10, max 20).",
                "default": DEFAULT_CHILD_TURNS,
                "minimum": 1,
                "maximum": MAX_CHILD_TURNS,
            },
            "subagent_type": {
                "type": "string",
                "description": (
                    "Optional. Name of a registered subagent definition (e.g. "
                    "'sub_policy_researcher', 'sub_evidence_checker'). When set, "
                    "the subagent inherits the definition's system_prompt, tools, "
                    "max_turns, and budget defaults — you still provide the "
                    "specific 'description' and 'prompt' for this dispatch. "
                    "Omit for the generic (anonymous) subagent."
                ),
            },
        },
        "required": ["description", "prompt"],
    },
)
async def spawn_subagent(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id", "kb_ids"])

    # ── 1) 嵌套深度限制（防 runaway）──
    if ctx.depth >= MAX_DEPTH:
        return mcp_json_response({
            "error": "nested_spawn_forbidden",
            "message": "Subagents cannot spawn further subagents.",
        })

    # ── 2) 本 turn 子 Agent 数上限（防 token 爆炸）──
    if ctx.subagent_count_this_turn >= MAX_SUBAGENTS_PER_TURN:
        return mcp_json_response({
            "error": "too_many_subagents",
            "message": f"Max {MAX_SUBAGENTS_PER_TURN} subagents per parent turn.",
        })

    description = str(args.get("description") or "").strip()
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return mcp_json_response({"error": "empty_prompt"})

    # ── 2.5) 可选：按命名 definition 派（Phase 2.5.3）──
    subagent_type = (args.get("subagent_type") or "").strip() or None
    definition = None
    if subagent_type:
        from ..definitions import get_definition

        definition = get_definition(subagent_type)
        if definition is None:
            return mcp_json_response({
                "error": "unknown_subagent_type",
                "message": f"No registered subagent definition named {subagent_type!r}.",
            })
        if definition.kind != "subagent":
            return mcp_json_response({
                "error": "wrong_definition_kind",
                "message": (
                    f"Definition {subagent_type!r} is a {definition.kind}, "
                    "not a subagent."
                ),
            })
        if (
            ctx.allowed_subagent_types is not None
            and subagent_type not in ctx.allowed_subagent_types
        ):
            return mcp_json_response({
                "error": "subagent_type_not_allowed",
                "message": (
                    f"Parent agent is not permitted to spawn {subagent_type!r}. "
                    f"Allowed: {list(ctx.allowed_subagent_types)}."
                ),
            })

    # ── 3) max_turns：definition 默认 > arg 提示；上限 MAX_CHILD_TURNS ──
    if definition is not None:
        default_turns = definition.max_turns
    else:
        default_turns = DEFAULT_CHILD_TURNS
    max_turns = int(args.get("max_turns") or default_turns)
    max_turns = max(1, min(max_turns, MAX_CHILD_TURNS))

    # ── 4) 工具白名单：definition.tools > arg.allowed_tools > 父继承 ──
    parent_tools = list(ctx.tool_names) if ctx.tool_names else _all_registered_tool_names()
    parent_tools = [t for t in parent_tools if t != "spawn_subagent"]
    if definition is not None:
        from ..definitions import resolve_tools

        defn_tools = resolve_tools(definition, parent_tools=parent_tools)
        # definition 的 tools 必须是父工具集的子集（spawn_subagent 已经先剔除）
        allowed = [t for t in (defn_tools or []) if t in parent_tools]
        if not allowed:
            return mcp_json_response({
                "error": "definition_tools_unavailable",
                "message": (
                    f"Subagent definition {subagent_type!r} requires tools "
                    f"{definition.tools}, none of which are in parent whitelist "
                    f"{parent_tools}."
                ),
            })
    else:
        requested = [t for t in (args.get("allowed_tools") or []) if isinstance(t, str)]
        if requested:
            allowed = [t for t in requested if t in parent_tools]
            if not allowed:
                return mcp_json_response({
                    "error": "no_allowed_tools",
                    "message": f"None of {requested} are in parent whitelist {parent_tools}.",
                })
        else:
            allowed = list(parent_tools)

    # ── 5) 预算：definition 优先 > DEFAULT > 父预算的 50% ──
    parent_budget = ctx.max_budget_usd
    if definition is not None and definition.max_budget_usd is not None:
        base_budget = definition.max_budget_usd
    else:
        base_budget = DEFAULT_CHILD_BUDGET_USD
    if parent_budget and parent_budget > 0:
        child_budget = min(base_budget, parent_budget * 0.5)
    else:
        child_budget = base_budget

    # ── 5) 模型继承 ──
    model = ctx.model_config
    if model is None:
        return mcp_json_response({
            "error": "no_model_config",
            "message": "Parent agent has no model config; cannot spawn child.",
        })

    # ── 6a) 配额（Phase 3.1b）：hard_enforce 超限直接拒 ──
    try:
        from api.db.services.tenant_quota_service import (
            QuotaExceeded,
            TenantUsageService,
            check_subagent_daily,
        )
        try:
            check_subagent_daily(ctx.tenant_id)
        except QuotaExceeded as qe:
            return mcp_json_response({
                "error": "quota_exceeded",
                "metric": qe.metric,
                "used": qe.used,
                "limit": qe.limit,
            })
        TenantUsageService.increment(ctx.tenant_id, subagent_spawns=1)
    except Exception:
        pass

    # ── 6b) 创建 trace 记录 + emit start 事件 ──
    trace = SubagentTraceService.start(
        parent_session_id=ctx.session_id or "",
        parent_tool_call_id=ctx.current_tool_call_id or "",
        description=description,
        prompt=prompt,
        allowed_tools=allowed,
        max_turns=max_turns,
        max_budget_usd=child_budget,
    )
    trace_id = trace.id

    # import 放函数内避免循环（runner -> tools -> runner）
    from .. import event as ev
    from ..runner import AgentRunner

    await emit_event(ev.subagent_start(
        trace_id=trace_id,
        description=description,
        parent_tool_call_id=ctx.current_tool_call_id or "",
        allowed_tools=allowed,
        max_turns=max_turns,
        max_budget_usd=child_budget,
    ))

    # ── 7) 记录本 turn subagent 计数（ctx 是 frozen 风格但 dataclass 不 frozen；直接改）──
    ctx.subagent_count_this_turn += 1

    # ── 8) 跑子 Agent ──
    start_ms = int(time.time() * 1000)
    if definition is not None:
        child_system_prompt = _build_child_system_prompt_from_definition(
            ctx, definition, description,
        )
        child_enforce = definition.citation_enforce
        child_numeric_strict = definition.citation_numeric_strict
    else:
        child_system_prompt = _build_child_system_prompt(ctx, description)
        child_enforce = "warn"
        child_numeric_strict = True
    try:
        child = AgentRunner(
            tenant_id=ctx.tenant_id,
            kb_ids=list(ctx.kb_ids),
            system_prompt=child_system_prompt,
            model=model,
            tool_names=allowed,
            user_id=ctx.user_id,
            max_turns=max_turns,
            max_budget_usd=child_budget,
            parent_session_id=ctx.session_id,
            depth=ctx.depth + 1,
            citation_enforce_level=child_enforce,
            citation_numeric_strict=child_numeric_strict,
        )

        text_parts: list[str] = []
        usage_dict: dict[str, Any] = {}
        cost_usd = 0.0
        last_error: str | None = None

        async for event in child.run(prompt):
            t = event.type
            d = event.data
            if t == "text_delta":
                text_parts.append(d.get("text", ""))
            elif t == "error":
                last_error = d.get("message") or d.get("code")
            elif t == "end":
                usage_dict = d.get("usage") or {}
                cost_usd = float(usage_dict.get("total_cost_usd") or 0.0)

        final_text = "".join(text_parts).strip()

        if last_error and not final_text:
            SubagentTraceService.finish(
                trace_id, status="error", error=last_error,
                token_usage=usage_dict, cost_usd=cost_usd,
            )
            await emit_event(ev.subagent_end(
                trace_id=trace_id, status="error",
                error_message=last_error,
                cost_usd=cost_usd,
                duration_ms=int(time.time() * 1000) - start_ms,
                token_usage=usage_dict,
            ))
            return mcp_json_response({"error": last_error, "trace_id": trace_id})

        truncated = len(final_text) > MAX_RESULT_CHARS
        if truncated:
            final_text = final_text[:MAX_RESULT_CHARS] + "\n\n[result truncated at 32 KB]"

        status = "truncated" if truncated else "success"
        SubagentTraceService.finish(
            trace_id,
            status=status,
            result_preview=final_text,
            token_usage=usage_dict,
            cost_usd=cost_usd,
        )
        duration_ms = int(time.time() * 1000) - start_ms
        await emit_event(ev.subagent_end(
            trace_id=trace_id,
            status=status,
            result_preview=final_text[:512],
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            token_usage=usage_dict,
        ))

        return mcp_json_response({
            "result": final_text,
            "trace_id": trace_id,
            "description": description,
            "cost_usd": cost_usd,
            "truncated": truncated,
        }, truncate=False)  # 父 LLM 需要拿完整结果做综合

    except Exception as exc:
        logger.exception("subagent run failed: %s", trace_id)
        with contextlib.suppress(Exception):
            SubagentTraceService.finish(
                trace_id, status="error", error=str(exc),
            )
        with contextlib.suppress(Exception):
            await emit_event(ev.subagent_end(
                trace_id=trace_id,
                status="error",
                error_message=str(exc),
                duration_ms=int(time.time() * 1000) - start_ms,
            ))
        return mcp_json_response({"error": str(exc), "trace_id": trace_id})


def _build_child_system_prompt_from_definition(
    ctx, definition, description: str,
) -> str:
    """Definition 模式：用 definition.system_prompt 做主体，父 prompt 仅作背景。

    对比 ``_build_child_system_prompt``（通用 spawn 模式）：
    - 通用模式下子没有自己的角色定义，只能继承父的系统提示
    - Definition 模式下子是个「命名角色」（如 `sub_policy_researcher`），
      它的 system_prompt 就是权威；父 prompt 只作域约束的背景参考
    """
    defn_sp = definition.resolve_system_prompt({"description": description}) or ""
    parent_sp = ctx.system_prompt or ""
    return (
        f"{defn_sp}\n\n"
        "--- 当前派遣上下文 ---\n"
        f"父 Agent 派你做的事：{description or '（未指定）'}\n\n"
        "你不能再派 subagents。输出时：\n"
        "- 只给父需要的最终交付，不要 meta commentary\n"
        "- 引用时用 [1][2] 标注 rag_retrieve 返回的来源\n"
        "- 做不到就一句话说做不到并停止\n\n"
        "--- 父 Agent 的域约束（仅供参考，不要重复父的工作）---\n"
        f"{parent_sp}\n"
        "--- 结束 ---\n"
    )


def _build_child_system_prompt(ctx, description: str) -> str:
    """给子 Agent 的 system prompt。

    关键：告诉子它是被派来做一件聚焦事、产出格式要给父好吞、不能再派孙。
    """
    parent_sp = ctx.system_prompt or ""
    return (
        "You are a focused subagent dispatched by a parent agent to complete "
        "one well-scoped task.\n\n"
        f"Your task title: {description or '(untitled)'}\n\n"
        "Rules:\n"
        "1. Follow the parent's domain constraints (reproduced below).\n"
        "2. Execute the task independently — make reasonable assumptions "
        "and note them; don't ask for clarification.\n"
        "3. Cite sources with [1][2] footnotes when basing claims on retrieval "
        "results from rag_retrieve.\n"
        "4. Output ONLY the final deliverable. No meta commentary like "
        "'Here is what I found:' or 'I will now...'.\n"
        "5. If the task is impossible with the available tools, say so in "
        "one sentence and stop.\n"
        "6. You CANNOT spawn further subagents.\n\n"
        "Parent's domain constraints:\n"
        "--- PARENT SYSTEM PROMPT ---\n"
        f"{parent_sp}\n"
        "--- END PARENT SYSTEM PROMPT ---\n"
    )


def _all_registered_tool_names() -> list[str]:
    """懒引用以避免 registry→tools→registry 循环。"""
    from ..registry import ALL_TOOLS
    return [n for n in ALL_TOOLS if n != "spawn_subagent"]

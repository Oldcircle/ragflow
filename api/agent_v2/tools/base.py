"""工具基础设施：SDK 装饰器 re-export + 运行时上下文。

Runner 在每次 ``run()`` 前用 ``set_ctx()`` 注入 RAGFlow 上下文（基于 ContextVar，
同事件循环内自动传播）。工具通过 ``get_ctx()`` 读取。

不使用环境变量的原因：MCP In-Process Server 的工具在 **SDK 调用方的 Python 进程**
里执行，而 ``ClaudeAgentOptions.env`` 的环境变量只注入到 Claude Code CLI **子进程**。
两者不是同一个进程空间，env vars 传不过来。
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import tool  # noqa: F401 — re-export for tools

from ..errors import ContextError

if TYPE_CHECKING:
    from ..runner import ModelConfig


@dataclass
class ToolContext:
    """Runner 向所有工具透传的运行时上下文。

    Phase 2.3 扩展：加上 depth / session_id / 允许工具集 / 模型配置等，
    以便 ``spawn_subagent`` 能派出独立的子 Runner。
    """

    tenant_id: str
    kb_ids: tuple[str, ...]
    user_id: str | None = None

    # ────────── Phase 2.3 — Multi-Agent ──────────
    session_id: str | None = None
    """父 session 的 id；用作子 trace 的外键."""

    system_prompt: str = ""
    """父 Agent 的 system prompt，传给子 Agent 做背景参考."""

    tool_names: tuple[str, ...] | None = None
    """父允许的工具白名单；None 表示全部已注册工具。子的白名单必须是这个的子集."""

    model_config: "ModelConfig | None" = None
    """父用的模型配置；子默认继承."""

    max_budget_usd: float | None = None
    """父总预算（仅用作子预算上限参考，v1 不做扣减）."""

    depth: int = 0
    """0 = 父（顶层），≥1 = 子；子不能再派孙 Agent。"""

    subagent_count_this_turn: int = 0
    """本次顶层 turn 已派出的子数；用于限流（v1 每 turn 最多 3 个）."""

    # ────────── Phase 2.7 — Attachments ──────────
    attachments: tuple = ()
    """Session-scoped attachments snapshot (tuple[AttachmentInfo, ...]).

    Read-only view populated by ``agent_v2_app.send_message`` at turn boundary.
    Tools that need to access full attachment content or mutate status go
    through ``AgentV2AttachmentService`` — this tuple is just for prompt
    display + targeted tool lookup (e.g. ``doc_ingest_attachment(id=...)``).
    """

    event_emitter: Callable[[Any], Awaitable[None]] | None = None
    """把事件推回父 SSE 流的回调；None 时默认丢弃."""

    current_tool_call_id: str | None = None
    """当前正在执行的 tool call 的 ID（SDK 提供，SubagentTrace 用作父 key）."""

    # ────────── Phase 2.5.3 — Agent Definition Manifest ──────────
    allowed_subagent_types: tuple[str, ...] | None = None
    """父 Agent 允许派哪些命名 subagent（定义名）。
    - ``None``：不做限制（老 session 行为）——任何注册的 subagent definition 都可用
    - ``()``：空 tuple = 不允许任何命名 subagent（只能 spawn 通用子）
    - ``("sub_a", "sub_b")``：只允许这些名字的 subagent
    """

    # ────────── Phase 2.6 v0.4 — plan gating ──────────
    pending_plan_status: str | None = None
    """进入本轮时 session 上的 plan 状态快照（waiting / approved / rejected /
    request_changes / None）。@require_kb_write 用它判断写工具是否放行。"""

    pending_plan_id: str | None = None
    """当前 waiting/approved plan 的 pending_id；仅作审计引用用。"""

    plan_submitted_this_turn: bool = False
    """本次 Agent run 内 submit_plan 是否已调过。只要为 True，同轮后续写工具
    必须被拒——不能 submit_plan 之后立刻接着执行，必须等下一轮用户批。"""

    extra: dict = field(default_factory=dict)


_ctx_var: contextvars.ContextVar[ToolContext | None] = contextvars.ContextVar(
    "ragflow_agent_v2_tool_ctx", default=None
)


def set_ctx(ctx: ToolContext) -> contextvars.Token:
    """设置当前上下文，返回 reset token。"""
    return _ctx_var.set(ctx)


def reset_ctx(token: contextvars.Token) -> None:
    """恢复之前的上下文。"""
    _ctx_var.reset(token)


def get_ctx(require: list[str] | None = None) -> ToolContext:
    """在工具内部读取当前 ToolContext。

    Args:
        require: 必需字段名（``"tenant_id"``/``"kb_ids"``/``"user_id"``）。
                 缺一则抛 ``ContextError``。默认 ``["tenant_id", "kb_ids"]``。
    """
    if require is None:
        require = ["tenant_id", "kb_ids"]
    ctx = _ctx_var.get()
    if ctx is None:
        raise ContextError(
            "Tool invoked outside Runner context — did you call AgentRunner.run()?"
        )
    for field_name in require:
        value = getattr(ctx, field_name, None)
        if not value:
            raise ContextError(
                f"Missing required tool context field: {field_name!r}"
            )
    return ctx


def replace_ctx(**updates: Any) -> contextvars.Token:
    """在当前 ctx 基础上派生一份新 ctx 并设进去；返回 reset token。"""
    current = _ctx_var.get()
    if current is None:
        raise ContextError("replace_ctx called without parent context")
    data = {k: getattr(current, k) for k in current.__dataclass_fields__}
    data.update(updates)
    new_ctx = ToolContext(**data)  # type: ignore[arg-type]
    return _ctx_var.set(new_ctx)


async def emit_event(event: Any) -> None:
    """工具内发事件的便捷 helper；未设 emitter 时安静丢弃."""
    ctx = _ctx_var.get()
    if ctx is None or ctx.event_emitter is None:
        return
    try:
        res = ctx.event_emitter(event)
        if asyncio.iscoroutine(res):
            await res
    except Exception:
        pass  # 事件推送失败绝不影响工具本身


# 单次 tool 输出最大字节数（保护上下文不被炸）
MAX_TOOL_OUTPUT_BYTES = 32 * 1024


def mcp_text_response(text: str, *, truncate: bool = True) -> dict:
    """生成符合 MCP tool-result 规范的文本输出。

    Args:
        text: 输出文本
        truncate: 超过 MAX_TOOL_OUTPUT_BYTES 时自动截断并附"... [truncated]"后缀
    """
    if truncate:
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_TOOL_OUTPUT_BYTES:
            cut = encoded[:MAX_TOOL_OUTPUT_BYTES].decode("utf-8", errors="ignore")
            text = cut + "\n\n... [truncated due to size limit]"
    return {"content": [{"type": "text", "text": text}]}


def mcp_json_response(obj, *, truncate: bool = True) -> dict:
    """生成符合 MCP tool-result 规范的 JSON 文本输出。"""
    return mcp_text_response(
        json.dumps(obj, ensure_ascii=False, indent=2, default=str),
        truncate=truncate,
    )


__all__ = [
    "tool",
    "ToolContext",
    "set_ctx",
    "reset_ctx",
    "get_ctx",
    "replace_ctx",
    "emit_event",
    "mcp_text_response",
    "mcp_json_response",
    "MAX_TOOL_OUTPUT_BYTES",
]

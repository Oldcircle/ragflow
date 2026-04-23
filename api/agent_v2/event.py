"""Agent v2 统一事件类型。

SDK 返回的 Message 种类多样，我们把它们翻译成前端友好的统一事件流。
"""

from dataclasses import dataclass, field
from typing import Any, Literal


EventType = Literal[
    "text_delta",
    "thinking",
    "tool_call_start",
    "tool_call_end",
    "subagent_start",
    "subagent_end",
    "citation_warning",
    # Phase 2.6 — interactive tools that yield to the user before Agent continues
    "ask_user_question",
    "plan_submitted",
    "error",
    "end",
]


@dataclass
class Event:
    """统一事件。通过 SSE 流式返回给前端。"""

    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}


def text_delta(text: str) -> Event:
    return Event(type="text_delta", data={"text": text})


def thinking(text: str) -> Event:
    return Event(type="thinking", data={"text": text})


def tool_call_start(tool_id: str, name: str, args: dict) -> Event:
    return Event(
        type="tool_call_start",
        data={"id": tool_id, "name": name, "args": args},
    )


def tool_call_end(
    tool_id: str,
    result: Any = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> Event:
    return Event(
        type="tool_call_end",
        data={
            "id": tool_id,
            "result": result,
            "error": error,
            "duration_ms": duration_ms,
        },
    )


def error(code: str, message: str) -> Event:
    return Event(type="error", data={"code": code, "message": message})


def end(usage: dict | None = None) -> Event:
    return Event(type="end", data={"usage": usage or {}})


def subagent_start(
    *,
    trace_id: str,
    description: str,
    parent_tool_call_id: str,
    allowed_tools: list[str],
    max_turns: int,
    max_budget_usd: float | None,
) -> Event:
    return Event(
        type="subagent_start",
        data={
            "trace_id": trace_id,
            "description": description,
            "parent_tool_call_id": parent_tool_call_id,
            "allowed_tools": allowed_tools,
            "max_turns": max_turns,
            "max_budget_usd": max_budget_usd,
        },
    )


def citation_warning(
    *,
    issues: list[dict],
    level: Literal["warn", "strict_rewritten", "strict_failed"],
) -> Event:
    """P2.5.1 — 答复里 [N] 脚注或数字型断言未通过 EvidenceIndex 校验。

    ``issues`` 每项形如::

        {"kind": "number_unsupported", "citation_index": None,
         "claim": "...那句话...",
         "detail": "Numerical claim '21 周岁' is not present..."}
    """
    return Event(
        type="citation_warning",
        data={"issues": issues or [], "level": level},
    )


def ask_user_question(
    *,
    pending_id: str,
    question: str,
    header: str,
    options: list[dict],
    multi_select: bool,
    tool_use_id: str | None,
) -> Event:
    """Phase 2.6 — Agent 向用户发起多选澄清。

    前端渲染多选/单选卡片；用户选择后在**下一个 user turn** 里带回答复。
    ``pending_id`` 允许前端把响应和原问题对齐。
    """
    return Event(
        type="ask_user_question",
        data={
            "pending_id": pending_id,
            "tool_use_id": tool_use_id,
            "question": question,
            "header": header,
            "options": options,
            "multi_select": bool(multi_select),
        },
    )


def plan_submitted(
    *,
    pending_id: str,
    title: str,
    steps: list[str],
    affected_resources: list[dict],
    risk_level: str,
    estimated_cost_usd: float | None,
    reversible: bool,
    reversible_hint: str | None,
    tool_use_id: str | None,
) -> Event:
    """Phase 2.6 — Agent 提交执行计划供用户审批。"""
    return Event(
        type="plan_submitted",
        data={
            "pending_id": pending_id,
            "tool_use_id": tool_use_id,
            "title": title,
            "steps": steps,
            "affected_resources": affected_resources,
            "risk_level": risk_level,
            "estimated_cost_usd": estimated_cost_usd,
            "reversible": bool(reversible),
            "reversible_hint": reversible_hint,
        },
    )


def subagent_end(
    *,
    trace_id: str,
    status: Literal["success", "error", "truncated", "cancelled"],
    result_preview: str | None = None,
    error_message: str | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    token_usage: dict | None = None,
) -> Event:
    return Event(
        type="subagent_end",
        data={
            "trace_id": trace_id,
            "status": status,
            "result_preview": result_preview,
            "error": error_message,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
            "token_usage": token_usage or {},
        },
    )

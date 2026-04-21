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

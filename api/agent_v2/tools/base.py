"""工具基础设施：SDK 装饰器 re-export + 运行时上下文。

Runner 在每次 ``run()`` 前用 ``set_ctx()`` 注入 RAGFlow 上下文（基于 ContextVar，
同事件循环内自动传播）。工具通过 ``get_ctx()`` 读取。

不使用环境变量的原因：MCP In-Process Server 的工具在 **SDK 调用方的 Python 进程**
里执行，而 ``ClaudeAgentOptions.env`` 的环境变量只注入到 Claude Code CLI **子进程**。
两者不是同一个进程空间，env vars 传不过来。
"""

from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass, field

from claude_agent_sdk import tool  # noqa: F401 — re-export for tools

from ..errors import ContextError


@dataclass(frozen=True)
class ToolContext:
    """Runner 向所有工具透传的运行时上下文。"""

    tenant_id: str
    kb_ids: tuple[str, ...]
    user_id: str | None = None
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
    "mcp_text_response",
    "mcp_json_response",
    "MAX_TOOL_OUTPUT_BYTES",
]

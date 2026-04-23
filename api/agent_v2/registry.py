"""工具聚合 — 构造一个 MCP In-Process Server 供 SDK 使用。"""

from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server

from .tools.ask_user_question import ask_user_question
from .tools.doc_ops import (
    doc_archive,
    doc_rename,
    doc_reparse,
    doc_tag,
    doc_upload_from_url,
    kb_create,
)
from .tools.rag_graph_query import rag_graph_query
from .tools.rag_list_docs import rag_list_docs
from .tools.rag_read_doc import rag_read_doc
from .tools.rag_retrieve import rag_retrieve
from .tools.spawn_subagent import spawn_subagent
from .tools.submit_plan import submit_plan

# 所有已实现工具的注册表
ALL_TOOLS = {
    # 读：RAG 检索
    "rag_retrieve": rag_retrieve,
    "rag_list_docs": rag_list_docs,
    "rag_read_doc": rag_read_doc,
    "rag_graph_query": rag_graph_query,
    # 委派
    "spawn_subagent": spawn_subagent,
    # Phase 2.6 — 文档写操作（sub_archivist 专用）
    "doc_tag": doc_tag,
    "doc_rename": doc_rename,
    "doc_archive": doc_archive,
    "doc_reparse": doc_reparse,
    "doc_upload_from_url": doc_upload_from_url,
    "kb_create": kb_create,
    # Phase 2.6 — 交互式工具
    "ask_user_question": ask_user_question,
    "submit_plan": submit_plan,
}

# MCP server 名（给 SDK 用）；SDK 生成的工具名是 ``mcp__<server>__<tool>``
MCP_SERVER_NAME = "ragflow"


def list_tool_names() -> list[str]:
    """返回所有 Agent 可见的 MCP 工具名（带 SDK 前缀）。"""
    return [f"mcp__{MCP_SERVER_NAME}__{n}" for n in ALL_TOOLS]


def build_mcp_server(enabled: list[str] | None = None):
    """构造 MCP In-Process Server。

    Args:
        enabled: 仅启用这些工具名（短名，如 ``["rag_retrieve"]``）。
                 ``None`` 表示启用全部已实现工具。

    Returns:
        ``McpSdkServerConfig`` 可直接传给 ``ClaudeAgentOptions.mcp_servers``。
    """
    if enabled is None:
        tools = list(ALL_TOOLS.values())
    else:
        tools = [ALL_TOOLS[n] for n in enabled if n in ALL_TOOLS]
    return create_sdk_mcp_server(MCP_SERVER_NAME, "0.1.0", tools=tools)

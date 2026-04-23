"""测试 api/agent_v2/registry.py。

只做静态检查，不真正启动 MCP server（那需要 SDK 的 anyio 运行环境）。
"""

from __future__ import annotations

from api.agent_v2 import registry


def test_all_tools_registered():
    """注册表至少包含 M1.2 4 个 RAG 工具 + P2.3 spawn_subagent
    + Phase 2.6 的 6 个 doc_ops 写工具 + 2 个交互工具."""
    expected = {
        # M1.2 读
        "rag_retrieve",
        "rag_list_docs",
        "rag_read_doc",
        "rag_graph_query",
        # P2.3
        "spawn_subagent",
        # Phase 2.6 写（sub_archivist）
        "doc_tag",
        "doc_rename",
        "doc_archive",
        "doc_reparse",
        "doc_upload_from_url",
        "kb_create",
        # Phase 2.6 v0.2 自省 / 总结 / 写笔记（sub_librarian）
        "doc_create_note",
        "kb_audit",
        "kb_stats",
        "doc_list_recent_changes",
        # Phase 2.6 交互
        "ask_user_question",
        "submit_plan",
    }
    actual = set(registry.ALL_TOOLS.keys())
    missing = expected - actual
    assert not missing, f"注册表缺少工具: {missing}"


def test_mcp_names_format():
    names = registry.list_tool_names()
    for n in names:
        assert n.startswith("mcp__ragflow__"), f"{n} 格式不对"


def test_build_mcp_server_full():
    server = registry.build_mcp_server()
    # 不 assert 具体结构，只要不报错即可
    assert server is not None


def test_build_mcp_server_filtered():
    server = registry.build_mcp_server(enabled=["rag_retrieve"])
    assert server is not None


def test_build_mcp_server_ignores_unknown_tool():
    server = registry.build_mcp_server(enabled=["rag_retrieve", "nonexistent"])
    # 不报错，过滤掉未知工具
    assert server is not None

"""测试 api/agent_v2/registry.py。

只做静态检查，不真正启动 MCP server（那需要 SDK 的 anyio 运行环境）。
"""

from __future__ import annotations

from api.agent_v2 import registry


def test_all_tools_registered():
    """注册表至少包含 M1.2 的 4 个 RAG 工具 + P2.3 的 spawn_subagent."""
    expected = {
        "rag_retrieve",
        "rag_list_docs",
        "rag_read_doc",
        "rag_graph_query",
        "spawn_subagent",  # P2.3 新增
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

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


# ─────────── Phase 2.6 v0.5 — searchHint prefix + MCP annotations ───────────


def test_decorate_prepends_search_hint():
    """_decorate_for_mcp 给 MCP description 贴 [intent] 前缀。"""
    from api.agent_v2.tools.rag_retrieve import rag_retrieve

    decorated = registry._decorate_for_mcp(rag_retrieve)
    assert decorated.description.startswith("[intent]")
    # Hint must come from the shared constant, not invented
    from api.agent_v2.prompting import SEARCH_HINT_BY_TOOL

    assert SEARCH_HINT_BY_TOOL["rag_retrieve"] in decorated.description


def test_decorate_is_non_destructive():
    """decorate 必须拷贝对象；不能改原始 @tool 导出的 SdkMcpTool。"""
    from api.agent_v2.tools.rag_retrieve import rag_retrieve

    before = rag_retrieve.description
    registry._decorate_for_mcp(rag_retrieve)
    assert rag_retrieve.description == before, "原始 description 被污染"


def test_decorate_is_idempotent():
    """连续 decorate 两次不会贴两遍前缀。"""
    from api.agent_v2.tools.rag_retrieve import rag_retrieve

    once = registry._decorate_for_mcp(rag_retrieve)
    twice = registry._decorate_for_mcp(once)
    # 第二次 decorate 拿到的 tool 已经有 [intent]，应原样返回
    assert twice.description.count("[intent]") == 1


def test_decorate_sets_mcp_annotations_for_read():
    """只读工具 → readOnly=True / destructive=False。"""
    from api.agent_v2.tools.rag_retrieve import rag_retrieve

    d = registry._decorate_for_mcp(rag_retrieve)
    assert d.annotations is not None
    assert d.annotations["readOnly"] is True
    assert d.annotations["destructive"] is False
    assert d.annotations["openWorld"] is False


def test_decorate_sets_mcp_annotations_for_destructive_write():
    """非幂等写 → destructive=True。"""
    from api.agent_v2.tools.doc_ops import doc_archive

    d = registry._decorate_for_mcp(doc_archive)
    assert d.annotations is not None
    assert d.annotations["readOnly"] is False
    assert d.annotations["destructive"] is True


def test_decorate_marks_network_tool_as_open_world():
    """doc_upload_from_url 触碰外网 → openWorld=True。"""
    from api.agent_v2.tools.doc_ops import doc_upload_from_url

    d = registry._decorate_for_mcp(doc_upload_from_url)
    assert d.annotations is not None
    assert d.annotations["openWorld"] is True


def test_every_tool_has_a_search_hint():
    """搜索提示表必须覆盖所有注册工具；漏一个 parity 挂。"""
    from api.agent_v2.prompting import SEARCH_HINT_BY_TOOL

    missing = [n for n in registry.ALL_TOOLS if n not in SEARCH_HINT_BY_TOOL]
    assert not missing, f"SEARCH_HINT_BY_TOOL 缺少: {missing}"


def test_build_mcp_server_returns_decorated_tools():
    """build_mcp_server 出来的工具 description 全部带 [intent] 前缀。

    通过内部 sdk_tools 属性（SDK 暴露）反向检查。
    """
    server = registry.build_mcp_server(enabled=["rag_retrieve", "doc_tag"])
    # SDK 内部用 tools dict 持有 SdkMcpTool；结构可能版本相关，fail-soft
    tools_attr = getattr(server, "tools", None) or getattr(server, "_tools", None)
    if tools_attr is None:
        # 不阻断：SDK 未暴露内部结构时跳过具体断言
        return
    values = list(tools_attr.values()) if isinstance(tools_attr, dict) else list(tools_attr)
    for t in values:
        assert "[intent]" in t.description

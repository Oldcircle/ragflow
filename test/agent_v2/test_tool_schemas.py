"""工具 JSON Schema 结构健全性检查。"""

from __future__ import annotations

import pytest

from api.agent_v2 import registry


@pytest.mark.parametrize("name", ["rag_retrieve", "rag_list_docs", "rag_read_doc", "rag_graph_query"])
def test_tool_has_non_empty_description(name):
    tool = registry.ALL_TOOLS[name]
    desc = getattr(tool, "description", "")
    assert isinstance(desc, str) and len(desc) >= 20, f"{name} 描述太短或空"


@pytest.mark.parametrize("name", ["rag_retrieve", "rag_list_docs", "rag_read_doc", "rag_graph_query"])
def test_tool_schema_is_valid(name):
    tool = registry.ALL_TOOLS[name]
    schema = tool.input_schema
    assert isinstance(schema, dict), f"{name} schema 必须是 dict"
    assert schema.get("type") == "object", f"{name} schema.type 应为 object"
    assert "properties" in schema, f"{name} schema.properties 缺失"


@pytest.mark.parametrize("name", ["rag_retrieve", "rag_list_docs", "rag_read_doc", "rag_graph_query"])
def test_tool_handler_is_async_callable(name):
    import inspect

    tool = registry.ALL_TOOLS[name]
    handler = tool.handler
    assert callable(handler), f"{name} handler 不是 callable"
    assert inspect.iscoroutinefunction(handler), f"{name} handler 必须是 async"


def test_rag_retrieve_required_fields():
    schema = registry.ALL_TOOLS["rag_retrieve"].input_schema
    assert "query" in schema.get("required", []), "rag_retrieve 必须要求 query"


def test_rag_read_doc_required_fields():
    schema = registry.ALL_TOOLS["rag_read_doc"].input_schema
    assert "doc_id" in schema.get("required", []), "rag_read_doc 必须要求 doc_id"

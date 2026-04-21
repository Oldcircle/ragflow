"""测试 api/agent_v2/tools/base.py 的纯函数部分（上下文 + 截断）。"""

from __future__ import annotations

import json

import pytest

from api.agent_v2.errors import ContextError
from api.agent_v2.tools.base import (
    MAX_TOOL_OUTPUT_BYTES,
    ToolContext,
    get_ctx,
    mcp_json_response,
    mcp_text_response,
    reset_ctx,
    set_ctx,
)


# ---------- ToolContext + ContextVar ----------


def test_get_ctx_outside_scope_raises():
    with pytest.raises(ContextError):
        get_ctx()


def test_set_ctx_and_get_ctx_roundtrip():
    ctx = ToolContext(tenant_id="t1", kb_ids=("kb1", "kb2"), user_id="u1")
    token = set_ctx(ctx)
    try:
        got = get_ctx()
        assert got.tenant_id == "t1"
        assert got.kb_ids == ("kb1", "kb2")
        assert got.user_id == "u1"
    finally:
        reset_ctx(token)

    with pytest.raises(ContextError):
        get_ctx()


def test_get_ctx_missing_required_field():
    ctx = ToolContext(tenant_id="t1", kb_ids=())  # kb_ids empty
    token = set_ctx(ctx)
    try:
        with pytest.raises(ContextError, match="kb_ids"):
            get_ctx()
    finally:
        reset_ctx(token)


def test_get_ctx_custom_require_list():
    ctx = ToolContext(tenant_id="t1", kb_ids=())
    token = set_ctx(ctx)
    try:
        got = get_ctx(require=["tenant_id"])  # kb_ids not required
        assert got.tenant_id == "t1"
    finally:
        reset_ctx(token)


# ---------- MCP 响应格式 + 截断 ----------


def test_mcp_text_response_basic():
    r = mcp_text_response("hello")
    assert r == {"content": [{"type": "text", "text": "hello"}]}


def test_mcp_json_response_serialises_utf8():
    r = mcp_json_response({"cn": "中文"})
    assert "中文" in r["content"][0]["text"]


def test_mcp_text_response_truncates_oversized():
    big = "A" * (MAX_TOOL_OUTPUT_BYTES + 1000)
    r = mcp_text_response(big)
    out = r["content"][0]["text"]
    assert len(out.encode("utf-8")) <= MAX_TOOL_OUTPUT_BYTES + 60  # 允许截断后缀
    assert "truncated" in out


def test_mcp_text_response_no_truncate_when_disabled():
    big = "A" * (MAX_TOOL_OUTPUT_BYTES + 1000)
    r = mcp_text_response(big, truncate=False)
    assert len(r["content"][0]["text"]) == len(big)


def test_mcp_json_response_truncates_oversized():
    big_obj = {"items": ["x" * 1000] * 100}  # ~100KB
    r = mcp_json_response(big_obj)
    text = r["content"][0]["text"]
    assert len(text.encode("utf-8")) <= MAX_TOOL_OUTPUT_BYTES + 60
    assert "truncated" in text


def test_mcp_json_response_default_str_handles_nonstandard():
    """default=str 让 json.dumps 能处理 datetime 等类型，不抛错。"""
    from datetime import datetime

    r = mcp_json_response({"t": datetime(2026, 1, 1, 12, 0, 0)})
    payload = json.loads(r["content"][0]["text"])
    assert "2026" in payload["t"]

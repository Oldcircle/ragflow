"""测试 api/agent_v2/event.py 的事件构造函数。"""

from __future__ import annotations

from api.agent_v2 import event as ev


def test_text_delta():
    e = ev.text_delta("hello")
    assert e.type == "text_delta"
    assert e.data == {"text": "hello"}


def test_thinking():
    e = ev.thinking("reasoning...")
    assert e.type == "thinking"
    assert e.data == {"text": "reasoning..."}


def test_tool_call_start():
    e = ev.tool_call_start("id-1", "rag_retrieve", {"query": "foo"})
    assert e.type == "tool_call_start"
    assert e.data["id"] == "id-1"
    assert e.data["name"] == "rag_retrieve"
    assert e.data["args"] == {"query": "foo"}


def test_tool_call_end_success():
    e = ev.tool_call_end("id-1", result={"ok": True}, duration_ms=42)
    assert e.type == "tool_call_end"
    assert e.data["error"] is None
    assert e.data["duration_ms"] == 42


def test_tool_call_end_error():
    e = ev.tool_call_end("id-2", error="boom")
    assert e.data["error"] == "boom"


def test_error_event():
    e = ev.error("ValueError", "bad input")
    assert e.type == "error"
    assert e.data == {"code": "ValueError", "message": "bad input"}


def test_end_default_usage():
    e = ev.end()
    assert e.type == "end"
    assert e.data == {"usage": {}}


def test_event_to_dict_roundtrip():
    e = ev.text_delta("hi")
    assert e.to_dict() == {"type": "text_delta", "data": {"text": "hi"}}

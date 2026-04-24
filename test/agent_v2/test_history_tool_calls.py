"""Phase 2.6 v0.5 — history now carries tool_use/tool_result breadcrumbs.

Covers two layers:

1. ``AgentV2MessageService.list_for_runner(include_tool_calls=True)`` joins
   ``AgentV2ToolCall`` rows onto each assistant message.
2. ``_format_tool_calls_for_history`` + ``AgentRunner._build_prompt_with_history``
   render those calls as a compact ``[tool] name(args) → result`` block inside
   the ``<conversation-history>`` prompt section.

The first layer is DB-bound so we only assert against the pure renderer; the
integration path is exercised indirectly by the endpoint once it's running.
"""

from __future__ import annotations

import pytest


class TestFormatToolCallsForHistory:
    """Pure function: list of ToolCall dicts → list of str lines."""

    def test_empty_returns_empty(self):
        from api.agent_v2.runner import _format_tool_calls_for_history

        assert _format_tool_calls_for_history([]) == []

    def test_none_safe(self):
        from api.agent_v2.runner import _format_tool_calls_for_history

        assert _format_tool_calls_for_history(None or []) == []

    def test_renders_tool_name_and_args(self):
        from api.agent_v2.runner import _format_tool_calls_for_history

        lines = _format_tool_calls_for_history([
            {
                "tool_name": "rag_retrieve",
                "args": {"query": "保障房申请条件", "top_n": 5},
                "result": '{"total":5}',
                "status": "success",
                "duration_ms": 412,
            }
        ])
        assert len(lines) == 1
        line = lines[0]
        assert "[tool]" in line
        assert "rag_retrieve" in line
        assert "保障房申请条件" in line
        assert "→" in line
        assert "412ms" in line

    def test_truncates_long_args(self):
        from api.agent_v2.runner import _format_tool_calls_for_history

        long_query = "x" * 500
        lines = _format_tool_calls_for_history([
            {
                "tool_name": "rag_retrieve",
                "args": {"query": long_query},
                "result": '{"ok":1}',
                "status": "success",
                "duration_ms": 1,
            }
        ])
        assert "…" in lines[0]
        # The line itself shouldn't balloon past a few hundred chars for args
        assert len(lines[0]) < 800

    def test_truncates_long_result(self):
        from api.agent_v2.runner import _format_tool_calls_for_history

        big = "y" * 2000
        lines = _format_tool_calls_for_history([
            {
                "tool_name": "kb_audit",
                "args": {"kb_id": "k1"},
                "result": big,
                "status": "success",
                "duration_ms": 600,
            }
        ])
        assert "…" in lines[0]
        assert len(lines[0]) < 1100

    def test_error_status_shows_error(self):
        from api.agent_v2.runner import _format_tool_calls_for_history

        lines = _format_tool_calls_for_history([
            {
                "tool_name": "doc_archive",
                "args": {"doc_id": "d1"},
                "result": "",
                "error": "embedding mismatch",
                "status": "error",
                "duration_ms": 50,
            }
        ])
        assert "ERROR" in lines[0]
        assert "embedding mismatch" in lines[0]

    def test_caps_at_six_per_turn(self):
        from api.agent_v2.runner import _format_tool_calls_for_history

        many = [
            {
                "tool_name": f"t{i}",
                "args": {},
                "result": "ok",
                "status": "success",
                "duration_ms": 1,
            }
            for i in range(10)
        ]
        lines = _format_tool_calls_for_history(many)
        # 6 rendered + 1 "… N more" sentinel
        assert len(lines) == 7
        assert "more call(s) omitted" in lines[-1]
        assert "4" in lines[-1]  # 10 - 6 = 4 omitted

    def test_empty_result_shows_placeholder(self):
        from api.agent_v2.runner import _format_tool_calls_for_history

        lines = _format_tool_calls_for_history([
            {
                "tool_name": "doc_tag",
                "args": {"doc_id": "d1", "operation": "set", "tags": []},
                "result": None,
                "status": "success",
                "duration_ms": 80,
            }
        ])
        assert "(no output)" in lines[0]


class TestBuildPromptWithTools:
    """End-to-end: history dict with ``tool_calls`` → rendered prompt."""

    @staticmethod
    def _build(history, user_msg="继续刚才的操作"):
        """Invoke the private prompt builder without touching SDK/env."""
        from api.agent_v2.runner import AgentRunner, ModelConfig

        runner = AgentRunner.__new__(AgentRunner)
        runner.tenant_id = "t1"
        runner.kb_ids = ["kb1"]
        runner.system_prompt = ""
        runner.model = ModelConfig()
        return runner._build_prompt_with_history(
            user_msg, history=history, summary_text=None
        )

    def test_tool_lines_appear_in_history_block(self):
        prompt = self._build(history=[
            {"role": "user", "content": "这个库里有多少政策文档？", "tool_calls": []},
            {
                "role": "assistant",
                "content": "一共 22 份，集中在 2023-2024 年",
                "tool_calls": [
                    {
                        "tool_name": "kb_stats",
                        "args": {"kb_id": "kb1"},
                        "result": '{"doc_count":22}',
                        "status": "success",
                        "duration_ms": 80,
                    }
                ],
            },
        ])
        assert "<conversation-history>" in prompt
        assert "kb_stats" in prompt
        # The tool line should be indented under the assistant message
        assert "      [tool]" in prompt

    def test_history_without_tool_calls_still_renders(self):
        """回溯兼容：老历史不带 tool_calls 字段不能炸。"""
        prompt = self._build(history=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ])
        assert "hi" in prompt
        assert "hello" in prompt

    def test_assistant_with_only_tool_activity(self):
        """assistant 纯操作、没有文本也应保留，在历史里标记 tool activity only。"""
        prompt = self._build(history=[
            {"role": "user", "content": "给文档 d1 加 urgent 标签"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "tool_name": "doc_tag",
                        "args": {"doc_id": "d1", "operation": "add", "tags": ["urgent"]},
                        "result": '{"status":"ok"}',
                        "status": "success",
                        "duration_ms": 200,
                    }
                ],
            },
        ])
        assert "tool activity only" in prompt
        assert "doc_tag" in prompt


class TestListForRunnerIncludeToolCalls:
    """Shape-only test for the service method — DB-agnostic."""

    def test_include_tool_calls_flag_exists(self):
        import inspect
        from api.db.services.agent_v2_service import AgentV2MessageService

        sig = inspect.signature(AgentV2MessageService.list_for_runner.__func__)
        assert "include_tool_calls" in sig.parameters
        # default must be False — 向后兼容老 caller
        assert sig.parameters["include_tool_calls"].default is False

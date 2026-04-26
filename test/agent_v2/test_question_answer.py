"""Phase 2.8.3 — Interactive Tool Pause Framework 单元测试。

覆盖三层（runner force-stop 走集成测，本文件聚焦 unit）：
  1. ``parse_question_answer`` 前缀解析（多种合法 / 非法形态）
  2. ``augment_for_question_answer`` directive 注入
  3. ``ask_user_question`` / ``submit_plan`` 工具返回值带 ``pause_loop=True``
  4. ``_has_pause_loop_marker`` runner helper 的真假判断

参照 ``test_interactive_tools.py`` + ``test_validators.py`` 的现有单测风格。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from api.agent_v2.plan_decision import (
    augment_for_question_answer,
    parse_question_answer,
)
from api.agent_v2.runner import _has_pause_loop_marker
from api.agent_v2.tools.ask_user_question import ask_user_question
from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.submit_plan import submit_plan


# ───────── parse_question_answer ─────────


@pytest.mark.p0
class TestParseQuestionAnswer:
    def test_bracketed_single_label(self):
        cleaned, ans = parse_question_answer("[answer: 选项A]")
        assert ans == {"labels": ["选项A"], "raw_payload": "选项A"}
        assert cleaned == ""

    def test_bracketed_multi_label_comma(self):
        cleaned, ans = parse_question_answer("[answer: A, B, C]")
        assert ans is not None
        assert ans["labels"] == ["A", "B", "C"]
        assert cleaned == ""

    def test_bracketed_multi_label_chinese_separator(self):
        cleaned, ans = parse_question_answer("[answer: 选项A、选项B]")
        assert ans is not None
        assert ans["labels"] == ["选项A", "选项B"]

    def test_bracketed_chinese_prefix(self):
        cleaned, ans = parse_question_answer("[选择: 是]")
        assert ans is not None
        assert ans["labels"] == ["是"]

    def test_bracketed_user_answer_alias(self):
        cleaned, ans = parse_question_answer("[user answer: 法务]")
        assert ans is not None
        assert ans["labels"] == ["法务"]

    def test_bracketed_with_residual_note(self):
        cleaned, ans = parse_question_answer("[answer: A] 顺便加个备注")
        assert ans is not None and ans["labels"] == ["A"]
        assert cleaned == "顺便加个备注"

    def test_bare_text_no_match(self):
        """无 [] 包裹 → 不匹配，避免普通 user message 被误识别。"""
        cleaned, ans = parse_question_answer("answer: 选项A")
        assert ans is None
        assert cleaned == "answer: 选项A"

    def test_unrelated_bracket_no_match(self):
        cleaned, ans = parse_question_answer("[plan approved]")
        assert ans is None
        assert cleaned == "[plan approved]"

    def test_empty_payload_no_match(self):
        """``[answer:]`` 命中前缀但 payload 空 → 当未识别处理。"""
        cleaned, ans = parse_question_answer("[answer:]")
        assert ans is None
        # 不剥前缀，原样返回（避免吞掉一段后续文字）
        assert cleaned == "[answer:]"

    def test_empty_message(self):
        cleaned, ans = parse_question_answer("")
        assert ans is None
        assert cleaned == ""

    def test_oversized_bracket_no_match(self):
        """超过 256 字的 ``[...]`` 不消费——防一段长 markdown 被误剥。"""
        long_payload = "x" * 300
        msg = f"[answer: {long_payload}] tail"
        cleaned, ans = parse_question_answer(msg)
        assert ans is None
        assert cleaned == msg


# ───────── augment_for_question_answer ─────────


@pytest.mark.p1
class TestAugmentForQuestionAnswer:
    def test_directive_injected_with_labels(self):
        out = augment_for_question_answer(
            user_message="",
            answer={"labels": ["法务"], "raw_payload": "法务"},
        )
        assert "[question system]" in out
        assert "**法务**" in out

    def test_user_note_appended_after_directive(self):
        out = augment_for_question_answer(
            user_message="另外别忘了 X",
            answer={"labels": ["A"], "raw_payload": "A"},
        )
        assert out.startswith("[question system]")
        assert "另外别忘了 X" in out
        assert "User's accompanying note:" in out

    def test_no_answer_returns_message_unchanged(self):
        original = "原样保留"
        assert augment_for_question_answer(user_message=original, answer=None) == original

    def test_empty_labels_returns_message_unchanged(self):
        out = augment_for_question_answer(
            user_message="x", answer={"labels": [], "raw_payload": ""}
        )
        assert out == "x"


# ───────── _has_pause_loop_marker ─────────


@pytest.mark.p0
class TestHasPauseLoopMarker:
    def test_dict_with_marker_true(self):
        assert _has_pause_loop_marker({"pause_loop": True, "x": 1}) is True

    def test_dict_with_marker_false(self):
        assert _has_pause_loop_marker({"pause_loop": False}) is False

    def test_dict_without_marker(self):
        assert _has_pause_loop_marker({"status": "ok"}) is False

    def test_json_string_with_marker(self):
        assert _has_pause_loop_marker('{"pause_loop": true, "x": 1}') is True

    def test_unparseable_string_false(self):
        # 非 JSON 字符串绝不能误命中（防御 — 普通工具 result 经常是文本）
        assert _has_pause_loop_marker("just some text") is False

    def test_none_false(self):
        assert _has_pause_loop_marker(None) is False

    def test_non_dict_json_false(self):
        # JSON list / number 也不算命中
        assert _has_pause_loop_marker("[1,2,3]") is False


# ───────── ask_user_question pause_loop 注入 ─────────


def _call_tool(tool, args):
    return asyncio.run(tool.handler(args))


def _parse_resp(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _ctx_with_session(emitted: list | None = None) -> ToolContext:
    async def emit(ev):
        if emitted is not None:
            emitted.append(ev)

    return ToolContext(
        tenant_id="t1",
        kb_ids=("kb1",),
        user_id="u1",
        session_id="s1",
        event_emitter=emit,
        current_tool_call_id="toolu_fake",
    )


@pytest.mark.p0
class TestAskUserQuestionPauseMarker:
    def test_response_has_pause_loop_true(self):
        token = set_ctx(_ctx_with_session([]))
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"), \
                 patch(
                     "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_question"
                 ):
                resp = _call_tool(
                    ask_user_question,
                    {
                        "question": "Pick one?",
                        "header": "Pick",
                        "options": [
                            {"label": "A", "description": ""},
                            {"label": "B", "description": ""},
                        ],
                    },
                )
        finally:
            reset_ctx(token)
        payload = _parse_resp(resp)
        assert payload.get("pause_loop") is True
        assert payload["status"] == "waiting"
        assert payload["pending_id"]

    def test_set_pending_question_called_with_body(self):
        """工具应把完整 question payload 持久化到 session.pending_question_body。"""
        token = set_ctx(_ctx_with_session([]))
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"), \
                 patch(
                     "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_question"
                 ) as mock_set:
                _call_tool(
                    ask_user_question,
                    {
                        "question": "Pick one?",
                        "header": "Pick",
                        "options": [
                            {"label": "A", "description": "first"},
                            {"label": "B", "description": "second"},
                        ],
                        "multi_select": True,
                    },
                )
        finally:
            reset_ctx(token)
        assert mock_set.call_count == 1
        kwargs = mock_set.call_args.kwargs
        assert kwargs["session_id"] == "s1"
        body = kwargs["question_body"]
        assert body["question"] == "Pick one?"
        assert body["multi_select"] is True
        assert len(body["options"]) == 2


# ───────── submit_plan pause_loop 注入 ─────────


@pytest.mark.p0
class TestSubmitPlanPauseMarker:
    def test_response_has_pause_loop_true(self):
        token = set_ctx(_ctx_with_session([]))
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"), \
                 patch(
                     "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan"
                 ):
                resp = _call_tool(
                    submit_plan,
                    {
                        "title": "Tag 3 docs",
                        "steps": ["doc_tag d1", "doc_tag d2", "doc_tag d3"],
                        "affected_resources": [
                            {"kind": "doc", "id": "d1"},
                            {"kind": "doc", "id": "d2"},
                        ],
                        "risk_level": "low",
                        "estimated_cost_usd": 0.0,
                        "reversible": True,
                    },
                )
        finally:
            reset_ctx(token)
        payload = _parse_resp(resp)
        assert payload.get("pause_loop") is True
        assert payload["status"] == "waiting"

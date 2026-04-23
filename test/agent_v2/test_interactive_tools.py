"""Phase 2.6 — ask_user_question + submit_plan 交互工具单测。

覆盖：输入校验、SSE 事件是否 emit、audit 是否写 allow。
实际 HTTP 层的异步回流在集成/真机里验证（超出 unit 范畴）。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from api.agent_v2.tools.ask_user_question import ask_user_question
from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.submit_plan import submit_plan


def _call(tool, args):
    return asyncio.run(tool.handler(args))


def _parse(resp):
    return json.loads(resp["content"][0]["text"])


def _ctx(emitted: list | None = None):
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


# ───────── ask_user_question ─────────


@pytest.mark.p0
class TestAskUserQuestion:
    def test_emits_event_and_returns_waiting(self):
        emitted: list = []
        token = set_ctx(_ctx(emitted))
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    ask_user_question,
                    {
                        "question": "归档到哪个 KB？",
                        "header": "目标库",
                        "options": [
                            {"label": "法务", "description": "合同、协议"},
                            {"label": "合规", "description": "规范、内控"},
                        ],
                    },
                )
        finally:
            reset_ctx(token)
        payload = _parse(resp)
        assert payload["status"] == "waiting"
        assert payload["options_count"] == 2
        assert payload["multi_select"] is False
        assert len(emitted) == 1
        ev = emitted[0]
        assert ev.type == "ask_user_question"
        assert ev.data["question"] == "归档到哪个 KB？"
        assert ev.data["header"] == "目标库"
        assert len(ev.data["options"]) == 2

    def test_too_few_options_rejected(self):
        token = set_ctx(_ctx())
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    ask_user_question,
                    {
                        "question": "X?",
                        "header": "X",
                        "options": [{"label": "only one"}],
                    },
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_too_many_options_rejected(self):
        token = set_ctx(_ctx())
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    ask_user_question,
                    {
                        "question": "pick?",
                        "header": "X",
                        "options": [{"label": f"o{i}"} for i in range(5)],
                    },
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_empty_question_rejected(self):
        token = set_ctx(_ctx())
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    ask_user_question,
                    {
                        "question": "   ",
                        "header": "X",
                        "options": [{"label": "a"}, {"label": "b"}],
                    },
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_multi_select_preserved(self):
        emitted: list = []
        token = set_ctx(_ctx(emitted))
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    ask_user_question,
                    {
                        "question": "pick many",
                        "header": "tags",
                        "options": [
                            {"label": "a"}, {"label": "b"}, {"label": "c"},
                        ],
                        "multi_select": True,
                    },
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["multi_select"] is True
        assert emitted[0].data["multi_select"] is True

    def test_audit_log_written_allow(self):
        audits: list = []
        token = set_ctx(_ctx())
        try:
            with patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                _call(
                    ask_user_question,
                    {
                        "question": "q?",
                        "header": "H",
                        "options": [{"label": "a"}, {"label": "b"}],
                    },
                )
        finally:
            reset_ctx(token)
        assert len(audits) == 1
        assert audits[0]["action"] == "agent_v2.ask_user"
        assert audits[0]["result"] == "allow"
        assert "pending_id" in audits[0]["metadata"]


# ───────── submit_plan ─────────


@pytest.mark.p0
class TestSubmitPlan:
    def test_emits_plan_submitted_event(self):
        emitted: list = []
        token = set_ctx(_ctx(emitted))
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    submit_plan,
                    {
                        "title": "归档 12 份合同",
                        "steps": [
                            "rag_list_docs 筛出 expired=true 的合同",
                            "doc_archive 每份到法务-过期库",
                            "汇总结果",
                        ],
                        "affected_resources": [
                            {"kind": "kb", "id": "law-active", "action": "source"},
                            {"kind": "kb", "id": "law-expired", "action": "target"},
                            {"kind": "doc_count", "value": 12, "action": "move"},
                        ],
                        "risk_level": "medium",
                        "estimated_cost_usd": 0.05,
                        "reversible": True,
                    },
                )
        finally:
            reset_ctx(token)
        payload = _parse(resp)
        assert payload["status"] == "waiting"
        assert payload["steps_count"] == 3
        assert payload["risk_level"] == "medium"
        assert len(emitted) == 1
        ev = emitted[0]
        assert ev.type == "plan_submitted"
        assert ev.data["title"] == "归档 12 份合同"
        assert len(ev.data["steps"]) == 3
        assert ev.data["estimated_cost_usd"] == 0.05

    def test_invalid_risk_rejected(self):
        token = set_ctx(_ctx())
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    submit_plan,
                    {
                        "title": "T",
                        "steps": ["a"],
                        "risk_level": "catastrophic",
                    },
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_empty_steps_rejected(self):
        token = set_ctx(_ctx())
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    submit_plan, {"title": "T", "steps": []},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_too_many_steps_rejected(self):
        token = set_ctx(_ctx())
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    submit_plan,
                    {
                        "title": "T",
                        "steps": [f"step {i}" for i in range(20)],
                    },
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_negative_cost_coerced_to_zero(self):
        emitted: list = []
        token = set_ctx(_ctx(emitted))
        try:
            with patch("api.db.services.audit_log_service.AuditLogService.log"):
                _call(
                    submit_plan,
                    {
                        "title": "T",
                        "steps": ["a"],
                        "estimated_cost_usd": -99.9,
                    },
                )
        finally:
            reset_ctx(token)
        assert emitted[0].data["estimated_cost_usd"] == 0.0

    def test_audit_log_captures_plan_shape(self):
        audits: list = []
        token = set_ctx(_ctx())
        try:
            with patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                _call(
                    submit_plan,
                    {
                        "title": "重解析 5 份",
                        "steps": ["1", "2"],
                        "risk_level": "high",
                        "reversible": False,
                    },
                )
        finally:
            reset_ctx(token)
        meta = audits[0]["metadata"]
        assert meta["risk_level"] == "high"
        assert meta["reversible"] is False
        assert meta["step_count"] == 2

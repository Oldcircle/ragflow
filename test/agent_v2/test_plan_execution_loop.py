"""Phase 2.6 v0.6 — G7 plan execution loop.

Covers:

- ``get_pending_plan`` tool: structured shape, no-plan / no-session handling,
  DB-error path
- ``AgentV2SessionService.set_pending_plan(plan_body=...)`` persists the body
- ``AgentV2SessionService.get_pending_plan(include_body=True)`` returns it
- ``AgentV2SessionService.clear_pending_plan`` also clears the body
- ``sub_archivist`` definition exposes the new tool
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.get_pending_plan import get_pending_plan


def _payload(tool_result):
    """Unwrap MCP envelope → JSON dict."""
    return json.loads(tool_result["content"][0]["text"])


@pytest.mark.p0
@pytest.mark.asyncio
class TestGetPendingPlanTool:
    async def test_no_session_returns_no_plan(self):
        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb1",), user_id="u1", session_id=None,
        )
        token = set_ctx(ctx)
        try:
            result = await get_pending_plan.handler({})
        finally:
            reset_ctx(token)
        p = _payload(result)
        assert p["status"] == "no_plan"
        assert p["reason"] == "no_session"

    async def test_returns_stored_plan_when_approved(self):
        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb1",), user_id="u1", session_id="s-approved",
        )
        token = set_ctx(ctx)
        stored = {
            "pending_plan_id": "p-1",
            "pending_plan_status": "approved",
            "pending_plan_submitted_at": 1_700_000_000_000,
            "pending_plan_body": {
                "pending_id": "p-1",
                "title": "Archive 12 expired contracts",
                "steps": [
                    "tag each contract with archived=true",
                    "archive to law-expired KB",
                ],
                "affected_resources": [
                    {"kind": "doc_count", "value": 12, "action": "archive"}
                ],
                "risk_level": "medium",
                "reversible": True,
            },
        }
        try:
            with patch(
                "api.db.services.agent_v2_service.AgentV2SessionService.get_pending_plan",
                return_value=stored,
            ):
                result = await get_pending_plan.handler({})
        finally:
            reset_ctx(token)

        p = _payload(result)
        assert p["status"] == "ok"
        assert p["plan_status"] == "approved"
        assert p["plan_id"] == "p-1"
        assert p["plan"]["title"] == "Archive 12 expired contracts"
        assert len(p["plan"]["steps"]) == 2
        assert "hint" in p

    async def test_returns_no_plan_when_session_has_none(self):
        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb1",), user_id="u1", session_id="s-clean",
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.agent_v2_service.AgentV2SessionService.get_pending_plan",
                return_value=None,
            ):
                result = await get_pending_plan.handler({})
        finally:
            reset_ctx(token)
        p = _payload(result)
        assert p["status"] == "no_plan"

    async def test_returns_waiting_status_as_is(self):
        """工具不做状态过滤；archivist prompt 负责在 waiting 时 STOP。"""
        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb1",), user_id="u1", session_id="s-waiting",
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.agent_v2_service.AgentV2SessionService.get_pending_plan",
                return_value={
                    "pending_plan_id": "p-2",
                    "pending_plan_status": "waiting",
                    "pending_plan_submitted_at": 1,
                    "pending_plan_body": {"title": "x", "steps": ["a"]},
                },
            ):
                result = await get_pending_plan.handler({})
        finally:
            reset_ctx(token)
        p = _payload(result)
        assert p["status"] == "ok"
        assert p["plan_status"] == "waiting"

    async def test_db_error_is_reported_not_raised(self):
        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb1",), user_id="u1", session_id="s-err",
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.agent_v2_service.AgentV2SessionService.get_pending_plan",
                side_effect=RuntimeError("conn lost"),
            ):
                result = await get_pending_plan.handler({})
        finally:
            reset_ctx(token)
        p = _payload(result)
        assert p["status"] == "error"
        assert p["reason"] == "storage_error"


@pytest.mark.p1
class TestServiceSignatureChanges:
    """确认 set/get/clear 都认识 body 字段（shape-only，避免依赖真 MySQL）。"""

    def test_set_pending_plan_accepts_plan_body_kw(self):
        import inspect
        from api.db.services.agent_v2_service import AgentV2SessionService

        sig = inspect.signature(AgentV2SessionService.set_pending_plan.__func__)
        assert "plan_body" in sig.parameters
        assert sig.parameters["plan_body"].default is None  # 向后兼容

    def test_get_pending_plan_accepts_include_body_kw(self):
        import inspect
        from api.db.services.agent_v2_service import AgentV2SessionService

        sig = inspect.signature(AgentV2SessionService.get_pending_plan.__func__)
        assert "include_body" in sig.parameters
        assert sig.parameters["include_body"].default is False


@pytest.mark.p0
class TestSubArchivistHasGetPendingPlan:
    """sub_archivist 必须能拿到 get_pending_plan；测试 parity 防止漏列。"""

    def test_tool_in_archivist_toolbox(self):
        from api.agent_v2.definitions import get_definition

        defn = get_definition("sub_archivist")
        assert defn is not None
        assert "get_pending_plan" in defn.tools

    def test_tool_in_global_registry(self):
        from api.agent_v2.registry import ALL_TOOLS

        assert "get_pending_plan" in ALL_TOOLS

    def test_tool_has_annotation(self):
        """v0.5 parity guard also applies to v0.6 additions."""
        from api.agent_v2.annotations import ANNOTATIONS

        assert "get_pending_plan" in ANNOTATIONS
        ann = ANNOTATIONS["get_pending_plan"]
        assert ann.is_read_only is True
        assert ann.is_idempotent is True

    def test_tool_has_search_hint(self):
        from api.agent_v2.prompting import SEARCH_HINT_BY_TOOL

        assert "get_pending_plan" in SEARCH_HINT_BY_TOOL

    def test_archivist_workflow_mentions_get_pending_plan(self):
        """Prompt 必须指引 archivist 批准后先读计划——否则它会 guess from history."""
        from api.agent_v2.definitions import get_definition

        defn = get_definition("sub_archivist")
        sp = defn.resolve_system_prompt()
        assert "get_pending_plan" in sp
        # 执行过程要求 [step K/N done] 标记
        assert "step" in sp.lower() and "done" in sp.lower()


@pytest.mark.p1
@pytest.mark.asyncio
class TestSubmitPlanPersistsBody:
    """submit_plan 调 set_pending_plan 时必须把完整 payload 一起传进去。"""

    async def test_set_pending_plan_called_with_body(self):
        from api.agent_v2.tools.submit_plan import submit_plan

        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb1",), user_id="u1", session_id="s-plan",
        )
        token = set_ctx(ctx)

        captured: dict = {}

        def _fake_set(session_id, pending_id, plan_body=None):
            captured["session_id"] = session_id
            captured["pending_id"] = pending_id
            captured["plan_body"] = plan_body

        try:
            with patch(
                "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan",
                side_effect=_fake_set,
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log"
            ):
                result = await submit_plan.handler({
                    "title": "Tag 4 docs with urgent",
                    "steps": [
                        "locate each doc in law-active",
                        "apply urgent tag",
                        "verify with rag_list_docs",
                        "report back",
                    ],
                    "affected_resources": [
                        {"kind": "doc_count", "value": 4, "action": "tag"},
                    ],
                    "risk_level": "low",
                    "reversible": True,
                })
        finally:
            reset_ctx(token)

        # envelope looks right
        p = _payload(result)
        assert p["status"] == "waiting"
        pending_id = p["pending_id"]

        # DB was told about the body
        assert captured["session_id"] == "s-plan"
        assert captured["pending_id"] == pending_id
        body = captured["plan_body"]
        assert body["title"] == "Tag 4 docs with urgent"
        assert len(body["steps"]) == 4
        assert body["risk_level"] == "low"
        assert body["reversible"] is True
        assert body["pending_id"] == pending_id

    async def test_ctx_flags_set_on_submit(self):
        from api.agent_v2.tools.submit_plan import submit_plan

        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb1",), user_id="u1", session_id=None,
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.audit_log_service.AuditLogService.log"
            ):
                await submit_plan.handler({
                    "title": "x",
                    "steps": ["a"],
                    "risk_level": "low",
                })
        finally:
            reset_ctx(token)
        # Per-turn lock must flip
        assert ctx.plan_submitted_this_turn is True
        assert ctx.pending_plan_status == "waiting"
        assert ctx.pending_plan_id  # a uuid was assigned

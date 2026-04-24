"""Phase 2.6 — doc_ops 共享装饰器 + idempotency 单测。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.doc_ops._common import (
    check_idempotency,
    err,
    ok,
    remember_result,
    require_kb_write,
)


@pytest.mark.p0
@pytest.mark.asyncio
class TestRequireKbWrite:
    """装饰器的 5 条路径都要覆盖：成功 / RBAC deny / 无 kb 跳过 RBAC / 异常 / audit metadata."""

    async def test_successful_call_writes_allow_audit(self):
        audits: list[dict] = []

        @require_kb_write(action="test.op", kb_id_from=lambda a: a.get("kb_id"))
        async def tool_fn(args):
            return ok(kb_id=args.get("kb_id"), touched=True)

        ctx = ToolContext(
            tenant_id="t1",
            kb_ids=("kb1",),
            user_id="u1",
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                result = await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        assert isinstance(result, dict)
        # Exactly one audit row, allow
        assert len(audits) == 1
        rec = audits[0]
        assert rec["action"] == "test.op"
        assert rec["result"] == "allow"
        assert rec["resource_id"] == "kb1"
        # op is included in metadata
        assert rec["metadata"]["op"] == "test.op"

    async def test_rbac_deny_short_circuits_and_audits(self):
        audits: list[dict] = []
        from api.db.services.dataset_access_service import AccessDeniedError

        @require_kb_write(action="test.op", kb_id_from=lambda a: a.get("kb_id"))
        async def tool_fn(args):
            raise RuntimeError("should not be called")

        ctx = ToolContext(tenant_id="t1", kb_ids=("kb1",), user_id="u1")
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least",
                side_effect=AccessDeniedError(
                    message="denied", kb_id="kb1", user_id="u1",
                    required="contributor", actual="viewer",
                ),
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                result = await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        # tool body was NOT invoked — we see the deny envelope instead
        import json

        payload = json.loads(result["content"][0]["text"])
        assert payload["error"] == "no_access"
        assert len(audits) == 1
        assert audits[0]["result"] == "deny"
        assert audits[0]["reason"] == "insufficient_role"

    async def test_missing_kb_id_skips_rbac(self):
        """kb_id_from returns None → RBAC step skipped (used by kb_create)."""
        audits: list[dict] = []

        @require_kb_write(action="kb.create", kb_id_from=lambda _a: None)
        async def tool_fn(args):
            return ok(ran=True)

        ctx = ToolContext(tenant_id="t1", kb_ids=("kb1",), user_id="u1")
        token = set_ctx(ctx)
        try:
            # require_at_least must NOT be called — patch with assert_not_called-like sentinel
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ) as rbac_mock, patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                await tool_fn({"name": "Foo"})
                rbac_mock.assert_not_called()
        finally:
            reset_ctx(token)
        assert audits and audits[0]["result"] == "allow"

    async def test_tool_exception_is_audited_and_reraised(self):
        audits: list[dict] = []

        @require_kb_write(action="test.fail", kb_id_from=lambda a: a.get("kb_id"))
        async def tool_fn(_args):
            raise RuntimeError("boom")

        ctx = ToolContext(tenant_id="t1", kb_ids=("kb1",), user_id="u1")
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                with pytest.raises(RuntimeError):
                    await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        assert len(audits) == 1
        assert audits[0]["result"] == "deny"
        assert "RuntimeError" in audits[0]["reason"]

    async def test_extra_audit_metadata_merges_into_audit_record(self):
        audits: list[dict] = []

        @require_kb_write(
            action="test.extra",
            kb_id_from=lambda a: a.get("kb_id"),
            extra_audit_metadata=lambda _a, _r, _c: {"detail": "enriched"},
        )
        async def tool_fn(_args):
            return ok()

        ctx = ToolContext(tenant_id="t1", kb_ids=("kb1",), user_id="u1")
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)
        assert audits[0]["metadata"]["detail"] == "enriched"


@pytest.mark.p1
@pytest.mark.asyncio
class TestIdempotency:
    async def test_first_call_returns_none_second_returns_cached(self):
        # 强制走 memory path（mock redis 不可用）
        import rag.utils.redis_conn as redis_conn_mod

        orig = redis_conn_mod.REDIS_CONN
        try:
            redis_conn_mod.REDIS_CONN = type(
                "NoRedis", (), {"REDIS": None}
            )()
            args = {"doc_id": "d1", "operation": "add", "tags": ["a"]}
            first = await check_idempotency(
                action="kb.doc.tag", args=args, caller_id="u1",
            )
            assert first is None
            await remember_result(
                action="kb.doc.tag", args=args, caller_id="u1",
                result={"status": "ok"},
            )
            second = await check_idempotency(
                action="kb.doc.tag", args=args, caller_id="u1",
            )
            assert second == {"status": "ok"}
        finally:
            redis_conn_mod.REDIS_CONN = orig

    async def test_explicit_idempotency_key_overrides_hash(self):
        import rag.utils.redis_conn as redis_conn_mod

        orig = redis_conn_mod.REDIS_CONN
        try:
            redis_conn_mod.REDIS_CONN = type(
                "NoRedis", (), {"REDIS": None}
            )()
            await remember_result(
                action="kb.doc.tag",
                args={"idempotency_key": "user-provided-key"},
                caller_id="u1",
                result={"status": "cached"},
            )
            # 不同 args 但同 idempotency_key → 命中
            hit = await check_idempotency(
                action="kb.doc.tag",
                args={"idempotency_key": "user-provided-key", "x": 1},
                caller_id="u1",
            )
            assert hit == {"status": "cached"}
        finally:
            redis_conn_mod.REDIS_CONN = orig


@pytest.mark.p2
class TestResponseHelpers:
    def test_ok_envelope(self):
        import json

        r = ok(foo=1, bar="b")
        payload = json.loads(r["content"][0]["text"])
        assert payload == {"status": "ok", "foo": 1, "bar": "b"}

    def test_err_envelope(self):
        import json

        r = err("bad_thing", "it broke", extra=42)
        payload = json.loads(r["content"][0]["text"])
        assert payload == {"error": "bad_thing", "message": "it broke", "extra": 42}


# ────────────────────────────── Phase 2.6 v0.4 — plan gate ──────────────────────────────


@pytest.mark.p0
@pytest.mark.asyncio
class TestPlanGate:
    """@require_kb_write 的 plan gate 分支：per-turn 锁 + 跨轮 DB 状态。"""

    @staticmethod
    def _make_ctx(**overrides):
        """无 session_id 的 ctx → gate 走 ctx.pending_plan_status 快照分支。"""
        defaults = {
            "tenant_id": "t1",
            "kb_ids": ("kb1",),
            "user_id": "u1",
            "session_id": None,  # 不查 DB
        }
        defaults.update(overrides)
        return ToolContext(**defaults)

    async def test_per_turn_lock_blocks_writes_after_submit_plan(self):
        """同一轮内 submit_plan 跑过之后，任何写都必须被拒。"""
        import json

        audits: list[dict] = []

        @require_kb_write(action="kb.doc.tag", kb_id_from=lambda a: a.get("kb_id"))
        async def tool_fn(_args):
            return ok(touched=True)

        ctx = self._make_ctx(plan_submitted_this_turn=True, pending_plan_id="p1")
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                result = await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        payload = json.loads(result["content"][0]["text"])
        assert payload["error"] == "plan_gate"
        assert payload["reason"] == "plan_submitted_same_turn"
        assert len(audits) == 1
        assert audits[0]["result"] == "deny"
        assert audits[0]["reason"] == "plan_submitted_same_turn"
        assert audits[0]["metadata"]["plan_id"] == "p1"

    async def test_status_waiting_blocks_writes(self):
        import json

        @require_kb_write(action="kb.doc.tag", kb_id_from=lambda a: a.get("kb_id"))
        async def tool_fn(_args):
            return ok(touched=True)

        ctx = self._make_ctx(pending_plan_status="waiting", pending_plan_id="p2")
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch("api.db.services.audit_log_service.AuditLogService.log"):
                result = await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        payload = json.loads(result["content"][0]["text"])
        assert payload["error"] == "plan_gate"
        assert payload["reason"] == "plan_waiting_user_decision"

    async def test_status_rejected_blocks_writes(self):
        import json

        @require_kb_write(action="kb.doc.tag", kb_id_from=lambda a: a.get("kb_id"))
        async def tool_fn(_args):
            return ok()

        ctx = self._make_ctx(pending_plan_status="rejected")
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch("api.db.services.audit_log_service.AuditLogService.log"):
                result = await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        assert json.loads(result["content"][0]["text"])["reason"] == "plan_rejected"

    async def test_status_request_changes_blocks_writes(self):
        import json

        @require_kb_write(action="kb.doc.tag", kb_id_from=lambda a: a.get("kb_id"))
        async def tool_fn(_args):
            return ok()

        ctx = self._make_ctx(pending_plan_status="request_changes")
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch("api.db.services.audit_log_service.AuditLogService.log"):
                result = await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        assert (
            json.loads(result["content"][0]["text"])["reason"]
            == "plan_request_changes"
        )

    async def test_status_approved_lets_writes_through(self):
        """approved plan → 写工具正常执行 + allow audit."""
        audits: list[dict] = []

        @require_kb_write(action="kb.doc.tag", kb_id_from=lambda a: a.get("kb_id"))
        async def tool_fn(_args):
            return ok(touched=True)

        ctx = self._make_ctx(pending_plan_status="approved", pending_plan_id="p3")
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                result = await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        # body 跑通了：allow audit，无 error envelope
        import json

        payload = json.loads(result["content"][0]["text"])
        assert payload.get("status") == "ok"
        assert audits and audits[0]["result"] == "allow"

    async def test_plan_gated_false_bypasses_gate(self):
        """plan_gated=False 的工具（如 doc_create_note）不受 gate 约束。"""
        audits: list[dict] = []

        @require_kb_write(
            action="kb.doc.note_create",
            kb_id_from=lambda a: a.get("kb_id"),
            plan_gated=False,
        )
        async def tool_fn(_args):
            return ok(title="note")

        ctx = self._make_ctx(
            plan_submitted_this_turn=True,
            pending_plan_status="waiting",
            pending_plan_id="p4",
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
                side_effect=lambda **kw: audits.append(kw),
            ):
                result = await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        import json

        payload = json.loads(result["content"][0]["text"])
        assert payload.get("status") == "ok"
        assert audits and audits[0]["result"] == "allow"

    async def test_db_state_overrides_ctx_snapshot_when_session_id_present(self):
        """有 session_id 时 gate 应查 DB，不信任 ctx snapshot（防止跨 subagent
        写时参数过时）。"""
        import json

        @require_kb_write(action="kb.doc.tag", kb_id_from=lambda a: a.get("kb_id"))
        async def tool_fn(_args):
            return ok()

        # ctx 说 None，DB 说 waiting → 应走 DB，拒绝
        ctx = ToolContext(
            tenant_id="t1",
            kb_ids=("kb1",),
            user_id="u1",
            session_id="s1",
            pending_plan_status=None,
            pending_plan_id=None,
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.agent_v2_service.AgentV2SessionService.get_pending_plan",
                return_value={
                    "pending_plan_id": "p5",
                    "pending_plan_status": "waiting",
                    "pending_plan_submitted_at": 0,
                },
            ), patch("api.db.services.audit_log_service.AuditLogService.log"):
                result = await tool_fn({"kb_id": "kb1"})
        finally:
            reset_ctx(token)

        payload = json.loads(result["content"][0]["text"])
        assert payload["reason"] == "plan_waiting_user_decision"
        assert payload["plan_status"] == "waiting"


@pytest.mark.p1
class TestPlanDecisionParse:
    """Endpoint-layer prefix stripping + decision extraction."""

    def test_bracket_approved(self):
        from api.agent_v2.plan_decision import parse_plan_decision as _parse_plan_decision

        cleaned, decision = _parse_plan_decision("[plan approved] go ahead")
        assert decision == "approved"
        assert cleaned == "go ahead"

    def test_bracket_rejected_with_colon(self):
        from api.agent_v2.plan_decision import parse_plan_decision as _parse_plan_decision

        cleaned, decision = _parse_plan_decision(
            "[plan rejected]: the URL list has typos"
        )
        assert decision == "rejected"
        assert cleaned == "the URL list has typos"

    def test_request_changes_variants(self):
        from api.agent_v2.plan_decision import parse_plan_decision as _parse_plan_decision

        assert _parse_plan_decision("[plan request changes] please skip doc_3")[1] == (
            "request_changes"
        )
        assert _parse_plan_decision("[plan request change] tighten scope")[1] == (
            "request_changes"
        )

    def test_chinese_prefixes(self):
        from api.agent_v2.plan_decision import parse_plan_decision as _parse_plan_decision

        assert _parse_plan_decision("[计划批准] 继续")[1] == "approved"
        assert _parse_plan_decision("[计划拒绝] 停下")[1] == "rejected"
        assert _parse_plan_decision("[计划修改] 去掉第三步")[1] == "request_changes"

    def test_bare_prefix_without_brackets(self):
        from api.agent_v2.plan_decision import parse_plan_decision as _parse_plan_decision

        cleaned, decision = _parse_plan_decision("plan approved, proceed")
        assert decision == "approved"
        assert cleaned == "proceed"

    def test_word_boundary_prevents_false_match(self):
        from api.agent_v2.plan_decision import parse_plan_decision as _parse_plan_decision

        # "planner approved" should NOT match "plan approved"
        cleaned, decision = _parse_plan_decision("planner approved this design")
        assert decision is None
        assert cleaned == "planner approved this design"

    def test_no_prefix_passes_through(self):
        from api.agent_v2.plan_decision import parse_plan_decision as _parse_plan_decision

        cleaned, decision = _parse_plan_decision("tag doc 123 with urgent")
        assert decision is None
        assert cleaned == "tag doc 123 with urgent"

    def test_empty_message_safe(self):
        from api.agent_v2.plan_decision import parse_plan_decision as _parse_plan_decision

        assert _parse_plan_decision("")[1] is None
        assert _parse_plan_decision("   ")[1] is None

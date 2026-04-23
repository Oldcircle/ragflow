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

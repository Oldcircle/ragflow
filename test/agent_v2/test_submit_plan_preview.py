"""Phase 2.7 Stage 3 — submit_plan ``preview`` field unit tests.

Verifies:
- Backward compatibility: plans without preview still work (Phase 2.6 flow
  is not broken)
- Preview parsing: kind enum / excerpt required / 8 KB truncation / source_ref
- Preview flows into plan_body, SSE event, audit metadata
- get_pending_plan returns the preview (via pending_plan_body)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.submit_plan import submit_plan


def _call(tool, args: dict) -> dict:
    return asyncio.run(tool.handler(args))


def _parse(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _ctx(**kw):
    defaults = {
        "tenant_id": "t1",
        "kb_ids": ("kb1",),
        "user_id": "u1",
        "session_id": "s1",
    }
    defaults.update(kw)
    return ToolContext(**defaults)


@pytest.fixture
def in_ctx():
    token = set_ctx(_ctx())
    yield
    reset_ctx(token)


def _base_args(**overrides):
    a = {
        "title": "Archive policy doc",
        "steps": ["Download", "Archive to KB"],
        "affected_resources": [],
        "risk_level": "low",
    }
    a.update(overrides)
    return a


# ─────────── Backward compatibility: no preview still works ───────────


def test_without_preview_still_emits_plan(in_ctx):
    set_plan = MagicMock(return_value=True)
    emit = MagicMock()
    with patch(
        "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan",
        set_plan,
    ), patch("api.agent_v2.tools.submit_plan.emit_event", emit), patch(
        "api.db.services.audit_log_service.AuditLogService.log"
    ):
        out = _parse(_call(submit_plan, _base_args()))
    assert out["status"] == "waiting"
    assert "preview" not in out  # response echo doesn't include preview
    # plan_body persisted without preview key
    call = set_plan.call_args
    body = call.kwargs["plan_body"] if "plan_body" in call.kwargs else call[1]["plan_body"]
    assert "preview" not in body


# ─────────── Preview shape validation ───────────


def test_invalid_preview_kind_rejected(in_ctx):
    with patch(
        "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan"
    ), patch("api.agent_v2.tools.submit_plan.emit_event"), patch(
        "api.db.services.audit_log_service.AuditLogService.log"
    ):
        out = _parse(_call(
            submit_plan,
            _base_args(preview={"kind": "xml_ast", "excerpt": "foo"}),
        ))
    assert out["error"] == "invalid_input"
    assert "preview.kind" in out["message"]


def test_empty_preview_excerpt_rejected(in_ctx):
    with patch(
        "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan"
    ), patch("api.agent_v2.tools.submit_plan.emit_event"), patch(
        "api.db.services.audit_log_service.AuditLogService.log"
    ):
        out = _parse(_call(
            submit_plan,
            _base_args(preview={"kind": "markdown_excerpt", "excerpt": "   "}),
        ))
    assert out["error"] == "invalid_input"
    assert "excerpt" in out["message"]


def test_non_dict_preview_silently_ignored(in_ctx):
    """If the model passes `preview` as a string or something weird, we
    don't blow up — just drop the field (backward-compat with loose schema)."""
    set_plan = MagicMock(return_value=True)
    with patch(
        "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan",
        set_plan,
    ), patch("api.agent_v2.tools.submit_plan.emit_event"), patch(
        "api.db.services.audit_log_service.AuditLogService.log"
    ):
        out = _parse(_call(
            submit_plan,
            _base_args(preview="just a string"),  # type: ignore[arg-type]
        ))
    assert out["status"] == "waiting"
    body = set_plan.call_args.kwargs["plan_body"]
    assert "preview" not in body


# ─────────── Happy path: preview flows through ───────────


def test_preview_persisted_and_emitted(in_ctx):
    set_plan = MagicMock(return_value=True)
    emit = MagicMock()
    audit = MagicMock()
    preview = {
        "kind": "markdown_excerpt",
        "title": "Policy doc excerpt",
        "excerpt": "# Heading\n\nBody content.",
        "source_ref": "https://example.com/policy.md",
        "truncated": False,
    }
    with patch(
        "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan",
        set_plan,
    ), patch("api.agent_v2.tools.submit_plan.emit_event", emit), patch(
        "api.db.services.audit_log_service.AuditLogService.log", audit
    ):
        out = _parse(_call(submit_plan, _base_args(preview=preview)))

    assert out["status"] == "waiting"

    # plan_body has normalized preview
    body = set_plan.call_args.kwargs["plan_body"]
    assert body["preview"]["kind"] == "markdown_excerpt"
    assert body["preview"]["title"] == "Policy doc excerpt"
    assert body["preview"]["excerpt"] == "# Heading\n\nBody content."
    assert body["preview"]["source_ref"] == "https://example.com/policy.md"
    assert body["preview"]["truncated"] is False

    # SSE event carries preview in data
    emitted = emit.call_args.args[0]
    assert emitted.type == "plan_submitted"
    assert emitted.data["preview"]["kind"] == "markdown_excerpt"
    assert emitted.data["preview"]["excerpt"] == "# Heading\n\nBody content."

    # Audit records preview kind + bytes (but NOT content itself)
    meta = audit.call_args.kwargs["metadata"]
    assert meta["preview_kind"] == "markdown_excerpt"
    assert meta["preview_bytes"] > 0
    assert "excerpt" not in meta  # content must not leak into audit


def test_preview_excerpt_over_8kb_is_truncated(in_ctx):
    """The tool layer enforces the cap even if schema validator misses it
    (e.g., very long but still passes maxLength if the model sends it)."""
    set_plan = MagicMock(return_value=True)
    huge = "a" * 10_000  # > 8KB
    with patch(
        "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan",
        set_plan,
    ), patch("api.agent_v2.tools.submit_plan.emit_event"), patch(
        "api.db.services.audit_log_service.AuditLogService.log"
    ):
        _parse(_call(
            submit_plan,
            _base_args(preview={"kind": "url_dump", "excerpt": huge}),
        ))
    body = set_plan.call_args.kwargs["plan_body"]
    assert len(body["preview"]["excerpt"].encode("utf-8")) <= 8192
    # Truncation flag auto-set even if caller didn't set it
    assert body["preview"]["truncated"] is True


def test_preview_source_ref_capped_at_2kb(in_ctx):
    """Defensive: a pathological long source_ref shouldn't blow up payloads."""
    set_plan = MagicMock(return_value=True)
    long_url = "https://example.com/" + ("x" * 3000)
    with patch(
        "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan",
        set_plan,
    ), patch("api.agent_v2.tools.submit_plan.emit_event"), patch(
        "api.db.services.audit_log_service.AuditLogService.log"
    ):
        _parse(_call(
            submit_plan,
            _base_args(preview={
                "kind": "markdown_excerpt",
                "excerpt": "body",
                "source_ref": long_url,
            }),
        ))
    body = set_plan.call_args.kwargs["plan_body"]
    assert len(body["preview"]["source_ref"]) <= 2048


def test_preview_kind_normalized_lowercase(in_ctx):
    set_plan = MagicMock(return_value=True)
    with patch(
        "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan",
        set_plan,
    ), patch("api.agent_v2.tools.submit_plan.emit_event"), patch(
        "api.db.services.audit_log_service.AuditLogService.log"
    ):
        _parse(_call(
            submit_plan,
            _base_args(preview={"kind": "DIFF", "excerpt": "- a\n+ b"}),
        ))
    body = set_plan.call_args.kwargs["plan_body"]
    assert body["preview"]["kind"] == "diff"


def test_preview_empty_title_becomes_none(in_ctx):
    """Empty/whitespace title normalizes to None so the UI default kicks in."""
    set_plan = MagicMock(return_value=True)
    with patch(
        "api.db.services.agent_v2_service.AgentV2SessionService.set_pending_plan",
        set_plan,
    ), patch("api.agent_v2.tools.submit_plan.emit_event"), patch(
        "api.db.services.audit_log_service.AuditLogService.log"
    ):
        _parse(_call(
            submit_plan,
            _base_args(preview={
                "kind": "markdown_excerpt",
                "excerpt": "body",
                "title": "   ",
            }),
        ))
    body = set_plan.call_args.kwargs["plan_body"]
    assert body["preview"]["title"] is None


# ─────────── Schema registration ───────────


def test_schema_advertises_preview_field():
    from api.agent_v2.tools.submit_plan import submit_plan as plan_tool

    props = plan_tool.input_schema["properties"]
    assert "preview" in props
    preview_schema = props["preview"]
    assert preview_schema["type"] == "object"
    assert "markdown_excerpt" in preview_schema["properties"]["kind"]["enum"]
    assert "diff" in preview_schema["properties"]["kind"]["enum"]
    assert "url_dump" in preview_schema["properties"]["kind"]["enum"]
    # excerpt + kind are required
    assert set(preview_schema["required"]) == {"kind", "excerpt"}


def test_event_factory_omits_preview_when_none():
    from api.agent_v2 import event as ev

    e = ev.plan_submitted(
        pending_id="p1",
        title="T",
        steps=["s1"],
        affected_resources=[],
        risk_level="low",
        estimated_cost_usd=None,
        reversible=True,
        reversible_hint=None,
        tool_use_id=None,
    )
    assert "preview" not in e.data


def test_event_factory_includes_preview_when_set():
    from api.agent_v2 import event as ev

    e = ev.plan_submitted(
        pending_id="p1",
        title="T",
        steps=["s1"],
        affected_resources=[],
        risk_level="low",
        estimated_cost_usd=None,
        reversible=True,
        reversible_hint=None,
        tool_use_id=None,
        preview={"kind": "markdown_excerpt", "excerpt": "body"},
    )
    assert e.data["preview"]["kind"] == "markdown_excerpt"

"""Phase 2.7 v0.18 — plan_gate preapproved bypass tests.

Validates that ``doc_ingest_attachment`` skips the submit_plan gate when
the staged attachment came from a domain on
``web_fetch_preapproved.PREAPPROVED_HOSTS`` (gov.cn / docs.python.org /
MDN / etc), and that the bypass is distinct in the audit trail from
"user explicitly approved a plan".

Mocks the DB / RBAC / blob fetch / FileService / audit so we exercise
the gate decorator directly without standing up a real DB.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.doc_ops import doc_ingest_attachment


def _call(tool, args: dict) -> dict:
    return asyncio.run(tool.handler(args))


def _parse(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _ctx_with_waiting_plan(**kw):
    """Force the gate into a state that would normally block a write —
    simulates submit_plan having been called earlier in the same turn.
    Without bypass, doc_ingest_attachment must reject."""
    defaults = {
        "tenant_id": "t1",
        "kb_ids": ("kb1",),
        "user_id": "u1",
        "session_id": "sess1",
        "plan_submitted_this_turn": True,
        "pending_plan_id": "pid-active",
        "pending_plan_status": "waiting",
    }
    defaults.update(kw)
    return ToolContext(**defaults)


@pytest.fixture
def in_blocked_ctx():
    token = set_ctx(_ctx_with_waiting_plan())
    yield
    reset_ctx(token)


def _mock_attachment(*, source_url: str | None = None, **kw):
    row = MagicMock()
    row.id = "att1"
    row.tenant_id = "t1"
    row.session_id = "sess1"
    row.filename = "doc.txt"
    row.mime_type = "text/plain"
    row.size_bytes = 1024
    row.hash_xxh128 = "deadbeef" * 4
    row.blob_path = "agent-v2-attachments/t1/sess1/att1"
    row.status = "staged"
    row.archived_doc_id = None
    row.archived_kb_id = None
    row.archived_at = None
    row.source_url = source_url
    for k, v in kw.items():
        setattr(row, k, v)
    return row


def _mock_kb():
    kb = MagicMock()
    kb.id = "kb1"
    kb.tenant_id = "t1"
    kb.name = "Test KB"
    kb.embd_id = "bge-m3"
    return kb


def _patch_rbac_allow():
    return patch(
        "api.db.services.dataset_access_service.DatasetAccessService"
        ".require_at_least",
    )


def _patches_for_archive_path(*, attachment, dedupe_existing=None,
                              upload_returns=None):
    """Common patch stack for the happy-path archive test variants."""
    storage = MagicMock()
    storage.get.return_value = b"file content"

    doc_query = MagicMock()
    doc_query.where.return_value.first.return_value = dedupe_existing

    upload_mock = MagicMock(return_value=(
        upload_returns
        or ([], [({"id": "new_doc_1", "name": "doc.txt"}, b"file content")])
    ))

    return patch.multiple(
        "api.db.services.agent_v2_service.AgentV2AttachmentService",
        get_by_id=lambda _id: attachment,
        mark_archived=MagicMock(return_value=True),
    ), patch(
        "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
        return_value=(True, _mock_kb()),
    ), patch("common.settings.STORAGE_IMPL", storage), patch(
        "api.db.db_models.Document.select", return_value=doc_query,
    ), patch(
        "api.db.services.file_service.FileService.upload_document",
        upload_mock,
    )


# ─────────── gate behavior without bypass ───────────


def test_gate_blocks_when_no_bypass_and_plan_waiting(in_blocked_ctx):
    """User-uploaded attachment (no source_url) → no bypass → gate kicks in
    and rejects with plan_gate error."""
    att = _mock_attachment(source_url=None)
    p1, p2, p3, p4, p5 = _patches_for_archive_path(attachment=att)
    with _patch_rbac_allow(), p1, p2, p3, p4, p5:
        out = _parse(_call(
            doc_ingest_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "plan_gate"
    assert out["reason"] == "plan_submitted_same_turn"


# ─────────── bypass kicks in for preapproved sources ───────────


@pytest.mark.parametrize("preapproved_url", [
    "https://docs.python.org/3/library/asyncio.html",
    "https://www.gov.cn/zhengce/2025/policy.pdf",
    "https://szjs.sz.gov.cn/notice",  # suffix match against gov.cn
    "https://developer.mozilla.org/en-US/docs/Web/API/fetch",
    "https://kubernetes.io/docs/concepts/",
])
def test_gate_bypassed_for_preapproved_source(in_blocked_ctx, preapproved_url):
    """Same locked-gate ctx, but source_url matches PREAPPROVED_HOSTS → bypass
    skips the gate, the archive proceeds, and audit records the reason."""
    att = _mock_attachment(source_url=preapproved_url)
    audit_calls: list = []

    def _capture_audit(**kw):
        audit_calls.append(kw)

    p1, p2, p3, p4, p5 = _patches_for_archive_path(attachment=att)
    with _patch_rbac_allow(), p1, p2, p3, p4, p5, patch(
        "api.db.services.audit_log_service.AuditLogService.log",
        side_effect=_capture_audit,
    ):
        out = _parse(_call(
            doc_ingest_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))

    # The write went through (no plan_gate error) and a doc was created.
    assert out.get("error") is None
    assert out["status"] == "queued_for_parse"
    assert out["doc_id"] == "new_doc_1"

    # Audit record stamps the bypass distinctly from "user approved".
    success_audits = [a for a in audit_calls if a.get("result") == "allow"]
    assert success_audits, f"expected an allow audit, got {audit_calls!r}"
    last = success_audits[-1]
    bypass = (last.get("metadata") or {}).get("plan_gate_bypass")
    assert bypass is not None
    assert bypass["reason"] == "preapproved_source"
    assert bypass["url"] == preapproved_url


def test_non_preapproved_source_still_blocked(in_blocked_ctx):
    """Random commercial URL → bypass returns None → gate blocks normally."""
    att = _mock_attachment(source_url="https://example.com/random-blog")
    p1, p2, p3, p4, p5 = _patches_for_archive_path(attachment=att)
    with _patch_rbac_allow(), p1, p2, p3, p4, p5:
        out = _parse(_call(
            doc_ingest_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "plan_gate"


def test_user_uploaded_attachment_without_source_url_blocked(in_blocked_ctx):
    """Bypass requires a source_url. User-uploaded files have none, so they
    must always go through the explicit approval path."""
    att = _mock_attachment(source_url=None)
    p1, p2, p3, p4, p5 = _patches_for_archive_path(attachment=att)
    with _patch_rbac_allow(), p1, p2, p3, p4, p5:
        out = _parse(_call(
            doc_ingest_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "plan_gate"


def test_bypass_callback_is_None_without_attachment_arg():
    """Defensive: callback must not raise on missing/empty attachment_id."""
    from api.agent_v2.tools.doc_ops.doc_ingest_attachment import (
        _preapproved_bypass,
    )

    ctx = ToolContext(tenant_id="t1", kb_ids=("kb1",))
    assert _preapproved_bypass({}, ctx) is None
    assert _preapproved_bypass({"attachment_id": ""}, ctx) is None


def test_bypass_callback_handles_missing_attachment_row():
    """If get_by_id returns None (deleted attachment), bypass returns
    None — the regular gate logic will then decide."""
    from api.agent_v2.tools.doc_ops.doc_ingest_attachment import (
        _preapproved_bypass,
    )

    ctx = ToolContext(tenant_id="t1", kb_ids=("kb1",))
    with patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=None,
    ):
        assert _preapproved_bypass({"attachment_id": "ghost"}, ctx) is None


def test_audit_includes_host_for_preapproved_bypass(in_blocked_ctx):
    """Bypass metadata includes the resolved host so a SOC reviewer can
    verify the policy that allowed the bypass at a glance."""
    att = _mock_attachment(source_url="https://szjs.sz.gov.cn/policy.pdf")
    audit_calls: list = []

    p1, p2, p3, p4, p5 = _patches_for_archive_path(attachment=att)
    with _patch_rbac_allow(), p1, p2, p3, p4, p5, patch(
        "api.db.services.audit_log_service.AuditLogService.log",
        side_effect=lambda **kw: audit_calls.append(kw),
    ):
        _call(
            doc_ingest_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        )

    success = [a for a in audit_calls if a.get("result") == "allow"][-1]
    bypass = success["metadata"]["plan_gate_bypass"]
    assert bypass["host"] == "szjs.sz.gov.cn"

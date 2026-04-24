"""Phase 2.7 Stage 2 — doc_archive_attachment tool unit tests.

Focus on entry validation, state-machine invariants, and tenant/KB isolation.
Happy-path (real MinIO + FileService + task_executor) stays in the smoke
script — too much scaffolding for unit tests.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.doc_ops import doc_archive_attachment


def _call(tool, args: dict) -> dict:
    return asyncio.run(tool.handler(args))


def _parse(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _ctx(**kw):
    defaults = {"tenant_id": "t1", "kb_ids": ("kb1",), "user_id": "u1"}
    defaults.update(kw)
    return ToolContext(**defaults)


@pytest.fixture
def in_ctx():
    token = set_ctx(_ctx())
    yield
    reset_ctx(token)


def _patch_rbac_allow():
    return patch(
        "api.db.services.dataset_access_service.DatasetAccessService.require_at_least",
    )


def _mock_attachment(**kw):
    row = MagicMock()
    row.id = "att1"
    row.tenant_id = "t1"
    row.session_id = "s1"
    row.filename = "report.pdf"
    row.mime_type = "application/pdf"
    row.size_bytes = 1024
    row.hash_xxh128 = "deadbeef" * 4
    row.blob_path = "agent_v2_attachments/t1/s1/att1"
    row.status = "staged"
    row.archived_doc_id = None
    row.archived_kb_id = None
    row.archived_at = None
    for k, v in kw.items():
        setattr(row, k, v)
    return row


def _mock_kb(**kw):
    kb = MagicMock()
    kb.id = "kb1"
    kb.tenant_id = "t1"
    kb.name = "Test KB"
    kb.embd_id = "bge-m3"
    for k, v in kw.items():
        setattr(kb, k, v)
    return kb


# ─────────── Input validation ───────────


def test_requires_attachment_id_and_kb_id(in_ctx):
    with _patch_rbac_allow():
        out = _parse(_call(doc_archive_attachment, {"kb_id": "kb1"}))
    assert out["error"] == "invalid_input"
    with _patch_rbac_allow():
        out = _parse(_call(doc_archive_attachment, {"attachment_id": "x"}))
    assert out["error"] == "invalid_input"


def test_attachment_not_found(in_ctx):
    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=None,
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "ghost", "kb_id": "kb1"},
        ))
    assert out["error"] == "not_found"


def test_cross_tenant_attachment_rejected(in_ctx):
    att = _mock_attachment(tenant_id="OTHER_TENANT")
    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "out_of_scope"


# ─────────── State machine ───────────


def test_already_archived_to_same_kb_is_idempotent(in_ctx):
    att = _mock_attachment(
        status="archived", archived_doc_id="doc123", archived_kb_id="kb1",
        archived_at=1_700_000_000_000,
    )
    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["status"] == "already_archived"
    assert out["doc_id"] == "doc123"


def test_archived_to_different_kb_is_error(in_ctx):
    att = _mock_attachment(
        status="archived", archived_doc_id="doc123", archived_kb_id="kb_OLD",
    )
    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb_NEW"},
        ))
    assert out["error"] == "already_archived_elsewhere"
    assert "kb_OLD" in out["message"]


def test_rejected_state_cannot_be_archived(in_ctx):
    att = _mock_attachment(status="rejected")
    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "invalid_state"


def test_expired_state_cannot_be_archived(in_ctx):
    att = _mock_attachment(status="expired")
    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "invalid_state"


# ─────────── Target KB checks ───────────


def test_target_kb_not_found(in_ctx):
    att = _mock_attachment()
    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ), patch(
        "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
        return_value=(False, None),
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "ghost_kb"},
        ))
    assert out["error"] == "not_found"


def test_cross_tenant_kb_rejected(in_ctx):
    att = _mock_attachment()
    kb = _mock_kb(tenant_id="OTHER_TENANT")
    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ), patch(
        "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
        return_value=(True, kb),
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "out_of_scope"


# ─────────── Blob retrieval ───────────


def test_blob_path_malformed(in_ctx):
    att = _mock_attachment(blob_path="no-slash-here")
    kb = _mock_kb()
    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ), patch(
        "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
        return_value=(True, kb),
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "blob_path_malformed"


def test_blob_fetch_failure_returns_clean_error(in_ctx):
    att = _mock_attachment()
    kb = _mock_kb()

    storage = MagicMock()
    storage.get.side_effect = RuntimeError("minio down")

    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ), patch(
        "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
        return_value=(True, kb),
    ), patch("common.settings.STORAGE_IMPL", storage):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "blob_unavailable"
    assert "minio down" in out["message"] or "RuntimeError" in out["message"]


def test_empty_blob_rejected(in_ctx):
    att = _mock_attachment()
    kb = _mock_kb()

    storage = MagicMock()
    storage.get.return_value = b""

    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ), patch(
        "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
        return_value=(True, kb),
    ), patch("common.settings.STORAGE_IMPL", storage):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))
    assert out["error"] == "blob_empty"


# ─────────── Dedup path ───────────


def test_dedup_existing_doc_in_kb(in_ctx):
    att = _mock_attachment()
    kb = _mock_kb()

    storage = MagicMock()
    storage.get.return_value = b"bytes"

    existing_doc = MagicMock()
    existing_doc.id = "doc_existing"
    existing_doc.name = "existing.pdf"

    # Query().where().first() chain
    doc_query = MagicMock()
    doc_query.where.return_value.first.return_value = existing_doc

    mark_archived = MagicMock(return_value=True)

    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ), patch(
        "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
        return_value=(True, kb),
    ), patch("common.settings.STORAGE_IMPL", storage), patch(
        "api.db.db_models.Document.select", return_value=doc_query,
    ), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.mark_archived",
        mark_archived,
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))

    assert out["status"] == "duplicate"
    assert out["doc_id"] == "doc_existing"
    mark_archived.assert_called_once()  # still flips the attachment row


# ─────────── Full success path (mocked) ───────────


def test_happy_path_queues_for_parse(in_ctx):
    att = _mock_attachment()
    kb = _mock_kb()
    storage = MagicMock()
    storage.get.return_value = b"file content"

    # Document.select().where().first() returns None (no dedup)
    doc_query = MagicMock()
    doc_query.where.return_value.first.return_value = None

    # FileService.upload_document returns (err_list, [(doc_dict, blob)])
    upload_mock = MagicMock(return_value=(
        [], [({"id": "new_doc_id", "name": "report.pdf"}, b"file content")]
    ))

    mark_archived = MagicMock(return_value=True)

    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ), patch(
        "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
        return_value=(True, kb),
    ), patch("common.settings.STORAGE_IMPL", storage), patch(
        "api.db.db_models.Document.select", return_value=doc_query,
    ), patch(
        "api.db.services.file_service.FileService.upload_document",
        upload_mock,
    ), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.mark_archived",
        mark_archived,
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))

    assert out["status"] == "queued_for_parse"
    assert out["doc_id"] == "new_doc_id"
    assert out["mime_type"] == "application/pdf"
    assert "next_steps" in out and len(out["next_steps"]) >= 2
    # Image-specific hint should NOT appear for a PDF
    assert not any("image" in s.lower() for s in out["next_steps"])


def test_image_mime_emits_ocr_hint(in_ctx):
    att = _mock_attachment(mime_type="image/png", filename="scan.png")
    kb = _mock_kb()
    storage = MagicMock()
    storage.get.return_value = b"PNG bytes"

    doc_query = MagicMock()
    doc_query.where.return_value.first.return_value = None

    upload_mock = MagicMock(return_value=(
        [], [({"id": "img_doc", "name": "scan.png"}, b"PNG bytes")]
    ))

    with _patch_rbac_allow(), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.get_by_id",
        return_value=att,
    ), patch(
        "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
        return_value=(True, kb),
    ), patch("common.settings.STORAGE_IMPL", storage), patch(
        "api.db.db_models.Document.select", return_value=doc_query,
    ), patch(
        "api.db.services.file_service.FileService.upload_document",
        upload_mock,
    ), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.mark_archived",
        return_value=True,
    ):
        out = _parse(_call(
            doc_archive_attachment,
            {"attachment_id": "att1", "kb_id": "kb1"},
        ))

    assert out["status"] == "queued_for_parse"
    # The image-specific next_step surfaces OCR caveat
    assert any("ocr" in s.lower() or "paddle" in s.lower() for s in out["next_steps"])


# ─────────── Registry parity ───────────


def test_registered_and_annotated():
    from api.agent_v2.annotations import ANNOTATIONS
    from api.agent_v2.prompting import SEARCH_HINT_BY_TOOL
    from api.agent_v2.registry import ALL_TOOLS

    assert "doc_archive_attachment" in ALL_TOOLS
    assert "doc_archive_attachment" in ANNOTATIONS
    assert "doc_archive_attachment" in SEARCH_HINT_BY_TOOL
    # Sub_archivist should have access; others should not.
    from api.agent_v2.definitions.built_in.sub_archivist import ARCHIVIST_TOOLS
    from api.agent_v2.definitions.built_in.sub_librarian import LIBRARIAN_TOOLS
    assert "doc_archive_attachment" in ARCHIVIST_TOOLS
    assert "doc_archive_attachment" not in LIBRARIAN_TOOLS


def test_annotation_metadata_sanity():
    from api.agent_v2.annotations import ANNOTATIONS

    ann = ANNOTATIONS["doc_archive_attachment"]
    assert ann.is_read_only is False
    assert ann.is_idempotent is True  # same attachment → same doc
    assert ann.cost_class == "expensive"
    assert ann.supports_next_steps is True

"""Phase 2.6 — 6 doc_ops 写工具的入口 / 校验 / 错误路径单测。

聚焦"拒绝无效输入"和"RBAC / scope 守门"；happy-path（真正写 ES / MySQL）
留给 RAGFLOW_TEST_DB=1 的集成测试与真机验证。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.doc_ops import (
    doc_archive,
    doc_rename,
    doc_reparse,
    doc_tag,
    doc_upload_from_url,
    kb_create,
)


def _call(tool, args: dict) -> dict:
    """Invoke an MCP tool handler synchronously (they're async)."""
    import asyncio

    return asyncio.run(tool.handler(args))


def _parse(resp: dict) -> dict:
    """Decode MCP envelope to inner JSON dict."""
    text = resp["content"][0]["text"]
    return json.loads(text)


def _ctx(**kw):
    """Default ctx with contributor access mocked — RBAC never denies unless caller
    patches DatasetAccessService.require_at_least differently."""
    defaults = {"tenant_id": "t1", "kb_ids": ("kb1",), "user_id": "u1"}
    defaults.update(kw)
    return ToolContext(**defaults)


def _patch_rbac_allow():
    """Helper context manager: RBAC always allows."""
    return patch(
        "api.db.services.dataset_access_service.DatasetAccessService.require_at_least",
    )


def _patch_audit():
    """Helper context manager: silence audit writes."""
    return patch("api.db.services.audit_log_service.AuditLogService.log")


# ───────── doc_tag ─────────


@pytest.mark.p0
class TestDocTag:
    def test_empty_doc_id_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit():
                resp = _call(doc_tag, {"doc_id": "", "tags": ["a"]})
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_empty_tags_list_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(True, MagicMock(kb_id="kb1", name="doc")),
            ):
                resp = _call(doc_tag, {"doc_id": "d1", "tags": []})
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_unknown_operation_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(True, MagicMock(kb_id="kb1", name="doc")),
            ):
                resp = _call(
                    doc_tag,
                    {"doc_id": "d1", "tags": ["a"], "operation": "DESTROY"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_out_of_scope_kb_rejected(self):
        token = set_ctx(_ctx(kb_ids=("kb1",)))
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(True, MagicMock(kb_id="foreign_kb", name="doc")),
            ):
                resp = _call(doc_tag, {"doc_id": "d1", "tags": ["x"]})
        finally:
            reset_ctx(token)
        # kb_id_from pulls "foreign_kb" before scope-check runs; RBAC allow was patched
        # but our scope check still fires
        assert _parse(resp)["error"] == "out_of_scope"

    def test_noop_when_add_of_existing_tag(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(True, MagicMock(kb_id="kb1", name="doc")),
            ), patch(
                "api.db.services.doc_metadata_service."
                "DocMetadataService.get_document_metadata",
                return_value={"tags": ["existing"]},
            ):
                resp = _call(
                    doc_tag,
                    {"doc_id": "d1", "tags": ["existing"], "operation": "add"},
                )
        finally:
            reset_ctx(token)
        payload = _parse(resp)
        assert payload["status"] == "noop"
        assert payload["before_tags"] == ["existing"]

    def test_add_merges_and_dedupes(self):
        token = set_ctx(_ctx())
        written: list = []
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(True, MagicMock(kb_id="kb1", name="doc")),
            ), patch(
                "api.db.services.doc_metadata_service."
                "DocMetadataService.get_document_metadata",
                return_value={"tags": ["old"]},
            ), patch(
                "api.db.services.doc_metadata_service."
                "DocMetadataService.update_document_metadata",
                side_effect=lambda did, meta: (written.append(meta), True)[1],
            ):
                resp = _call(
                    doc_tag,
                    {
                        "doc_id": "d1",
                        "tags": ["new", "old", "old", "extra"],
                        "operation": "add",
                    },
                )
        finally:
            reset_ctx(token)
        payload = _parse(resp)
        assert payload["status"] == "ok"
        assert payload["after_tags"] == ["old", "new", "extra"]
        # dedup preserved order: old first (kept), new/extra appended
        assert len(written) == 1 and written[0]["tags"] == ["old", "new", "extra"]


# ───────── doc_rename ─────────


@pytest.mark.p0
class TestDocRename:
    def test_empty_new_name_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit():
                resp = _call(doc_rename, {"doc_id": "d1", "new_name": "   "})
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_forbidden_chars_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit():
                resp = _call(
                    doc_rename, {"doc_id": "d1", "new_name": "bad/name.pdf"},
                )
        finally:
            reset_ctx(token)
        payload = _parse(resp)
        assert payload["error"] == "invalid_input"
        assert "forbidden" in payload["message"].lower()

    def test_noop_when_name_unchanged(self):
        # MagicMock(name=...) 会被当作 mock 自己的 repr 名字，而不是 attribute；
        # 要用 configure_mock 显式设置真正的 .name 字段。
        doc = MagicMock(kb_id="kb1")
        doc.configure_mock(name="same.pdf")
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(True, doc),
            ):
                resp = _call(
                    doc_rename, {"doc_id": "d1", "new_name": "same.pdf"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["status"] == "noop"


# ───────── kb_create ─────────


@pytest.mark.p0
class TestKbCreate:
    def test_empty_name_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_audit():
                resp = _call(kb_create, {"name": ""})
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_invalid_permission_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_audit(), patch(
                "api.db.services.knowledgebase_service."
                "KnowledgebaseService.create_with_name",
                return_value=(True, {"id": "k1", "name": "n", "parser_id": "naive"}),
            ), patch(
                "api.db.services.knowledgebase_service."
                "KnowledgebaseService.get_by_id",
                return_value=(True, MagicMock(parser_id="naive", embd_id="bge")),
            ):
                resp = _call(
                    kb_create,
                    {"name": "Archive", "permission": "WORLD", "embd_id": "bge"},
                )
        finally:
            reset_ctx(token)
        payload = _parse(resp)
        assert payload["error"] == "invalid_input"

    def test_quota_exceeded_returns_error(self):
        token = set_ctx(_ctx())
        try:
            with _patch_audit(), patch(
                "api.db.services.tenant_quota_service."
                "TenantQuotaService.get",
                return_value=MagicMock(
                    hard_enforce=True, kb_max=1, to_dict=lambda: {},
                ),
            ), patch(
                "api.agent_v2.tools.doc_ops.kb_create._current_kb_count",
                return_value=5,
            ):
                resp = _call(kb_create, {"name": "Archive", "embd_id": "bge"})
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "quota_exceeded"


# ───────── doc_archive ─────────


@pytest.mark.p0
class TestDocArchive:
    def test_same_source_and_target_returns_noop(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(True, MagicMock(kb_id="kb1")),
            ):
                resp = _call(
                    doc_archive,
                    {"doc_id": "d1", "target_kb_id": "kb1"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "noop"

    def test_cross_tenant_target_rejected(self):
        token = set_ctx(_ctx(tenant_id="t1"))
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(True, MagicMock(kb_id="src_kb")),
            ), patch(
                "api.db.services.knowledgebase_service."
                "KnowledgebaseService.get_by_id",
                side_effect=[
                    (True, MagicMock(tenant_id="t1", embd_id="bge")),  # src
                    (True, MagicMock(tenant_id="t2", embd_id="bge")),  # target
                ],
            ):
                resp = _call(
                    doc_archive,
                    {"doc_id": "d1", "target_kb_id": "cross_kb"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "out_of_scope"

    def test_embedding_mismatch_rejected_when_chunks_exist(self):
        src = MagicMock(kb_id="src_kb", chunk_num=10, token_num=100, name="doc")
        src_kb = MagicMock(tenant_id="t1", embd_id="bge")
        tgt_kb = MagicMock(tenant_id="t1", embd_id="openai-ada")
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(True, src),
            ), patch(
                "api.db.services.knowledgebase_service."
                "KnowledgebaseService.get_by_id",
                side_effect=[(True, src_kb), (True, tgt_kb)],
            ):
                resp = _call(
                    doc_archive,
                    {"doc_id": "d1", "target_kb_id": "tgt"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "embedding_mismatch"


# ───────── doc_reparse ─────────


@pytest.mark.p0
class TestDocReparse:
    def test_doc_not_found(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "api.db.services.document_service.DocumentService.get_by_id",
                return_value=(False, None),
            ):
                resp = _call(doc_reparse, {"doc_id": "d_missing"})
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "not_found"


# ───────── doc_upload_from_url ─────────


@pytest.mark.p0
class TestDocUploadFromUrl:
    def test_file_scheme_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit():
                resp = _call(
                    doc_upload_from_url,
                    {"url": "file:///etc/passwd", "kb_id": "kb1"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "unsupported_scheme"

    def test_ftp_scheme_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit():
                resp = _call(
                    doc_upload_from_url,
                    {"url": "ftp://example.com/x.pdf", "kb_id": "kb1"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "unsupported_scheme"

    def test_private_ip_blocked(self):
        """SSRF: 10.x 是 private，resolve 到就拒。"""
        token = set_ctx(_ctx())
        try:
            # Mock DNS to resolve to private IP
            with _patch_rbac_allow(), _patch_audit(), patch(
                "socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("10.0.0.1", 80))],
            ):
                resp = _call(
                    doc_upload_from_url,
                    {"url": "http://internal-host/x.pdf", "kb_id": "kb1"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "ssrf_blocked"

    def test_loopback_blocked(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit(), patch(
                "socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("127.0.0.1", 80))],
            ):
                resp = _call(
                    doc_upload_from_url,
                    {"url": "http://localhost/x.pdf", "kb_id": "kb1"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "ssrf_blocked"

    def test_missing_url_or_kb_rejected(self):
        token = set_ctx(_ctx())
        try:
            with _patch_rbac_allow(), _patch_audit():
                resp = _call(
                    doc_upload_from_url, {"url": "", "kb_id": "kb1"},
                )
                payload = _parse(resp)
                assert payload["error"] == "invalid_input"
        finally:
            reset_ctx(token)

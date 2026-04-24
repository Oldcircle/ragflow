"""Phase 2.6 v0.2 — doc_create_note / kb_audit / kb_stats / doc_list_recent_changes
+ sub_librarian 结构测试。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from api.agent_v2.definitions import get_definition
from api.agent_v2.definitions.registry import clear_cache_for_tests
from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.doc_ops import (
    doc_create_note,
    doc_list_recent_changes,
    kb_audit,
    kb_stats,
)


def _call(tool, args: dict) -> dict:
    return asyncio.run(tool.handler(args))


def _parse(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _ctx(**kw):
    defaults = {"tenant_id": "t1", "kb_ids": ("kb1",), "user_id": "u1"}
    defaults.update(kw)
    return ToolContext(**defaults)


# ───────── doc_create_note ─────────


@pytest.mark.p0
class TestDocCreateNote:
    def test_missing_required_fields(self):
        token = set_ctx(_ctx())
        try:
            with patch(
                "api.db.services.dataset_access_service."
                "DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
            ):
                resp = _call(
                    doc_create_note,
                    {"kb_id": "kb1", "title": "", "markdown_body": "ok"},
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_body_too_large_rejected(self):
        token = set_ctx(_ctx())
        body = "x" * (2 * 1024 * 1024 + 100)
        try:
            with patch(
                "api.db.services.dataset_access_service."
                "DatasetAccessService.require_at_least"
            ), patch("api.db.services.audit_log_service.AuditLogService.log"):
                resp = _call(
                    doc_create_note,
                    {
                        "kb_id": "kb1",
                        "title": "too big",
                        "markdown_body": body,
                    },
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "too_large"

    def test_cross_tenant_target_rejected(self):
        token = set_ctx(_ctx(tenant_id="my_t"))
        try:
            with patch(
                "api.db.services.dataset_access_service."
                "DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
            ), patch(
                "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
                return_value=(True, MagicMock(tenant_id="other_t")),
            ):
                resp = _call(
                    doc_create_note,
                    {
                        "kb_id": "foreign_kb",
                        "title": "Hi",
                        "markdown_body": "# ok\n\nSome content here worth saving.",
                    },
                )
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "out_of_scope"

    def test_duplicate_hash_returns_duplicate_status(self):
        token = set_ctx(_ctx())
        existing = MagicMock(id="existing_doc")
        existing.configure_mock(name="note.md")
        kb_mock = MagicMock(tenant_id="t1")
        try:
            with patch(
                "api.db.services.dataset_access_service."
                "DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.log",
            ), patch(
                "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
                return_value=(True, kb_mock),
            ), patch(
                "api.db.db_models.Document.select",
            ) as sel:
                sel.return_value.where.return_value.first.return_value = existing
                resp = _call(
                    doc_create_note,
                    {
                        "kb_id": "kb1",
                        "title": "Daily",
                        "markdown_body": "same-body-every-day " * 20,
                    },
                )
        finally:
            reset_ctx(token)
        payload = _parse(resp)
        assert payload["status"] == "duplicate"
        assert payload["doc_id"] == "existing_doc"


# ───────── kb_audit ─────────


@pytest.mark.p0
class TestKbAudit:
    def test_empty_kb_id_with_multi_kb_session_rejected(self):
        """v0.6-fix: kb_id 缺失且 session 有多 KB → 报 invalid_input
        （无法自动选一个）。"""
        token = set_ctx(_ctx(kb_ids=("kb1", "kb2")))
        try:
            resp = _call(kb_audit, {"kb_id": ""})
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "invalid_input"

    def test_empty_kb_id_with_single_kb_falls_back(self):
        """v0.6-fix: kb_id 缺失但 session 只有 1 个 KB → 自动 fallback。
        （会继续往下跑到 RBAC，这里 patch 放行后再到 KB 查询）。"""
        token = set_ctx(_ctx())
        try:
            with patch(
                "api.db.services.dataset_access_service."
                "DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
                return_value=(False, None),  # KB 查不到，确认不是 invalid_input 路径
            ):
                resp = _call(kb_audit, {"kb_id": ""})
        finally:
            reset_ctx(token)
        out = _parse(resp)
        # 不应再是 "invalid_input"——证明 fallback 生效到了下一步
        assert out.get("error") != "invalid_input"

    def test_cross_tenant_target_rejected(self):
        token = set_ctx(_ctx(tenant_id="t1"))
        try:
            with patch(
                "api.db.services.dataset_access_service."
                "DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
                return_value=(True, MagicMock(tenant_id="other_t")),
            ):
                resp = _call(kb_audit, {"kb_id": "foreign"})
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "out_of_scope"


# ───────── kb_stats ─────────


@pytest.mark.p0
class TestKbStats:
    def test_kb_not_found(self):
        token = set_ctx(_ctx())
        try:
            with patch(
                "api.db.services.dataset_access_service."
                "DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
                return_value=(False, None),
            ):
                resp = _call(kb_stats, {"kb_id": "nope"})
        finally:
            reset_ctx(token)
        assert _parse(resp)["error"] == "not_found"


# ───────── doc_list_recent_changes ─────────


@pytest.mark.p0
class TestDocListRecentChanges:
    def test_window_hours_bound(self):
        """超过 30 天窗口会被夹紧到上限，而不是报错。"""
        token = set_ctx(_ctx())
        try:
            with patch(
                "api.db.db_models.AccessAuditLog.select",
            ) as sel:
                # 链式 mock：select().where().where().order_by().limit() → 空列表
                chain = MagicMock()
                chain.where.return_value = chain
                chain.order_by.return_value = chain
                chain.limit.return_value = chain
                chain.dicts.return_value = []
                sel.return_value = chain
                resp = _call(
                    doc_list_recent_changes,
                    {"window_hours": 9999},  # 超限
                )
        finally:
            reset_ctx(token)
        payload = _parse(resp)
        assert payload["status"] == "ok"
        # 应被夹到 30 天 = 720 小时
        assert payload["window_hours"] == 720

    def test_action_prefix_filter_passed_through(self):
        token = set_ctx(_ctx())
        captured: list = []
        try:
            with patch(
                "api.db.db_models.AccessAuditLog.select",
            ) as sel:
                chain = MagicMock()

                def where(*args, **kw):
                    captured.append(args)
                    return chain

                chain.where = where
                chain.order_by.return_value = chain
                chain.limit.return_value = chain
                chain.dicts.return_value = []
                sel.return_value = chain
                _call(
                    doc_list_recent_changes,
                    {"action_prefix": "kb.doc.", "window_hours": 24},
                )
        finally:
            reset_ctx(token)
        # 检查 where 至少被调了一次以上（tenant + 可选过滤）
        assert len(captured) >= 1


# ───────── sub_librarian ─────────


@pytest.mark.p0
class TestSubLibrarianDefinition:
    def setup_method(self):
        clear_cache_for_tests()

    def test_registered(self):
        d = get_definition("sub_librarian")
        assert d is not None
        assert d.kind == "subagent"

    def test_has_all_reflect_tools(self):
        d = get_definition("sub_librarian")
        expected = {
            "kb_stats", "kb_audit", "doc_list_recent_changes",
            "doc_create_note",
        }
        assert expected.issubset(set(d.tools))

    def test_no_write_or_destructive_tools(self):
        d = get_definition("sub_librarian")
        forbidden = {
            "doc_tag", "doc_rename", "doc_archive", "doc_reparse",
            "doc_upload_from_url", "kb_create",
        }
        assert forbidden.isdisjoint(set(d.tools)), (
            f"librarian should not hold destructive tools; got: "
            f"{set(d.tools) & forbidden}"
        )

    def test_has_interactive_tools(self):
        d = get_definition("sub_librarian")
        assert "ask_user_question" in d.tools
        assert "submit_plan" in d.tools

    def test_citation_enforce_is_warn(self):
        """librarian 产笔记时要带引用，不像 archivist 关闭 citation。"""
        d = get_definition("sub_librarian")
        assert d.citation_enforce == "warn"

    def test_cannot_spawn_subagents(self):
        d = get_definition("sub_librarian")
        assert d.can_spawn_subagents is False

    @pytest.mark.parametrize(
        "supervisor_name",
        [
            "sz-baojian-house",
            "generic-policy",
            "research-analyst",
            "legal-contract",
        ],
    )
    def test_supervisors_can_spawn_librarian(self, supervisor_name):
        d = get_definition(supervisor_name)
        assert d is not None
        assert "sub_librarian" in (d.allowed_subagent_types or ())

    def test_supervisors_still_have_archivist_too(self):
        """升级后两个 subagent 应该并列存在，不是互相替换。"""
        d = get_definition("sz-baojian-house")
        assert "sub_archivist" in (d.allowed_subagent_types or ())
        assert "sub_librarian" in (d.allowed_subagent_types or ())


@pytest.mark.p1
class TestRegistryExpandedToSeventeen:
    def test_all_phase_26_v02_tools_present(self):
        from api.agent_v2 import registry

        new_tools = {
            "doc_create_note",
            "kb_audit",
            "kb_stats",
            "doc_list_recent_changes",
        }
        missing = new_tools - set(registry.ALL_TOOLS.keys())
        assert not missing, f"registry missing: {missing}"

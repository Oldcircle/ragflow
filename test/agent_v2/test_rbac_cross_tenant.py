"""Phase 2.1 — cross-tenant RBAC penetration tests。

三条强制路径必须 100% 拒绝未授权访问 + 100% 写审计：

  1. ``api/agent_v2/tools/rag_retrieve.py`` — 工具层深度防御
  2. ``api/apps/agent_v2_app.py::create_session`` — HTTP 入口
  3. ``api/db/services/dialog_service.py::async_ask`` — 传统 chat 流

本文件主要覆盖（1）的完整闭环（纯 unit，mock RBAC + Audit），
和 RBAC 服务 + 跨 tenant 场景的真 DB 集成用例（RAGFLOW_TEST_DB=1 才跑）。
"""

from __future__ import annotations

import asyncio
import json
import os
import warnings
from unittest.mock import patch

import pytest

# rag_retrieve 的 import 链会触发 xgboost/compat.py 里的 pkg_resources UserWarning；
# pyproject.toml 的 filterwarnings=['error'] 把它升级成异常。这里做一次总压制，
# 然后在测试用例内部 lazy import。
warnings.filterwarnings("ignore", category=UserWarning)

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx  # noqa: E402


def _lazy_import_rag_retrieve():
    """Lazy 导入，避开 pytest collection 阶段的 warning-as-error 问题。"""
    from api.agent_v2.tools.rag_retrieve import rag_retrieve as rag_retrieve_tool
    return rag_retrieve_tool


def _call_handler(handler, args):
    """SdkMcpTool.handler 调用入口（async fn -> dict）."""
    return asyncio.run(handler(args))


# ───────── Unit: rag_retrieve 深度防御 ─────────


@pytest.mark.p0
class TestRagRetrieveRbacDenies:
    """rag_retrieve 对不可访问的 kb 必须拒绝 + 写审计。

    纯 unit — 不碰 MySQL / ES，全靠 patch RBAC 和 Audit service。
    """

    def _run_with_ctx(self, *, user_id: str | None, kb_ids: tuple, accessible_subset: list):
        """在一个 ctx 下跑 rag_retrieve.handler，返回 (result_dict, audit_calls)."""
        audit_calls: list[dict] = []

        def _deny_spy(**kw):
            audit_calls.append(kw)

        rag_retrieve_tool = _lazy_import_rag_retrieve()

        ctx = ToolContext(
            tenant_id="tenant_A",
            kb_ids=kb_ids,
            user_id=user_id,
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.filter_accessible_kb_ids",
                return_value=accessible_subset,
            ), patch(
                "api.db.services.audit_log_service.AuditLogService.deny",
                side_effect=_deny_spy,
            ):
                raw = _call_handler(rag_retrieve_tool.handler, {"query": "x"})
        finally:
            reset_ctx(token)

        # 工具返回 MCP envelope；把 text 解成 dict 方便断言
        if isinstance(raw, dict) and "content" in raw:
            text = raw["content"][0]["text"]
            try:
                return json.loads(text), audit_calls
            except json.JSONDecodeError:
                return {"_raw_text": text}, audit_calls
        return raw, audit_calls

    def test_zero_access_returns_no_access_and_audits_every_kb(self):
        body, audits = self._run_with_ctx(
            user_id="user_of_tenant_B",
            kb_ids=("kb_of_A_1", "kb_of_A_2"),
            accessible_subset=[],
        )
        assert body.get("error") == "no_access"
        assert "kb_of_A_1" in body.get("message", "")
        assert "kb_of_A_2" in body.get("message", "")
        # 每个 kb 都要落一条审计
        assert len(audits) == 2
        kb_ids_audited = {a["resource_id"] for a in audits}
        assert kb_ids_audited == {"kb_of_A_1", "kb_of_A_2"}
        for a in audits:
            assert a["action"] == "kb.retrieve"
            assert a["resource_type"] == "knowledgebase"
            assert a["reason"] == "rag_retrieve_no_access"
            assert a["user_id"] == "user_of_tenant_B"

    def test_partial_access_uses_subset_but_audits_denied(self):
        """有权访问 kb_A；kb_B 被拒。应继续检索（用 kb_A）并为 kb_B 写审计."""
        with patch(
            "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_ids",
            # 让后续检索路径上"同 embedding 模型"断言失败，提前退出
            # （跨 KB 配置不一致在这个 unit 里无意义 — 用一个 empty list 短路）
            return_value=[],
        ):
            body, audits = self._run_with_ctx(
                user_id="user_mixed",
                kb_ids=("kb_A", "kb_B"),
                accessible_subset=["kb_A"],  # kb_B 被拒
            )
        # 不再返 no_access；但 kb_B 的 deny 审计一定要落
        assert body.get("error") != "no_access"
        denied_ids = {a["resource_id"] for a in audits}
        assert denied_ids == {"kb_B"}

    def test_anonymous_user_skips_rbac_check(self):
        """ctx.user_id 为 None 时（内部 cron / system caller）走旧行为，不查 RBAC."""
        audit_calls: list[dict] = []

        def _deny_spy(**kw):
            audit_calls.append(kw)

        rag_retrieve_tool = _lazy_import_rag_retrieve()

        ctx = ToolContext(
            tenant_id="tenant_A",
            kb_ids=("kb_A",),
            user_id=None,  # anonymous / system
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.filter_accessible_kb_ids",
            ) as rbac_mock, patch(
                "api.db.services.audit_log_service.AuditLogService.deny",
                side_effect=_deny_spy,
            ), patch(
                "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_ids",
                return_value=[],
            ):
                _call_handler(rag_retrieve_tool.handler, {"query": "x"})
                # RBAC 根本不应被调用
                rbac_mock.assert_not_called()
        finally:
            reset_ctx(token)
        # 也不应有 deny 审计（因为跳过了 RBAC 检查）
        assert audit_calls == []


# ───────── Integration: 真 DB 跨租户场景 ─────────


@pytest.mark.p1
@pytest.mark.skipif(
    os.environ.get("RAGFLOW_TEST_DB") != "1",
    reason="需要真 MySQL，设 RAGFLOW_TEST_DB=1 启用",
)
class TestCrossTenantRbacIntegration:
    """在真 DB 上构造两个 tenant + 两个 KB + 两个 user，
    验证 DatasetAccessService 完全隔离。"""

    @pytest.fixture(scope="class", autouse=True)
    def _init_ragflow(self):
        from common import settings as rf_settings

        rf_settings.init_settings()
        from api.db.db_models import init_database_tables

        init_database_tables()

    @pytest.fixture
    def seed_two_tenants(self):
        """创建 tenant_A / tenant_B + 各自 user + 各自 KB，返回清理钩子。

        RBAC 只关心 dataset_access + knowledgebase + user_tenant 三张表的关系，
        直接写最小必要行；测完删。
        """
        from common.misc_utils import get_uuid

        from api.db.db_models import DB, Knowledgebase, UserTenant

        tenant_a = f"rbac_t_A_{get_uuid()[:8]}"
        tenant_b = f"rbac_t_B_{get_uuid()[:8]}"
        user_a = f"rbac_u_A_{get_uuid()[:8]}"
        user_b = f"rbac_u_B_{get_uuid()[:8]}"
        kb_a = f"rbac_kb_A_{get_uuid()[:8]}"
        kb_b = f"rbac_kb_B_{get_uuid()[:8]}"

        with DB.atomic():
            UserTenant.create(
                id=get_uuid(),
                tenant_id=tenant_a,
                user_id=user_a,
                role="owner",
                status="1",
                invited_by="system",
            )
            UserTenant.create(
                id=get_uuid(),
                tenant_id=tenant_b,
                user_id=user_b,
                role="owner",
                status="1",
                invited_by="system",
            )
            Knowledgebase.create(
                id=kb_a,
                tenant_id=tenant_a,
                created_by=user_a,
                name="KB_A",
                permission="team",  # 只有同 tenant 可见
                status="1",
                doc_num=0,
                token_num=0,
                chunk_num=0,
            )
            Knowledgebase.create(
                id=kb_b,
                tenant_id=tenant_b,
                created_by=user_b,
                name="KB_B",
                permission="team",
                status="1",
                doc_num=0,
                token_num=0,
                chunk_num=0,
            )

        yield {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "user_a": user_a,
            "user_b": user_b,
            "kb_a": kb_a,
            "kb_b": kb_b,
        }

        # cleanup
        with DB.atomic():
            Knowledgebase.delete().where(Knowledgebase.id.in_([kb_a, kb_b])).execute()
            UserTenant.delete().where(UserTenant.user_id.in_([user_a, user_b])).execute()

    def test_user_A_has_owner_on_kb_A(self, seed_two_tenants):
        from api.db.services.dataset_access_service import (
            DatasetAccessService,
            DatasetRole,
        )

        d = seed_two_tenants
        role = DatasetAccessService.effective_role(d["kb_a"], d["user_a"])
        assert role == DatasetRole.OWNER

    def test_user_B_has_no_access_to_kb_A(self, seed_two_tenants):
        from api.db.services.dataset_access_service import (
            AccessDeniedError,
            DatasetAccessService,
            DatasetRole,
        )

        d = seed_two_tenants
        assert DatasetAccessService.effective_role(d["kb_a"], d["user_b"]) is None
        assert DatasetAccessService.has_at_least(
            d["kb_a"], d["user_b"], DatasetRole.VIEWER
        ) is False
        assert DatasetAccessService.filter_accessible_kb_ids(
            [d["kb_a"], d["kb_b"]], d["user_b"]
        ) == [d["kb_b"]]  # 只能看到自己的
        with pytest.raises(AccessDeniedError):
            DatasetAccessService.require_at_least(
                d["kb_a"], d["user_b"], DatasetRole.VIEWER
            )

    def test_anonymous_user_has_no_access(self, seed_two_tenants):
        from api.db.services.dataset_access_service import DatasetAccessService

        d = seed_two_tenants
        assert DatasetAccessService.effective_role(d["kb_a"], None) is None
        assert DatasetAccessService.filter_accessible_kb_ids(
            [d["kb_a"], d["kb_b"]], None
        ) == []

    def test_explicit_viewer_grant_overrides_denial(self, seed_two_tenants):
        """显式 dataset_access 记录优先于 team 兜底。"""
        from api.db.services.dataset_access_service import (
            DatasetAccessService,
            DatasetRole,
        )

        d = seed_two_tenants
        DatasetAccessService.grant(
            d["kb_a"], d["user_b"], DatasetRole.VIEWER, granted_by=d["user_a"]
        )
        try:
            role = DatasetAccessService.effective_role(d["kb_a"], d["user_b"])
            assert role == DatasetRole.VIEWER
        finally:
            DatasetAccessService.revoke(d["kb_a"], d["user_b"])

        # 撤销后恢复为 None
        assert DatasetAccessService.effective_role(d["kb_a"], d["user_b"]) is None

    def test_cannot_grant_owner_role(self, seed_two_tenants):
        from api.db.services.dataset_access_service import (
            DatasetAccessService,
            DatasetRole,
        )

        d = seed_two_tenants
        with pytest.raises(ValueError, match="OWNER"):
            DatasetAccessService.grant(
                d["kb_a"], d["user_b"], DatasetRole.OWNER, granted_by=d["user_a"]
            )

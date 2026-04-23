"""租户配额与用量查询 HTTP 端点（Phase 3.1b）。

URL 前缀：``/v1/tenant_quota``。

  GET /v1/tenant_quota        → 当前 tenant 的配额 + 今日用量 + 本月累计
  GET /v1/tenant_quota/range?days=30  → 最近 N 天日用量（给图表用）
"""

from __future__ import annotations

from quart import request

from api.apps import current_user, login_required
from api.db.services.agent_v2_service import AgentV2SessionService  # noqa: F401
from api.db.services.tenant_quota_service import (
    TenantQuotaService,
    TenantUsageService,
)
from api.utils.api_utils import get_json_result, server_error_response


def _safe_int(s, default=30):
    try:
        v = int(s)
        return max(1, min(v, 180))
    except Exception:
        return default


@manager.route("", methods=["GET"])  # noqa: F821
@login_required
async def get_quota_and_usage():
    try:
        tenant_id = current_user.id
        quota = TenantQuotaService.get(tenant_id)
        today = TenantUsageService.get_today(tenant_id)
        month = TenantUsageService.get_month(tenant_id)

        # 顺带给 KB / Doc 实时计数（上限检查用）
        kb_count = 0
        doc_count = 0
        try:
            from api.db.db_models import Document, Knowledgebase
            kb_count = Knowledgebase.select().where(
                Knowledgebase.tenant_id == tenant_id
            ).count()
            doc_count = Document.select().join(
                Knowledgebase,
                on=(Document.kb_id == Knowledgebase.id),
            ).where(Knowledgebase.tenant_id == tenant_id).count()
        except Exception:
            pass

        return get_json_result(data={
            "quota": quota.to_dict(),
            "usage": {
                "kb_count": kb_count,
                "doc_count": doc_count,
                "today": today,
                "month": month,
            },
        })
    except Exception as e:
        return server_error_response(e)


@manager.route("/range", methods=["GET"])  # noqa: F821
@login_required
async def get_usage_range():
    try:
        days = _safe_int(request.args.get("days"), default=30)
        rows = TenantUsageService.get_range(current_user.id, days=days)
        return get_json_result(data={"days": days, "entries": rows})
    except Exception as e:
        return server_error_response(e)

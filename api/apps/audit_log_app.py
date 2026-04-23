"""审计日志查询端点（Phase 2.1）。

URL 前缀：`/v1/audit_log`
"""

from __future__ import annotations

from quart import request

from api.apps import current_user, login_required
from api.db.services.audit_log_service import AuditLogService
from api.utils.api_utils import get_json_result, server_error_response


@manager.route("/list", methods=["GET"])  # noqa: F821
@login_required
async def list_audit_logs():
    """查询当前 tenant 的审计日志（按 create_time 倒序）。

    Query params:
      action, resource_type, resource_id, result, user_id, start_ms, end_ms, page, page_size
    """
    try:
        page = max(int(request.args.get("page", 1)), 1)
        page_size = min(max(int(request.args.get("page_size", 50)), 1), 200)

        kwargs: dict = {"tenant_id": current_user.id}
        for k in ("action", "resource_type", "resource_id", "result", "user_id"):
            v = request.args.get(k)
            if v:
                kwargs[k] = v
        for k in ("start_ms", "end_ms"):
            v = request.args.get(k)
            if v:
                kwargs[k] = int(v)

        rows = AuditLogService.query_logs(
            **kwargs, limit=page_size, offset=(page - 1) * page_size,
        )
        total = AuditLogService.count_logs(**kwargs)

        return get_json_result(data={
            "logs": [
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "tenant_id": r.tenant_id,
                    "action": r.action,
                    "resource_type": r.resource_type,
                    "resource_id": r.resource_id,
                    "result": r.result,
                    "reason": r.reason,
                    "metadata": r.metadata,
                    "ip": r.ip,
                    "user_agent": r.user_agent,
                    "create_time": r.create_time,
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        })
    except Exception as e:
        return server_error_response(e)

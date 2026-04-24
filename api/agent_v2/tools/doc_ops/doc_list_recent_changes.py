"""doc_list_recent_changes — Agent 自省已发生的操作（Phase 2.6 v0.2）。

查 access_audit_log，返回指定 tenant（可选 kb_id 过滤）最近 N 小时的
写操作记录。让 sub_archivist 能在做完一批操作后核对"我真的全改了吗？"。

RBAC：VIEWER+（如果提供了 kb_id）或无 KB 限制时直接读 tenant 级 audit
（和 /v1/audit_log/list 端点行为一致）。读-only。
"""

from __future__ import annotations

import logging
import time

from ..base import get_ctx, mcp_json_response, tool

logger = logging.getLogger("ragflow.agent_v2.doc_ops.doc_list_recent_changes")

_DEFAULT_WINDOW_HOURS = 24
_MAX_WINDOW_HOURS = 24 * 30  # 30 天
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@tool(
    name="doc_list_recent_changes",
    description=(
        "Use this tool when you need to verify recent write activity: did "
        "my last batch of archives succeed? who touched this KB last week? "
        "why does this document show up in an unexpected KB?\n\n"
        "Reads the `access_audit_log` table and returns rows in reverse "
        "chronological order, each containing action / result / resource_id "
        "/ reason / user_id / timestamp / metadata.\n\n"
        "Usage notes:\n"
        "- Default window 24 hours, max 30 days. Values above the max "
        "are clamped, not rejected.\n"
        "- Filter by `kb_id` to focus on a KB's activity; by `action_prefix` "
        "(e.g. 'kb.doc.') to narrow to a category.\n"
        "- Read-only. Requires VIEWER+ on `kb_id` when supplied; else "
        "tenant-level access."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kb_id": {
                "type": "string",
                "description": (
                    "Optional filter: only rows referencing this KB "
                    "(matches resource_id OR metadata substring)."
                ),
            },
            "window_hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_WINDOW_HOURS,
                "default": _DEFAULT_WINDOW_HOURS,
                "description": (
                    "Lookback window in hours. Default 24, max 720 (30 days)."
                ),
            },
            "action_prefix": {
                "type": "string",
                "description": (
                    "Optional prefix filter on the action field "
                    "(e.g. 'kb.doc.' or 'agent_v2.')."
                ),
            },
            "result_filter": {
                "type": "string",
                "enum": ["allow", "deny", "any"],
                "default": "any",
                "description": (
                    "Restrict to allow-only / deny-only / both."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_LIMIT,
                "default": _DEFAULT_LIMIT,
                "description": (
                    "Max rows returned. Default 50, max 200."
                ),
            },
        },
    },
)
async def doc_list_recent_changes(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    user_id = ctx.user_id
    kb_id = (args.get("kb_id") or "").strip() or None
    window_hours = max(
        1,
        min(int(args.get("window_hours") or _DEFAULT_WINDOW_HOURS), _MAX_WINDOW_HOURS),
    )
    action_prefix = (args.get("action_prefix") or "").strip() or None
    result_filter = (args.get("result_filter") or "any").lower()
    limit = max(1, min(int(args.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT))

    # KB 级权限检查（如果指定了 kb_id）
    if kb_id and user_id:
        from api.db.services.dataset_access_service import (
            AccessDeniedError,
            DatasetAccessService,
            DatasetRole,
        )

        try:
            DatasetAccessService.require_at_least(kb_id, user_id, DatasetRole.VIEWER)
        except AccessDeniedError as e:
            return mcp_json_response({
                "error": "no_access",
                "message": f"Need viewer+ on {kb_id}: {e.actual or 'none'}",
            })

    from api.db.db_models import AccessAuditLog

    cutoff_ms = int(time.time() * 1000) - window_hours * 3600 * 1000
    q = AccessAuditLog.select().where(
        (AccessAuditLog.tenant_id == tenant_id)
        & (AccessAuditLog.create_time >= cutoff_ms)
    )
    if kb_id:
        # resource_id 可能是 kb_id、doc_id、或 session_id；直接匹配是窄口径
        # 但 doc 相关审计 metadata 里常带 kb_id，所以用 OR 组合
        q = q.where(
            (AccessAuditLog.resource_id == kb_id)
            | (AccessAuditLog.metadata.contains(kb_id))  # 粗匹配 JSON 文本
        )
    if action_prefix:
        q = q.where(AccessAuditLog.action.startswith(action_prefix))
    if result_filter in ("allow", "deny"):
        q = q.where(AccessAuditLog.result == result_filter)

    q = q.order_by(AccessAuditLog.create_time.desc()).limit(limit)

    try:
        rows = list(q.dicts())
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_list_recent_changes query failed")
        return mcp_json_response({
            "error": "storage_error",
            "message": f"{type(exc).__name__}: {exc}",
        })

    records = []
    for r in rows:
        records.append({
            "id": r.get("id"),
            "ts_ms": r.get("create_time"),
            "action": r.get("action"),
            "result": r.get("result"),
            "resource_type": r.get("resource_type"),
            "resource_id": r.get("resource_id"),
            "reason": r.get("reason"),
            "user_id": r.get("user_id"),
            "metadata": r.get("metadata"),
        })

    # 简单统计
    total = len(records)
    by_action: dict[str, int] = {}
    by_result: dict[str, int] = {"allow": 0, "deny": 0}
    for r in records:
        a = r.get("action") or "unknown"
        by_action[a] = by_action.get(a, 0) + 1
        res = r.get("result") or "allow"
        if res in by_result:
            by_result[res] += 1

    return mcp_json_response({
        "status": "ok",
        "tenant_id": tenant_id,
        "kb_id": kb_id,
        "window_hours": window_hours,
        "filters": {
            "action_prefix": action_prefix,
            "result": result_filter,
        },
        "returned_count": total,
        "limit": limit,
        "by_action": by_action,
        "by_result": by_result,
        "records": records,
        "truncated_hint": (
            f"Only last {limit} records returned. Use action_prefix or "
            "narrower window_hours to drill down if needed."
            if total == limit else None
        ),
    })

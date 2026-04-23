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
        "【WHEN】**需要核对一批操作是否成功 / 谁改过 KB**时用：\n"
        "- sub_archivist 做完批量 archive 后，确认『我的 12 个 doc_archive 都成功了吗』\n"
        "- 用户问『这个 KB 最近谁改过？』\n"
        "- 审计回溯事故：某个 doc 是怎么跑到这里来的\n\n"
        "【WHAT】读 access_audit_log，按时间倒序返回：\n"
        "- 每条 record 含 action / result / resource_id / reason / user_id / ts / metadata\n"
        "- 默认窗口 24 小时；最多 30 天\n"
        "- 可按 kb_id 过滤（只看那个 KB 相关）\n"
        "- 可按 action prefix 过滤（如 'kb.doc.' 只看文档相关）\n\n"
        "读-only；如果传 kb_id 则 VIEWER+，否则按 tenant 级。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kb_id": {
                "type": "string",
                "description": "可选：只看这个 KB 相关的记录",
            },
            "window_hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_WINDOW_HOURS,
                "default": _DEFAULT_WINDOW_HOURS,
            },
            "action_prefix": {
                "type": "string",
                "description": "可选：action 的前缀过滤，如 'kb.doc.' 或 'agent_v2.'",
            },
            "result_filter": {
                "type": "string",
                "enum": ["allow", "deny", "any"],
                "default": "any",
                "description": "只看 allow 记录 / 只看 deny 记录 / 全部",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_LIMIT,
                "default": _DEFAULT_LIMIT,
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

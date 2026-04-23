"""kb_stats — 轻量 KB 健康快照（Phase 2.6 v0.2）。

``kb_audit`` 的极简版：只返几个关键数字，< 50 ms 响应。适合 Agent 在 plan
步骤里频繁取温度 / 在 `doc_create_note` 之前核对目标库状态。

RBAC：VIEWER+。读-only。
"""

from __future__ import annotations

import logging
import time

from ..base import get_ctx, mcp_json_response, tool

logger = logging.getLogger("ragflow.agent_v2.doc_ops.kb_stats")


@tool(
    name="kb_stats",
    description=(
        "【WHEN】**需要快速拿 KB 的几个关键数字**时用，例如：\n"
        "- 写计划前问『这个 KB 现在多大？』\n"
        "- doc_create_note 之前确认目标库还能装\n"
        "- 周期巡检的 ping（配 Phase 3 的 Trigger 用）\n\n"
        "相比 kb_audit：只返 totals + 最旧/最新文档时间 + embedding 模型，"
        "响应体 < 1KB。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kb_id": {"type": "string", "description": "KB ID"},
        },
        "required": ["kb_id"],
    },
)
async def kb_stats(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    user_id = ctx.user_id
    kb_id = str(args.get("kb_id") or "").strip()
    if not kb_id:
        return mcp_json_response({"error": "invalid_input", "message": "kb_id required"})

    from api.db.services.dataset_access_service import (
        AccessDeniedError,
        DatasetAccessService,
        DatasetRole,
    )
    from api.db.services.knowledgebase_service import KnowledgebaseService

    if user_id:
        try:
            DatasetAccessService.require_at_least(kb_id, user_id, DatasetRole.VIEWER)
        except AccessDeniedError as e:
            return mcp_json_response({
                "error": "no_access",
                "message": f"Need viewer+ on {kb_id}: {e.actual or 'none'}",
            })

    k_ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not k_ok or not kb:
        return mcp_json_response({"error": "not_found", "message": f"KB {kb_id!r} not found"})
    if kb.tenant_id != tenant_id:
        return mcp_json_response({"error": "out_of_scope", "message": "KB not in tenant"})

    from api.db.db_models import Document

    try:
        oldest = (
            Document.select(Document.create_time)
            .where(Document.kb_id == kb_id)
            .order_by(Document.create_time.asc())
            .first()
        )
        newest = (
            Document.select(Document.create_time, Document.update_time)
            .where(Document.kb_id == kb_id)
            .order_by(Document.update_time.desc())
            .first()
        )
        unparsed = (
            Document.select()
            .where((Document.kb_id == kb_id) & (Document.progress < 1.0))
            .count()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("kb_stats DB error")
        return mcp_json_response({
            "error": "storage_error",
            "message": f"{type(exc).__name__}: {exc}",
        })

    return mcp_json_response({
        "status": "ok",
        "kb_id": kb_id,
        "kb_name": kb.name,
        "doc_num": int(kb.doc_num or 0),
        "chunk_num": int(kb.chunk_num or 0),
        "token_num": int(kb.token_num or 0),
        "embd_id": kb.embd_id,
        "parser_id": kb.parser_id,
        "oldest_doc_created_ms": oldest.create_time if oldest else None,
        "newest_doc_updated_ms": newest.update_time if newest else None,
        "unparsed_count": unparsed,
        "snapshot_at_ms": int(time.time() * 1000),
    })

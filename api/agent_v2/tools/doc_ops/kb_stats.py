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
        "Use this tool when you need a quick numeric snapshot of a KB — "
        "before writing a plan ('how big is this KB?'), before "
        "`doc_create_note` to confirm the target isn't at quota, or as a "
        "periodic health ping.\n\n"
        "Returns doc_num / chunk_num / token_num / embd_id / parser_id / "
        "oldest & newest document timestamps / unparsed count. Response "
        "is under 1 KB — cheap to call repeatedly. For a full health "
        "report use `kb_audit`.\n\n"
        "Read-only. Requires VIEWER+."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kb_id": {
                "type": "string",
                "description": (
                    "KB ID to snapshot. Optional when the session has exactly "
                    "one KB in scope — falls back to that KB automatically."
                ),
            },
        },
    },
)
async def kb_stats(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    user_id = ctx.user_id
    kb_id = str(args.get("kb_id") or "").strip()
    # v0.6-fix — fall back to ctx.kb_ids[0] when the session scope is a single
    # KB. The LLM often forgets to pass kb_id because the session prompt
    # doesn't echo specific IDs; this makes the tool forgiving rather than
    # returning `invalid_input` on an obvious-single-KB session.
    if not kb_id:
        if ctx.kb_ids and len(ctx.kb_ids) == 1:
            kb_id = ctx.kb_ids[0]
        else:
            return mcp_json_response({
                "error": "invalid_input",
                "message": (
                    "kb_id required — this session has multiple KBs "
                    f"({len(ctx.kb_ids or [])}), so ambiguity cannot be resolved."
                    if ctx.kb_ids else
                    "kb_id required and no KB is in scope."
                ),
            })

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

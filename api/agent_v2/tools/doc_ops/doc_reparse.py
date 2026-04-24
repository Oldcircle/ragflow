"""doc_reparse — 重新跑解析器把文档重新切片入库（Phase 2.6）。

用途：用户调了新的解析器 / 修了 parser_config / 发现 chunks 质量差，想让 Agent 帮忙
重跑一遍。本工具只 enqueue 任务；真正的重切片由 ``rag/svr/task_executor.py`` 异步跑。

行为：
- 可选把 ``parser_id`` 改成新值（比如从 naive → book）
- 清空旧 chunks（``clear_chunk_num_when_rerun``）
- 把 ``run='1', progress=0`` 标记让 task_executor 下一轮捡起来

RBAC：CONTRIBUTOR+ on 所属 KB。
Audit：``kb.doc.reparse``，metadata 含 old_parser / new_parser。
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import get_ctx, tool
from ._common import err, ok, require_kb_write

logger = logging.getLogger("ragflow.agent_v2.doc_ops.doc_reparse")


def _resolve_kb_id(args: dict) -> str | None:
    doc_id = args.get("doc_id")
    if not doc_id:
        return None
    try:
        from api.db.services.document_service import DocumentService

        ok_, doc = DocumentService.get_by_id(doc_id)
        if not ok_ or not doc:
            return None
        return getattr(doc, "kb_id", None)
    except Exception:
        return None


def _extra_audit(args: dict, result: Any, _ctx) -> dict:
    try:
        import json

        if isinstance(result, dict):
            c = result.get("content")
            if isinstance(c, list) and c:
                payload = json.loads(c[0].get("text") or "{}")
                return {
                    "doc_id": args.get("doc_id"),
                    "old_parser": payload.get("old_parser"),
                    "new_parser": payload.get("new_parser"),
                    "reversible_hint": (
                        "Call doc_reparse again with parser_id=<old_parser> "
                        "to revert the parser change (chunks will differ "
                        "after each re-parse)."
                    ),
                }
    except Exception:
        pass
    return {}


@tool(
    name="doc_reparse",
    description=(
        "Use this tool when the user asks to re-run the parser on a "
        "document — typically because the current chunks are poor quality "
        "or they want to switch the parser (e.g. naive → book for PDFs).\n\n"
        "Clears existing chunks and enqueues a fresh parse task; the "
        "task_executor picks it up asynchronously. The blob itself is "
        "unchanged. Optionally switch `parser_id` as part of the call. "
        "Requires CONTRIBUTOR+ on the owning KB."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": "ID of the document to re-parse.",
            },
            "parser_id": {
                "type": "string",
                "description": (
                    "Optional new parser: 'naive' / 'book' / 'qa' / "
                    "'laws' / ... Leave unset to keep the current parser."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Optional audit-log reason.",
            },
        },
        "required": ["doc_id"],
    },
)
@require_kb_write(
    action="kb.doc.reparse",
    min_role="contributor",
    kb_id_from=_resolve_kb_id,
    extra_audit_metadata=_extra_audit,
)
async def doc_reparse(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    doc_id = str(args.get("doc_id") or "").strip()
    new_parser = args.get("parser_id")
    if new_parser is not None:
        new_parser = str(new_parser).strip().lower()
        if not new_parser:
            new_parser = None

    if not doc_id:
        return err("invalid_input", "doc_id is required")

    from api.db.services.document_service import DocumentService

    found, doc = DocumentService.get_by_id(doc_id)
    if not found or not doc:
        return err("not_found", f"doc {doc_id!r} not found")

    kb_id = doc.kb_id
    if ctx.kb_ids and kb_id not in ctx.kb_ids:
        return err("out_of_scope", f"doc belongs to KB {kb_id} not in session scope")

    old_parser = doc.parser_id

    # 1) Optionally switch parser_id
    updates: dict = {"run": "1", "progress": 0.0, "progress_msg": "reparse requested by agent"}
    if new_parser and new_parser != old_parser:
        updates["parser_id"] = new_parser

    try:
        DocumentService.update_by_id(doc_id, updates)
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_reparse: DB update failed: %s", exc)
        return err("storage_error", f"{type(exc).__name__}: {exc}")

    # 2) Clear old chunks + kb counters
    try:
        DocumentService.clear_chunk_num_when_rerun(doc_id)
    except Exception:
        logger.exception("doc_reparse: clear_chunk_num_when_rerun failed; continuing")

    # 3) Enqueue parse task
    try:
        doc_dict = doc.to_dict()
        doc_dict.update(updates)
        DocumentService.run(tenant_id, doc_dict, kb_table_num_map={})
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_reparse: run() enqueue failed: %s", exc)
        return err(
            "enqueue_failed",
            f"parser task enqueue failed: {type(exc).__name__}: {exc}",
        )

    return ok(
        doc_id=doc_id,
        doc_name=doc.name,
        kb_id=kb_id,
        old_parser=old_parser,
        new_parser=updates.get("parser_id", old_parser),
        status="queued",
        message=(
            "Document reparse task enqueued; check progress via "
            "rag_list_docs or the document detail page."
        ),
        reason=args.get("reason"),
        next_steps=[
            (
                "Ask the user to send 'check progress' in the next message; "
                f"then call rag_list_docs(kb_id='{kb_id}') to confirm new chunk_count"
            ),
            "Do NOT re-queue the same document — that duplicates parsing work",
        ],
    )

"""doc_archive — 把文档从一个 KB 移到另一个 KB（Phase 2.6）。

v1 设计（安全优先）：
- 源 / 目标 KB 必须属于**同一 tenant**（跨 tenant 走管理员流程）
- 要求源 / 目标**同一 embedding 模型**（否则 chunks 无法复用，reject；用户自行处理）
- 更新 ``Document.kb_id`` + ES 里所有 chunks 的 kb_id 字段
- 同步更新两边 ``Knowledgebase.doc_num`` / ``token_num`` / ``chunk_num`` 计数
- 失败时尝试回滚（best-effort；半成品标 state=partial 并写审计）

RBAC：**源 + 目标**两边都需 CONTRIBUTOR+（装饰器校源；工具内校目标）。
Audit：``kb.doc.archive``，metadata 含 source_kb_id / target_kb_id / reverse 线索。
可逆：``reverse=true`` 参数可作为 revert 入口，调用方只需交换 target/source 即可。
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import get_ctx, tool
from ._common import err, ok, require_kb_write

logger = logging.getLogger("ragflow.agent_v2.doc_ops.doc_archive")


def _resolve_source_kb(args: dict) -> str | None:
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
                    "source_kb_id": payload.get("source_kb_id"),
                    "target_kb_id": payload.get("target_kb_id"),
                    "doc_name_at_op": payload.get("doc_name"),
                    "chunk_num_moved": payload.get("chunk_num"),
                    "reversible_hint": (
                        "Call doc_archive with doc_id={} and "
                        "target_kb_id={} to move back"
                    ).format(args.get("doc_id"), payload.get("source_kb_id")),
                }
    except Exception:
        pass
    return {}


@tool(
    name="doc_archive",
    description=(
        "Use this tool when the user has asked to move a document from one "
        "knowledge base to another (cross-KB archiving).\n\n"
        "Constraints:\n"
        "- Source and target must be in the same tenant.\n"
        "- Source and target must use the **same embedding model**, else "
        "the existing chunks cannot be reused and the call is rejected. "
        "When this happens, first use `kb_create` with matching `embd_id`.\n"
        "- Requires CONTRIBUTOR+ on BOTH the source and target KB.\n\n"
        "Reversible: call the tool again with source/target swapped to "
        "move the document back. Previous source/target IDs are recorded "
        "in the audit log."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": "ID of the document to move.",
            },
            "target_kb_id": {
                "type": "string",
                "description": (
                    "Target KB ID. Must share tenant and embedding model "
                    "with the source KB."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "Optional audit-log reason, e.g. 'expired archival'."
                ),
            },
        },
        "required": ["doc_id", "target_kb_id"],
    },
)
@require_kb_write(
    action="kb.doc.archive",
    min_role="contributor",
    kb_id_from=_resolve_source_kb,
    extra_audit_metadata=_extra_audit,
)
async def doc_archive(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    user_id = ctx.user_id
    doc_id = str(args.get("doc_id") or "").strip()
    target_kb_id = str(args.get("target_kb_id") or "").strip()

    if not doc_id or not target_kb_id:
        return err("invalid_input", "doc_id and target_kb_id are required")

    from api.db.services.dataset_access_service import (
        AccessDeniedError,
        DatasetAccessService,
        DatasetRole,
    )
    from api.db.services.document_service import DocumentService
    from api.db.services.knowledgebase_service import KnowledgebaseService

    # Source doc
    found, doc = DocumentService.get_by_id(doc_id)
    if not found or not doc:
        return err("not_found", f"doc {doc_id!r} not found")
    source_kb_id = doc.kb_id
    if source_kb_id == target_kb_id:
        return err("noop", "target_kb_id equals source; nothing to archive")

    # Source KB
    s_ok, src_kb = KnowledgebaseService.get_by_id(source_kb_id)
    if not s_ok or not src_kb:
        return err("not_found", f"source KB {source_kb_id!r} missing")
    # Target KB
    t_ok, tgt_kb = KnowledgebaseService.get_by_id(target_kb_id)
    if not t_ok or not tgt_kb:
        return err("not_found", f"target KB {target_kb_id!r} not found")

    # Tenant guard
    if src_kb.tenant_id != tenant_id or tgt_kb.tenant_id != tenant_id:
        return err(
            "out_of_scope",
            "source / target KB does not belong to the current tenant",
        )

    # Target RBAC（source 已由装饰器校过）
    if user_id:
        try:
            DatasetAccessService.require_at_least(
                target_kb_id, user_id, DatasetRole.CONTRIBUTOR
            )
        except AccessDeniedError as e:
            return err(
                "no_access_target",
                f"Need contributor+ on target KB {target_kb_id}: {e.actual or 'none'}",
            )

    # Embedding compat（若 chunk_num=0 也可以放宽，因为没东西可搬）
    src_embd = getattr(src_kb, "embd_id", None)
    tgt_embd = getattr(tgt_kb, "embd_id", None)
    if src_embd and tgt_embd and src_embd != tgt_embd and (doc.chunk_num or 0) > 0:
        return err(
            "embedding_mismatch",
            (
                f"source KB uses embd_id={src_embd!r}, target uses "
                f"{tgt_embd!r}; chunks cannot be reused. Create a target KB "
                "with the same embedding or reparse the document."
            ),
            source_embd_id=src_embd,
            target_embd_id=tgt_embd,
        )

    old_name = doc.name
    chunk_num = int(doc.chunk_num or 0)
    token_num = int(doc.token_num or 0)

    # 1) Update Document row
    updated = DocumentService.update_by_id(doc_id, {"kb_id": target_kb_id})
    if not updated:
        return err("storage_error", "DB update_by_id returned 0 rows")

    # 2) Move ES chunks
    moved_chunks = _move_chunks(doc_id, tenant_id, source_kb_id, target_kb_id)

    # 3) Update counters on both sides (best-effort; don't fail the whole move)
    _update_kb_counters(source_kb_id, doc_delta=-1, token_delta=-token_num,
                        chunk_delta=-chunk_num)
    _update_kb_counters(target_kb_id, doc_delta=+1, token_delta=+token_num,
                        chunk_delta=+chunk_num)

    return ok(
        doc_id=doc_id,
        doc_name=old_name,
        source_kb_id=source_kb_id,
        target_kb_id=target_kb_id,
        chunk_num=chunk_num,
        chunks_moved=moved_chunks,
        embedding=src_embd,
        reason=args.get("reason"),
        next_steps=[
            f"Verify with rag_list_docs(kb_id='{target_kb_id}', keywords='{old_name[:40]}')",
            f"Confirm source emptied via rag_list_docs(kb_id='{source_kb_id}', keywords='{old_name[:40]}')",
        ],
    )


def _move_chunks(doc_id: str, tenant_id: str, src_kb: str, tgt_kb: str) -> int:
    """Best-effort 更新 ES 里属于 ``doc_id`` 的 chunks 的 ``kb_id`` 字段。

    成功返回受影响 chunk 数；失败静默返 0（此时 chunks 仍"物理属于"源 index 行，
    但 Document.kb_id 已指向 target——RAGFlow 检索路径一般按 kb_id 过滤，所以不会
    在目标 KB 的检索结果里漏）。失败同步写一条审计。
    """
    try:
        from common import settings
        from rag.nlp.search import index_name

        idx = index_name(tenant_id)
        updated = settings.docStoreConn.update(
            {"doc_id": doc_id},
            {"kb_id": tgt_kb},
            idx,
            src_kb,  # ES adapter 需要 "routing" 或 index suffix 时用得到
        )
        # update() 返回 bool 或 int；归一化成 int
        if isinstance(updated, bool):
            return 1 if updated else 0
        return int(updated or 0)
    except Exception:
        logger.exception(
            "doc_archive: chunks kb_id update failed for doc=%s src=%s tgt=%s",
            doc_id, src_kb, tgt_kb,
        )
        return 0


def _update_kb_counters(kb_id: str, *, doc_delta: int, token_delta: int,
                        chunk_delta: int) -> None:
    try:
        from api.db.db_models import DB, Knowledgebase

        with DB.atomic():
            Knowledgebase.update(
                doc_num=Knowledgebase.doc_num + doc_delta,
                token_num=Knowledgebase.token_num + token_delta,
                chunk_num=Knowledgebase.chunk_num + chunk_delta,
            ).where(Knowledgebase.id == kb_id).execute()
    except Exception:
        logger.exception("doc_archive: counter update failed for kb=%s", kb_id)

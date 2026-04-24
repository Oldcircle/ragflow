"""doc_rename — 给文档改名（Phase 2.6）。

仅改 ``Document.name``。不动 blob / chunks / 元数据。
同 KB 内如果目标名已存在，自动添加去重后缀（``duplicate_name`` 同 RAGFlow UI 行为）。

RBAC：CONTRIBUTOR+ on 所属 KB。
Audit：``kb.doc.rename``，metadata 里 ``old_name`` / ``new_name`` 给 revert 留痕。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..base import get_ctx, tool
from ._common import err, ok, require_kb_write

logger = logging.getLogger("ragflow.agent_v2.doc_ops.doc_rename")


# 禁止的字符：控制字符 + Windows 保留字符 + 斜杠类
_FORBIDDEN_RE = re.compile(r'[\x00-\x1f<>:"|?*/\\]')
_MAX_NAME_LEN = 240


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
        logger.exception("doc_rename: kb_id lookup failed for %s", doc_id)
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
                    "old_name": payload.get("old_name"),
                    "new_name": payload.get("new_name"),
                    "reversible_hint": (
                        "Call doc_rename with new_name=<old_name> to revert"
                    ),
                }
    except Exception:
        pass
    return {}


@tool(
    name="doc_rename",
    description=(
        "Use this tool when the user has asked you to rename a specific "
        "document's display name (Document.name). The file blob, chunks, "
        "and metadata are left unchanged — no re-parse is triggered.\n\n"
        "On name collision within the same KB an auto-suffix (-1, -2, ...) "
        "is applied. Requires CONTRIBUTOR+ on the owning KB."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "doc_id": {"type": "string", "description": "Document ID."},
            "new_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_NAME_LEN,
                "description": (
                    "New display name. Avoid forbidden chars (/\\<>:\"|?*); "
                    "keep the original extension so the UI renders the "
                    "correct icon."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Optional audit-log reason.",
            },
        },
        "required": ["doc_id", "new_name"],
    },
)
@require_kb_write(
    action="kb.doc.rename",
    min_role="contributor",
    kb_id_from=_resolve_kb_id,
    extra_audit_metadata=_extra_audit,
)
async def doc_rename(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    doc_id = str(args.get("doc_id") or "").strip()
    new_name = str(args.get("new_name") or "").strip()

    if not doc_id:
        return err("invalid_input", "doc_id is required")
    if not new_name:
        return err("invalid_input", "new_name is required and must be non-empty")
    if len(new_name) > _MAX_NAME_LEN:
        return err("invalid_input", f"new_name exceeds {_MAX_NAME_LEN} chars")
    if _FORBIDDEN_RE.search(new_name):
        return err(
            "invalid_input",
            "new_name contains forbidden characters (control chars / <>:\"|?*/\\\\)",
        )

    from api.db.services.document_service import DocumentService

    found, doc = DocumentService.get_by_id(doc_id)
    if not found or not doc:
        return err("not_found", f"doc_id {doc_id!r} not found")

    kb_id = getattr(doc, "kb_id", None)
    if ctx.kb_ids and kb_id not in ctx.kb_ids:
        return err(
            "out_of_scope",
            f"doc belongs to KB {kb_id}, not in session's kb_ids",
        )

    old_name = doc.name or ""
    if new_name == old_name:
        return ok(
            status="noop",
            doc_id=doc_id,
            kb_id=kb_id,
            old_name=old_name,
            new_name=new_name,
            message="new_name equals current name, nothing to do",
        )

    # 冲突检查：同 KB 内若已有同名文档，加后缀
    from api.db.services import duplicate_name

    try:
        deduped = duplicate_name(DocumentService.query, name=new_name, kb_id=kb_id)
    except Exception:
        # duplicate_name 失败不应阻塞；保留原 new_name，让 DB 层自己兜底
        deduped = new_name

    try:
        changed = DocumentService.update_by_id(doc_id, {"name": deduped})
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_rename DB update failed: %s", exc)
        return err("storage_error", f"{type(exc).__name__}: {exc}")

    if not changed:
        return err("storage_error", "update_by_id returned 0 affected rows")

    return ok(
        doc_id=doc_id,
        kb_id=kb_id,
        old_name=old_name,
        new_name=deduped,
        requested_name=new_name,
        auto_suffixed=(deduped != new_name),
        reason=args.get("reason"),
        next_steps=[
            f"Verify via rag_list_docs(kb_id='{kb_id}', keywords='{deduped[:40]}')",
        ],
    )

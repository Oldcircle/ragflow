"""doc_tag — 给 Document 加/去/设标签（Phase 2.6 最小风险工具）。

Tags 存在 ES 的 DocMetadata 里（``meta_fields.tags: list[str]``）——复用 RAGFlow
已有的元数据存储路径，**不**新建 MySQL 列。

- ``operation="add"``：并集；重复标签去重
- ``operation="remove"``：减集；不存在的标签忽略不报错
- ``operation="set"``：完全覆盖现有 tags

RBAC：**CONTRIBUTOR+** on 所属 KB。
Audit：``kb.doc.tag``，metadata 里附 ``before`` + ``after``，给 revert 留痕。
Idempotency：非严格——重复 add 同一 tag 就是 no-op；重复 set 可覆盖。不做 cache。
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import get_ctx, tool
from ._common import err, ok, require_kb_write

logger = logging.getLogger("ragflow.agent_v2.doc_ops.doc_tag")


def _resolve_kb_id(args: dict) -> str | None:
    """doc_tag 的 kb_id 要先从 doc_id 查出来（用户只给 doc_id）。"""
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
        logger.exception("doc_tag: failed to resolve kb_id from doc_id=%s", doc_id)
        return None


def _extra_audit(args: dict, result: Any, _ctx) -> dict:
    """把 before/after 标签写进 audit metadata，便于 revert。"""
    try:
        import json

        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list) and content:
                payload = json.loads(content[0].get("text") or "{}")
                return {
                    "doc_id": args.get("doc_id"),
                    "operation": args.get("operation"),
                    "tags_in": list(args.get("tags") or []),
                    "before": payload.get("before_tags"),
                    "after": payload.get("after_tags"),
                    "reversible_hint": _hint(args.get("operation")),
                }
    except Exception:
        pass
    return {}


def _hint(op: str | None) -> str:
    if op == "add":
        return "Call doc_tag with operation=remove and the same tags"
    if op == "remove":
        return "Call doc_tag with operation=add and the same tags"
    if op == "set":
        return "Call doc_tag with operation=set and the previous tag list"
    return ""


@tool(
    name="doc_tag",
    description=(
        "Use this tool when the user has explicitly asked to tag a specific "
        "document — add / remove / replace tags on its metadata.\n\n"
        "Operation semantics:\n"
        "- add: union with existing tags (duplicates deduped)\n"
        "- remove: difference; missing tags are silently ignored\n"
        "- set: full replacement of the tag list with `tags`\n\n"
        "Requires CONTRIBUTOR+ on the owning KB. Returns status=noop when "
        "the requested change leaves tags unchanged."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": (
                    "Document ID (from rag_list_docs / rag_retrieve)."
                ),
            },
            "tags": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
                "minItems": 1,
                "maxItems": 20,
                "description": (
                    "Tag list to apply. Each tag 1-64 chars; up to 20 tags."
                ),
            },
            "operation": {
                "type": "string",
                "enum": ["add", "remove", "set"],
                "default": "add",
                "description": "add / remove / set (full replacement).",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Optional free-text reason recorded in the audit log."
                ),
            },
        },
        "required": ["doc_id", "tags"],
    },
)
@require_kb_write(
    action="kb.doc.tag",
    min_role="contributor",
    kb_id_from=_resolve_kb_id,
    extra_audit_metadata=_extra_audit,
)
async def doc_tag(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    doc_id = str(args.get("doc_id") or "").strip()
    raw_tags = args.get("tags") or []
    operation = (args.get("operation") or "add").lower()

    if not doc_id:
        return err("invalid_input", "doc_id is required")
    if not isinstance(raw_tags, list) or not raw_tags:
        return err("invalid_input", "tags must be a non-empty list")
    if operation not in ("add", "remove", "set"):
        return err("invalid_input", f"unknown operation: {operation!r}")

    # 标准化：strip + 去空 + 去重（保留顺序）
    tags: list[str] = []
    seen: set[str] = set()
    for t in raw_tags:
        s = str(t).strip()
        if not s or s in seen or len(s) > 64:
            continue
        seen.add(s)
        tags.append(s)
    if not tags:
        return err("invalid_input", "tags contains no usable entries")
    if len(tags) > 20:
        return err("invalid_input", "too many tags (max 20 per call)")

    # Scope guard：doc 必须属于 ctx.kb_ids 里的某个 KB（即会话声明的范围）
    from api.db.services.document_service import DocumentService
    from api.db.services.doc_metadata_service import DocMetadataService

    found, doc = DocumentService.get_by_id(doc_id)
    if not found or not doc:
        return err("not_found", f"doc_id {doc_id!r} not found")
    kb_id = getattr(doc, "kb_id", None)
    if ctx.kb_ids and kb_id not in ctx.kb_ids:
        return err(
            "out_of_scope",
            f"doc belongs to KB {kb_id} which is not in this session's kb_ids",
        )

    # 读当前 tags
    existing_meta = DocMetadataService.get_document_metadata(doc_id) or {}
    before_tags = _coerce_tags(existing_meta.get("tags"))

    # 计算新 tags
    if operation == "add":
        merged = list(before_tags)
        for t in tags:
            if t not in merged:
                merged.append(t)
        after_tags = merged
    elif operation == "remove":
        after_tags = [t for t in before_tags if t not in set(tags)]
    else:  # set
        after_tags = list(tags)

    if after_tags == before_tags:
        return ok(
            status="noop",
            doc_id=doc_id,
            kb_id=kb_id,
            operation=operation,
            before_tags=before_tags,
            after_tags=after_tags,
            message="no change — requested operation leaves tags identical",
        )

    # 写回
    new_meta = dict(existing_meta)
    new_meta["tags"] = after_tags

    try:
        if existing_meta:
            applied = DocMetadataService.update_document_metadata(doc_id, new_meta)
        else:
            applied = DocMetadataService.insert_document_metadata(doc_id, new_meta)
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_tag write failed: %s", exc)
        return err("storage_error", f"{type(exc).__name__}: {exc}")

    if not applied:
        return err("storage_error", "metadata write reported failure")

    return ok(
        doc_id=doc_id,
        doc_name=getattr(doc, "name", None),
        kb_id=kb_id,
        operation=operation,
        before_tags=before_tags,
        after_tags=after_tags,
        reason=args.get("reason"),
    )


def _coerce_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        # ES 可能把 list 存成逗号串
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []

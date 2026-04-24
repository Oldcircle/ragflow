"""Phase 2.7 Stage 2 — archive a session attachment into a KB.

Writes are funnelled through ``FileService.upload_document`` so:
- Parsing queue handles PDF / docx / xlsx / images uniformly (task executor
  dispatches based on ``FileType``).
- Images ship via ``FileType.VISUAL``; upstream pipeline handles thumbnail +
  downstream OCR when configured (Paddle / MinerU). We don't hard-bind OCR
  here — that stays optional per RAGFlow tenant config.
- Dedup by content hash against the target KB, consistent with
  ``doc_upload_from_url``.

Design notes (对齐 ``PLAN-attachments.md`` §3 + §五)：
- Sub_archivist 专用 — ``@require_kb_write(plan_gated=True)`` 强制走审批流
- Source of truth 是 ``AgentV2Attachment`` 行，不是 MinIO blob；但 blob 必须
  已经存在于 MinIO（Stage 1 HTTP 上传时写入）
- Idempotent：同一 attachment 再次归档返 existing doc_id + status=duplicate
"""

from __future__ import annotations

import json
import logging
from typing import Any

from common import settings

from ..base import get_ctx, tool
from ._common import err, ok, require_kb_write

logger = logging.getLogger("ragflow.agent_v2.doc_ops.doc_archive_attachment")


def _resolve_kb_id(args: dict) -> str | None:
    return args.get("kb_id")


def _extra_audit(args: dict, result: Any, _ctx) -> dict:
    """Produce audit metadata from the tool's response envelope."""
    try:
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list) and content:
                payload = json.loads(content[0].get("text") or "{}")
                return {
                    "attachment_id": args.get("attachment_id"),
                    "kb_id": args.get("kb_id"),
                    "doc_id": payload.get("doc_id"),
                    "doc_name": payload.get("doc_name"),
                    "mime_type": payload.get("mime_type"),
                    "size_bytes": payload.get("size_bytes"),
                    "content_hash": payload.get("content_hash"),
                    "source": "attachment",
                    "reversible_hint": (
                        "Delete the newly-created document via admin console "
                        "or a future doc_delete tool (Phase 3). The original "
                        "MinIO blob is referenced by the attachment row."
                    ),
                }
    except Exception:
        pass
    return {}


@tool(
    name="doc_archive_attachment",
    description=(
        "Use this tool when the user has attached a staged file to this "
        "session and asked you to archive it into a specific knowledge base. "
        "The attachment id comes from `ctx.attachments` (the `# Session "
        "attachments` section in your system prompt); do NOT fabricate ids.\n\n"
        "This is the write path for both scenarios:\n"
        "1. User uploaded file → you archive here\n"
        "2. You called `web_fetch_to_attachment` → you archive here\n\n"
        "Behavior:\n"
        "- Loads the blob from MinIO, runs `FileService.upload_document` "
        "(same pipeline as the dataset UI upload), including mime dispatch "
        "(text / office / image → RAGFlow's native parser task).\n"
        "- Flips the attachment status from `staged` → `archived` and stamps "
        "the resulting doc_id / kb_id onto the row.\n"
        "- Content-hash dedupe against the target KB — if the same content is "
        "already in the KB, returns `status=duplicate` with the existing "
        "`doc_id` and does NOT re-archive. Status is still updated though "
        "(the attachment is considered resolved).\n"
        "- Requires CONTRIBUTOR+ on the target KB.\n"
        "- Plan-gated: first write in a turn must follow an approved "
        "`submit_plan`. Show the user the preview of what you'll archive via "
        "the plan's `preview` field before calling.\n\n"
        "Images: they go in as `FileType.VISUAL`; OCR happens downstream if "
        "the tenant has PaddleOCR / MinerU configured. If not, the image is "
        "archived with a thumbnail but content is not searchable — warn the "
        "user in that case."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "attachment_id": {
                "type": "string",
                "description": (
                    "The attachment id from `ctx.attachments` — a 32-char "
                    "hex uuid. Only `status=staged` attachments may be "
                    "archived; other states return an error."
                ),
            },
            "kb_id": {
                "type": "string",
                "description": (
                    "Target knowledge base id. The caller must have "
                    "CONTRIBUTOR or higher on this KB."
                ),
            },
            "doc_name_override": {
                "type": "string",
                "description": (
                    "Optional override for the stored document name. If "
                    "omitted, uses the attachment's `filename`. Useful when "
                    "the attachment is generic (e.g. `report.pdf`) but you "
                    "want a meaningful name in the KB."
                ),
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional tags to apply after ingest. Stored in "
                    "`document.meta_fields.tags`. Use for grouping by "
                    "category / quarter / author."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Optional audit log reason.",
            },
        },
        "required": ["attachment_id", "kb_id"],
    },
)
@require_kb_write(
    action="kb.doc.archive_attachment",
    min_role="contributor",
    kb_id_from=_resolve_kb_id,
    extra_audit_metadata=_extra_audit,
)
async def doc_archive_attachment(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    attachment_id = str(args.get("attachment_id") or "").strip()
    kb_id = str(args.get("kb_id") or "").strip()

    if not attachment_id or not kb_id:
        return err("invalid_input", "attachment_id and kb_id are required")

    # ─── 1) Fetch attachment row + validate state ───
    from api.db.services.agent_v2_service import AgentV2AttachmentService

    att = AgentV2AttachmentService.get_by_id(attachment_id)
    if not att:
        return err("not_found", f"attachment {attachment_id!r} does not exist")
    if att.tenant_id != tenant_id:
        return err(
            "out_of_scope",
            "attachment belongs to a different tenant",
        )
    if att.status == "archived":
        # Idempotent: if already archived to the same KB, just restate the
        # existing doc. If to a different KB, that's an error (we do NOT
        # silently re-archive to a different target).
        if att.archived_kb_id == kb_id:
            return ok(
                status="already_archived",
                attachment_id=attachment_id,
                doc_id=att.archived_doc_id,
                kb_id=att.archived_kb_id,
                archived_at=att.archived_at,
                message="This attachment was already archived to this KB.",
            )
        return err(
            "already_archived_elsewhere",
            f"attachment is already archived to kb_id={att.archived_kb_id!r}; "
            "cannot re-archive to a different KB",
        )
    if att.status != "staged":
        return err(
            "invalid_state",
            f"attachment.status={att.status!r}; only 'staged' can be archived",
        )

    # ─── 2) Load target KB + verify tenant + optional embedding compat ───
    from api.db.services.knowledgebase_service import KnowledgebaseService

    k_ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not k_ok or not kb:
        return err("not_found", f"target KB {kb_id!r} not found")
    if kb.tenant_id != tenant_id:
        return err(
            "out_of_scope",
            "target KB does not belong to current tenant",
        )

    # ─── 3) Load blob from MinIO ───
    # ``blob_path`` was stored as "{bucket}/{object_key}"; split it back.
    bucket, sep, key = (att.blob_path or "").partition("/")
    if not sep or not bucket or not key:
        return err(
            "blob_path_malformed",
            f"attachment.blob_path={att.blob_path!r} is not bucket/key",
        )
    try:
        blob = settings.STORAGE_IMPL.get(bucket, key)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "doc_archive_attachment: blob fetch failed: %s", exc
        )
        return err(
            "blob_unavailable",
            f"MinIO fetch failed: {type(exc).__name__}: {exc}",
        )
    if not blob:
        return err("blob_empty", "MinIO returned 0 bytes for attachment blob")

    # ─── 4) Hash-based dedupe against target KB ───
    from api.db.db_models import Document

    content_hash = att.hash_xxh128  # already computed at upload time
    existing_doc = (
        Document.select()
        .where(
            (Document.kb_id == kb_id)
            & (Document.content_hash == content_hash)
        )
        .first()
    )
    if existing_doc:
        # Mark attachment archived pointing to existing doc (so the UI
        # reflects the resolution). This is safe because content is identical.
        AgentV2AttachmentService.mark_archived(
            attachment_id=attachment_id,
            doc_id=existing_doc.id,
            kb_id=kb_id,
        )
        return ok(
            status="duplicate",
            attachment_id=attachment_id,
            doc_id=existing_doc.id,
            doc_name=existing_doc.name,
            kb_id=kb_id,
            size_bytes=att.size_bytes,
            mime_type=att.mime_type,
            content_hash=content_hash,
            message=(
                "Same content already exists in the target KB as "
                f"{existing_doc.name!r}; not re-uploading. Attachment "
                "status flipped to archived."
            ),
            next_steps=[
                (
                    "The content is already searchable via "
                    f"rag_retrieve(kb_ids=['{kb_id}'], query=...)."
                ),
                "No further action needed on this attachment.",
            ],
        )

    # ─── 5) Upload via FileService (same pipeline as UI upload) ───
    doc_name = str(
        args.get("doc_name_override") or ""
    ).strip() or att.filename

    try:
        from api.db.services.file_service import FileService

        user_id = ctx.user_id or tenant_id
        file_obj = _FakeFileUpload(doc_name, blob)
        err_list, files = FileService.upload_document(kb, [file_obj], user_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_archive_attachment upload failed: %s", exc)
        return err("upload_failed", f"{type(exc).__name__}: {exc}")

    if err_list:
        return err(
            "upload_failed",
            "; ".join(err_list[:3]),
            detail=err_list[:5],
        )
    if not files:
        return err("upload_failed", "no document created")

    doc_dict, _ = files[0]
    new_doc_id = doc_dict.get("id")
    new_doc_name = doc_dict.get("name")

    # ─── 6) Optional tag apply ───
    tags = args.get("tags") or []
    applied_tags: list[str] = []
    if isinstance(tags, list) and tags and new_doc_id:
        try:
            from api.db.services.document_service import DocumentService

            dok, doc = DocumentService.get_by_id(new_doc_id)
            if dok and doc:
                meta = doc.meta_fields or {}
                existing_tags = meta.get("tags") or []
                merged = list({*existing_tags, *(str(t).strip() for t in tags if str(t).strip())})
                meta["tags"] = merged
                DocumentService.update_by_id(
                    new_doc_id, {"meta_fields": meta}
                )
                applied_tags = merged
        except Exception as exc:  # noqa: BLE001
            logger.warning("doc_archive_attachment: tag apply failed: %s", exc)

    # ─── 7) Flip attachment row status ───
    AgentV2AttachmentService.mark_archived(
        attachment_id=attachment_id,
        doc_id=new_doc_id,
        kb_id=kb_id,
    )

    is_image = att.mime_type.startswith("image/")
    next_steps = [
        (
            "Ask the user to send 'check progress' in the next message; "
            f"then call rag_retrieve(kb_ids=['{kb_id}'], query=...) to "
            "check the content indexed."
        ),
        "The attachment is now status=archived; do NOT archive it again.",
    ]
    if is_image:
        next_steps.append(
            "This is an image — OCR / indexing depends on tenant parser "
            "config. If after 30s rag_retrieve returns empty, suggest the "
            "user check PaddleOCR / MinerU configuration."
        )

    return ok(
        status="queued_for_parse",
        attachment_id=attachment_id,
        doc_id=new_doc_id,
        doc_name=new_doc_name,
        kb_id=kb_id,
        size_bytes=att.size_bytes,
        mime_type=att.mime_type,
        content_hash=content_hash,
        applied_tags=applied_tags,
        reason=args.get("reason"),
        next_steps=next_steps,
    )


class _FakeFileUpload:
    """Mimic the subset of FileStorage that FileService.upload_document uses.

    Identical to the one in ``doc_upload_from_url`` — kept standalone to
    avoid cross-module import cycles between sibling tools.
    """

    def __init__(self, filename: str, blob: bytes):
        self.filename = filename
        self._blob = blob

    def read(self) -> bytes:
        return self._blob

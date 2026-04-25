"""Phase 2.7 Stage 2 — archive a session attachment into a KB.

Writes are funnelled through ``FileService.upload_document`` so:
- Parsing queue handles PDF / docx / xlsx uniformly (task executor dispatches
  based on ``FileType``).
- Images take a different path: we synchronously run PaddleOCR via
  ``_image_ocr.run_image_ocr`` and compose a markdown sub-document
  (``<name>.ocr.md``) with provenance front-matter linking back to the
  original MinIO blob. Doing OCR at archive time gives the user immediate
  searchable text and a single review surface (the markdown), instead of
  silently relying on whatever downstream parser the tenant happens to
  have wired up.
- Dedup by content hash against the target KB, consistent with
  ``doc_upload_from_url``. Note: dedup uses the *attachment* hash (raw
  image bytes), not the markdown-text hash — so the same image archived
  twice still dedupes even if OCR jitter produces different markdown.

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

logger = logging.getLogger("ragflow.agent_v2.doc_ops.doc_ingest_attachment")


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
    name="doc_ingest_attachment",
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
        "Images: synchronous PaddleOCR runs at archive time; the resulting "
        "text is composed into a markdown sub-document (`<name>.ocr.md`) "
        "with provenance front-matter and that markdown — not the raw image "
        "— is what gets indexed. The original image blob stays in MinIO and "
        "is referenced via `meta_fields.source_blob` so a future vision-LLM "
        "re-OCR can find it. The response carries `ocr_signal` "
        "(rich / low / none) — relay that to the user; for `none` images "
        "suggest manual description or wait for the vision-LLM path."
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
async def doc_ingest_attachment(args: dict) -> dict:
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
            "doc_ingest_attachment: blob fetch failed: %s", exc
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

    # Image branch: synchronously OCR → emit a markdown sub-document with
    # provenance front-matter. The original blob is preserved in MinIO under
    # the attachment row; the markdown is what gets indexed by the KB.
    is_image = (att.mime_type or "").startswith("image/")
    ocr_signal: str | None = None
    ocr_chars: int | None = None
    ocr_elapsed_ms: int | None = None
    if is_image:
        from ._image_ocr import (
            build_markdown_for_image,
            derive_markdown_filename,
            run_image_ocr,
        )

        ocr_result = await run_image_ocr(blob)
        ocr_signal = ocr_result.signal
        ocr_chars = ocr_result.char_count
        ocr_elapsed_ms = ocr_result.elapsed_ms
        markdown = build_markdown_for_image(
            original_filename=doc_name,
            mime_type=att.mime_type,
            size_bytes=att.size_bytes,
            content_hash=content_hash,
            blob_path=att.blob_path,
            ocr_result=ocr_result,
        )
        upload_blob = markdown.encode("utf-8")
        upload_name = derive_markdown_filename(doc_name)
    else:
        upload_blob = blob
        upload_name = doc_name

    try:
        from api.db.services.file_service import FileService

        user_id = ctx.user_id or tenant_id
        file_obj = _FakeFileUpload(upload_name, upload_blob)
        err_list, files = FileService.upload_document(kb, [file_obj], user_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_ingest_attachment upload failed: %s", exc)
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

    # ─── 6) Optional tag apply + image provenance stamp ───
    tags = args.get("tags") or []
    applied_tags: list[str] = []
    needs_meta_update = bool(
        (isinstance(tags, list) and tags) or is_image
    ) and new_doc_id
    if needs_meta_update:
        try:
            from api.db.services.document_service import DocumentService

            dok, doc = DocumentService.get_by_id(new_doc_id)
            if dok and doc:
                meta = doc.meta_fields or {}
                if isinstance(tags, list) and tags:
                    existing_tags = meta.get("tags") or []
                    merged = list({
                        *existing_tags,
                        *(str(t).strip() for t in tags if str(t).strip()),
                    })
                    meta["tags"] = merged
                    applied_tags = merged
                if is_image:
                    # Provenance: link the markdown doc back to the original
                    # image blob in MinIO. Lets a future re-OCR job find the
                    # source even after the attachment row is gc'd.
                    meta["source_blob"] = att.blob_path
                    meta["source_mime"] = att.mime_type
                    meta["source_attachment_id"] = attachment_id
                    meta["ocr_engine"] = "deepdoc.vision.OCR"
                    meta["ocr_signal"] = ocr_signal
                    meta["ocr_char_count"] = ocr_chars
                    meta["ocr_elapsed_ms"] = ocr_elapsed_ms
                DocumentService.update_by_id(
                    new_doc_id, {"meta_fields": meta}
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("doc_ingest_attachment: meta update failed: %s", exc)

    # ─── 7) Flip attachment row status ───
    AgentV2AttachmentService.mark_archived(
        attachment_id=attachment_id,
        doc_id=new_doc_id,
        kb_id=kb_id,
    )

    next_steps = [
        (
            "Ask the user to send 'check progress' in the next message; "
            f"then call rag_retrieve(kb_ids=['{kb_id}'], query=...) to "
            "check the content indexed."
        ),
        "The attachment is now status=archived; do NOT archive it again.",
    ]
    if is_image:
        if ocr_signal == "rich":
            next_steps.append(
                f"OCR extracted {ocr_chars} chars ({ocr_elapsed_ms}ms) and "
                "wrote them into a markdown sub-document; original image "
                "blob is preserved at the attachment's MinIO path."
            )
        elif ocr_signal == "low":
            next_steps.append(
                f"OCR returned only {ocr_chars} chars (likely a stamp / "
                "watermark / short label). Indexed as markdown but warn "
                "the user that the image may need a vision LLM for richer "
                "description."
            )
        else:
            next_steps.append(
                "OCR found no extractable text (likely a photo / diagram / "
                "logo). The markdown stub is searchable by filename; the "
                "original image is preserved in MinIO. Suggest the user "
                "describe the image manually or we'll re-OCR with a vision "
                "LLM when that path lands."
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
        ocr_signal=ocr_signal,
        ocr_char_count=ocr_chars,
        ocr_elapsed_ms=ocr_elapsed_ms,
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

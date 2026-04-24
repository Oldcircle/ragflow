"""Phase 2.7 Stage 1 — session-scoped attachment helpers.

Separated from ``attachments_app.py`` so:
1. Unit tests can exercise size / mime / preview logic without the HTTP layer
2. Runner / tool layer can re-use ``attachment_to_info_dict`` without circular
   imports through ``api.apps``.

Design notes (对齐 ``PLAN-attachments.md`` §4 + §7)：
- MinIO path: ``{AGENT_V2_ATTACHMENT_BUCKET}`` 作 bucket，``{tenant}/{session}/
  {attachment_id}`` 作 object key。一个 bucket 够用（per-tenant quota 单独管）。
- 大小上限 50 MB 单文件，沿用 ``doc_upload_from_url`` 的阈值
- MIME 白名单：文本 / 办公 / 图片（图片走 Stage 2 OCR 分流）
- Preview 8 KB 首段明文；PDF / docx 抽首页，图片占位 "[image, OCR pending]"
- dedupe: xxhash128 per tenant，命中 staged/archived 直接复用 attachment_id
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

import xxhash

logger = logging.getLogger("ragflow.agent_v2.attachments")


# Bucket name follows DNS-compliant S3 naming rules (3-63 chars, lowercase,
# digits / hyphens only, no underscores). Some S3-compatible backends (e.g.
# stricter MinIO variants) reject underscores at region-lookup time.
AGENT_V2_ATTACHMENT_BUCKET = "agent-v2-attachments"

# 对齐 PLAN-attachments.md §7 & §一 — 单文件 50 MB；注意 HTTP 层可能会先
# 命中 Flask/Quart 的 MAX_CONTENT_LENGTH 拒绝，这里做第二道防御。
MAX_ATTACHMENT_SIZE_BYTES = 50 * 1024 * 1024

# 单 session 上限：200 MB / 20 个 staged 附件
MAX_SESSION_STAGED_BYTES = 200 * 1024 * 1024
MAX_SESSION_STAGED_COUNT = 20

# Preview 抽取 8 KB 首段纯文本
MAX_PREVIEW_BYTES = 8 * 1024

# MIME 白名单 — key 是 MIME，value 是"人类可读的简短描述"（用在 audit 里）。
#
# 顺序：文本（LLM 直接读）→ 办公（deepdoc 解析）→ 图片（Stage 2 OCR）。
# 不接 bmp/svg/tiff：RAGFlow 的 FileType.VISUAL 在 file_utils 是更宽的白名单，
# 但 agent v2 场景先收紧；有需要再扩。
MIME_WHITELIST: dict[str, str] = {
    # 纯文本家族
    "text/plain": "plain text",
    "text/markdown": "markdown",
    "text/csv": "csv",
    "application/json": "json",
    "text/html": "html",
    # 办公文档
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    # 图片（Stage 2 OCR 路径）
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


# Filename extension → MIME fallback. 用于客户端发送 ``application/octet-stream``
# 之类含糊 MIME 时的兜底，避免因浏览器/curl 行为差异把合法附件误拒。
_EXT_TO_MIME: dict[str, str] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def resolve_mime_type(client_mime: str, filename: str) -> str | None:
    """Return the best MIME guess; ``None`` if neither sanctioned nor resolvable.

    Clients often lie (browser autodetect / octet-stream / empty). Strategy:
    1. If the client MIME is in whitelist → trust it.
    2. Else lookup by filename extension → if extension maps to a whitelisted
       MIME → use that.
    3. Else return None (caller rejects with ``unsupported_mime``).
    """
    m = (client_mime or "").strip().lower().split(";", 1)[0]
    if m in MIME_WHITELIST:
        return m
    if filename:
        ext = filename.lower().rsplit(".", 1)
        if len(ext) == 2:
            guessed = _EXT_TO_MIME.get("." + ext[1])
            if guessed:
                return guessed
    return None


def object_key(*, tenant_id: str, session_id: str, attachment_id: str) -> str:
    """MinIO object key scheme — keep stable; referenced by blob GC cron."""
    return f"{tenant_id}/{session_id}/{attachment_id}"


def hash_content(blob: bytes) -> str:
    """128-bit hex, used for dedupe key."""
    return xxhash.xxh128(blob).hexdigest()


# ─────────────────────────── Preview extraction ───────────────────────────


def extract_preview(blob: bytes, mime_type: str) -> str:
    """Return up to ``MAX_PREVIEW_BYTES`` chars of plain text for UI preview.

    Never raises — on failure returns a placeholder. Mime-dispatched:
    - text/*, application/json: decode utf-8, truncate
    - pdf: try ``pypdf`` first page; fallback to placeholder
    - docx: try ``python-docx`` first paragraphs; fallback to placeholder
    - xlsx: first sheet, top 10 rows CSV; fallback to placeholder
    - image/*: placeholder (Stage 2 OCR populates real text on archive)
    - other office: placeholder
    """
    try:
        # HTML needs special stripping (before the generic text/* path).
        if mime_type == "text/html":
            # Stripping HTML cheaply — bs4 is already in env for web_fetch
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(blob[:MAX_PREVIEW_BYTES * 3], "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
            return text[:MAX_PREVIEW_BYTES]

        # Generic text + JSON — decode as UTF-8, truncate.
        if mime_type.startswith("text/") or mime_type == "application/json":
            return blob[:MAX_PREVIEW_BYTES].decode("utf-8", errors="replace")

        if mime_type == "application/pdf":
            return _preview_pdf(blob)

        if mime_type in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ):
            return _preview_docx(blob)

        if mime_type in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
        ):
            return _preview_xlsx(blob)

        if mime_type.startswith("image/"):
            return "[image, OCR pending at archive time]"

        return f"[{MIME_WHITELIST.get(mime_type, mime_type)}, preview not available]"
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "extract_preview failed for mime=%s: %s: %s",
            mime_type, type(e).__name__, e,
        )
        return f"[preview extraction failed: {type(e).__name__}]"


def _preview_pdf(blob: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(blob))
        pages = []
        total = 0
        for page in reader.pages[:3]:  # cap at 3 pages to keep the fast path fast
            txt = page.extract_text() or ""
            pages.append(txt)
            total += len(txt.encode("utf-8"))
            if total >= MAX_PREVIEW_BYTES * 2:
                break
        return "\n\n".join(pages)[:MAX_PREVIEW_BYTES]
    except Exception as e:  # noqa: BLE001
        logger.debug("pdf preview fallback: %s", e)
        return "[pdf, preview unavailable]"


def _preview_docx(blob: bytes) -> str:
    try:
        from docx import Document

        doc = Document(BytesIO(blob))
        paras = []
        total = 0
        for p in doc.paragraphs:
            txt = p.text or ""
            if not txt.strip():
                continue
            paras.append(txt)
            total += len(txt.encode("utf-8"))
            if total >= MAX_PREVIEW_BYTES:
                break
        return "\n\n".join(paras)[:MAX_PREVIEW_BYTES]
    except Exception as e:  # noqa: BLE001
        logger.debug("docx preview fallback: %s", e)
        return "[docx, preview unavailable]"


def _preview_xlsx(blob: bytes) -> str:
    try:
        from openpyxl import load_workbook

        wb = load_workbook(BytesIO(blob), read_only=True, data_only=True)
        ws = wb.active
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 10:
                break
            rows.append(
                ", ".join(str(c) if c is not None else "" for c in row)
            )
        return "\n".join(rows)[:MAX_PREVIEW_BYTES]
    except Exception as e:  # noqa: BLE001
        logger.debug("xlsx preview fallback: %s", e)
        return "[xlsx, preview unavailable]"


# ─────────────────────────── AttachmentInfo DTO ───────────────────────────


@dataclass(frozen=True)
class AttachmentInfo:
    """Frozen view over an AgentV2Attachment row — passed to tools via
    ``ToolContext.attachments`` and rendered to the supervisor system prompt.

    Intentionally minimal — the Runner doesn't expose raw DB handles to tools.
    Archiving logic fetches the model via the service layer.
    """

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    origin: str
    status: str
    preview_text: str | None
    source_url: str | None
    archived_doc_id: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "origin": self.origin,
            "status": self.status,
            "preview_text": self.preview_text,
            "source_url": self.source_url,
            "archived_doc_id": self.archived_doc_id,
        }

    @classmethod
    def from_row(cls, row) -> "AttachmentInfo":
        return cls(
            id=row.id,
            filename=row.filename,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            origin=row.origin,
            status=row.status,
            preview_text=row.preview_text or None,
            source_url=row.source_url or None,
            archived_doc_id=row.archived_doc_id or None,
        )


def render_attachments_prompt_section(
    attachments: tuple["AttachmentInfo", ...], *, lang: str = "en"
) -> str:
    """Compose the ``# Session attachments`` system-prompt section.

    Only **staged** attachments are described as actionable — archived ones
    get a one-liner for context but aren't call-to-action. Empty → "".

    Mirrors Claude Code's ``getPlanModeAttachments()`` convention
    (ref: ``src/utils/attachments.ts:1425``) — keep the section **stable**
    across turns so prompt cache isn't busted unnecessarily.
    """
    if not attachments:
        return ""

    staged = [a for a in attachments if a.status == "staged"]
    archived = [a for a in attachments if a.status == "archived"]

    # Bullet format: - `{filename}` (mime, size_kb KB, origin=upload|web_fetch, id=xxxx)
    def _size_label(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / 1024 / 1024:.1f} MB"

    def _bullet(a) -> str:
        parts = [
            f"`{a.filename}`",
            f"({MIME_WHITELIST.get(a.mime_type, a.mime_type)},",
            f"{_size_label(a.size_bytes)},",
            f"origin={a.origin},",
            f"id=`{a.id}`)",
        ]
        if a.source_url:
            parts.append(f"[source: {a.source_url}]")
        return "  - " + " ".join(parts)

    if lang == "zh":
        lines = ["", "# 会话附件（待用户决定）", ""]
        if staged:
            lines.append(
                f"用户在本会话附带了 **{len(staged)} 个 staged 附件**，**等待归档决策**："
            )
            lines.extend(_bullet(a) for a in staged)
            lines.append("")
            lines.append(
                "归档工作流（严格按顺序）：\n"
                "1. 调用 `spawn_subagent(subagent_type='sub_archivist')`，在 prompt 里说明用户意图\n"
                "2. sub_archivist 检查 `ctx.attachments` 后调 `submit_plan(preview=...)` 把预览展示给用户\n"
                "3. 用户回复 `[plan approved]` 后，sub_archivist 调 `doc_archive_attachment(attachment_id, kb_id)` 入库\n"
                "**不要**自己直接调 `doc_archive_attachment`（supervisor 不持有写工具）。\n"
                "**不要**虚构附件（本清单是唯一来源——只能引用这些 `id` 值）。"
            )
        if archived:
            lines.append("")
            lines.append("已归档的附件（仅作参考，不必再处理）：")
            lines.extend(
                f"  - `{a.filename}` → doc_id=`{a.archived_doc_id}`"
                for a in archived
            )
        return "\n".join(lines).rstrip() + "\n"

    # English (default)
    lines = ["", "# Session attachments (pending user decision)", ""]
    if staged:
        lines.append(
            f"The user has attached **{len(staged)} staged file(s)** to this session, "
            "awaiting an archive decision:"
        )
        lines.extend(_bullet(a) for a in staged)
        lines.append("")
        lines.append(
            "Workflow to archive (strict order):\n"
            "1. Delegate to `sub_archivist` via `spawn_subagent(subagent_type='sub_archivist')`\n"
            "2. The archivist inspects `ctx.attachments`, then calls `submit_plan(preview=...)` "
            "to show the user the preview\n"
            "3. Once the user replies `[plan approved]`, the archivist calls "
            "`doc_archive_attachment(attachment_id, kb_id)` to persist to KB\n"
            "Do NOT call `doc_archive_attachment` directly (supervisors don't hold write tools). "
            "Do NOT fabricate attachments — the IDs above are the ONLY valid references."
        )
    if archived:
        lines.append("")
        lines.append("Already-archived attachments (for reference only):")
        lines.extend(
            f"  - `{a.filename}` → doc_id=`{a.archived_doc_id}`" for a in archived
        )
    return "\n".join(lines).rstrip() + "\n"

"""doc_create_note — Agent 把 Markdown 内容作为新文档入库（Phase 2.6 v0.2）。

**这是"自总结笔记"能力的核心**：Agent 自己生成综合 / FAQ / 每周报告 / 审计
摘要，通过本工具变成 KB 里的一个正式文档，后续其他 Agent 能检索到。

和 ``doc_upload_from_url`` 的区别：那个从外部 URL 拉文件；本工具从
**Agent 自己的 Markdown** 创建。走同一条 ``FileService.upload_document``
路径，解析 / 索引 / 审计一致。

RBAC：CONTRIBUTOR+ on target KB（同 doc_upload_from_url）。
Audit：``kb.doc.note_create`` + metadata 含 content_hash + source=agent_note。
Idempotency：90s TTL；content_hash 级 dedup 防重复笔记。
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import get_ctx, tool
from ._common import err, ok, require_kb_write

logger = logging.getLogger("ragflow.agent_v2.doc_ops.doc_create_note")

_MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB 上限（足够装周报 / FAQ / 总结）
_MAX_TITLE_LEN = 240


def _resolve_kb_id(args: dict) -> str | None:
    return args.get("kb_id")


def _extra_audit(args: dict, result: Any, _ctx) -> dict:
    try:
        import json

        if isinstance(result, dict):
            c = result.get("content")
            if isinstance(c, list) and c:
                payload = json.loads(c[0].get("text") or "{}")
                return {
                    "kb_id": args.get("kb_id"),
                    "doc_id": payload.get("doc_id"),
                    "doc_name": payload.get("doc_name"),
                    "content_hash": payload.get("content_hash"),
                    "size_bytes": payload.get("size_bytes"),
                    "tags_applied": payload.get("tags_applied"),
                    "source": "agent_note",
                    "reversible_hint": (
                        "Delete the document via admin console (Phase 3 will "
                        "add doc_delete with approval flow)."
                    ),
                }
    except Exception:
        pass
    return {}


@tool(
    name="doc_create_note",
    description=(
        "【WHEN】**需要把自己生成的 Markdown 内容作为新文档保存到 KB**时用。"
        "典型场景：\n"
        "- 用户要求『把这次检索总结成一份笔记存下来』\n"
        "- 自己做完 kb_audit 之后，生成『巡检报告』存回某个元数据 KB\n"
        "- 生成 FAQ / 政策速览 / 行业摘要 作为可检索文档\n\n"
        "【WHAT】把 ``markdown_body`` 作为 ``.md`` 文件写到目标 KB；自动排进解析队列，"
        "解析完成后其他 Agent 通过 rag_retrieve 能检索到。\n\n"
        "【限制】\n"
        "- 单次 markdown_body ≤ 2MB（足够一份报告，超过说明 prompt 出错）\n"
        "- 同 KB 内 content_hash 相同 → 不重复入库，返 duplicate 提示\n"
        "- 需要 CONTRIBUTOR+ 权限"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kb_id": {"type": "string", "description": "目标知识库 ID"},
            "title": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_TITLE_LEN,
                "description": (
                    "笔记标题。**不要**包含扩展名；工具会自动加 `.md`。"
                    "例：'2024-Q1 保障房政策速览' / '深圳户籍人才安居 FAQ'"
                ),
            },
            "markdown_body": {
                "type": "string",
                "minLength": 32,
                "description": (
                    "Markdown 正文。要点：\n"
                    "- 开头一行 `# <标题>` 方便人类阅读\n"
                    "- 用 `##` / `###` 分节便于检索 chunk 边界\n"
                    "- 引用其他文档时用链接或明确『依据：《XX 办法》第 N 条』\n"
                    "- **不要**凭训练知识补 KB 里没有的细节——这里是笔记不是答复"
                ),
            },
            "tags": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
                "maxItems": 10,
                "description": (
                    "可选：自动打标签，方便以后筛出 agent 生成的笔记。"
                    "建议至少带 ['agent_note'] 一个通用 tag。"
                ),
            },
            "reason": {"type": "string", "description": "可选；写进审计便于追溯。"},
        },
        "required": ["kb_id", "title", "markdown_body"],
    },
)
@require_kb_write(
    action="kb.doc.note_create",
    min_role="contributor",
    kb_id_from=_resolve_kb_id,
    extra_audit_metadata=_extra_audit,
)
async def doc_create_note(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    kb_id = str(args.get("kb_id") or "").strip()
    title = str(args.get("title") or "").strip()
    body = str(args.get("markdown_body") or "")
    raw_tags = args.get("tags") or []

    if not kb_id or not title or not body:
        return err("invalid_input", "kb_id, title, markdown_body all required")
    body_bytes = body.encode("utf-8")
    if len(body_bytes) > _MAX_BODY_BYTES:
        return err(
            "too_large",
            f"markdown_body is {len(body_bytes)}B, exceeds {_MAX_BODY_BYTES}B limit",
        )

    # Target KB + tenant guard
    from api.db.services.knowledgebase_service import KnowledgebaseService

    k_ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not k_ok or not kb:
        return err("not_found", f"KB {kb_id!r} not found")
    if kb.tenant_id != tenant_id:
        return err("out_of_scope", "KB does not belong to current tenant")

    # 文件名 + dedup hash
    import xxhash

    filename = f"{_sanitize_filename(title)}.md"
    content_hash = xxhash.xxh128(body_bytes).hexdigest()

    # 同 KB content_hash dedup
    from api.db.db_models import Document

    existing = (
        Document.select()
        .where((Document.kb_id == kb_id) & (Document.content_hash == content_hash))
        .first()
    )
    if existing:
        return ok(
            status="duplicate",
            doc_id=existing.id,
            doc_name=existing.name,
            kb_id=kb_id,
            content_hash=content_hash,
            size_bytes=len(body_bytes),
            message=(
                f"Identical note already exists as {existing.name!r}; "
                "not re-uploading. Update the existing doc if needed."
            ),
        )

    # Quota check (doc_max)
    try:
        from api.db.services.tenant_quota_service import TenantQuotaService

        limits = TenantQuotaService.get(tenant_id)
        if limits.hard_enforce:
            from api.db.db_models import Knowledgebase

            current = (
                Document.select()
                .join(
                    Knowledgebase,
                    on=(Document.kb_id == Knowledgebase.id),
                )
                .where(Knowledgebase.tenant_id == tenant_id)
                .count()
            )
            if current >= limits.doc_max:
                return err(
                    "quota_exceeded",
                    f"doc_max reached ({current}/{limits.doc_max})",
                )
    except Exception:
        logger.exception("doc_create_note: quota check error (proceeding)")

    # Upload via FileService
    try:
        from api.db.services.file_service import FileService

        user_id = ctx.user_id or tenant_id
        file_obj = _InlineFileUpload(filename, body_bytes)
        err_list, files = FileService.upload_document(kb, [file_obj], user_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_create_note upload failed: %s", exc)
        return err("upload_failed", f"{type(exc).__name__}: {exc}")

    if err_list:
        return err("upload_failed", "; ".join(err_list[:3]), detail=err_list[:5])
    if not files:
        return err("upload_failed", "no document created")

    doc_dict, _ = files[0]
    doc_id = doc_dict.get("id")

    # 自动打标签（tags + 一个隐式 ``source:agent_note``）
    tags_applied: list[str] = []
    want_tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    # 始终加 source:agent_note 作为内部标记
    if "source:agent_note" not in want_tags:
        want_tags.append("source:agent_note")
    want_tags = want_tags[:10]  # 安全上限

    if doc_id and want_tags:
        try:
            from api.db.services.doc_metadata_service import DocMetadataService

            DocMetadataService.insert_document_metadata(doc_id, {"tags": want_tags})
            tags_applied = want_tags
        except Exception:
            logger.exception("doc_create_note: tag insertion failed (doc still saved)")

    return ok(
        doc_id=doc_id,
        doc_name=doc_dict.get("name"),
        kb_id=kb_id,
        content_hash=content_hash,
        size_bytes=len(body_bytes),
        tags_applied=tags_applied,
        status_note="queued_for_parse",
        reason=args.get("reason"),
    )


def _sanitize_filename(name: str) -> str:
    """把 title 变成安全文件名（去非法字符，统一 -）。"""
    import re

    # 先去禁止字符
    cleaned = re.sub(r'[\x00-\x1f<>:"|?*/\\]+', " ", name).strip()
    # 多空格压成单个 -
    cleaned = re.sub(r"\s+", "-", cleaned)
    # 避免全空
    if not cleaned:
        cleaned = "agent-note"
    # 控制长度（扣掉 .md 的 3 字符）
    return cleaned[:200]


class _InlineFileUpload:
    """Mimic subset of werkzeug FileStorage that FileService.upload_document uses."""

    def __init__(self, filename: str, blob: bytes):
        self.filename = filename
        self._blob = blob

    def read(self) -> bytes:
        return self._blob

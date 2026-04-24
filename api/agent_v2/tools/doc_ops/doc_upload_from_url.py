"""doc_upload_from_url — 从 URL 拉文件入 KB（Phase 2.6）。

用途：用户说『把这份白皮书加到行业库』『从 https://... 拉一份合同归档』时用。

安全（非常重要）：
- URL scheme 只允 http / https（拒绝 file:// / ftp:// / gopher://）
- IP 层 SSRF 防御：resolve 出的 IP 不能是 private / loopback / link-local / 广播
- 总大小上限 50 MB（``MAX_BYTES``）；超过立即断流拒绝
- 下载超时 30 s，连接超时 10 s
- 响应 Content-Type 只做记录，不据此拒绝（很多 CDN 返 octet-stream）
- 不 follow redirect 超过 5 次
- 下载完做 xxhash128，若 target KB 已有同 hash 文档 → 返回去重提示不重新入库

实现上走 FileService.upload_document 的路径（和 UI 上传一致），避免绕过后端校验。

RBAC：CONTRIBUTOR+ on 目标 KB。
Audit：``kb.doc.upload`` + source=url + size + hash。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from ..base import get_ctx, tool
from ._common import err, ok, require_kb_write

logger = logging.getLogger("ragflow.agent_v2.doc_ops.doc_upload_from_url")

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 30.0
_MAX_REDIRECTS = 5


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
                    "url": args.get("url"),
                    "kb_id": args.get("kb_id"),
                    "doc_id": payload.get("doc_id"),
                    "doc_name": payload.get("doc_name"),
                    "size_bytes": payload.get("size_bytes"),
                    "content_hash": payload.get("content_hash"),
                    "source": "url",
                    "reversible_hint": (
                        "Delete the newly-created document via admin console "
                        "or a future doc_delete tool (Phase 3)."
                    ),
                }
    except Exception:
        pass
    return {}


@tool(
    name="doc_upload_from_url",
    description=(
        "Use this tool when the user supplied a public http/https URL and "
        "asked to ingest the file into a specific KB.\n\n"
        "Hard security constraints:\n"
        "- http / https scheme only (file://, ftp:// etc. are refused).\n"
        "- Hosts resolving to private / loopback / link-local IPs are "
        "refused (SSRF protection).\n"
        "- 50 MB body cap; streaming is aborted if exceeded.\n"
        "- Duplicate detection via xxhash128 against the target KB — if "
        "content matches an existing doc, status=duplicate and no new "
        "upload happens.\n\n"
        "Requires CONTRIBUTOR+ on the target KB. Ingested docs follow the "
        "same parse queue as UI uploads; check progress via `rag_list_docs`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "format": "uri",
                "description": (
                    "Public http/https URL. Internal hosts, file://, "
                    "ftp://, etc. are rejected."
                ),
            },
            "kb_id": {
                "type": "string",
                "description": "Target knowledge base ID.",
            },
            "name": {
                "type": "string",
                "description": (
                    "Optional custom filename. If omitted, we use the URL "
                    "path's last segment or the server's "
                    "Content-Disposition filename header."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Optional audit-log reason.",
            },
        },
        "required": ["url", "kb_id"],
    },
)
@require_kb_write(
    action="kb.doc.upload",
    min_role="contributor",
    kb_id_from=_resolve_kb_id,
    extra_audit_metadata=_extra_audit,
)
async def doc_upload_from_url(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    url = str(args.get("url") or "").strip()
    kb_id = str(args.get("kb_id") or "").strip()
    override_name = args.get("name")

    if not url or not kb_id:
        return err("invalid_input", "url and kb_id are required")

    # 1) Scheme check
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return err(
            "unsupported_scheme",
            f"scheme {parsed.scheme!r} not allowed; use http / https",
        )
    if not parsed.hostname:
        return err("invalid_input", "URL missing hostname")

    # 2) SSRF defense — resolve hostname, reject private / loopback / link-local
    try:
        addrinfos = socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as e:
        return err("dns_fail", f"DNS resolve failed: {e}")
    for ai in addrinfos:
        addr = ai[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return err(
                "ssrf_blocked",
                f"hostname resolves to non-public IP {addr}; refusing",
            )

    # 3) Target KB validation + tenant guard
    from api.db.services.knowledgebase_service import KnowledgebaseService

    k_ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not k_ok or not kb:
        return err("not_found", f"target KB {kb_id!r} not found")
    if kb.tenant_id != tenant_id:
        return err("out_of_scope", "target KB does not belong to current tenant")

    # 4) Quota check (doc_max)
    try:
        from api.db.services.tenant_quota_service import TenantQuotaService

        limits = TenantQuotaService.get(tenant_id)
        if limits.hard_enforce:
            from api.db.db_models import Document

            current = (
                Document.select()
                .join(
                    __import__(
                        "api.db.db_models", fromlist=["Knowledgebase"]
                    ).Knowledgebase,
                    on=(Document.kb_id == __import__(
                        "api.db.db_models", fromlist=["Knowledgebase"]
                    ).Knowledgebase.id),
                )
                .where(
                    __import__(
                        "api.db.db_models", fromlist=["Knowledgebase"]
                    ).Knowledgebase.tenant_id == tenant_id
                )
                .count()
            )
            if current >= limits.doc_max:
                return err(
                    "quota_exceeded",
                    f"doc_max reached ({current}/{limits.doc_max})",
                )
    except Exception:
        logger.exception("doc_upload_from_url: quota check error (proceeding)")

    # 5) Download with size cap
    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return err(
                        "http_error",
                        f"HTTP {resp.status_code} fetching {url}",
                    )
                # Content-Length 预检
                total_hint = resp.headers.get("content-length")
                if total_hint and int(total_hint) > _MAX_BYTES:
                    return err(
                        "too_large",
                        f"Content-Length {total_hint} > limit {_MAX_BYTES}",
                    )
                buf = BytesIO()
                acc = 0
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    acc += len(chunk)
                    if acc > _MAX_BYTES:
                        return err(
                            "too_large",
                            f"download exceeds cap {_MAX_BYTES} bytes",
                        )
                    buf.write(chunk)
                blob = buf.getvalue()
                filename = (
                    override_name
                    or _extract_filename(resp.headers, parsed.path)
                    or "downloaded"
                )
    except httpx.RequestError as e:
        return err("download_failed", f"{type(e).__name__}: {e}")

    if not blob:
        return err("empty_body", "downloaded 0 bytes")

    # 6) Hash + duplicate check within target KB
    import xxhash

    h = xxhash.xxh128(blob).hexdigest()

    from api.db.db_models import Document

    existing = (
        Document.select()
        .where((Document.kb_id == kb_id) & (Document.content_hash == h))
        .first()
    )
    if existing:
        return ok(
            status="duplicate",
            doc_id=existing.id,
            doc_name=existing.name,
            kb_id=kb_id,
            content_hash=h,
            size_bytes=len(blob),
            message=(
                f"Document with identical hash already exists in target KB "
                f"as {existing.name!r}; not re-uploading."
            ),
        )

    # 7) Upload via FileService.upload_document (same path as UI)
    try:
        from api.db.services.file_service import FileService

        user_id = ctx.user_id or tenant_id
        file_obj = _FakeFileUpload(filename, blob)
        err_list, files = FileService.upload_document(kb, [file_obj], user_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_upload_from_url upload failed: %s", exc)
        return err("upload_failed", f"{type(exc).__name__}: {exc}")

    if err_list:
        return err(
            "upload_failed",
            "; ".join(err_list[:3]),
            detail=err_list[:5],
        )

    if not files:
        return err("upload_failed", "no document created")

    # `files` is list of (doc_dict, blob_bytes)
    doc_dict, _ = files[0]

    return ok(
        doc_id=doc_dict.get("id"),
        doc_name=doc_dict.get("name"),
        kb_id=kb_id,
        size_bytes=len(blob),
        content_hash=h,
        source_url=url,
        status="queued_for_parse",
        reason=args.get("reason"),
        next_steps=[
            (
                "Ask the user to send 'check progress' in the next message; "
                f"then call rag_retrieve(kb_ids=['{kb_id}'], query=...) to "
                "check the content indexed"
            ),
            "Do NOT re-call doc_upload_from_url with the same URL — the content hash will dedup",
        ],
    )


def _extract_filename(headers, url_path: str) -> str | None:
    disposition = headers.get("content-disposition") or ""
    if "filename=" in disposition.lower():
        # naive parse: 'filename="x.pdf"' or "filename=x.pdf"
        try:
            parts = disposition.split("filename=", 1)[1].strip()
            if parts.startswith('"') and parts.endswith('"'):
                parts = parts[1:-1]
            else:
                parts = parts.split(";")[0].strip()
            if parts:
                return parts
        except Exception:
            pass
    if url_path:
        tail = url_path.rstrip("/").split("/")[-1]
        if tail:
            return tail
    return None


class _FakeFileUpload:
    """Mimic the subset of flask/werkzeug FileStorage that FileService uses."""

    def __init__(self, filename: str, blob: bytes):
        self.filename = filename
        self._blob = blob

    def read(self) -> bytes:
        return self._blob

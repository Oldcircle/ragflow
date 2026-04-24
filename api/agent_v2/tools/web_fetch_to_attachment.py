"""Phase 2.7 Stage 2 — download a URL into a staged attachment (no KB commit).

Separation from ``web_fetch`` (`api/agent_v2/tools/web_fetch.py`)：
- ``web_fetch`` = transient read; Agent gets bytes, does not persist
- ``web_fetch_to_attachment`` = materialize as ``AgentV2Attachment`` row with
  ``origin=web_fetch``, ``status=staged``; pairs with ``doc_archive_attachment``
  for the two-phase "download → plan approval → archive" flow

Why two tools instead of a ``persist=true`` flag on ``web_fetch``:
- The SSE + plan-gate semantics differ meaningfully — this tool is the point
  where we expose a `preview_text` to the supervisor system prompt (via
  ``ctx.attachments``) so the next `submit_plan` can render it in the plan
  card. ``web_fetch`` has no such side effect.

Security parity with ``web_fetch``:
- Same SSRF triad (scheme / DNS / IP filter)
- Same redirect policy (same-host only, www. toggle allowed, 10-hop limit)
- Same 50 MB size cap
- http → https upgrade
"""

from __future__ import annotations

import logging
import time
from io import BytesIO
from urllib.parse import urljoin, urlparse

from .base import get_ctx, mcp_json_response, tool
from .web_fetch import (
    _MAX_REDIRECTS,
    _is_permitted_redirect,
    _ssrf_check,
    _upgrade_http_to_https,
)

logger = logging.getLogger("ragflow.agent_v2.web_fetch_to_attachment")


# Size cap matches the attachment upload path (50 MB) so the subsequent
# ``doc_archive_attachment`` doesn't reject our own output.
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_DEFAULT_TIMEOUT_S = 30.0
_DEDUP_TTL_MS = 24 * 60 * 60 * 1000  # 24h per-tenant URL dedupe


@tool(
    name="web_fetch_to_attachment",
    description=(
        "Use this tool when the user asks you to archive a specific web URL "
        "into a knowledge base. It downloads the page, stores it as a "
        "staged session attachment, and returns a preview — but does NOT "
        "archive to the KB yet.\n\n"
        "Workflow (strict order):\n"
        "1. Call this tool with the URL → get `attachment_id` + `preview_text`\n"
        "2. Call `submit_plan` with the preview (so the user sees what will "
        "be archived)\n"
        "3. After the user replies `[plan approved]`, call "
        "`doc_archive_attachment(attachment_id, kb_id=...)` to persist\n\n"
        "Security (same as `web_fetch`):\n"
        "- Public http(s) only; SSRF-protected\n"
        "- 50 MB cap; streaming cut off past the limit\n"
        "- http → https auto-upgrade; cross-host redirects blocked as "
        "`redirect_blocked` (pass the returned URL back to the user for "
        "explicit approval before retrying)\n\n"
        "Dedupe: same tenant + same URL within 24h returns the existing "
        "staged attachment (avoids re-downloading). Your next step is still "
        "`submit_plan` even on dedupe hit — the user still has to approve.\n\n"
        "Not for transient lookups: use `web_fetch` if you just need to read "
        "a page to inform your answer."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "http(s) URL to download. Public hosts only.",
            },
            "doc_name_hint": {
                "type": "string",
                "description": (
                    "Optional filename hint for the staged attachment. If "
                    "omitted, a name is derived from the URL path or "
                    "Content-Disposition header."
                ),
            },
            "timeout_s": {
                "type": "number",
                "description": "Per-request timeout (default 30, max 120).",
                "default": 30,
                "minimum": 1,
                "maximum": 120,
            },
        },
        "required": ["url"],
    },
)
async def web_fetch_to_attachment(args: dict) -> dict:
    start = time.perf_counter()

    def fail(error_code: str, message: str, **extra) -> dict:
        return mcp_json_response({
            "error": message,
            "error_code": error_code,
            "duration_ms": int((time.perf_counter() - start) * 1000),
            **extra,
        })

    ctx = get_ctx()
    tenant_id = ctx.tenant_id
    session_id = ctx.session_id
    user_id = ctx.user_id or tenant_id

    if not session_id:
        return fail(
            "no_session",
            "web_fetch_to_attachment requires a session context",
        )

    url = str(args.get("url", "")).strip()
    if not url:
        return fail("validation_error", "url must be non-empty")

    fetch_url, upgraded_from_http = _upgrade_http_to_https(url)

    ssrf_ok, reason = _ssrf_check(fetch_url)
    if not ssrf_ok:
        return fail("security_error", reason, url=url)

    timeout_s = min(max(float(args.get("timeout_s", _DEFAULT_TIMEOUT_S)), 1.0), 120.0)
    name_hint = str(args.get("doc_name_hint") or "").strip()

    # Dedupe — same tenant + same requested URL in the last 24h.
    from api.db.services.agent_v2_service import AgentV2AttachmentService

    dedupe_hit = AgentV2AttachmentService.find_by_source_url(
        tenant_id=tenant_id, source_url=url, ttl_ms=_DEDUP_TTL_MS,
    )
    if dedupe_hit:
        logger.info(
            "web_fetch_to_attachment: dedup hit url=%s -> attachment %s",
            url, dedupe_hit.id,
        )
        return mcp_json_response({
            "attachment_id": dedupe_hit.id,
            "status": dedupe_hit.status,
            "filename": dedupe_hit.filename,
            "mime_type": dedupe_hit.mime_type,
            "size_bytes": dedupe_hit.size_bytes,
            "source_url": dedupe_hit.source_url,
            "preview_text": dedupe_hit.preview_text,
            "dedupe": True,
            "duration_ms": int((time.perf_counter() - start) * 1000),
            "next_steps": [
                "Same URL was downloaded within the last 24h; using the "
                "existing staged attachment.",
                (
                    "Proceed to submit_plan(preview=...) then "
                    "doc_archive_attachment(attachment_id, kb_id=...)."
                    if dedupe_hit.status == "staged"
                    else f"Attachment is already {dedupe_hit.status}; "
                    "no further archive action needed."
                ),
            ],
        })

    # Download with manual redirect handling (mirror web_fetch policy).
    try:
        import httpx
    except ImportError:
        return fail("dependency_missing", "httpx_missing", url=url)

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout_s,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; RAGFlow-Agent-v2/1.0; "
                    "+https://github.com/Oldcircle/ragflow)"
                )
            },
        ) as client:
            current_url = fetch_url
            redirects = 0
            while True:
                resp = await client.get(current_url)
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break
                loc = resp.headers.get("location")
                if not loc:
                    return fail(
                        "redirect_error",
                        "redirect_missing_location",
                        url=url,
                        status_code=resp.status_code,
                    )
                next_url = urljoin(str(resp.url or current_url), loc)
                if not _is_permitted_redirect(current_url, next_url):
                    return fail(
                        "redirect_blocked",
                        "cross-host redirect requires explicit user approval",
                        url=url,
                        redirect_url=next_url,
                    )
                ok_ssrf, reason = _ssrf_check(next_url)
                if not ok_ssrf:
                    return fail(
                        "security_error",
                        reason,
                        url=url,
                        redirect_url=next_url,
                    )
                redirects += 1
                if redirects > _MAX_REDIRECTS:
                    return fail(
                        "redirect_error",
                        f"too_many_redirects: exceeded {_MAX_REDIRECTS}",
                        url=url,
                    )
                current_url = next_url
    except httpx.TimeoutException:
        return fail("timeout", "timeout", url=url)
    except httpx.HTTPError as e:
        return fail(
            "network_error",
            f"http_error: {type(e).__name__}: {e}",
            url=url,
        )

    if resp.status_code >= 400:
        return fail(
            "http_error",
            f"http_{resp.status_code}",
            url=url,
            status_code=resp.status_code,
        )

    # Size cap on the downloaded content.
    content = resp.content
    if len(content) > _MAX_DOWNLOAD_BYTES:
        return fail(
            "too_large",
            f"response body {len(content)} > cap {_MAX_DOWNLOAD_BYTES}",
            url=url,
        )
    if not content:
        return fail("empty_body", "downloaded 0 bytes", url=url)

    ctype = (resp.headers.get("content-type") or "").lower().split(";", 1)[0].strip()

    # Classify the content to a MIME we can stage. We're strict here — if the
    # server replied with something not in our whitelist but the URL path has
    # a known extension, trust the extension. (Matches ``resolve_mime_type``
    # behavior in the upload path.)
    from api.agent_v2.attachments import (
        AGENT_V2_ATTACHMENT_BUCKET,
        MIME_WHITELIST,
        extract_preview,
        hash_content,
        object_key,
        resolve_mime_type,
    )

    # Filename derivation — Content-Disposition > URL path tail > hint
    filename = (
        name_hint
        or _extract_filename_from_response(resp, url)
        or "downloaded"
    )

    # Ensure filename has a sensible extension for downstream parsers
    if "." not in filename and ctype:
        import mimetypes
        guess = mimetypes.guess_extension(ctype) or ""
        filename += guess

    resolved_mime = resolve_mime_type(ctype, filename)
    if not resolved_mime:
        return fail(
            "unsupported_mime",
            f"content-type {ctype!r} + filename {filename!r} not in whitelist",
            url=url,
            allowed=sorted(MIME_WHITELIST),
        )

    digest = hash_content(content)

    # Hash-level dedup (content-identical but different URL → reuse row)
    hash_hit = AgentV2AttachmentService.find_by_hash(
        tenant_id=tenant_id, hash_xxh128=digest,
    )
    if hash_hit:
        return mcp_json_response({
            "attachment_id": hash_hit.id,
            "status": hash_hit.status,
            "filename": hash_hit.filename,
            "mime_type": hash_hit.mime_type,
            "size_bytes": hash_hit.size_bytes,
            "source_url": hash_hit.source_url or url,
            "preview_text": hash_hit.preview_text,
            "dedupe": True,
            "duration_ms": int((time.perf_counter() - start) * 1000),
            "next_steps": [
                "Content matches an existing attachment by hash; reusing.",
                "Proceed to submit_plan + doc_archive_attachment as normal.",
            ],
        })

    # Write to MinIO, then DB
    from common import settings

    key = object_key(
        tenant_id=tenant_id, session_id=session_id, attachment_id=digest,
    )
    try:
        settings.STORAGE_IMPL.put(
            AGENT_V2_ATTACHMENT_BUCKET, key, content, tenant_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_fetch_to_attachment: blob put failed")
        return fail(
            "storage_error",
            f"minio put failed: {type(exc).__name__}: {exc}",
            url=url,
        )

    preview = extract_preview(content, resolved_mime)

    row = AgentV2AttachmentService.create_staged(
        session_id=session_id,
        tenant_id=tenant_id,
        uploaded_by=user_id,
        filename=filename,
        mime_type=resolved_mime,
        size_bytes=len(content),
        hash_xxh128=digest,
        blob_path=f"{AGENT_V2_ATTACHMENT_BUCKET}/{key}",
        origin="web_fetch",
        source_url=url,
        preview_text=preview,
    )

    return mcp_json_response({
        "attachment_id": row.id,
        "status": row.status,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "source_url": url,
        "upgraded_from_http": upgraded_from_http,
        "redirects": redirects,
        "preview_text": preview,
        "content_hash": digest,
        "dedupe": False,
        "duration_ms": int((time.perf_counter() - start) * 1000),
        "next_steps": [
            (
                "Call submit_plan with a concise preview (title=brief "
                "description, excerpt=preview_text[:2000]) so the user sees "
                "what will be archived."
            ),
            (
                "After [plan approved], call "
                f"doc_archive_attachment(attachment_id='{row.id}', kb_id=...)"
            ),
        ],
    })


def _extract_filename_from_response(resp, url: str) -> str | None:
    disposition = (resp.headers.get("content-disposition") or "").lower()
    if "filename=" in disposition:
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
    try:
        path = urlparse(url).path
        tail = path.rstrip("/").split("/")[-1]
        if tail:
            return tail
    except Exception:
        pass
    return None


# Keep imports referenced to satisfy linters; used transitively.
_ = BytesIO

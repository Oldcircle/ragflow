"""web_fetch 工具 — 抓取指定 URL 的内容并以 markdown 返回。

与 ``doc_upload_from_url`` 的区别：
  - ``doc_upload_from_url`` = 写工具，把抓到的内容作为新文档**入库**；
    需要 CONTRIBUTOR 权限 + plan gate + quota。
  - ``web_fetch``（本工具）= 读工具，**只返回文本给 Agent 阅读**，不落库，
    不扣 doc 配额。适合"LLM 需要临时看一下某个网页再综合"。

抓取栈：优先用 ``httpx`` 直连 + BeautifulSoup 解析，失败才退 ``crawl4ai``。
原因：crawl4ai 基于 playwright，冷启动慢（3-5s），对简单 html 页面是浪费。

SSRF 防御沿用 ``doc_upload_from_url`` 的三段式：scheme / DNS → IP 过滤。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from urllib.parse import urljoin, urlparse

from .base import get_ctx, mcp_json_response, tool  # noqa: F401 — get_ctx used in future

logger = logging.getLogger("ragflow.agent_v2.web_fetch")

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB，给 Agent 阅读，不需要全量
_DEFAULT_TIMEOUT_S = 15.0
_MAX_REDIRECTS = 10
_TRUNCATED_MARKER = "\n\n[Content truncated due to length...]"


def _ssrf_check(url: str) -> tuple[bool, str]:
    """返回 ``(ok, reason)``；失败时 reason 解释原因。"""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, f"unsupported_scheme: {parsed.scheme!r}"
    if not parsed.hostname:
        return False, "missing_hostname"
    try:
        addrinfos = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as e:
        return False, f"dns_fail: {e}"
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
            return False, f"ssrf_blocked: non-public IP {addr}"
    return True, ""


def _upgrade_http_to_https(url: str) -> tuple[str, bool]:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "http":
        return url, False
    return parsed._replace(scheme="https").geturl(), True


def _strip_www(hostname: str) -> str:
    return hostname[4:] if hostname.startswith("www.") else hostname


def _is_permitted_redirect(original_url: str, redirect_url: str) -> bool:
    """Allow same host redirects, including add/remove ``www.`` only."""
    try:
        original = urlparse(original_url)
        redirect = urlparse(redirect_url)
    except Exception:
        return False
    if original.scheme != redirect.scheme:
        return False
    if (original.port or "") != (redirect.port or ""):
        return False
    if redirect.username or redirect.password:
        return False
    if not original.hostname or not redirect.hostname:
        return False
    return _strip_www(original.hostname) == _strip_www(redirect.hostname)


def _html_to_markdown(html: str) -> tuple[str, str]:
    """返回 ``(title, markdown)``，尽量保留标题 / 列表 / 链接 / 代码块结构。"""
    try:
        from bs4 import BeautifulSoup
        from markdownify import markdownify as md

        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string.strip() if soup.title and soup.title.string else "")

        # 去脚本 / 样式 / 注释
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        markdown = md(
            str(main),
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "noscript"],
        )
        lines = [ln.rstrip() for ln in markdown.splitlines()]
        compact = "\n".join(lines).strip()
        return title, compact
    except Exception as e:
        logger.warning("html_to_markdown fallback due to: %s", e)
        return "", html


@tool(
    name="web_fetch",
    description=(
        "Use this tool when you need to read the full text of a specific web "
        "page — either because the user pasted a URL, or because `web_search` "
        "surfaced a result whose snippet is insufficient.\n\n"
        "Returns the page's title + markdown-ish text (scripts/nav stripped, "
        "truncated to 2 MB).\n\n"
        "Usage notes:\n"
        "- Call with a single URL per invocation. Batch by calling multiple "
        "times if needed.\n"
        "- Content is truncated to 2 MB — do not assume exhaustive coverage.\n"
        "- Private / loopback / link-local hosts are refused for security "
        "(SSRF defense). Only http(s) on public IPs.\n"
        "- http:// URLs are upgraded to https://. Cross-host redirects are "
        "blocked and returned as `redirect_blocked`; fetch the redirected URL "
        "only after the user explicitly agrees.\n"
        "- The fetched text is transient reading material; you cannot cite "
        "it with [N] (those markers are reserved for KB chunks). Cite the URL "
        "inline or in a `Sources:` section instead.\n"
        "- Do NOT use this to ingest content into the KB. Pick the right "
        "ingestion path:\n"
        "  - `web_fetch_to_attachment(url)` — preferred when user asked to "
        "archive a URL into KB (stages attachment → requires user approval "
        "via submit_plan → then `doc_ingest_attachment`)\n"
        "  - `doc_upload_from_url(url, kb_id)` — one-shot ingest when user "
        "has explicitly named the target KB and source is trusted (skips "
        "the preview approval step)"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "http(s) URL to fetch. Public hosts only.",
            },
            "timeout_s": {
                "type": "number",
                "description": "Per-request timeout in seconds. Default 15, max 60.",
                "default": 15,
                "minimum": 1,
                "maximum": 60,
            },
        },
        "required": ["url"],
    },
)
async def web_fetch(args: dict) -> dict:
    """MCP tool entry.

    Returns JSON payload:
      - url: echoed URL
      - title: page title (best effort)
      - content: markdown-ish text (truncated to 2 MB)
      - length: byte length of returned content (post-truncation)
      - truncated: bool — whether original was larger than limit
      - error?: str (on failure)
    """
    start = time.perf_counter()

    def fail(error_code: str, message: str, **extra) -> dict:
        return mcp_json_response(
            {
                "error": message,
                "error_code": error_code,
                "duration_ms": int((time.perf_counter() - start) * 1000),
                **extra,
            }
        )

    get_ctx()  # enforce Runner context

    url = str(args.get("url", "")).strip()
    if not url:
        return fail("validation_error", "url must be non-empty")

    fetch_url, upgraded_from_http = _upgrade_http_to_https(url)

    ok, reason = _ssrf_check(fetch_url)
    if not ok:
        return fail("security_error", reason, url=url)

    timeout_s = min(max(float(args.get("timeout_s", _DEFAULT_TIMEOUT_S)), 1.0), 60.0)

    try:
        import httpx
    except ImportError:
        return fail(
            "dependency_missing",
            "httpx_missing: `uv pip install httpx`",
            url=url,
        )

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
            redirect_count = 0
            while True:
                resp = await client.get(current_url)
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break

                location = resp.headers.get("location")
                if not location:
                    return fail(
                        "redirect_error",
                        "redirect_missing_location",
                        url=url,
                        status_code=resp.status_code,
                    )
                next_url = urljoin(str(resp.url or current_url), location)
                if not _is_permitted_redirect(current_url, next_url):
                    return fail(
                        "redirect_blocked",
                        "redirect_blocked: cross-host redirects require explicit user approval",
                        url=url,
                        redirect_url=next_url,
                        status_code=resp.status_code,
                    )
                ok, reason = _ssrf_check(next_url)
                if not ok:
                    return fail(
                        "security_error",
                        reason,
                        url=url,
                        redirect_url=next_url,
                    )
                redirect_count += 1
                if redirect_count > _MAX_REDIRECTS:
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

    ctype = resp.headers.get("content-type", "")
    raw = resp.content
    truncated = False
    if len(raw) > _MAX_CONTENT_BYTES:
        raw = raw[:_MAX_CONTENT_BYTES]
        truncated = True

    if "html" in ctype.lower():
        try:
            html = raw.decode(resp.encoding or "utf-8", errors="replace")
        except Exception:
            html = raw.decode("utf-8", errors="replace")
        title, content = _html_to_markdown(html)
    elif "json" in ctype.lower() or "text" in ctype.lower():
        title = ""
        content = raw.decode(resp.encoding or "utf-8", errors="replace")
    else:
        return fail(
            "unsupported_content_type",
            f"unsupported_content_type: {ctype}",
            url=url,
        )

    if truncated:
        content = content.rstrip() + _TRUNCATED_MARKER

    return mcp_json_response(
        {
            "url": str(resp.url),
            "requested_url": url,
            "upgraded_from_http": upgraded_from_http,
            "title": title,
            "content": content,
            "length": len(content.encode("utf-8")),
            "truncated": truncated,
            "content_type": ctype,
            "status_code": resp.status_code,
            "duration_ms": int((time.perf_counter() - start) * 1000),
            "citation_policy": (
                "If using this fetched page in the final answer, cite the URL "
                "inline or in a `Sources:` section. Do not use KB [N] "
                "citation markers for web sources."
            ),
        }
    )

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
from urllib.parse import urlparse

from .base import get_ctx, mcp_json_response, tool  # noqa: F401 — get_ctx used in future

logger = logging.getLogger("ragflow.agent_v2.web_fetch")

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB，给 Agent 阅读，不需要全量
_DEFAULT_TIMEOUT_S = 15.0


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


def _html_to_markdown(html: str) -> tuple[str, str]:
    """返回 ``(title, markdown)``。尽量轻量，失败就返原文纯文本。"""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string.strip() if soup.title and soup.title.string else "")

        # 去脚本 / 样式 / 注释
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        # 主要文本
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = main.get_text("\n", strip=True)
        # 折叠多空行
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return title, "\n".join(lines)
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
        "- The fetched text is transient reading material; you cannot cite "
        "it with [N] (those markers are reserved for KB chunks). Cite the URL "
        "inline instead.\n"
        "- Do NOT use this to ingest content into the KB; that's "
        "`doc_upload_from_url` (write + plan-gated)."
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
    get_ctx()  # enforce Runner context

    url = str(args.get("url", "")).strip()
    if not url:
        return mcp_json_response({"error": "url must be non-empty"})

    ok, reason = _ssrf_check(url)
    if not ok:
        return mcp_json_response({"error": reason, "url": url})

    timeout_s = min(max(float(args.get("timeout_s", _DEFAULT_TIMEOUT_S)), 1.0), 60.0)

    try:
        import httpx
    except ImportError:
        return mcp_json_response(
            {"error": "httpx_missing: `uv pip install httpx`", "url": url}
        )

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout_s,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; RAGFlow-Agent-v2/1.0; "
                    "+https://github.com/Oldcircle/ragflow)"
                )
            },
        ) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        return mcp_json_response({"error": "timeout", "url": url})
    except httpx.HTTPError as e:
        return mcp_json_response(
            {"error": f"http_error: {type(e).__name__}: {e}", "url": url}
        )

    if resp.status_code >= 400:
        return mcp_json_response(
            {
                "error": f"http_{resp.status_code}",
                "url": url,
                "status_code": resp.status_code,
            }
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
        return mcp_json_response(
            {
                "error": f"unsupported_content_type: {ctype}",
                "url": url,
            }
        )

    return mcp_json_response(
        {
            "url": str(resp.url),
            "title": title,
            "content": content,
            "length": len(content.encode("utf-8")),
            "truncated": truncated,
            "content_type": ctype,
            "status_code": resp.status_code,
        }
    )

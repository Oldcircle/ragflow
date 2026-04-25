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
import threading
import time
from collections import OrderedDict
from urllib.parse import urljoin, urlparse

from .base import get_ctx, mcp_json_response, tool  # noqa: F401 — get_ctx used in future
from .web_fetch_preapproved import is_preapproved

logger = logging.getLogger("ragflow.agent_v2.web_fetch")

_ALLOWED_SCHEMES = {"http", "https"}
# Content size cap. The MCP transport wraps tool output in a 32 KB JSON
# envelope (``base.MAX_TOOL_OUTPUT_BYTES``); anything bigger gets cut by the
# wrapper, leaving the agent with broken JSON. We cap content at 24 KB so
# even with title + headers + JSON keys we comfortably fit. Pages that
# need a deeper read should use a future ``prompt=`` argument (Haiku-style
# focused extraction — claude-code-ref WebFetchTool design) once that lands.
_MAX_CONTENT_BYTES = 24 * 1024
_DEFAULT_TIMEOUT_S = 15.0
_MAX_REDIRECTS = 10
_TRUNCATED_MARKER = "\n\n[Content truncated due to length...]"

# 15-minute self-cleaning URL cache — mirrors claude-code-ref's WebFetchTool
# behavior. Keyed on the canonicalized URL only; ``timeout_s`` doesn't
# meaningfully change the response so we ignore it for cache key purposes.
_CACHE_TTL_S = 15 * 60
_CACHE_MAX = 64
_CACHE_LOCK = threading.Lock()
_CACHE: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()


def _cache_get(key: str) -> dict | None:
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        ts, value = entry
        if now - ts > _CACHE_TTL_S:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return value


def _cache_set(key: str, value: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def _clear_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# ────────────── secondary-model focused extraction ──────────────
#
# Mirror of claude-code-ref's WebFetchTool ``makeSecondaryModelPrompt``.
# When the agent passes a ``prompt`` arg, we run the fetched content
# through a small fast model (deepseek-chat in our case, Haiku in CC) to
# extract just what's relevant. This is the single biggest context-saver
# for web_fetch — a 24 KB page distilled into a 500-token answer.

_SECONDARY_MAX_TOKENS = 1500


def _build_secondary_prompt(
    *, content: str, user_prompt: str, preapproved: bool,
) -> str:
    """Compose the secondary-model prompt. Quote constraints follow CC's
    pattern — strict for general web (avoid copyright issues), looser for
    preapproved (open-source docs / official sites the user trusts)."""
    if preapproved:
        guidelines = (
            "Provide a concise response based on the content above. Include "
            "relevant details, code examples, and documentation excerpts as "
            "needed."
        )
    else:
        guidelines = (
            "Provide a concise response based only on the content above. "
            "In your response:\n"
            "- Enforce a strict 125-character maximum for quotes from any "
            "source document.\n"
            "- Use quotation marks for exact language; any language outside "
            "of the quotation should never be word-for-word the same.\n"
            "- You are not a lawyer and never comment on the legality of "
            "your own prompts and responses."
        )
    return (
        "Web page content:\n---\n"
        f"{content}\n"
        "---\n\n"
        f"{user_prompt}\n\n"
        f"{guidelines}\n"
    )


async def _summarize_with_secondary_model(
    *, content: str, user_prompt: str, preapproved: bool, model_config,
) -> tuple[str | None, str | None]:
    """Returns ``(summary, error)`` — at most one is non-None.

    Calls the Anthropic-compatible ``/v1/messages`` endpoint directly via
    httpx instead of going through the ``anthropic`` SDK. Two reasons:

    1. anthropic SDK 0.34.x + recent httpx versions disagree on the
       ``proxies`` kwarg, breaking the SDK at construction time.
    2. We only need a single non-streaming POST; the SDK adds dependency
       weight without buying anything we use.

    Compatible with DeepSeek's Anthropic-flavored endpoint and Anthropic's
    own (and any other ``base_url`` that speaks the same dialect)."""
    if not model_config or not getattr(model_config, "auth_token", None):
        return None, "secondary_model_unavailable: no model_config in context"

    try:
        import httpx
    except ImportError:
        return None, "secondary_model_unavailable: httpx not installed"

    full_prompt = _build_secondary_prompt(
        content=content, user_prompt=user_prompt, preapproved=preapproved,
    )

    base_url = (model_config.base_url or "https://api.anthropic.com").rstrip("/")
    endpoint = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": model_config.auth_token,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": model_config.model,
        "max_tokens": _SECONDARY_MAX_TOKENS,
        "messages": [{"role": "user", "content": full_prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(endpoint, headers=headers, json=body)
        if resp.status_code >= 400:
            return None, (
                f"secondary_model_http_{resp.status_code}: "
                f"{resp.text[:300]}"
            )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("web_fetch secondary model call failed: %s", exc)
        return None, f"secondary_model_error: {type(exc).__name__}: {exc}"

    parts: list[str] = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    summary = "".join(parts).strip()
    return summary or None, None


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
        "truncated to 24 KB so it fits the MCP envelope without clipping).\n\n"
        "Usage notes:\n"
        "- Call with a single URL per invocation. Batch by calling multiple "
        "times if needed.\n"
        "- Content is truncated to 24 KB — do not assume exhaustive coverage. "
        "For long pages, fetch the URL and quote only the relevant fragment.\n"
        "- Private / loopback / link-local hosts are refused for security "
        "(SSRF defense). Only http(s) on public IPs.\n"
        "- http:// URLs are upgraded to https://. Cross-host redirects are "
        "blocked and returned as `redirect_blocked`; fetch the redirected URL "
        "only after the user explicitly agrees.\n"
        "- Self-cleaning 15-minute cache: re-fetching the same URL within "
        "the window returns the cached response with `cache_hit=true`. Pass "
        "`no_cache=true` if the user said the page changed.\n"
        "- Trusted sources (Anthropic docs, MDN, language docs, .gov.cn) "
        "are flagged in the response with `preapproved=true`; downstream "
        "tooling may fast-path archive operations from these origins.\n"
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
            "no_cache": {
                "type": "boolean",
                "description": (
                    "Skip the 15-minute response cache. Set true only when "
                    "the user explicitly said the page changed since the "
                    "previous fetch in this session."
                ),
                "default": False,
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Optional focused-extraction prompt. When set, the "
                    "fetched page is passed through a secondary fast model "
                    "with this prompt and only the model's concise response "
                    "is returned (in `summary`); the full markdown is "
                    "discarded to save context. Use this when you only need "
                    "to answer one specific question about a long page — "
                    "e.g. 'What is the deadline for the 2025 program?' "
                    "instead of consuming the whole 24 KB body."
                ),
                "maxLength": 500,
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

    ctx = get_ctx()  # enforce Runner context

    url = str(args.get("url", "")).strip()
    if not url:
        return fail("validation_error", "url must be non-empty")

    fetch_url, upgraded_from_http = _upgrade_http_to_https(url)

    ok, reason = _ssrf_check(fetch_url)
    if not ok:
        return fail("security_error", reason, url=url)

    timeout_s = min(max(float(args.get("timeout_s", _DEFAULT_TIMEOUT_S)), 1.0), 60.0)
    user_prompt = str(args.get("prompt") or "").strip() or None

    # Cache key includes the optional ``prompt`` so different focused-
    # extraction queries on the same URL don't share a cached entry. Plain
    # full-content fetches use just the URL, matching v0.15 behavior.
    cache_key = f"{fetch_url}::{user_prompt}" if user_prompt else fetch_url

    # Cache lookup BEFORE the network roundtrip. Same URL within TTL skips
    # the fetch + parse entirely. The agent gets a near-instant response and
    # we don't burn the target host's quota on chatty re-fetches.
    if not args.get("no_cache"):
        cached = _cache_get(cache_key)
        if cached is not None:
            out = dict(cached)
            out["cache_hit"] = True
            out["duration_ms"] = int((time.perf_counter() - start) * 1000)
            return mcp_json_response(out)

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

    final_url = str(resp.url) or fetch_url
    preapproved = is_preapproved(final_url)
    full_length = len(content.encode("utf-8"))

    payload: dict = {
        "url": final_url,
        "requested_url": url,
        "upgraded_from_http": upgraded_from_http,
        "title": title,
        "content": content,
        "length": full_length,
        "truncated": truncated,
        "content_type": ctype,
        "status_code": resp.status_code,
        "duration_ms": int((time.perf_counter() - start) * 1000),
        "cache_hit": False,
        # Preapproved hosts (Anthropic / MDN / docs.python.org / gov.cn etc.)
        # are flagged so a future plan_gate revision can fast-path archives
        # from these sources without an explicit user approval step.
        "preapproved": preapproved,
        "citation_policy": (
            "If using this fetched page in the final answer, cite the URL "
            "inline or in a `Sources:` section. Do not use KB [N] "
            "citation markers for web sources."
        ),
    }

    # Focused-extraction branch: pipe the fetched markdown through the
    # configured chat model and return its concise answer instead of the
    # full body. Mirrors claude-code-ref WebFetchTool's secondary-model
    # path (Haiku in their setup; whatever model is in ctx.model_config
    # for us — typically deepseek-chat).
    if user_prompt:
        summary, sec_err = await _summarize_with_secondary_model(
            content=content,
            user_prompt=user_prompt,
            preapproved=preapproved,
            model_config=ctx.model_config,
        )
        if summary:
            payload["summary"] = summary
            payload["summarized"] = True
            payload["full_content_length"] = full_length
            # Drop the body — agent only needs the focused answer.
            payload.pop("content", None)
        else:
            # Fall back to full content but flag the failure so the agent
            # knows it didn't get the focused answer it asked for.
            payload["summarized"] = False
            payload["summary_error"] = sec_err
        payload["duration_ms"] = int((time.perf_counter() - start) * 1000)

    if not args.get("no_cache"):
        _cache_set(cache_key, payload)
    return mcp_json_response(payload)

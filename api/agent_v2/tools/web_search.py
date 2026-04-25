"""web_search 工具 — 多 provider 适配 + LRU 缓存（Phase 2.7 v0.14）。

设计直接对齐 ``claude-code-ref/packages/builtin-tools/src/tools/WebSearchTool``：

- **Adapter 模式**：``_TavilyAdapter`` / ``_DuckDuckGoAdapter`` 各自实现搜索，
  对应 Claude Code 的 ``apiAdapter / bingAdapter / braveAdapter``。Tavily 优先
  （效果最好），无 key 时自动降级到 DuckDuckGo。这是去年我们第一版只接 Tavily
  导致开发机必须配 key 才能跑的反向修复。

- **零 key 兜底**：DuckDuckGo HTML 搜索不需要 API key，单机调用 OK，但有限速，
  不适合生产高并发——所以仅作开发 / demo 场景的兜底。生产建议配 Tavily。

- **异步化**：两个 adapter 的底层 SDK 都是同步调用（阻塞事件循环），统一走
  ``asyncio.to_thread`` 卸载。Claude Code 用 ``shouldDefer: true`` 走 deferred
  execution，本质等价。

- **15 分钟 LRU**：同 query + provider + domain 过滤组合命中 cache 直接返。
  追问"刚才搜的那个再展开"不重抓 Tavily（省 1 credit/次）。

- **输出 cap 32 KB**：超过截断（claude-code 是 100 KB，但我们走 MCP 协议
  ``MAX_TOOL_OUTPUT_BYTES = 32 KB``，对齐内部限制）。

- **当前年月注入到 description**：和 Claude Code 提示词里强制 LLM 用当前年份
  搜索同样的目的——避免 cutoff 之后模型幻觉性地搜历史年份。

Provider 解析优先级（最终选定哪个 adapter）：
  1. 参数 ``provider`` 显式（``tavily`` / ``duckduckgo`` / ``auto``）
  2. 环境变量 ``AGENT_V2_WEB_SEARCH_PROVIDER`` 同上
  3. 兜底 ``auto`` —— Tavily key 在则 Tavily，否则 DuckDuckGo
"""

from __future__ import annotations

import abc
import asyncio
import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from .base import get_ctx, mcp_json_response, tool

logger = logging.getLogger("ragflow.agent_v2.web_search")


# ─────────────────────────── Adapter base + impls ───────────────────────────


class _SearchAdapter(abc.ABC):
    """Common surface for all search providers — mirrors
    ``WebSearchAdapter`` interface in claude-code-ref/.../adapters/types.ts."""

    name: str = "abstract"
    requires_key: bool = False

    @abc.abstractmethod
    def search(
        self,
        query: str,
        *,
        max_results: int,
        allowed_domains: list[str],
        blocked_domains: list[str],
        topic: str,
        search_depth: str,
    ) -> list[dict[str, Any]]:
        """Sync; result rows shaped like ``{"title", "url", "snippet",
        "score"?, "published_date"?}``. The async wrapper around this lives
        in :func:`web_search`."""


class _TavilyAdapter(_SearchAdapter):
    name = "tavily"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def search(
        self,
        query: str,
        *,
        max_results: int,
        allowed_domains: list[str],
        blocked_domains: list[str],
        topic: str,
        search_depth: str,
    ) -> list[dict[str, Any]]:
        from tavily import TavilyClient

        client = TavilyClient(api_key=self._api_key)
        payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "topic": topic,
            "search_depth": search_depth,
            "include_images": False,
            "include_raw_content": False,
            "include_answer": False,
        }
        if allowed_domains:
            payload["include_domains"] = allowed_domains
        if blocked_domains:
            payload["exclude_domains"] = blocked_domains
        res = client.search(**payload)
        out: list[dict[str, Any]] = []
        for r in (res.get("results") if isinstance(res, dict) else []) or []:
            out.append(
                {
                    "title": r.get("title", "") or "",
                    "url": r.get("url", "") or "",
                    "snippet": r.get("content", "") or "",
                    "score": r.get("score"),
                    "published_date": r.get("published_date"),
                }
            )
        return out


class _DuckDuckGoAdapter(_SearchAdapter):
    """Zero-key fallback. Uses the ``duckduckgo_search`` package which scrapes
    the DDG HTML / JSON endpoint. Limit: ~1 query/sec recommended, can fail
    intermittently with rate-limit / parse errors. Good for dev / demo, NOT
    production multi-tenant traffic."""

    name = "duckduckgo"
    requires_key = False

    def search(
        self,
        query: str,
        *,
        max_results: int,
        allowed_domains: list[str],
        blocked_domains: list[str],
        topic: str,
        search_depth: str,
    ) -> list[dict[str, Any]]:
        from duckduckgo_search import DDGS

        # ``topic`` and ``search_depth`` are Tavily-specific; map sensibly.
        # DDG ``news()`` is a separate endpoint; if topic=news, route there.
        rows: list[dict[str, Any]]
        with DDGS() as ddgs:
            if topic == "news":
                raw = list(ddgs.news(query, max_results=max_results)) or []
            else:
                raw = list(ddgs.text(query, max_results=max_results)) or []
        rows = []
        for r in raw:
            rows.append(
                {
                    "title": r.get("title") or "",
                    # DDG 'text' uses 'href'; 'news' uses 'url'.
                    "url": r.get("url") or r.get("href") or "",
                    "snippet": r.get("body") or r.get("excerpt") or "",
                    "score": None,
                    "published_date": r.get("date"),
                }
            )

        # DDG has no native domain filter — apply post-hoc.
        if allowed_domains:
            rows = [r for r in rows if _domain_matches(r["url"], allowed_domains)]
        if blocked_domains:
            rows = [r for r in rows if not _domain_matches(r["url"], blocked_domains)]
        return rows[:max_results]


def _domain_matches(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    for d in domains:
        d = d.lower()
        if host == d or host.endswith("." + d):
            return True
    return False


def _normalize_domain(d: str) -> str:
    """``https://www.gov.cn/`` → ``www.gov.cn``. Strips scheme + path + casing
    so adapter-side comparison is uniform."""
    d = (d or "").strip().lower()
    if not d:
        return ""
    if "://" in d:
        d = urlparse(d).hostname or ""
    return d.rstrip("/")


# ─────────────────────────── Key resolution ────────────────────────────────


def _resolve_tavily_key(tenant_id: str) -> str | None:
    """先查 TenantLLM，再回落到环境变量 ``TAVILY_API_KEY``。"""
    try:
        from api.db.db_models import TenantLLM

        row = (
            TenantLLM.select()
            .where(
                (TenantLLM.tenant_id == tenant_id)
                & (TenantLLM.llm_factory == "Tavily")
                & (TenantLLM.api_key.is_null(False))
            )
            .first()
        )
        if row and row.api_key:
            return row.api_key
    except Exception as e:
        logger.debug("resolve tavily key via TenantLLM failed: %s", e)
    return os.environ.get("TAVILY_API_KEY") or None


def _select_adapter(
    *, provider_hint: str | None, tenant_id: str
) -> tuple[_SearchAdapter | None, str | None]:
    """Choose backend. Returns ``(adapter, error_message)``. When provider
    selection fails (e.g. ``provider='tavily'`` but no key), ``adapter`` is
    None and ``error_message`` carries the reason."""
    explicit = (provider_hint or os.environ.get("AGENT_V2_WEB_SEARCH_PROVIDER") or "auto").lower().strip()

    if explicit == "tavily":
        key = _resolve_tavily_key(tenant_id)
        if not key:
            return None, (
                "no_tavily_api_key: provider='tavily' explicitly requested "
                "but no key found. Set TAVILY_API_KEY env var, configure "
                "'Tavily' in Tenant LLM settings, or pass provider='auto' / "
                "'duckduckgo' to use the zero-key fallback."
            )
        return _TavilyAdapter(key), None

    if explicit == "duckduckgo":
        return _DuckDuckGoAdapter(), None

    # ``auto`` (default): Tavily if key, else DuckDuckGo.
    key = _resolve_tavily_key(tenant_id)
    if key:
        return _TavilyAdapter(key), None
    return _DuckDuckGoAdapter(), None


# ─────────────────────────── LRU cache ─────────────────────────────────────

_CACHE_TTL_S = 15 * 60
_CACHE_MAX = 128
_CACHE_LOCK = threading.Lock()
_CACHE: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()


def _cache_key(
    *, provider: str, query: str, max_results: int, topic: str,
    search_depth: str, allowed_domains: list[str], blocked_domains: list[str],
) -> tuple:
    return (
        provider,
        query,
        max_results,
        topic,
        search_depth,
        tuple(sorted(allowed_domains)),
        tuple(sorted(blocked_domains)),
    )


def _cache_get(key: tuple) -> dict | None:
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


def _cache_set(key: tuple, value: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


# ─────────────────────────── Tool definition ───────────────────────────────


_CURRENT_MONTH_YEAR = datetime.now().strftime("%B %Y")


@tool(
    name="web_search",
    description=(
        "Use this tool when the user's question needs information from the "
        "open web (current events, external references, anything outside "
        "the configured knowledge bases).\n\n"
        "Returns a ranked list of web results (title, URL, snippet). Does "
        "not read full page content — follow up with `web_fetch` if you need "
        "to cite a specific passage.\n\n"
        "Provider auto-selection:\n"
        "- Tavily when an API key is configured (recommended; best ranking)\n"
        "- DuckDuckGo as zero-key fallback (rate-limited; dev / demo only)\n"
        "Override via the optional `provider` arg or env var "
        "`AGENT_V2_WEB_SEARCH_PROVIDER`.\n\n"
        "Usage notes:\n"
        "- Prefer `rag_retrieve` FIRST when the question plausibly falls "
        "inside the knowledge base; only escalate to the web if KB returns "
        "nothing relevant.\n"
        "- Use short keyword queries (3-8 terms). Full-sentence queries "
        "waste search budget.\n"
        "- Use `allowed_domains` when the user names a site (e.g. "
        "['gov.cn']), `blocked_domains` to exclude known-noisy sources. "
        "These are mutually exclusive — set at most one.\n"
        "- Do NOT use this to fetch a URL the user provided — that's what "
        "`web_fetch` is for.\n"
        "- Web results are NOT citable with the [N] markers reserved for "
        "KB chunks.\n"
        f"- Today is {_CURRENT_MONTH_YEAR}. When searching for recent "
        "information, documentation, or current events you MUST use the "
        "current year in the query, not last year.\n\n"
        "CRITICAL: when you use web_search results in the final answer, you "
        "MUST include a `Sources:` section at the end with markdown "
        "hyperlinks `[Title](URL)` for every web URL you relied on. This "
        "is mandatory — never skip sources. Never mix web URLs into KB "
        "[N] citations."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Keyword-style search query, 3-8 terms. Include language "
                    "markers if you need results in a specific language "
                    "(e.g. '深圳 保障房 新闻' vs 'Shenzhen affordable "
                    "housing news')."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (1-20). Default 6.",
                "default": 6,
                "minimum": 1,
                "maximum": 20,
            },
            "topic": {
                "type": "string",
                "description": (
                    "'general' for broad search, 'news' for real-time news. "
                    "DuckDuckGo maps news → news endpoint; Tavily maps "
                    "directly."
                ),
                "enum": ["general", "news"],
                "default": "general",
            },
            "search_depth": {
                "type": "string",
                "description": (
                    "'basic' = fast (1 Tavily credit); 'advanced' = deeper "
                    "extraction (2 credits). DuckDuckGo ignores this."
                ),
                "enum": ["basic", "advanced"],
                "default": "basic",
            },
            "allowed_domains": {
                "type": "array",
                "description": (
                    "Only return results from these domains (e.g. "
                    "['gov.cn', 'sz.gov.cn']). Mutually exclusive with "
                    "blocked_domains."
                ),
                "items": {"type": "string"},
                "default": [],
            },
            "blocked_domains": {
                "type": "array",
                "description": (
                    "Exclude results from these domains. Mutually exclusive "
                    "with allowed_domains."
                ),
                "items": {"type": "string"},
                "default": [],
            },
            "provider": {
                "type": "string",
                "description": (
                    "Force a specific search backend. 'auto' picks Tavily "
                    "when key configured, else DuckDuckGo."
                ),
                "enum": ["auto", "tavily", "duckduckgo"],
                "default": "auto",
            },
        },
        "required": ["query"],
    },
)
async def web_search(args: dict) -> dict:
    """MCP tool entry. See module docstring for design rationale."""
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

    ctx = get_ctx()
    query = str(args.get("query", "")).strip()
    if not query:
        return fail("validation_error", "query must be non-empty")
    if len(query) > 400:
        return fail(
            "validation_error",
            "query too long (>400 chars); split into sub-queries",
        )

    max_results = min(max(int(args.get("max_results", 6)), 1), 20)
    topic = args.get("topic") or "general"
    search_depth = args.get("search_depth") or "basic"
    provider_hint = args.get("provider")

    allowed_domains = [
        _normalize_domain(d) for d in (args.get("allowed_domains") or [])
    ]
    allowed_domains = [d for d in allowed_domains if d]
    blocked_domains = [
        _normalize_domain(d) for d in (args.get("blocked_domains") or [])
    ]
    blocked_domains = [d for d in blocked_domains if d]
    if allowed_domains and blocked_domains:
        return fail(
            "domain_filter_conflict",
            "allowed_domains and blocked_domains cannot both be set",
            query=query,
        )

    adapter, err = _select_adapter(
        provider_hint=provider_hint, tenant_id=ctx.tenant_id,
    )
    if adapter is None:
        # Preserve historical error_code so callers / tests / dashboards
        # that look for ``no_tavily_api_key`` still see it.
        return fail("no_tavily_api_key", err or "no_provider_available", query=query)

    cache_key = _cache_key(
        provider=adapter.name, query=query, max_results=max_results,
        topic=topic, search_depth=search_depth,
        allowed_domains=allowed_domains, blocked_domains=blocked_domains,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        # Re-stamp duration_ms so callers see "this hop was free", but keep
        # the original payload; cached.cache_hit lets the agent know.
        out = dict(cached)
        out["duration_ms"] = int((time.perf_counter() - start) * 1000)
        out["cache_hit"] = True
        return mcp_json_response(out)

    try:
        # All adapters are sync; offload to thread to keep the event loop
        # responsive (Tavily SDK uses requests; DDG package uses httpx
        # internally but exposes sync API).
        results = await asyncio.to_thread(
            adapter.search,
            query,
            max_results=max_results,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            topic=topic,
            search_depth=search_depth,
        )
    except ImportError as e:
        return fail(
            "dependency_missing",
            f"adapter dependency missing: {e}; "
            "`uv pip install tavily-python duckduckgo-search`",
            query=query, provider=adapter.name,
        )
    except Exception as e:
        logger.warning("web_search %s error: %s", adapter.name, e)
        return fail(
            "provider_error",
            f"{adapter.name}_error: {type(e).__name__}: {e}",
            query=query, provider=adapter.name,
        )

    payload = {
        "query": query,
        "provider": adapter.name,
        "duration_ms": int((time.perf_counter() - start) * 1000),
        "total": len(results),
        "results": results,
        "cache_hit": False,
        "citation_policy": (
            "If using these web results in the final answer, include a "
            "`Sources:` section with markdown links. Do not use KB [N] "
            "citation markers for web sources."
        ),
    }
    _cache_set(cache_key, payload)
    return mcp_json_response(payload)


def _clear_cache_for_tests() -> None:
    """Test helper — pytest fixtures call this between cases so cached
    results from one test don't leak into another."""
    with _CACHE_LOCK:
        _CACHE.clear()

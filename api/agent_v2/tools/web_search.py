"""web_search 工具 — 调用 Tavily（默认）或兼容的搜索 provider。

为什么不复用 ``agent/tools/tavily.py``：上游那份是给**旧 Dialog 画布**的
``ToolBase`` 子类，带 ``check_if_canceled / set_output`` 的组件生命周期，
和 Agent v2 的 MCP 工具模型不兼容。我们直接用 ``rag.utils.tavily_conn.Tavily``
这个薄封装或者 ``tavily-python`` 原生 SDK，省去适配层。

Schema / 描述风格**参照** ``claude-code-ref/packages/builtin-tools/src/tools/
WebSearchTool``：``query`` / ``allowed_domains`` / ``blocked_domains``。

Provider 解析优先级：
  1. 参数 ``provider`` 显式指定（当前只认 ``tavily``，预留扩展位）
  2. session 所属 tenant 的 TenantLLM 里有 ``Tavily`` factory 配置 → 用其 api_key
  3. 环境变量 ``TAVILY_API_KEY`` 兜底
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .base import get_ctx, mcp_json_response, tool

logger = logging.getLogger("ragflow.agent_v2.web_search")


def _resolve_tavily_key(tenant_id: str) -> str | None:
    """先查 TenantLLM，再回落到环境变量。

    TenantLLM 存 Tavily 的密钥是通过 llm_factory='Tavily' / model_type 约定，
    但 RAGFlow 里实际常见存储形式是 factory='Tavily' + api_key。读失败时
    安静回落到 env var，不抛异常。
    """
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


@tool(
    name="web_search",
    description=(
        "Use this tool when the user's question needs information from the "
        "open web (current events, external references, anything outside "
        "the configured knowledge bases).\n\n"
        "Returns a ranked list of web results (title, URL, snippet, score). "
        "Does not read full page content — follow up with `web_fetch` if you "
        "need to cite a specific passage.\n\n"
        "Usage notes:\n"
        "- Prefer `rag_retrieve` FIRST when the question plausibly falls "
        "inside the knowledge base; only escalate to the web if KB returns "
        "nothing relevant.\n"
        "- Use short keyword queries (3-8 terms). Full-sentence queries "
        "waste search budget.\n"
        "- Use `allowed_domains` when the user names a site (e.g. "
        "gov.cn only), `blocked_domains` to exclude known-noisy sources.\n"
        "- Do NOT use this to fetch a URL the user provided — that's what "
        "`web_fetch` is for.\n"
        "- Web results are NOT citable with the [N] markers reserved for "
        "KB chunks.\n"
        "- CRITICAL: when you use web_search results in the final answer, "
        "include a `Sources:` section with markdown links to every relevant "
        "web URL. Never mix web URLs into KB [N] citations."
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
                    "'general' for broad search, 'news' for real-time news "
                    "(politics, sports, current events)."
                ),
                "enum": ["general", "news"],
                "default": "general",
            },
            "search_depth": {
                "type": "string",
                "description": (
                    "'basic' = fast, 1 credit/query; 'advanced' = deeper "
                    "extraction, 2 credits/query."
                ),
                "enum": ["basic", "advanced"],
                "default": "basic",
            },
            "allowed_domains": {
                "type": "array",
                "description": (
                    "Only return results from these domains (e.g. "
                    "['gov.cn', 'sz.gov.cn']). Leave empty for unrestricted."
                ),
                "items": {"type": "string"},
                "default": [],
            },
            "blocked_domains": {
                "type": "array",
                "description": (
                    "Exclude results from these domains. Use to drop "
                    "known-noisy SEO sites."
                ),
                "items": {"type": "string"},
                "default": [],
            },
        },
        "required": ["query"],
    },
)
async def web_search(args: dict) -> dict:
    """MCP tool entry.

    Returns:
        ``{"content":[{"type":"text","text":"<json>"}]}``
        JSON payload:
          - query: echoed query
          - provider: "tavily"
          - duration_ms: int
          - total: int
          - results: [{title, url, snippet, score, published_date?}, ...]
          - error?: str, error_code?: str (only on failure)
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
    allowed_domains = [
        d.strip() for d in (args.get("allowed_domains") or []) if str(d).strip()
    ]
    blocked_domains = [
        d.strip() for d in (args.get("blocked_domains") or []) if str(d).strip()
    ]
    if allowed_domains and blocked_domains:
        return fail(
            "domain_filter_conflict",
            "allowed_domains and blocked_domains cannot both be set",
            query=query,
        )

    api_key = _resolve_tavily_key(ctx.tenant_id)
    if not api_key:
        return fail(
            "no_tavily_api_key",
            (
                "no_tavily_api_key: set TAVILY_API_KEY env var or "
                "configure 'Tavily' in Tenant LLM settings"
            ),
            query=query,
        )

    try:
        from tavily import TavilyClient
    except ImportError:
        return fail(
            "dependency_missing",
            "tavily_sdk_missing: `uv pip install tavily-python`",
            query=query,
        )

    client = TavilyClient(api_key=api_key)
    try:
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
    except Exception as e:
        logger.warning("web_search Tavily error: %s", e)
        return fail(
            "provider_error",
            f"tavily_error: {type(e).__name__}: {e}",
            query=query,
        )

    raw_results = res.get("results") if isinstance(res, dict) else []
    normalized = []
    for r in raw_results or []:
        normalized.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "score": r.get("score"),
                "published_date": r.get("published_date"),
            }
        )

    return mcp_json_response(
        {
            "query": query,
            "provider": "tavily",
            "duration_ms": int((time.perf_counter() - start) * 1000),
            "total": len(normalized),
            "results": normalized,
            "citation_policy": (
                "If using these web results in the final answer, include a "
                "`Sources:` section with markdown links. Do not use KB [N] "
                "citation markers for web sources."
            ),
        }
    )

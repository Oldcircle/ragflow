"""Phase 2.6 v0.7 — unit tests for web_search + web_fetch.

Happy-path smoke with mocked Tavily client / httpx AsyncClient, plus all the
defensive branches (empty input, SSRF, missing API key, unsupported scheme,
error propagation).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.web_fetch import _ssrf_check, web_fetch
from api.agent_v2.tools.web_search import web_search


# ───────────────── helpers ─────────────────


def _call(tool, args: dict) -> dict:
    return asyncio.run(tool.handler(args))


def _parse(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _ctx(**kw):
    defaults = {"tenant_id": "t1", "kb_ids": ("kb1",), "user_id": "u1"}
    defaults.update(kw)
    return ToolContext(**defaults)


@pytest.fixture
def in_ctx():
    from api.agent_v2.tools.web_fetch import _clear_cache_for_tests as _clear_fetch
    from api.agent_v2.tools.web_search import _clear_cache_for_tests as _clear_search

    _clear_search()
    _clear_fetch()
    token = set_ctx(_ctx())
    yield
    reset_ctx(token)
    _clear_search()
    _clear_fetch()


# ───────────────── web_search ─────────────────


def test_web_search_requires_query(in_ctx):
    out = _parse(_call(web_search, {"query": ""}))
    assert "error" in out
    assert "non-empty" in out["error"]


def test_web_search_rejects_long_query(in_ctx):
    out = _parse(_call(web_search, {"query": "x" * 401}))
    assert "error" in out
    assert "too long" in out["error"]
    assert out["error_code"] == "validation_error"


def test_web_search_rejects_domain_filter_conflict(in_ctx):
    out = _parse(
        _call(
            web_search,
            {
                "query": "深圳 保障房",
                "allowed_domains": ["gov.cn"],
                "blocked_domains": ["spam.example"],
            },
        )
    )
    assert out["error_code"] == "domain_filter_conflict"


def test_web_search_explicit_tavily_without_key_errors(in_ctx, monkeypatch):
    """Forcing provider=tavily without a key returns a clean error so the
    caller can surface the misconfig instead of silently downgrading."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_V2_WEB_SEARCH_PROVIDER", raising=False)
    with patch(
        "api.agent_v2.tools.web_search._resolve_tavily_key",
        return_value=None,
    ):
        out = _parse(
            _call(web_search, {"query": "深圳 保障房", "provider": "tavily"})
        )
    assert "error" in out
    assert out["error_code"] == "no_tavily_api_key"
    assert "no_tavily_api_key" in out["error"]


def test_web_search_auto_falls_back_to_duckduckgo_without_key(
    in_ctx, monkeypatch
):
    """No Tavily key + provider='auto' (default) → DuckDuckGo adapter runs."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_V2_WEB_SEARCH_PROVIDER", raising=False)

    fake_ddgs = MagicMock()
    fake_ddgs.__enter__.return_value = fake_ddgs
    fake_ddgs.__exit__.return_value = False
    fake_ddgs.text.return_value = [
        {"title": "DDG hit 1", "href": "https://example.com/a", "body": "snippet"},
        {"title": "DDG hit 2", "href": "https://other.com/b", "body": "snippet 2"},
    ]
    with patch(
        "api.agent_v2.tools.web_search._resolve_tavily_key",
        return_value=None,
    ), patch("duckduckgo_search.DDGS", return_value=fake_ddgs):
        out = _parse(_call(web_search, {"query": "深圳 保障房"}))

    assert out.get("error") is None
    assert out["provider"] == "duckduckgo"
    assert out["total"] == 2
    assert out["results"][0]["url"] == "https://example.com/a"
    fake_ddgs.text.assert_called_once()


def test_web_search_explicit_duckduckgo(in_ctx, monkeypatch):
    """provider='duckduckgo' bypasses Tavily even when a key would be there."""
    monkeypatch.delenv("AGENT_V2_WEB_SEARCH_PROVIDER", raising=False)
    fake_ddgs = MagicMock()
    fake_ddgs.__enter__.return_value = fake_ddgs
    fake_ddgs.__exit__.return_value = False
    fake_ddgs.text.return_value = [
        {"title": "DDG hit", "href": "https://x.gov.cn/page", "body": "..."},
    ]
    with patch(
        "api.agent_v2.tools.web_search._resolve_tavily_key",
        return_value="should-be-ignored",
    ), patch("duckduckgo_search.DDGS", return_value=fake_ddgs):
        out = _parse(_call(web_search, {"query": "test", "provider": "duckduckgo"}))
    assert out["provider"] == "duckduckgo"


def test_web_search_duckduckgo_post_filters_by_allowed_domain(
    in_ctx, monkeypatch
):
    """DDG has no native domain filter — adapter does it client-side."""
    fake_ddgs = MagicMock()
    fake_ddgs.__enter__.return_value = fake_ddgs
    fake_ddgs.__exit__.return_value = False
    fake_ddgs.text.return_value = [
        {"title": "in", "href": "https://www.gov.cn/policy", "body": ""},
        {"title": "out", "href": "https://example.com/blog", "body": ""},
        {"title": "in-sub", "href": "https://sz.gov.cn/notice", "body": ""},
    ]
    with patch("duckduckgo_search.DDGS", return_value=fake_ddgs):
        out = _parse(
            _call(
                web_search,
                {
                    "query": "test",
                    "provider": "duckduckgo",
                    "allowed_domains": ["gov.cn"],
                },
            )
        )
    assert out["total"] == 2
    urls = [r["url"] for r in out["results"]]
    assert "https://www.gov.cn/policy" in urls
    assert "https://sz.gov.cn/notice" in urls
    assert "https://example.com/blog" not in urls


def test_web_search_normalizes_domains_with_scheme():
    """User passing 'https://www.gov.cn/' should match a result on
    'www.gov.cn'. Domain comparison is post-normalization."""
    from api.agent_v2.tools.web_search import _normalize_domain

    assert _normalize_domain("https://www.gov.cn/") == "www.gov.cn"
    assert _normalize_domain("HTTP://Sz.Gov.Cn/x/y") == "sz.gov.cn"
    assert _normalize_domain("  example.com  ") == "example.com"


def test_web_search_lru_cache_returns_cached_payload(in_ctx, monkeypatch):
    """Same query within TTL skips the adapter entirely."""
    monkeypatch.delenv("AGENT_V2_WEB_SEARCH_PROVIDER", raising=False)

    fake_ddgs = MagicMock()
    fake_ddgs.__enter__.return_value = fake_ddgs
    fake_ddgs.__exit__.return_value = False
    fake_ddgs.text.return_value = [
        {"title": "T", "href": "https://e.com/a", "body": "x"},
    ]
    with patch(
        "api.agent_v2.tools.web_search._resolve_tavily_key",
        return_value=None,
    ), patch("duckduckgo_search.DDGS", return_value=fake_ddgs):
        first = _parse(_call(web_search, {"query": "cache-me"}))
        second = _parse(_call(web_search, {"query": "cache-me"}))

    assert first.get("cache_hit") is False
    assert second.get("cache_hit") is True
    assert second["results"] == first["results"]
    # Adapter ran exactly once across both calls
    fake_ddgs.text.assert_called_once()


def test_web_search_happy_path(in_ctx, monkeypatch):
    # Mock Tavily client
    fake_client = MagicMock()
    fake_client.search.return_value = {
        "results": [
            {
                "title": "Shenzhen housing 2024",
                "url": "https://example.gov.cn/doc1",
                "content": "snippet 1",
                "score": 0.92,
                "published_date": "2024-11-01",
            },
            {
                "title": "Housing blog",
                "url": "https://example.com/blog",
                "content": "snippet 2",
                "score": 0.71,
            },
        ]
    }
    with patch(
        "api.agent_v2.tools.web_search._resolve_tavily_key",
        return_value="fake-key",
    ), patch(
        "tavily.TavilyClient",
        return_value=fake_client,
    ):
        out = _parse(
            _call(
                web_search,
                {
                    "query": "深圳 保障房",
                    "max_results": 5,
                    "allowed_domains": ["gov.cn"],
                    "topic": "news",
                    "search_depth": "advanced",
                },
            )
        )
    assert out["provider"] == "tavily"
    assert out["total"] == 2
    assert out["results"][0]["title"] == "Shenzhen housing 2024"
    assert out["results"][0]["url"].startswith("https://")
    # Check the client was called with domain filters + non-defaults
    fake_client.search.assert_called_once()
    kwargs = fake_client.search.call_args.kwargs
    assert kwargs["query"] == "深圳 保障房"
    assert kwargs["max_results"] == 5
    assert kwargs["include_domains"] == ["gov.cn"]
    assert "exclude_domains" not in kwargs
    assert kwargs["topic"] == "news"
    assert kwargs["search_depth"] == "advanced"
    # These are always forced off:
    assert kwargs["include_images"] is False
    assert kwargs["include_raw_content"] is False
    assert out["duration_ms"] >= 0
    assert "Sources:" in out["citation_policy"]


def test_web_search_blocked_domains_passed_to_provider(in_ctx):
    fake_client = MagicMock()
    fake_client.search.return_value = {"results": []}
    with patch(
        "api.agent_v2.tools.web_search._resolve_tavily_key",
        return_value="fake-key",
    ), patch(
        "tavily.TavilyClient",
        return_value=fake_client,
    ):
        _parse(
            _call(
                web_search,
                {
                    "query": "深圳 保障房",
                    "blocked_domains": ["noisy.com"],
                },
            )
        )
    assert fake_client.search.call_args.kwargs["exclude_domains"] == ["noisy.com"]


def test_web_search_propagates_client_error(in_ctx):
    fake_client = MagicMock()
    fake_client.search.side_effect = RuntimeError("tavily down")
    with patch(
        "api.agent_v2.tools.web_search._resolve_tavily_key",
        return_value="k",
    ), patch(
        "tavily.TavilyClient",
        return_value=fake_client,
    ):
        out = _parse(_call(web_search, {"query": "x"}))
    assert "error" in out
    assert "tavily_error" in out["error"]
    assert "RuntimeError" in out["error"]


def test_web_search_clamps_max_results(in_ctx):
    fake_client = MagicMock()
    fake_client.search.return_value = {"results": []}
    with patch(
        "api.agent_v2.tools.web_search._resolve_tavily_key",
        return_value="k",
    ), patch(
        "tavily.TavilyClient",
        return_value=fake_client,
    ):
        _call(web_search, {"query": "q", "max_results": 999})
    assert fake_client.search.call_args.kwargs["max_results"] == 20
    with patch(
        "api.agent_v2.tools.web_search._resolve_tavily_key",
        return_value="k",
    ), patch(
        "tavily.TavilyClient",
        return_value=fake_client,
    ):
        _call(web_search, {"query": "q", "max_results": 0})
    assert fake_client.search.call_args.kwargs["max_results"] == 1


# ───────────────── web_fetch ─────────────────


def test_ssrf_rejects_private_ipv4():
    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("10.0.0.1", 80))],
    ):
        ok, reason = _ssrf_check("http://intranet.local/x")
    assert not ok
    assert "ssrf_blocked" in reason


def test_ssrf_rejects_loopback():
    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("127.0.0.1", 80))],
    ):
        ok, reason = _ssrf_check("http://localhost/x")
    assert not ok
    assert "ssrf_blocked" in reason


def test_ssrf_accepts_public_ipv4():
    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("8.8.8.8", 443))],
    ):
        ok, reason = _ssrf_check("https://dns.google/x")
    assert ok
    assert reason == ""


def test_ssrf_rejects_non_http_scheme():
    ok, reason = _ssrf_check("file:///etc/passwd")
    assert not ok
    assert "unsupported_scheme" in reason


def test_ssrf_rejects_missing_host():
    ok, reason = _ssrf_check("http:///path-only")
    assert not ok
    assert "missing_hostname" in reason


def test_web_fetch_requires_url(in_ctx):
    out = _parse(_call(web_fetch, {"url": ""}))
    assert "error" in out


def test_web_fetch_rejects_private(in_ctx):
    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("192.168.1.1", 80))],
    ):
        out = _parse(_call(web_fetch, {"url": "http://router.home/x"}))
    assert "error" in out
    assert "ssrf" in out["error"]


def test_web_fetch_happy_html(in_ctx):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "text/html; charset=utf-8"}
    fake_response.content = (
        b"<html><head><title>Hello</title></head>"
        b"<body><main><p>Hi there.</p>"
        b"<script>evil()</script></main></body></html>"
    )
    fake_response.encoding = "utf-8"
    fake_response.url = "https://example.com/"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            return fake_response

    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(),
    ):
        out = _parse(_call(web_fetch, {"url": "https://example.com/"}))
    assert "error" not in out
    assert out["title"] == "Hello"
    assert "Hi there" in out["content"]
    # script stripped
    assert "evil" not in out["content"]
    assert out["truncated"] is False
    assert out["duration_ms"] >= 0
    assert "Sources:" in out["citation_policy"]


def test_web_fetch_preserves_basic_markdown_structure(in_ctx):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "text/html; charset=utf-8"}
    fake_response.content = (
        b"<html><head><title>Docs</title></head><body><main>"
        b"<h1>Heading</h1><ul><li>One</li></ul>"
        b"<p><a href='https://example.com/a'>Link</a></p>"
        b"</main></body></html>"
    )
    fake_response.encoding = "utf-8"
    fake_response.url = "https://example.com/docs"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            return fake_response

    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(),
    ):
        out = _parse(_call(web_fetch, {"url": "https://example.com/docs"}))
    assert "# Heading" in out["content"]
    assert "- One" in out["content"]
    assert "[Link](https://example.com/a)" in out["content"]


def test_web_fetch_upgrades_http_to_https(in_ctx):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "text/plain"}
    fake_response.content = b"ok"
    fake_response.encoding = "utf-8"
    fake_response.url = "https://example.com/plain"
    seen_urls = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            seen_urls.append(url)
            return fake_response

    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(),
    ):
        out = _parse(_call(web_fetch, {"url": "http://example.com/plain"}))
    assert seen_urls == ["https://example.com/plain"]
    assert out["upgraded_from_http"] is True


def test_web_fetch_blocks_cross_host_redirect(in_ctx):
    redirect = MagicMock()
    redirect.status_code = 302
    redirect.headers = {"location": "https://evil.example/path"}
    redirect.url = "https://example.com/start"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            return redirect

    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(),
    ):
        out = _parse(_call(web_fetch, {"url": "https://example.com/start"}))
    assert out["error_code"] == "redirect_blocked"
    assert out["redirect_url"] == "https://evil.example/path"


def test_web_fetch_follows_same_host_redirect(in_ctx):
    redirect = MagicMock()
    redirect.status_code = 302
    redirect.headers = {"location": "/final"}
    redirect.url = "https://example.com/start"
    final = MagicMock()
    final.status_code = 200
    final.headers = {"content-type": "text/plain"}
    final.content = b"done"
    final.encoding = "utf-8"
    final.url = "https://example.com/final"
    responses = [redirect, final]
    seen_urls = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            seen_urls.append(url)
            return responses.pop(0)

    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(),
    ):
        out = _parse(_call(web_fetch, {"url": "https://example.com/start"}))
    assert seen_urls == ["https://example.com/start", "https://example.com/final"]
    assert out["content"] == "done"


def test_web_fetch_http_error_maps_cleanly(in_ctx):
    fake_response = MagicMock()
    fake_response.status_code = 404
    fake_response.headers = {"content-type": "text/html"}
    fake_response.content = b"nope"
    fake_response.encoding = "utf-8"
    fake_response.url = "https://example.com/missing"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            return fake_response

    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(),
    ):
        out = _parse(
            _call(web_fetch, {"url": "https://example.com/missing"})
        )
    assert out["error"] == "http_404"
    assert out["status_code"] == 404


def test_web_fetch_timeout(in_ctx):
    import httpx

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            raise httpx.TimeoutException("slow")

    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(),
    ):
        out = _parse(_call(web_fetch, {"url": "https://example.com/"}))
    assert out["error"] == "timeout"


def test_web_fetch_rejects_unsupported_content_type(in_ctx):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "application/octet-stream"}
    fake_response.content = b"\x00\x01\x02"
    fake_response.encoding = None
    fake_response.url = "https://example.com/binary"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            return fake_response

    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(),
    ):
        out = _parse(_call(web_fetch, {"url": "https://example.com/binary"}))
    assert "error" in out
    assert "unsupported_content_type" in out["error"]


# ───────────────── web_fetch cache + preapproved ─────────────────


def _make_html_client(*, body: bytes, url: str = "https://example.com/"):
    """Helper that builds a one-shot httpx-like async client returning the
    given HTML body. Used by the cache + preapproved tests below."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "text/html; charset=utf-8"}
    fake_response.content = body
    fake_response.encoding = "utf-8"
    fake_response.url = url

    get_calls = {"count": 0}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            get_calls["count"] += 1
            return fake_response

    return _FakeClient(), get_calls


def test_web_fetch_caches_within_ttl(in_ctx):
    """Two identical fetches in a row → second is a cache hit, no second
    network call."""
    client, calls = _make_html_client(
        body=(
            b"<html><head><title>Cached</title></head>"
            b"<body><main><p>fresh body</p></main></body></html>"
        ),
        url="https://example.com/cache-me",
    )
    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch("httpx.AsyncClient", return_value=client):
        first = _parse(_call(web_fetch, {"url": "https://example.com/cache-me"}))
        second = _parse(_call(web_fetch, {"url": "https://example.com/cache-me"}))

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["title"] == "Cached"
    assert second["content"] == first["content"]
    assert calls["count"] == 1  # second call hit the cache


def test_web_fetch_no_cache_flag_bypasses_cache(in_ctx):
    """``no_cache=true`` should always re-fetch."""
    client, calls = _make_html_client(
        body=b"<html><body><p>x</p></body></html>",
        url="https://example.com/skip",
    )
    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch("httpx.AsyncClient", return_value=client):
        _parse(_call(web_fetch, {"url": "https://example.com/skip"}))
        _parse(_call(
            web_fetch,
            {"url": "https://example.com/skip", "no_cache": True},
        ))
    assert calls["count"] == 2


def test_web_fetch_flags_preapproved_host(in_ctx):
    """Fetch from docs.python.org (in PREAPPROVED_HOSTS) → preapproved=true.
    Random commercial host → preapproved=false."""
    py_client, _ = _make_html_client(
        body=b"<html><body><p>docs</p></body></html>",
        url="https://docs.python.org/3/library/asyncio.html",
    )
    other_client, _ = _make_html_client(
        body=b"<html><body><p>random blog</p></body></html>",
        url="https://example.com/blog",
    )
    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch("httpx.AsyncClient", return_value=py_client):
        out_py = _parse(
            _call(
                web_fetch,
                {"url": "https://docs.python.org/3/library/asyncio.html"},
            )
        )
    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch("httpx.AsyncClient", return_value=other_client):
        out_other = _parse(_call(web_fetch, {"url": "https://example.com/blog"}))

    assert out_py["preapproved"] is True
    assert out_other["preapproved"] is False


def test_preapproved_suffix_match():
    """``szjs.sz.gov.cn`` should match the bare ``gov.cn`` entry via
    suffix-walk so we don't have to enumerate every subdomain."""
    from api.agent_v2.tools.web_fetch_preapproved import is_preapproved

    assert is_preapproved("https://www.gov.cn/policy") is True
    assert is_preapproved("https://szjs.sz.gov.cn/notice") is True
    assert is_preapproved("https://docs.python.org/3/") is True
    assert is_preapproved("https://example.com/blog") is False
    # Empty / malformed → false (shouldn't blow up)
    assert is_preapproved("") is False
    assert is_preapproved("not a url") is False


# ───────────────── registry + annotations parity ─────────────────


def test_web_tools_registered_and_annotated():
    from api.agent_v2.annotations import ANNOTATIONS
    from api.agent_v2.prompting import SEARCH_HINT_BY_TOOL
    from api.agent_v2.registry import ALL_TOOLS

    for name in ("web_search", "web_fetch"):
        assert name in ALL_TOOLS, f"{name} missing from ALL_TOOLS"
        assert name in ANNOTATIONS, f"{name} missing from ANNOTATIONS"
        assert ANNOTATIONS[name].is_read_only is True
        assert name in SEARCH_HINT_BY_TOOL, f"{name} missing search hint"


def test_research_supervisor_has_web_tools():
    from api.agent_v2.definitions.built_in.supervisor_research import DEFINITION

    assert "web_search" in DEFINITION.tools
    assert "web_fetch" in DEFINITION.tools


def test_policy_supervisors_do_not_have_web_tools():
    """Policy / legal / wiki / customer-support supervisors must stay KB-only."""
    from api.agent_v2.definitions.built_in.supervisor_baozhang import (
        DEFINITION as BZ,
    )
    from api.agent_v2.definitions.built_in.supervisor_generic_policy import (
        DEFINITION as GP,
    )
    from api.agent_v2.definitions.built_in.supervisor_internal_wiki import (
        DEFINITION as IW,
    )
    from api.agent_v2.definitions.built_in.supervisor_legal_contract import (
        DEFINITION as LC,
    )

    for d in (BZ, GP, IW, LC):
        assert "web_search" not in d.tools, (
            f"{d.name} must NOT have web_search — policy supervisors are "
            f"KB-only by design"
        )
        assert "web_fetch" not in d.tools, f"{d.name} must NOT have web_fetch"


# Keep unused imports warning quiet
_ = AsyncMock

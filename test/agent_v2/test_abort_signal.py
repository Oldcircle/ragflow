"""Phase 2.7 v0.20 — abort signal propagation tests.

Verifies that ``ToolContext.cancelled`` (an ``asyncio.Event``) actually
short-circuits long-running tool work:

1. ``check_cancelled()`` raises ``CancelledByCaller`` when the event is set
2. ``is_cancelled()`` returns True without raising
3. ``web_search`` returns a clean ``error_code='cancelled'`` envelope when
   the signal is set before the network call
4. ``web_fetch`` bails between redirect hops on cancel
5. ``web_fetch`` skips the secondary-model summarize branch on cancel,
   even after the page body has already been pulled
6. ``AgentRunner.cancel()`` sets the event so tools spawned by that
   runner will see the signal
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from api.agent_v2.runner import AgentRunner, ModelConfig
from api.agent_v2.tools.base import (
    CancelledByCaller,
    ToolContext,
    check_cancelled,
    is_cancelled,
    reset_ctx,
    set_ctx,
)


def _call(handler, args: dict) -> dict:
    return asyncio.run(handler(args))


def _parse(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


# ─────────── primitives ───────────


def test_check_cancelled_no_ctx_is_silent():
    """Tools running outside a Runner (unit tests, ad-hoc scripts) must
    not blow up — the helpers no-op when ctx is None."""
    check_cancelled()  # should not raise


def test_check_cancelled_event_unset_is_silent():
    ev = asyncio.Event()
    ctx = ToolContext(tenant_id="t", kb_ids=("k",), cancelled=ev)
    token = set_ctx(ctx)
    try:
        check_cancelled()
        assert is_cancelled() is False
    finally:
        reset_ctx(token)


def test_check_cancelled_event_set_raises():
    ev = asyncio.Event()
    ev.set()
    ctx = ToolContext(tenant_id="t", kb_ids=("k",), cancelled=ev)
    token = set_ctx(ctx)
    try:
        with pytest.raises(CancelledByCaller):
            check_cancelled()
        assert is_cancelled() is True
    finally:
        reset_ctx(token)


# ─────────── runner.cancel() integration ───────────


def test_runner_cancel_before_run_sets_event():
    """cancel() called before run() should still work — the persistent
    signal is what makes "click stop while page is loading" reliable."""
    r = AgentRunner(
        tenant_id="t1",
        kb_ids=["kb1"],
        system_prompt="x",
        model=ModelConfig(model="m", auth_token="k"),
    )
    r.cancel()
    assert r._cancel_event is not None
    assert r._cancel_event.is_set()


def test_runner_cancel_is_idempotent():
    r = AgentRunner(
        tenant_id="t1",
        kb_ids=["kb1"],
        system_prompt="x",
        model=ModelConfig(model="m", auth_token="k"),
    )
    r.cancel()
    r.cancel()  # second call must not crash; event stays set
    assert r._cancel_event.is_set()


# ─────────── web_search ───────────


def test_web_search_bails_when_cancelled_pre_flight():
    from api.agent_v2.tools.web_search import _clear_cache_for_tests, web_search

    _clear_cache_for_tests()
    ev = asyncio.Event()
    ev.set()
    ctx = ToolContext(
        tenant_id="t1", kb_ids=("kb1",), user_id="u1", cancelled=ev,
    )
    token = set_ctx(ctx)
    try:
        # Mock the adapter so we can prove it was NOT called
        adapter = MagicMock()
        adapter.search.return_value = []
        with patch(
            "api.agent_v2.tools.web_search._select_adapter",
            return_value=(adapter, None),
        ):
            out = _parse(_call(
                web_search.handler,
                {"query": "should not reach the network"},
            ))
        assert out["error_code"] == "cancelled"
        adapter.search.assert_not_called()
    finally:
        reset_ctx(token)


# ─────────── web_fetch ───────────


def _build_html_response(*, body: bytes, url: str, status: int = 200):
    fake_response = MagicMock()
    fake_response.status_code = status
    fake_response.headers = {"content-type": "text/html; charset=utf-8"}
    fake_response.content = body
    fake_response.encoding = "utf-8"
    fake_response.url = url
    return fake_response


def test_web_fetch_bails_before_first_get_when_cancelled():
    from api.agent_v2.tools.web_fetch import _clear_cache_for_tests, web_fetch

    _clear_cache_for_tests()
    ev = asyncio.Event()
    ev.set()
    ctx = ToolContext(
        tenant_id="t1", kb_ids=("kb1",), user_id="u1", cancelled=ev,
    )
    token = set_ctx(ctx)

    get_calls = {"count": 0}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            get_calls["count"] += 1
            return _build_html_response(body=b"<html/>", url="https://x.com/")

    try:
        with patch(
            "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 443))],
        ), patch("httpx.AsyncClient", return_value=_FakeClient()):
            out = _parse(_call(
                web_fetch.handler,
                {"url": "https://x.com/should-not-fetch"},
            ))
        # Cancel is checked at the top of the redirect loop — before any
        # network call. So get() must not have run.
        assert out["error_code"] == "cancelled"
        assert get_calls["count"] == 0
    finally:
        reset_ctx(token)


def test_web_fetch_skips_summarize_branch_when_cancelled_mid_flight():
    """The classic 'user clicks stop while page is downloading' case:
    cancel arrives between the GET completing and the secondary-model
    LLM round-trip. We should NOT pay the LLM cost — return the partial
    payload with summarized=False + a clear error string."""
    from api.agent_v2.tools.web_fetch import _clear_cache_for_tests, web_fetch

    _clear_cache_for_tests()

    cancel_event = asyncio.Event()

    fake_response = _build_html_response(
        body=b"<html><head><title>X</title></head><body>Hi</body></html>",
        url="https://example.com/page",
    )

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            # Trigger cancellation mid-flight: page just downloaded,
            # right before web_fetch checks the cancel signal again.
            cancel_event.set()
            return fake_response

    summarize_called = {"hit": False}

    async def _summarize_should_not_run(**_kw):
        summarize_called["hit"] = True
        return "should-not-run", None

    ctx = ToolContext(
        tenant_id="t1", kb_ids=("kb1",), user_id="u1",
        cancelled=cancel_event,
        model_config=ModelConfig(
            model="m", base_url="https://x", auth_token="k",
        ),
    )
    token = set_ctx(ctx)
    try:
        with patch(
            "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 443))],
        ), patch("httpx.AsyncClient", return_value=_FakeClient()), patch(
            "api.agent_v2.tools.web_fetch._summarize_with_secondary_model",
            side_effect=_summarize_should_not_run,
        ):
            out = _parse(_call(
                web_fetch.handler,
                {
                    "url": "https://example.com/page",
                    "prompt": "what is this page about",
                },
            ))
        assert out["summarized"] is False
        assert "cancelled" in (out.get("summary_error") or "").lower()
        assert summarize_called["hit"] is False, (
            "secondary model ran despite cancel — cost-control bug"
        )
    finally:
        reset_ctx(token)


def test_runner_cancel_propagates_into_tool_ctx():
    """Closing the loop: a runner's cancel() flag, propagated into the
    ToolContext via run(), must be visible to a tool's check_cancelled()
    call. Doesn't require running the SDK — the cancel field on ctx is
    set inside the runner's ``ctx = ToolContext(...)`` construction."""
    r = AgentRunner(
        tenant_id="t1",
        kb_ids=["kb1"],
        system_prompt="hi",
        model=ModelConfig(model="m", auth_token="k"),
    )

    async def _exercise() -> bool:
        # Mimic what the runner does internally to construct the ctx +
        # bind the cancel event. We don't actually start the SDK.
        if r._cancel_event is None:
            r._cancel_event = asyncio.Event()
        ctx = ToolContext(
            tenant_id=r.tenant_id,
            kb_ids=tuple(r.kb_ids),
            cancelled=r._cancel_event,
        )
        tok = set_ctx(ctx)
        try:
            r.cancel()
            try:
                check_cancelled()
                return False
            except CancelledByCaller:
                return True
        finally:
            reset_ctx(tok)

    assert asyncio.run(_exercise()) is True

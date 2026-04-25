"""Phase 2.7 v0.17 — regression: subagent inherits parent session_id.

Live-caught bug: ``spawn_subagent`` originally only passed
``parent_session_id=ctx.session_id`` to the child Runner, leaving the
child's ``self.session_id = None``. Tools that read ``ctx.session_id``
(web_fetch_to_attachment, submit_plan persistence, doc_ingest_attachment,
the plan_gate read) then refused with "no_session" — silently making
the two-step archive flow non-functional even though unit tests passed.

This file pins the fix as a direct regression test:
- Child Runner's constructor must receive ``session_id=ctx.session_id``
- The captured kwargs must match the parent ctx, not be None / different
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from api.agent_v2 import event as ev
from api.agent_v2.runner import ModelConfig
from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.spawn_subagent import spawn_subagent


def _call(handler, args: dict) -> dict:
    return asyncio.run(handler(args))


def _parse(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _stub_trace_service():
    """SubagentTraceService.start returns a stub row with .id."""

    class _StubTrace:
        id = "TRACE-A"

    return patch.multiple(
        "api.agent_v2.tools.spawn_subagent.SubagentTraceService",
        start=lambda **_kw: _StubTrace(),
        finish=lambda *a, **kw: None,
    )


def _capture_runner_kwargs():
    """Builds a stub AgentRunner that records its constructor kwargs and
    yields a single text_delta + end on .run() so the spawn handler
    completes cleanly."""
    captured: dict = {}

    class _StubRunner:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        async def run(self, _prompt, **_kw):
            yield ev.text_delta("ok")
            yield ev.end({"total_cost_usd": 0.0})

    return _StubRunner, captured


@pytest.fixture
def parent_ctx_with_session():
    """Set up a parent ctx with a known session_id + model_config."""
    captured_events: list[ev.Event] = []

    async def _emit(event):
        captured_events.append(event)

    ctx = ToolContext(
        tenant_id="tenant-A",
        kb_ids=("kb-1",),
        user_id="user-A",
        session_id="parent-sess-XYZ",
        event_emitter=_emit,
        model_config=ModelConfig(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/anthropic",
            auth_token="fake-key",
        ),
        allowed_subagent_types=None,  # allow any subagent
        pending_plan_status="approved",
        pending_plan_id="pid-7",
    )
    token = set_ctx(ctx)
    yield ctx, captured_events
    reset_ctx(token)


def test_child_runner_receives_parent_session_id(parent_ctx_with_session):
    """The actual fix: child Runner must be constructed with
    ``session_id=ctx.session_id``. Without this, the child's tools see
    ``ctx.session_id = None``."""
    parent_ctx, _events = parent_ctx_with_session
    Stub, captured = _capture_runner_kwargs()

    with _stub_trace_service(), patch("api.agent_v2.runner.AgentRunner", Stub):
        out = _parse(_call(spawn_subagent.handler, {
            "description": "test",
            "prompt": "do something",
        }))

    assert out.get("trace_id") == "TRACE-A"
    kwargs = captured["kwargs"]
    # The actual regression assertion — without the v0.17 fix this is None.
    assert kwargs["session_id"] == parent_ctx.session_id == "parent-sess-XYZ"
    # parent_session_id is also set (kept for audit lineage / future use)
    assert kwargs["parent_session_id"] == "parent-sess-XYZ"


def test_child_inherits_tenant_and_user(parent_ctx_with_session):
    """tenant_id + user_id must propagate so RBAC + tenant-scoped DB
    queries inside child tools resolve to the same tenant as the parent.
    Cross-tenant leak through subagents would be a security issue."""
    parent_ctx, _events = parent_ctx_with_session
    Stub, captured = _capture_runner_kwargs()

    with _stub_trace_service(), patch("api.agent_v2.runner.AgentRunner", Stub):
        _call(spawn_subagent.handler, {"description": "x", "prompt": "y"})
    kwargs = captured["kwargs"]
    assert kwargs["tenant_id"] == "tenant-A"
    assert kwargs["user_id"] == "user-A"


def test_child_inherits_pending_plan_state(parent_ctx_with_session):
    """plan gate state must propagate so a child spawned under an
    approved plan can actually execute writes; conversely, a child
    spawned under a 'waiting' plan stays blocked. Without propagation
    a child would see a fresh None state and the wrong gate decision."""
    parent_ctx, _events = parent_ctx_with_session
    Stub, captured = _capture_runner_kwargs()

    with _stub_trace_service(), patch("api.agent_v2.runner.AgentRunner", Stub):
        _call(spawn_subagent.handler, {"description": "x", "prompt": "y"})
    kwargs = captured["kwargs"]
    assert kwargs["pending_plan_status"] == "approved"
    assert kwargs["pending_plan_id"] == "pid-7"


def test_child_depth_increments(parent_ctx_with_session):
    """depth must be parent_depth + 1 so a child can detect 'I'm a
    subagent' (used by the prompting layer to forbid grand-child spawns
    and shape the system prompt)."""
    parent_ctx, _events = parent_ctx_with_session
    assert parent_ctx.depth == 0  # parent ctx default
    Stub, captured = _capture_runner_kwargs()

    with _stub_trace_service(), patch("api.agent_v2.runner.AgentRunner", Stub):
        _call(spawn_subagent.handler, {"description": "x", "prompt": "y"})
    assert captured["kwargs"]["depth"] == 1


def test_kb_ids_propagate_to_child(parent_ctx_with_session):
    """Child should be able to retrieve from the same KBs the parent
    can — without this, sub_archivist asked to ingest into kb1 wouldn't
    even be able to verify the KB exists."""
    parent_ctx, _events = parent_ctx_with_session
    Stub, captured = _capture_runner_kwargs()

    with _stub_trace_service(), patch("api.agent_v2.runner.AgentRunner", Stub):
        _call(spawn_subagent.handler, {"description": "x", "prompt": "y"})
    assert list(captured["kwargs"]["kb_ids"]) == ["kb-1"]

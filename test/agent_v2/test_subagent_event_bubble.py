"""Phase 2.7 v0.17 — child-event bubble-up to parent SSE stream.

Verifies that ``spawn_subagent`` forwards a child Runner's tool_call_*,
plan_submitted, ask_user_question, citation_warning, and text_delta events
to the parent's event_emitter, each stamped with the matching
``subagent_trace_id`` so the frontend can nest them under the
``subagent_start`` card it already gets.

Mocks the child Runner — the goal is to test the bubble plumbing, not the
SDK or any tool. Bubble logic depends on:
  - ``ctx.event_emitter`` being callable (Runner sets one up; we provide a
    capture-list emitter so we can introspect the bubbled events)
  - The child Runner's ``run()`` yielding a sequence of Events
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from api.agent_v2 import event as ev
from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.spawn_subagent import spawn_subagent


def _call(handler_callable, args: dict) -> dict:
    return asyncio.run(handler_callable(args))


def _parse(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


@pytest.fixture
def captured_events():
    """Sets up a ToolContext with a capturing event emitter and yields the
    list. Caller introspects after the spawn handler runs."""
    captured: list[ev.Event] = []

    async def _emit(event):
        captured.append(event)

    from api.agent_v2.runner import ModelConfig

    ctx = ToolContext(
        tenant_id="t1",
        kb_ids=("kb1",),
        user_id="u1",
        session_id="parent-session",
        event_emitter=_emit,
        model_config=ModelConfig(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/anthropic",
            auth_token="fake-key-for-tests",
        ),
        # Allow spawning everything during the test
        allowed_subagent_types=None,
    )
    token = set_ctx(ctx)
    yield captured
    reset_ctx(token)


def _stub_child_runner(events: list[ev.Event]):
    """Returns a class-like stub matching AgentRunner constructor + run()."""

    class _StubRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, _prompt):
            for e in events:
                yield e

    return _StubRunner


def _stub_subagent_trace_service():
    """Patch SubagentTraceService.start / finish to no-ops returning a stub
    trace row with .id == "TRACE-123" (matches the production return shape:
    a peewee Model instance with an id attr)."""

    class _StubTrace:
        id = "TRACE-123"

    return patch.multiple(
        "api.agent_v2.tools.spawn_subagent.SubagentTraceService",
        start=lambda **_kw: _StubTrace(),
        finish=lambda *a, **kw: None,
    )


def _baseline_args() -> dict:
    return {
        "description": "test child",
        "prompt": "do something",
    }


def test_bubbles_tool_call_events_with_trace_id(captured_events):
    """tool_call_start + tool_call_end fired by the child should appear on
    the parent stream with subagent_trace_id stamped in data."""
    child_events = [
        ev.tool_call_start("call-1", "rag_retrieve", {"query": "x"}),
        ev.tool_call_end("call-1", result={"hits": 0}, duration_ms=42),
        ev.text_delta("done."),
        ev.end({"total_cost_usd": 0.01}),
    ]

    Stub = _stub_child_runner(child_events)
    with _stub_subagent_trace_service(), patch(
        "api.agent_v2.runner.AgentRunner", Stub,
    ):
        out = _parse(_call(spawn_subagent.handler, _baseline_args()))
    assert out.get("trace_id") == "TRACE-123"

    types = [e.type for e in captured_events]
    # subagent_start (from spawn handler) + bubbled child events +
    # subagent_end (from spawn handler). The child's `end` is NOT bubbled.
    assert "subagent_start" in types
    assert "tool_call_start" in types
    assert "tool_call_end" in types
    assert "text_delta" in types
    assert types.count("end") == 0  # child's end stays private
    assert "subagent_end" in types

    # Every bubbled (non-spawn-internal) event carries trace_id +
    # agent_role tags. spawn_subagent's own subagent_start / subagent_end
    # don't need those (they identify themselves via trace_id at the top
    # level of data already).
    bubbled = [
        e for e in captured_events
        if e.type in ("tool_call_start", "tool_call_end", "text_delta")
    ]
    assert all(e.data.get("subagent_trace_id") == "TRACE-123" for e in bubbled)
    assert all(e.data.get("agent_role", "").startswith("subagent") for e in bubbled)


def test_bubbles_plan_submitted_from_child(captured_events):
    """The whole point of this fix — sub_archivist's submit_plan should be
    visible on the parent stream so the UI can pop the approval card."""
    child_events = [
        ev.plan_submitted(
            pending_id="pid-1",
            title="Archive policy",
            steps=["fetch", "ingest"],
            affected_resources=[{"kind": "kb", "id": "kb1"}],
            risk_level="low",
            estimated_cost_usd=0.0,
            reversible=True,
            reversible_hint="manual delete",
            tool_use_id="t1",
            preview=None,
        ),
        ev.text_delta("Plan submitted for your review."),
        ev.end({"total_cost_usd": 0.01}),
    ]
    Stub = _stub_child_runner(child_events)
    with _stub_subagent_trace_service(), patch(
        "api.agent_v2.runner.AgentRunner", Stub,
    ):
        _parse(_call(spawn_subagent.handler, _baseline_args()))

    plan_events = [e for e in captured_events if e.type == "plan_submitted"]
    assert len(plan_events) == 1
    assert plan_events[0].data["pending_id"] == "pid-1"
    assert plan_events[0].data["subagent_trace_id"] == "TRACE-123"


def test_bubbles_ask_user_question_from_child(captured_events):
    child_events = [
        ev.ask_user_question(
            pending_id="q-1",
            question="Which KB should I use?",
            header="Disambiguation",
            options=[{"id": "kb_a", "label": "KB A"}],
            multi_select=False,
            tool_use_id="t1",
        ),
        ev.end({"total_cost_usd": 0.0}),
    ]
    Stub = _stub_child_runner(child_events)
    with _stub_subagent_trace_service(), patch(
        "api.agent_v2.runner.AgentRunner", Stub,
    ):
        _parse(_call(spawn_subagent.handler, _baseline_args()))

    asks = [e for e in captured_events if e.type == "ask_user_question"]
    assert len(asks) == 1
    assert asks[0].data["pending_id"] == "q-1"
    assert asks[0].data["subagent_trace_id"] == "TRACE-123"


def test_does_not_double_emit_end(captured_events):
    """The child's `end` event must not bubble — parent has its own end
    semantics and emitting two would confuse stream consumers downstream."""
    child_events = [
        ev.text_delta("hi"),
        ev.end({"total_cost_usd": 0.05}),
    ]
    Stub = _stub_child_runner(child_events)
    with _stub_subagent_trace_service(), patch(
        "api.agent_v2.runner.AgentRunner", Stub,
    ):
        _parse(_call(spawn_subagent.handler, _baseline_args()))

    # Only subagent_end (from spawn handler), zero `end` from the bubble.
    assert sum(1 for e in captured_events if e.type == "end") == 0


def test_named_subagent_marks_role_with_definition_name(captured_events):
    """When the parent specifies subagent_type, the bubbled events should
    carry agent_role=subagent:<definition.name>."""
    child_events = [
        ev.text_delta("ack"),
        ev.end({"total_cost_usd": 0.0}),
    ]
    Stub = _stub_child_runner(child_events)

    # Use the real sub_archivist definition resolver — it's registered.
    args = {**_baseline_args(), "subagent_type": "sub_archivist"}
    with _stub_subagent_trace_service(), patch(
        "api.agent_v2.runner.AgentRunner", Stub,
    ):
        _parse(_call(spawn_subagent.handler, args))

    text_events = [e for e in captured_events if e.type == "text_delta"]
    assert text_events
    assert text_events[0].data["agent_role"] == "subagent:sub_archivist"


def test_bubble_failure_does_not_break_child_run(captured_events):
    """If the parent's emitter is flaky, the child run should still
    complete and the spawn handler should still return a result envelope.
    Bubbling is best-effort, never fatal."""

    bad_emit_count = {"calls": 0}

    async def _bad_emit(_e):
        bad_emit_count["calls"] += 1
        raise RuntimeError("simulated emitter outage")

    # Replace the captured-events fixture's emitter mid-flight.
    from api.agent_v2.tools.base import _ctx_var
    ctx = _ctx_var.get()
    ctx.event_emitter = _bad_emit  # type: ignore[attr-defined]

    child_events = [
        ev.tool_call_start("c-1", "x", {}),
        ev.text_delta("survived"),
        ev.end({}),
    ]
    Stub = _stub_child_runner(child_events)
    with _stub_subagent_trace_service(), patch(
        "api.agent_v2.runner.AgentRunner", Stub,
    ):
        out = _parse(_call(spawn_subagent.handler, _baseline_args()))

    # Result envelope is still well-formed even with broken emitter.
    assert out.get("result", "").strip() == "survived"
    # Bubble was attempted on each child event (and tail subagent_end), but
    # no exception leaked.
    assert bad_emit_count["calls"] >= 2

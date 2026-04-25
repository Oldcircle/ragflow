"""Phase 2.7 v0.21 — runner registry unit tests.

Covers register / unregister / cancel / is_active / active_session_count
+ the safety paths around concurrent registrations on the same session.
The HTTP cancel endpoint integration test lives in
``test_agent_v2_app_cancel.py``.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from api.agent_v2 import runner_registry as rr


@pytest.fixture(autouse=True)
def _wipe_registry():
    rr._clear_for_tests()
    yield
    rr._clear_for_tests()


def _stub_runner():
    runner = MagicMock(name="runner")
    runner.cancel = MagicMock()
    return runner


# ─────────── basic register / unregister ───────────


def test_register_then_is_active():
    r = _stub_runner()
    rr.register("sess-1", r)
    assert rr.is_active("sess-1")
    assert rr.active_session_count() == 1


def test_register_unregister_pair():
    r = _stub_runner()
    rr.register("sess-1", r)
    rr.unregister("sess-1", r)
    assert rr.is_active("sess-1") is False
    assert rr.active_session_count() == 0


def test_unregister_unknown_is_noop():
    rr.unregister("never-registered")  # must not raise
    assert rr.active_session_count() == 0


def test_unregister_with_runner_mismatch_keeps_entry():
    """Late finally racing with a fresh registration: the old runner's
    finally block calls unregister(session_id, old_runner). If a new
    runner has already taken the slot, we must NOT drop it — that would
    orphan the new active run from the registry."""
    old = _stub_runner()
    new = _stub_runner()
    rr.register("sess-1", old)
    rr.register("sess-1", new)  # replaces old
    # Old's finally fires now
    rr.unregister("sess-1", old)
    # The new runner should still be reachable
    assert rr.is_active("sess-1")


# ─────────── cancel ───────────


def test_cancel_active_session_calls_runner_cancel():
    r = _stub_runner()
    rr.register("sess-1", r)
    assert rr.cancel("sess-1") is True
    r.cancel.assert_called_once()


def test_cancel_unknown_session_returns_false_no_raise():
    assert rr.cancel("ghost") is False


def test_cancel_idempotent_for_same_session():
    """Calling cancel twice in quick succession (user double-clicks
    stop) must be safe and call runner.cancel each time."""
    r = _stub_runner()
    rr.register("sess-1", r)
    assert rr.cancel("sess-1") is True
    assert rr.cancel("sess-1") is True
    assert r.cancel.call_count == 2


def test_cancel_after_unregister_returns_false():
    r = _stub_runner()
    rr.register("sess-1", r)
    rr.unregister("sess-1", r)
    assert rr.cancel("sess-1") is False
    r.cancel.assert_not_called()


def test_cancel_does_not_crash_when_runner_cancel_raises():
    bad = MagicMock(name="bad")
    bad.cancel.side_effect = RuntimeError("event loop closed")
    rr.register("sess-1", bad)
    # Returns False because the cancel attempt blew up — but does not
    # raise out to the HTTP handler.
    assert rr.cancel("sess-1") is False


# ─────────── replacement semantics ───────────


def test_re_register_same_session_cancels_previous_runner():
    """Defensive: if someone forgot to unregister and re-registers a
    new runner on the same session_id, the previous runner's cancel
    is invoked so it stops eating budget. The new runner takes the
    slot."""
    old = _stub_runner()
    new = _stub_runner()
    rr.register("sess-1", old)
    rr.register("sess-1", new)
    old.cancel.assert_called_once()  # auto-stopped
    new.cancel.assert_not_called()  # left alone
    # And cancel now hits the new one
    rr.cancel("sess-1")
    new.cancel.assert_called_once()


def test_re_register_same_runner_instance_no_op():
    r = _stub_runner()
    rr.register("sess-1", r)
    rr.register("sess-1", r)  # same instance — shouldn't cancel itself
    r.cancel.assert_not_called()


# ─────────── empty session_id guards ───────────


def test_empty_session_id_register_is_noop():
    r = _stub_runner()
    rr.register("", r)
    assert rr.active_session_count() == 0


def test_empty_session_id_cancel_is_false():
    assert rr.cancel("") is False
    assert rr.cancel(None) is False  # type: ignore[arg-type]


# ─────────── concurrency smoke ───────────


def test_concurrent_register_unregister_consistent():
    """Hit the registry from many threads simultaneously and confirm
    final state is consistent. Doesn't prove perfect linearizability
    but catches the common 'pop from another thread' KeyError."""
    runners = [_stub_runner() for _ in range(20)]
    sessions = [f"sess-{i}" for i in range(20)]

    def worker(i):
        rr.register(sessions[i], runners[i])
        rr.cancel(sessions[i])
        rr.unregister(sessions[i], runners[i])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert rr.active_session_count() == 0
    assert all(r.cancel.call_count >= 1 for r in runners)

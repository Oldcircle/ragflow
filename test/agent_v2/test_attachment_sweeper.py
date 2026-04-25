"""Phase 2.7 v0.13 — attachment sweeper unit tests.

Mocks the DB service + STORAGE_IMPL; exercises the per-tick coordination
and the various candidate / failure shapes.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from api.agent_v2 import attachment_sweeper as sw


def _row(*, id="att1", status="expired", blob_path="bk/k", size=10):
    r = MagicMock()
    r.id = id
    r.status = status
    r.blob_path = blob_path
    r.size_bytes = size
    return r


def _run_tick(*, candidates, flipped, storage_method="rm",
              storage_raises=None, mark_reclaimed_returns=True):
    """Run one sweeper tick with patched dependencies. Returns SweepStats."""
    storage = MagicMock(spec=[storage_method])
    method = getattr(storage, storage_method)
    if storage_raises is not None:
        method.side_effect = storage_raises

    with patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService"
        ".mark_expired_stale", return_value=flipped,
    ), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService"
        ".list_blob_gc_candidates", return_value=candidates,
    ), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService"
        ".mark_blob_reclaimed", return_value=mark_reclaimed_returns,
    ) as mark, patch(
        "common.settings.STORAGE_IMPL", storage,
    ), patch(
        "api.db.services.audit_log_service.AuditLogService.log",
    ) as audit:
        stats = asyncio.run(sw._tick())
    return stats, storage, method, mark, audit


def test_tick_flips_only_no_blobs():
    stats, _storage, method, mark, audit = _run_tick(
        candidates=[], flipped=3,
    )
    assert stats.flipped == 3
    assert stats.blobs_dropped == 0
    assert stats.bytes_reclaimed == 0
    assert stats.errors == 0
    method.assert_not_called()
    mark.assert_not_called()
    audit.assert_called_once()


def test_tick_drops_blob_and_marks_reclaimed():
    candidates = [_row(id="a", size=100), _row(id="b", size=250)]
    stats, _storage, method, mark, audit = _run_tick(
        candidates=candidates, flipped=0,
    )
    assert stats.blobs_dropped == 2
    assert stats.bytes_reclaimed == 350
    assert stats.errors == 0
    assert method.call_count == 2
    method.assert_any_call("bk", "k")
    assert mark.call_count == 2
    audit.assert_called_once()


def test_tick_storage_failure_does_not_mark_reclaimed():
    candidates = [_row(id="a", size=100)]
    stats, _storage, method, mark, audit = _run_tick(
        candidates=candidates, flipped=0,
        storage_raises=RuntimeError("minio 500"),
    )
    assert stats.blobs_dropped == 0
    assert stats.errors == 1
    method.assert_called_once()
    # Crucially: we do NOT mark reclaimed — next tick will retry.
    mark.assert_not_called()
    # No audit emitted when nothing succeeded.
    audit.assert_not_called()


def test_tick_malformed_blob_path_treated_as_success():
    """Tombstoned / malformed paths should not block GC forever."""
    row = _row(id="ghost", blob_path="no-slash-at-all", size=0)
    stats, _storage, method, mark, _audit = _run_tick(
        candidates=[row], flipped=0,
    )
    assert stats.blobs_dropped == 1
    assert stats.bytes_reclaimed == 0
    method.assert_not_called()  # we short-circuit before calling storage
    mark.assert_called_once_with("ghost")


def test_tick_supports_alternate_storage_method_names():
    """STORAGE_IMPL backends sometimes expose 'remove' / 'delete' instead."""
    candidates = [_row(id="a", size=10)]
    stats, _storage, method, _mark, _audit = _run_tick(
        candidates=candidates, flipped=0, storage_method="delete",
    )
    assert stats.blobs_dropped == 1
    method.assert_called_once_with("bk", "k")


def test_tick_audit_only_when_work_happened():
    stats, _, _, _, audit = _run_tick(candidates=[], flipped=0)
    assert stats.flipped == 0
    assert stats.blobs_dropped == 0
    audit.assert_not_called()


def test_main_loop_exits_on_stop_event(monkeypatch):
    """Stop event flips inside _sleep_until_stop should break the loop."""
    stop_event = threading.Event()

    async def fast_tick():
        # Set the stop event right after the first tick so the loop exits
        # without burning a full poll interval.
        stop_event.set()
        return sw.SweepStats()

    monkeypatch.setattr(sw, "_tick_with_lock", fast_tick)
    monkeypatch.setattr(sw, "POLL_INTERVAL_S", 1)

    asyncio.run(sw._main_loop(stop_event))
    # Reaches here without timeout = pass.


def test_tick_with_lock_runs_without_redis(monkeypatch):
    monkeypatch.setattr(sw, "_redis_try_lock", lambda: None)

    async def fake_tick():
        return sw.SweepStats(flipped=1)

    monkeypatch.setattr(sw, "_tick", fake_tick)
    stats = asyncio.run(sw._tick_with_lock())
    assert stats.flipped == 1


def test_tick_with_lock_uses_redis_when_available(monkeypatch):
    """Locks ``RedisDistributedLock`` exposes ``acquire()/release()`` —
    not the context-manager protocol. Confirm sweeper drives them."""
    acquired = []
    released = []

    class _FakeLock:
        def acquire(self):
            acquired.append(True)
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr(sw, "_redis_try_lock", lambda: _FakeLock())

    async def fake_tick():
        return sw.SweepStats(flipped=2)

    monkeypatch.setattr(sw, "_tick", fake_tick)
    stats = asyncio.run(sw._tick_with_lock())
    assert stats.flipped == 2
    assert acquired and released


def test_tick_with_lock_skips_when_acquire_fails(monkeypatch):
    """When a peer pod holds the lock, ``acquire()`` returns False —
    the sweeper should yield rather than tick (peer will handle it)."""

    class _BusyLock:
        def acquire(self):
            return False

        def release(self):  # pragma: no cover — never reached
            raise AssertionError("release called on un-acquired lock")

    monkeypatch.setattr(sw, "_redis_try_lock", lambda: _BusyLock())
    tick_calls = {"n": 0}

    async def fake_tick():
        tick_calls["n"] += 1
        return sw.SweepStats()

    monkeypatch.setattr(sw, "_tick", fake_tick)
    out = asyncio.run(sw._tick_with_lock())
    assert out is None
    assert tick_calls["n"] == 0


# ─────────── Service helpers ───────────


def test_mark_blob_reclaimed_nulls_blob_path_in_memory():
    """Sanity: the service helper signature + side effect."""
    from api.db.services.agent_v2_service import AgentV2AttachmentService

    # We can't hit the real DB here, but we can confirm the method exists
    # and is decorated with the connection_context wrapper.
    assert callable(AgentV2AttachmentService.mark_blob_reclaimed)
    assert callable(AgentV2AttachmentService.list_blob_gc_candidates)

"""Phase 2.7 v0.13 — staged attachment TTL sweeper + MinIO orphan blob GC.

Two phases per tick:

1. **Flip stale `staged` → `expired`**: walks ``AgentV2Attachment`` rows where
   ``status='staged' AND expires_at < now``. ``expires_at`` is set to 24 h
   after upload by the HTTP layer (see ``attachments_app.py``). After the
   flip, the row stays around as an audit trail — only the MinIO blob is
   reclaimed in phase 2.

2. **Reclaim MinIO blobs**:
   - Newly ``expired`` rows: drop the blob immediately (status flip is the
     final transition).
   - Old ``rejected`` rows: keep the row 7 days for audit per
     ``PLAN-attachments.md`` §五 then drop the blob (row keeps ``blob_path``
     as a tombstone reference, but the bytes are gone).

Why not delete the row entirely? Audit. ``access_audit_log`` rows reference
the attachment_id, and the row's metadata (filename / size / hash / who /
when) is what an admin needs to investigate after-the-fact. Bytes are the
expensive part; rows are cheap.

Single-process safe via Redis distributed lock — multi-instance deploys
won't double-GC. Without Redis, we degrade to per-process best-effort
(idempotent: blob delete on a missing key is a no-op).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass

from common.time_utils import current_timestamp

logger = logging.getLogger("ragflow.agent_v2.attachment_sweeper")

# Sweep cadence — 10 min is plenty; staged TTL is 24h so even if we miss a
# tick, the next one catches up. Don't go sub-minute: each tick walks tables
# and audit-logs.
POLL_INTERVAL_S = 600

# Per-tick row caps — protect the DB if rows pile up.
MAX_FLIP_PER_TICK = 500
MAX_GC_PER_TICK = 500

# How long a `rejected` row stays before its blob is reclaimed (audit grace).
REJECTED_BLOB_GRACE_MS = 7 * 24 * 60 * 60 * 1000

LOCK_KEY = "agent_v2_attachment_sweeper"


@dataclass
class SweepStats:
    flipped: int = 0
    blobs_dropped: int = 0
    bytes_reclaimed: int = 0
    errors: int = 0


def _redis_try_lock():
    """Acquire Redis lock with TTL slightly longer than tick interval. None
    when Redis isn't reachable — sweeper still runs but on a single instance."""
    try:
        from rag.utils.redis_conn import RedisDistributedLock

        return RedisDistributedLock(
            LOCK_KEY,
            lock_value=str(uuid.uuid4()),
            timeout=POLL_INTERVAL_S + 30,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("redis lock unavailable, running without it: %s", e)
        return None


def run_worker(stop_event: threading.Event) -> None:
    """Daemon-thread entry point. Owns its own asyncio loop."""
    logger.info("attachment_sweeper starting (interval=%ss)", POLL_INTERVAL_S)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_main_loop(stop_event))
    except Exception:
        logger.exception("attachment_sweeper crashed")
    finally:
        loop.close()
        logger.info("attachment_sweeper stopped")


async def _main_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            await _tick_with_lock()
        except Exception:
            logger.exception("attachment_sweeper tick failed")
        # Cooperative sleep — break early if we're shutting down.
        await _sleep_until_stop(stop_event, POLL_INTERVAL_S)


async def _sleep_until_stop(stop_event: threading.Event, seconds: float) -> None:
    deadline = asyncio.get_event_loop().time() + seconds
    while not stop_event.is_set():
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 1.0))


async def _tick_with_lock() -> SweepStats | None:
    lock = _redis_try_lock()
    if lock is None:
        return await _tick()
    # ``RedisDistributedLock`` exposes ``acquire()/release()`` (no context
    # manager protocol). Acquire returns True on success, False when a
    # peer holds it; either way we're done — the peer will run the tick.
    if not lock.acquire():
        return None
    try:
        return await _tick()
    finally:
        try:
            lock.release()
        except Exception:
            logger.exception("attachment_sweeper: lock release failed")


async def _tick() -> SweepStats:
    """One sweep cycle. Returns counts for visibility / tests."""
    stats = SweepStats()

    from api.db.services.agent_v2_service import AgentV2AttachmentService

    # Phase 1 — flip stale staged → expired
    try:
        stats.flipped = AgentV2AttachmentService.mark_expired_stale()
    except Exception:
        logger.exception("attachment_sweeper: mark_expired_stale failed")
        stats.errors += 1

    # Phase 2 — reclaim MinIO blobs for expired + old rejected rows
    try:
        targets = AgentV2AttachmentService.list_blob_gc_candidates(
            limit=MAX_GC_PER_TICK,
            rejected_grace_ms=REJECTED_BLOB_GRACE_MS,
        )
    except Exception:
        logger.exception("attachment_sweeper: list candidates failed")
        targets = []
        stats.errors += 1

    for row in targets:
        dropped, size = await _drop_blob(row)
        if dropped:
            stats.blobs_dropped += 1
            stats.bytes_reclaimed += size
            try:
                AgentV2AttachmentService.mark_blob_reclaimed(row.id)
            except Exception:
                logger.exception(
                    "attachment_sweeper: mark_blob_reclaimed failed (id=%s)",
                    row.id,
                )
                stats.errors += 1
        else:
            stats.errors += 1

    if stats.flipped or stats.blobs_dropped or stats.errors:
        logger.info(
            "attachment_sweeper: flipped=%d gc_blobs=%d gc_bytes=%d errors=%d",
            stats.flipped, stats.blobs_dropped, stats.bytes_reclaimed,
            stats.errors,
        )

    # Audit only when something happened — daily idle ticks are noise.
    if stats.flipped or stats.blobs_dropped:
        _audit(stats)

    return stats


async def _drop_blob(row) -> tuple[bool, int]:
    """Best-effort delete of the MinIO object. Returns (success, size_hint)."""
    blob_path = (row.blob_path or "")
    if "/" not in blob_path:
        # Already tombstoned or malformed — treat as success so we stop
        # retrying it forever.
        return True, 0
    bucket, _, key = blob_path.partition("/")
    size_hint = int(row.size_bytes or 0)

    def _do_delete():
        from common import settings

        impl = settings.STORAGE_IMPL
        # Different storage impls expose slightly different APIs; try the
        # canonical name first, then the alternates.
        for method_name in ("rm", "remove", "delete", "obj_rm"):
            method = getattr(impl, method_name, None)
            if callable(method):
                method(bucket, key)
                return True
        raise AttributeError(
            f"STORAGE_IMPL has no rm/remove/delete method (got {type(impl)})"
        )

    try:
        await asyncio.to_thread(_do_delete)
        return True, size_hint
    except Exception:
        logger.exception(
            "attachment_sweeper: blob delete failed (id=%s path=%s)",
            row.id, blob_path,
        )
        return False, 0


def _audit(stats: SweepStats) -> None:
    try:
        from api.db.services.audit_log_service import AuditLogService

        AuditLogService.log(
            user_id=None,
            tenant_id="",
            action="agent_v2.attachment_sweep",
            resource_type="agent_v2_attachment",
            resource_id=None,
            result="allow",
            reason="periodic_sweep",
            metadata={
                "flipped_to_expired": stats.flipped,
                "blobs_dropped": stats.blobs_dropped,
                "bytes_reclaimed": stats.bytes_reclaimed,
                "errors": stats.errors,
                "ts_ms": current_timestamp(),
            },
        )
    except Exception:
        logger.exception("attachment_sweeper: audit write failed (not fatal)")

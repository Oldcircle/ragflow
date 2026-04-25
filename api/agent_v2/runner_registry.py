"""Process-wide registry of active AgentRunners (Phase 2.7 v0.21).

Powers the HTTP cancel endpoint: when the user clicks "stop" in the UI,
the frontend POSTs to ``/v1/agent_v2/session/<id>/cancel``; the handler
looks up the matching runner here and calls ``runner.cancel()`` (v0.20
cooperative abort signal).

Multi-instance caveat:
The registry is in-process. If session ``S`` is being served by pod A and
the cancel POST hits pod B, B can't see A's runner — it returns
``not_found`` and the user's first stop click looks like a no-op. For
single-pod local dev this is fine; production multi-pod deployment will
need a Redis pub/sub layer (filed as a follow-up). The cancel call is
still always safe — registering twice on the same session_id replaces
the old entry; cancelling a non-existent session is a no-op.

Thread / loop safety:
``register`` / ``unregister`` / ``cancel`` are sync methods using an
``threading.Lock`` (not asyncio.Lock) so they're safe to call from
either an asyncio handler OR a Flask thread. ``runner.cancel()`` itself
is implemented as ``Event.set()`` which is thread-safe per Python docs.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import AgentRunner

logger = logging.getLogger("ragflow.agent_v2.runner_registry")

_LOCK = threading.Lock()
_RUNNERS: dict[str, "AgentRunner"] = {}


def register(session_id: str, runner: "AgentRunner") -> None:
    """Bind a runner to its session_id. Replaces any existing entry —
    a session running concurrently in the same process is unsupported,
    and the latest run wins."""
    if not session_id:
        return
    with _LOCK:
        existing = _RUNNERS.get(session_id)
        if existing is not None and existing is not runner:
            # Defensive: the previous run for this session is still in
            # the registry. Probably a bug elsewhere (forgot to
            # unregister), but cancel its tools so it stops eating budget.
            logger.warning(
                "runner_registry: replacing active runner for session %s; "
                "cancelling the previous one to free up resources",
                session_id,
            )
            try:
                existing.cancel()
            except Exception:
                logger.exception("runner_registry: failed to cancel stale runner")
        _RUNNERS[session_id] = runner


def unregister(session_id: str, runner: "AgentRunner | None" = None) -> None:
    """Drop the entry. If ``runner`` is provided, only drop when the
    registered runner is the same instance — protects against a late
    unregister from an old run racing with a fresh registration on the
    same session."""
    if not session_id:
        return
    with _LOCK:
        cur = _RUNNERS.get(session_id)
        if cur is None:
            return
        if runner is not None and cur is not runner:
            return
        _RUNNERS.pop(session_id, None)


def cancel(session_id: str) -> bool:
    """Look up the runner for ``session_id`` and call ``cancel()`` on it.

    Returns True when a runner was found and cancel was attempted, False
    when no run is active for that session in this process."""
    if not session_id:
        return False
    with _LOCK:
        runner = _RUNNERS.get(session_id)
    if runner is None:
        return False
    try:
        runner.cancel()
        return True
    except Exception:
        logger.exception(
            "runner_registry: cancel raised for session %s", session_id,
        )
        return False


def is_active(session_id: str) -> bool:
    """Returns True iff there's a runner currently registered for the
    session in this process."""
    if not session_id:
        return False
    with _LOCK:
        return session_id in _RUNNERS


def active_session_count() -> int:
    """Snapshot the number of active runs for monitoring / health
    endpoints. O(1)."""
    with _LOCK:
        return len(_RUNNERS)


def _clear_for_tests() -> None:
    """Test helper — wipe state between cases."""
    with _LOCK:
        _RUNNERS.clear()

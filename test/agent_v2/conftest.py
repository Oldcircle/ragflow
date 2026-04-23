"""Agent v2 测试套件共用 fixture / 环境预热。

Pre-import xgboost in a permissive warning context: pyproject.toml sets
``filterwarnings = ["error", ...]`` which upgrades any UserWarning to an
exception during test collection. xgboost's ``compat.py`` emits a UserWarning
on ``import pkg_resources``, which then aborts the import statement itself
and leaves ``pkg_resources`` unbound two lines later (``NameError``). Warming
xgboost here — before agent_v2 tests try to import chains that transitively
load xgboost — sidesteps the issue entirely.
"""

from __future__ import annotations

import asyncio
import contextlib
import warnings as _warnings

import pytest

_TRACKED_LOOPS: set[asyncio.AbstractEventLoop] = set()


class _TrackingEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    """Event-loop policy that lets tests close loops created by pytest-asyncio."""

    def new_event_loop(self):
        loop = super().new_event_loop()
        _TRACKED_LOOPS.add(loop)
        return loop


_ORIGINAL_EVENT_LOOP_POLICY = asyncio.get_event_loop_policy()
_TRACKING_EVENT_LOOP_POLICY = _TrackingEventLoopPolicy()
asyncio.set_event_loop_policy(_TRACKING_EVENT_LOOP_POLICY)


@pytest.fixture(scope="session")
def event_loop_policy():
    return _TRACKING_EVENT_LOOP_POLICY


@pytest.fixture(scope="session", autouse=True)
def _close_tracked_event_loops_at_end():
    yield
    _close_tracked_event_loops()


with _warnings.catch_warnings():
    _warnings.simplefilter("ignore")
    try:
        import xgboost  # noqa: F401
    except Exception:  # pragma: no cover
        # 真机没装 xgboost 也能继续跑；只有碰 rag pipeline 的测试受影响
        pass


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Close pytest-asyncio's lazily-created default loop.

    In Python 3.12, ``asyncio.get_event_loop()`` creates a selector loop with a
    socketpair. pytest-asyncio may create that loop while swapping event-loop
    policies, then leave it as the process default. The individual async tests
    still pass, but pytest's unraisable-exception collector later treats the
    loop/socket ResourceWarning as an error because this repo uses
    ``filterwarnings = ["error"]``.
    """
    _close_tracked_event_loops()


def pytest_unconfigure(config):  # noqa: ARG001
    _close_tracked_event_loops()


def _close_tracked_event_loops() -> None:
    for policy in {
        asyncio.get_event_loop_policy(),
        _TRACKING_EVENT_LOOP_POLICY,
        _ORIGINAL_EVENT_LOOP_POLICY,
    }:
        _close_policy_default_loop(policy)
    for loop in list(_TRACKED_LOOPS):
        with contextlib.suppress(Exception):
            if not loop.is_closed() and not loop.is_running():
                loop.close()
    _TRACKED_LOOPS.clear()


def _close_policy_default_loop(policy: asyncio.AbstractEventLoopPolicy) -> None:
    """Close a policy-held default loop without creating a fresh one."""
    with contextlib.suppress(Exception):
        local = getattr(policy, "_local", None)
        loop = getattr(local, "_loop", None)
        if loop and not loop.is_closed() and not loop.is_running():
            loop.close()
    with contextlib.suppress(Exception):
        policy.set_event_loop(None)

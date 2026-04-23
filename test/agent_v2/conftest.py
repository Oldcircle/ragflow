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

import warnings as _warnings

with _warnings.catch_warnings():
    _warnings.simplefilter("ignore")
    try:
        import xgboost  # noqa: F401
    except Exception:  # pragma: no cover
        # 真机没装 xgboost 也能继续跑；只有碰 rag pipeline 的测试受影响
        pass

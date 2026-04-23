"""Phase 2.5.3 — Agent Definition Registry。

集中注册所有已知 ``AgentDefinition``。运行时由：

1. ``built_in/*.py``：每个文件 export 一个 ``DEFINITION``（或 ``DEFINITIONS``
   list），本模块 import 时自动发现（参考 claude-code-ref
   ``loadAgentsDir.ts`` 的按目录加载思路）
2. 未来：``agent_v2_definition`` 表里的 tenant-custom 定义（Phase 3）

用法::

    from api.agent_v2.definitions.registry import (
        get_definition, list_definitions,
    )
    defn = get_definition("sub_policy_researcher")
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Iterable

from .schema import AgentDefinition

logger = logging.getLogger("ragflow.agent_v2.definitions.registry")


_registry: dict[str, AgentDefinition] = {}
_loaded = False


def _load_built_in() -> None:
    """扫 ``built_in/`` 目录，自动收集每个模块里的 ``DEFINITION`` /
    ``DEFINITIONS``。
    """
    global _loaded
    if _loaded:
        return
    try:
        from . import built_in as pkg
    except ImportError:
        logger.warning("built_in package missing; no definitions loaded")
        _loaded = True
        return

    for _, mod_name, _ in pkgutil.iter_modules(pkg.__path__):
        full = f"{pkg.__name__}.{mod_name}"
        try:
            m = importlib.import_module(full)
        except Exception:
            logger.exception("failed to import %s", full)
            continue
        # 支持两种 export 风格
        defn: AgentDefinition | None = getattr(m, "DEFINITION", None)
        defns: Iterable[AgentDefinition] | None = getattr(m, "DEFINITIONS", None)
        if defn is not None:
            _register(defn, source=full)
        if defns:
            for d in defns:
                _register(d, source=full)
    _loaded = True


def _register(defn: AgentDefinition, *, source: str) -> None:
    if not isinstance(defn, AgentDefinition):
        logger.warning("%s exported non-AgentDefinition; skipping", source)
        return
    if defn.name in _registry:
        # 允许后加载覆盖（DB 自定义覆盖内置），但内置之间不能重名
        logger.info("overriding existing definition %s from %s", defn.name, source)
    _registry[defn.name] = defn


def get_definition(name: str) -> AgentDefinition | None:
    _load_built_in()
    return _registry.get(name)


def list_definitions(kind: str | None = None) -> list[AgentDefinition]:
    _load_built_in()
    out = list(_registry.values())
    if kind:
        out = [d for d in out if d.kind == kind]
    # 稳定排序：先 category，再 name
    out.sort(key=lambda d: (d.category, d.name))
    return out


def clear_cache_for_tests() -> None:
    """测试专用：重置模块级缓存。生产勿调。"""
    global _loaded
    _registry.clear()
    _loaded = False


__all__ = [
    "clear_cache_for_tests",
    "get_definition",
    "list_definitions",
]

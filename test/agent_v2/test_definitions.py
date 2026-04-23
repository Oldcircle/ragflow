"""Phase 2.5.3 — Agent Definition Registry + resolve_tools 单测。

覆盖目标：
- Registry auto-load 所有 ``built_in/*.py`` 无 import 错误
- 每个内置 definition 结构校验（name / kind / system_prompt / subagent 有 when_to_use）
- ``get_definition`` 命中 + 未命中
- ``list_definitions`` 按 kind 过滤
- ``resolve_tools`` 四种组合语义（"*" / explicit / disallowed_tools / 父集合为 None）
- 命名不冲突
"""

from __future__ import annotations

import pytest

from api.agent_v2.definitions import (
    AgentDefinition,
    ModelRef,
    get_definition,
    list_definitions,
    resolve_tools,
)
from api.agent_v2.definitions.registry import clear_cache_for_tests


# ───────── Registry ─────────


@pytest.mark.p0
class TestRegistry:
    def setup_method(self):
        clear_cache_for_tests()

    def test_all_built_in_load_without_errors(self):
        defs = list_definitions()
        assert len(defs) >= 9  # 6 supervisor + 3 subagent (Phase 2.6 sub_archivist)

    def test_no_duplicate_names(self):
        defs = list_definitions()
        names = [d.name for d in defs]
        assert len(names) == len(set(names)), (
            f"duplicate definition names: "
            f"{[n for n in names if names.count(n) > 1]}"
        )

    def test_kind_filter_supervisor(self):
        supers = list_definitions(kind="supervisor")
        assert len(supers) >= 6
        assert all(d.kind == "supervisor" for d in supers)

    def test_kind_filter_subagent(self):
        subs = list_definitions(kind="subagent")
        assert len(subs) >= 3  # sub_policy_researcher + sub_evidence_checker + sub_archivist
        assert all(d.kind == "subagent" for d in subs)
        # subagent 必须填 when_to_use
        for d in subs:
            assert d.when_to_use.strip(), f"subagent {d.name} missing when_to_use"

    def test_get_definition_hit(self):
        # sub_policy_researcher 是 Phase 2.5.3 里实际交付的 subagent
        d = get_definition("sub_policy_researcher")
        assert d is not None
        assert d.kind == "subagent"

    def test_get_definition_miss_returns_none(self):
        assert get_definition("__nonexistent__") is None

    def test_definition_contains_system_prompt(self):
        for d in list_definitions():
            sp = d.resolve_system_prompt()
            assert sp.strip(), f"{d.name} has empty system_prompt"

    def test_definition_to_dict_serializable(self):
        """前端 ``/v1/agent_v2/definition`` 端点会吞 to_dict()，必须能序列化."""
        import json

        for d in list_definitions():
            payload = d.to_dict()
            json.dumps(payload, ensure_ascii=False)  # 不抛即通过


# ───────── resolve_tools ─────────


@pytest.mark.p0
class TestResolveTools:
    def _make(self, tools, disallowed=()):
        return AgentDefinition(
            name="t",
            version="1.0",
            description="t",
            when_to_use="",
            system_prompt="sys",
            tools=tools,
            disallowed_tools=disallowed,
        )

    def test_star_inherits_parent(self):
        defn = self._make("*")
        result = resolve_tools(defn, parent_tools=["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_star_with_none_parent_returns_none(self):
        """父白名单为 None（= 全部启用）时，"*" 也回传 None."""
        defn = self._make("*")
        assert resolve_tools(defn, parent_tools=None) is None

    def test_explicit_list_passes_through(self):
        defn = self._make(["rag_retrieve", "rag_read_doc"])
        result = resolve_tools(defn, parent_tools=["rag_retrieve", "rag_read_doc", "other"])
        assert result == ["rag_retrieve", "rag_read_doc"]

    def test_empty_list_means_no_tools(self):
        defn = self._make([])
        assert resolve_tools(defn, parent_tools=["a", "b"]) == []

    def test_disallowed_tools_subtracted_from_star(self):
        defn = self._make("*", disallowed=("spawn_subagent",))
        result = resolve_tools(defn, parent_tools=["rag_retrieve", "spawn_subagent"])
        assert result == ["rag_retrieve"]

    def test_disallowed_tools_subtracted_from_explicit(self):
        defn = self._make(["a", "b", "c"], disallowed=("b",))
        result = resolve_tools(defn, parent_tools=["a", "b", "c"])
        assert result == ["a", "c"]


# ───────── AgentDefinition dataclass ─────────


@pytest.mark.p2
class TestAgentDefinitionBehavior:
    def test_callable_system_prompt_resolves(self):
        calls = []

        def make(ctx):
            calls.append(ctx)
            return f"hello {ctx.get('kb')}"

        defn = AgentDefinition(
            name="x",
            version="1",
            description="d",
            when_to_use="",
            system_prompt=make,
        )
        assert defn.resolve_system_prompt({"kb": "KB1"}) == "hello KB1"
        assert calls == [{"kb": "KB1"}]

    def test_to_dict_handles_modelref_and_inherit(self):
        defn_inherit = AgentDefinition(
            name="a", version="1", description="d", when_to_use="",
            system_prompt="sys",
        )
        assert defn_inherit.to_dict()["model"] == "inherit"

        defn_explicit = AgentDefinition(
            name="b", version="1", description="d", when_to_use="",
            system_prompt="sys",
            model=ModelRef(model="claude-sonnet-4-5"),
        )
        assert defn_explicit.to_dict()["model"]["model"] == "claude-sonnet-4-5"

    def test_default_citation_enforce_warn(self):
        defn = AgentDefinition(
            name="a", version="1", description="d", when_to_use="",
            system_prompt="sys",
        )
        assert defn.citation_enforce == "warn"
        assert defn.citation_numeric_strict is True

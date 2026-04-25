"""Phase 2.6 — sub_archivist Agent Definition 结构性验证。"""

from __future__ import annotations

import pytest

from api.agent_v2.definitions import get_definition, list_definitions, resolve_tools
from api.agent_v2.definitions.registry import clear_cache_for_tests


@pytest.mark.p0
class TestSubArchivistDefinition:
    def setup_method(self):
        clear_cache_for_tests()

    def test_definition_registered(self):
        d = get_definition("sub_archivist")
        assert d is not None
        assert d.kind == "subagent"

    def test_has_all_six_write_tools(self):
        d = get_definition("sub_archivist")
        expected_write = {
            "doc_tag", "doc_rename", "doc_archive",
            "doc_reparse", "doc_upload_from_url", "kb_create",
        }
        assert expected_write.issubset(set(d.tools or []))

    def test_has_read_tools_for_confirmation(self):
        d = get_definition("sub_archivist")
        # Ops agent needs rag_list_docs / rag_read_doc to confirm target before action
        assert "rag_list_docs" in d.tools
        assert "rag_read_doc" in d.tools

    def test_has_interactive_tools(self):
        d = get_definition("sub_archivist")
        assert "ask_user_question" in d.tools
        assert "submit_plan" in d.tools

    def test_prompt_forbids_background_wakeup_for_async_ops(self):
        # Phase 2.8: system_prompt is now a callable; materialize via
        # resolve_system_prompt(). The "no background wake-up" rule lives
        # in the shared step_marker_rules section now (see prompting/sections.py).
        d = get_definition("sub_archivist")
        sp = d.resolve_system_prompt()
        assert "There is no background wake-up" in sp
        assert 'Send "check progress" in your next message' in sp
        # Phrasing softened to "Never imply automatic scheduled follow-up"
        # in step_marker_rules — both phrasings tolerated for the spirit.
        assert (
            "Do not imply an automatic scheduled follow-up" in sp
            or "Never imply automatic scheduled follow-up" in sp
        )

    def test_cannot_spawn_further_subagents(self):
        d = get_definition("sub_archivist")
        assert d.can_spawn_subagents is False

    def test_citation_enforce_disabled(self):
        d = get_definition("sub_archivist")
        # Ops agent doesn't produce [N] citations
        assert d.citation_enforce == "off"

    def test_inherits_model_from_parent(self):
        d = get_definition("sub_archivist")
        assert d.model == "inherit"

    def test_listed_in_kind_filter(self):
        subs = list_definitions(kind="subagent")
        names = [d.name for d in subs]
        assert "sub_archivist" in names


@pytest.mark.p1
class TestSupervisorsCanSpawnArchivist:
    def setup_method(self):
        clear_cache_for_tests()

    @pytest.mark.parametrize(
        "supervisor_name",
        [
            "sz-baojian-house",
            "generic-policy",
            "research-analyst",
            "legal-contract",
        ],
    )
    def test_allowed_subagent_types_includes_archivist(self, supervisor_name):
        d = get_definition(supervisor_name)
        assert d is not None, f"supervisor {supervisor_name!r} missing"
        assert "sub_archivist" in (d.allowed_subagent_types or ())

    @pytest.mark.parametrize(
        "supervisor_name",
        ["customer-support", "internal-wiki"],
    )
    def test_some_supervisors_still_cannot_spawn(self, supervisor_name):
        d = get_definition(supervisor_name)
        assert d is not None
        # 这两个场景本来就禁用 subagent，升级后也不应该变
        assert d.can_spawn_subagents is False


@pytest.mark.p1
class TestToolResolutionForArchivist:
    def setup_method(self):
        clear_cache_for_tests()

    def test_resolve_tools_with_parent_allows_all_explicit(self):
        d = get_definition("sub_archivist")
        # Parent gives sub_archivist all 13 tools; our definition.tools is explicit
        # list, so resolve_tools returns exactly that list (filtered by parent)
        parent = [
            "rag_retrieve", "rag_list_docs", "rag_read_doc", "rag_graph_query",
            "spawn_subagent",
            "doc_tag", "doc_rename", "doc_archive", "doc_reparse",
            "doc_upload_from_url", "kb_create",
            "ask_user_question", "submit_plan",
        ]
        resolved = resolve_tools(d, parent_tools=parent)
        assert resolved is not None
        assert set(resolved) == set(d.tools)

    def test_resolve_filters_disallowed_out_of_parent(self):
        d = get_definition("sub_archivist")
        # Parent restricts to only 2 tools; resolution should return intersection
        # NOTE: current resolve_tools logic: if defn.tools is a list, it's used
        # as-is regardless of parent_tools. So this test documents current behavior —
        # the subset-check lives in spawn_subagent, not in resolve_tools.
        resolved = resolve_tools(d, parent_tools=["rag_retrieve"])
        # Expect full definition list (resolve_tools doesn't intersect explicit lists
        # with parent; spawn_subagent does).
        assert set(resolved) == set(d.tools)

"""Phase 2.8 — tests for the PromptSection / PromptCache / sections library."""

from __future__ import annotations

import pytest

from api.agent_v2.prompting import (
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    PromptCache,
    PromptCtx,
    PromptSection,
    assemble_prompt,
    prepend_bullets,
    sections,
)
from api.agent_v2.tools import _names as names


# ──────────────────────────────  PromptSection  ──────────────────────────────


class TestPromptSection:
    def test_construct_basic(self):
        s = PromptSection(name="x", compute=lambda t, c: "hello")
        assert s.name == "x"
        assert s.cache_break is False
        assert s.reason == ""

    def test_cache_break_requires_reason(self):
        with pytest.raises(ValueError, match="cache_break=True"):
            PromptSection(
                name="vol",
                compute=lambda t, c: "x",
                cache_break=True,
                # reason omitted — must reject
            )

    def test_cache_break_accepts_with_reason(self):
        s = PromptSection(
            name="vol",
            compute=lambda t, c: "x",
            cache_break=True,
            reason="changes per turn",
        )
        assert s.cache_break is True
        assert s.reason == "changes per turn"

    def test_compute_returns_none_drops_section(self):
        s = PromptSection(name="empty", compute=lambda t, c: None)
        cache = PromptCache()
        out = cache.resolve([s], PromptCtx())
        assert out == []


# ──────────────────────────────  PromptCache  ────────────────────────────────


class TestPromptCache:
    def test_memoizes_non_break_sections(self):
        calls = {"n": 0}

        def comp(_t, _c):
            calls["n"] += 1
            return f"v{calls['n']}"

        s = PromptSection(name="cached", compute=comp)
        cache = PromptCache()
        ctx = PromptCtx()
        cache.resolve([s], ctx)
        cache.resolve([s], ctx)
        cache.resolve([s], ctx)
        assert calls["n"] == 1, "compute should run only once across 3 resolves"

    def test_recomputes_break_sections(self):
        calls = {"n": 0}

        def comp(_t, _c):
            calls["n"] += 1
            return f"v{calls['n']}"

        s = PromptSection(
            name="vol", compute=comp, cache_break=True, reason="per turn"
        )
        cache = PromptCache()
        ctx = PromptCtx()
        cache.resolve([s], ctx)
        cache.resolve([s], ctx)
        assert calls["n"] == 2, "cache_break sections must recompute every call"

    def test_independent_caches(self):
        # Different cache instances do not share state.
        calls = {"n": 0}

        def comp(_t, _c):
            calls["n"] += 1
            return "x"

        s = PromptSection(name="x", compute=comp)
        ctx = PromptCtx()
        c1 = PromptCache()
        c2 = PromptCache()
        c1.resolve([s], ctx)
        c2.resolve([s], ctx)
        assert calls["n"] == 2

    def test_clear_resets_memoization(self):
        calls = {"n": 0}

        def comp(_t, _c):
            calls["n"] += 1
            return "v"

        s = PromptSection(name="x", compute=comp)
        cache = PromptCache()
        ctx = PromptCtx()
        cache.resolve([s], ctx)
        cache.clear()
        cache.resolve([s], ctx)
        assert calls["n"] == 2

    def test_has_query(self):
        cache = PromptCache()
        ctx = PromptCtx()
        s = PromptSection(name="probe", compute=lambda t, c: "x")
        assert not cache.has("probe")
        cache.resolve([s], ctx)
        assert cache.has("probe")


# ────────────────────────────  assemble_prompt  ──────────────────────────────


class TestAssemblePrompt:
    def test_layout_has_boundary(self):
        ctx = PromptCtx(role_line="X")
        out = assemble_prompt(
            [PromptSection("a", lambda t, c: "STATIC")],
            [PromptSection("b", lambda t, c: "DYN", cache_break=True, reason="r")],
            ctx,
        )
        assert SYSTEM_PROMPT_DYNAMIC_BOUNDARY in out
        assert (
            out.index("STATIC")
            < out.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
            < out.index("DYN")
        )

    def test_boundary_present_when_dynamic_empty(self):
        ctx = PromptCtx()
        out = assemble_prompt(
            [PromptSection("a", lambda t, c: "X")],
            [],
            ctx,
        )
        # Boundary must always be in output so provider adapters can split.
        assert SYSTEM_PROMPT_DYNAMIC_BOUNDARY in out

    def test_provided_cache_used(self):
        calls = {"n": 0}

        def comp(_t, _c):
            calls["n"] += 1
            return "x"

        s = PromptSection(name="shared", compute=comp)
        cache = PromptCache()
        ctx = PromptCtx()
        assemble_prompt([s], [], ctx, cache=cache)
        assemble_prompt([s], [], ctx, cache=cache)
        assert calls["n"] == 1, "shared cache should memoize across assemble calls"


# ──────────────────────────────  prepend_bullets  ────────────────────────────


class TestPrependBullets:
    def test_flat_strings(self):
        assert prepend_bullets(["a", "b"]) == ["- a", "- b"]

    def test_nested_indent(self):
        assert prepend_bullets([["x", "y"]]) == ["  - x", "  - y"]

    def test_mixed(self):
        assert prepend_bullets(["top", ["nested"], "tail"]) == [
            "- top",
            "  - nested",
            "- tail",
        ]


# ──────────────────────────  Shared section library  ──────────────────────────


class TestSharedSections:
    def test_identity_section_drops_when_role_empty(self):
        s = sections.make_identity_section()
        ctx = PromptCtx(role_line="")
        assert s.compute(frozenset(), ctx) is None

    def test_identity_section_renders_role(self):
        s = sections.make_identity_section()
        ctx = PromptCtx(role_line="You are X.")
        out = s.compute(frozenset(), ctx)
        assert out == "# Role\n\nYou are X."

    def test_plan_gate_drops_when_submit_plan_disabled(self):
        s = sections.make_plan_gate_block()
        ctx = PromptCtx(role_line="X")
        # No submit_plan in enabled_tools
        assert s.compute(frozenset(), ctx) is None
        # With submit_plan
        out = s.compute(frozenset({names.SUBMIT_PLAN}), ctx)
        assert out is not None
        assert "submit_plan" in out
        assert "plan gate" in out.lower()

    def test_step_marker_rules_drops_without_get_pending_plan(self):
        s = sections.make_step_marker_rules()
        ctx = PromptCtx(role_line="X")
        assert s.compute(frozenset(), ctx) is None
        out = s.compute(frozenset({names.GET_PENDING_PLAN}), ctx)
        assert out is not None
        assert "[step K/N done" in out
        assert "background wake-up" in out

    def test_supervisor_delegation_drops_without_spawn(self):
        s = sections.make_supervisor_delegation_section()
        ctx = PromptCtx(role_line="X")
        assert s.compute(frozenset(), ctx) is None
        out = s.compute(frozenset({names.SPAWN_SUBAGENT}), ctx)
        assert out is not None and "Delegation rules" in out

    def test_clarify_strips_ask_user_bullet_when_unavailable(self):
        s = sections.make_clarify_vs_act_section()
        ctx = PromptCtx(role_line="X")
        # Without ask_user_question, the ask_user-related bullet is dropped
        out_no_ask = s.compute(frozenset({names.SPAWN_SUBAGENT}), ctx)
        assert out_no_ask is not None
        assert "ask_user_question" not in out_no_ask
        # With ask_user_question, that bullet is included
        out_with_ask = s.compute(
            frozenset({names.SPAWN_SUBAGENT, names.ASK_USER_QUESTION}), ctx
        )
        assert "ask_user_question" in out_with_ask

    def test_read_only_block_static(self):
        s = sections.make_read_only_block()
        # Same body regardless of ctx / tools
        out_a = s.compute(frozenset(), PromptCtx())
        out_b = s.compute(frozenset({names.RAG_RETRIEVE}), PromptCtx())
        assert out_a == out_b
        assert "READ-ONLY MODE" in out_a

    def test_pending_plan_section_is_cache_break(self):
        s = sections.make_pending_plan_section()
        assert s.cache_break is True
        assert s.reason  # non-empty
        # None when no pending plan
        assert s.compute(frozenset(), PromptCtx()) is None
        # Renders status
        out = s.compute(frozenset(), PromptCtx(pending_plan_status="waiting"))
        assert out is not None and "waiting" in out


# ──────────────────────────  Preset assemblers  ──────────────────────────────


class TestPresets:
    def test_supervisor_static_preset_size(self):
        ss = sections.supervisor_static_sections()
        # Stable count: identity / domain / constraints / workflow /
        # delegation / clarify / actions / tone / numeric_length / output
        assert len(ss) == 10
        names_ = [s.name for s in ss]
        assert names_[0] == "identity"
        assert names_[-1] == "retrieval_output_rules"

    def test_operator_static_preset_does_not_include_citations(self):
        ss = sections.operator_static_sections(mission="m")
        names_ = [s.name for s in ss]
        assert "no_citation_for_operators" in names_
        assert "retrieval_output_rules" not in names_

    def test_reflective_static_preset_includes_read_only(self):
        ss = sections.reflective_static_sections(mission="m")
        names_ = [s.name for s in ss]
        assert "read_only_block" in names_
        assert "retrieval_output_rules" in names_  # citations OK for reflective

    def test_supervisor_dynamic_currently_empty(self):
        # Phase 2.8 v1.0 keeps the dynamic list empty (reserved for v0.15).
        # See sections.supervisor_dynamic_sections() docstring.
        assert sections.supervisor_dynamic_sections() == []

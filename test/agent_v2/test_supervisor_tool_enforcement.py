"""Phase 2.6 v0.6-fix — supervisor tool-whitelist enforcement at session level.

Bug surfaced in live test: supervisor was calling doc_reparse / doc_tag
directly (bypassing spawn_subagent + plan gate) because:

  1. Session was created with ``tool_names=None`` or ``{}`` (frontend didn't
     send a list) → stored that way in DB.
  2. Runner path had ``tool_names=list(session.tool_names) if session.tool_names
     else None`` → falsy session.tool_names → runner gets ``None`` → "enable
     all 18 tools".
  3. Supervisor's AgentDefinition.tools = SUPERVISOR_TOOLS was only consulted
     inside spawn_subagent (for children), NOT at the supervisor level itself.

Architecture rule: the supervisor (top-level agent) must be restricted to
SUPERVISOR_TOOLS (4 read + kb_stats + 3 meta). Writes ONLY reach the session
through spawn_subagent('sub_archivist'). Plan gate + RBAC depend on this.

This module pins:
- Session endpoint falls back to SUPERVISOR_TOOLS when req didn't specify
- Conversation endpoint falls back to SUPERVISOR_TOOLS when session row has
  a stale empty value (protects existing broken sessions)
"""

from __future__ import annotations

import pytest


class TestSupervisorToolsConstant:
    """SUPERVISOR_TOOLS should not include any write-capable tool."""

    def test_has_only_read_and_meta_tools(self):
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        for bad in (
            "doc_tag", "doc_rename", "doc_archive", "doc_reparse",
            "doc_upload_from_url", "kb_create", "doc_create_note",
            "kb_audit", "doc_list_recent_changes", "get_pending_plan",
        ):
            assert bad not in SUPERVISOR_TOOLS, (
                f"SUPERVISOR_TOOLS leaked a write/subagent-only tool: {bad}. "
                "Supervisor must not call destructive or subagent-exclusive "
                "tools directly; they go through spawn_subagent."
            )

    def test_includes_the_safe_eight(self):
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        for need in (
            "rag_retrieve", "rag_list_docs", "rag_read_doc", "rag_graph_query",
            "kb_stats",
            "spawn_subagent", "ask_user_question", "submit_plan",
        ):
            assert need in SUPERVISOR_TOOLS, (
                f"SUPERVISOR_TOOLS missing required safe tool: {need}"
            )

    def test_count_is_eight(self):
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        assert len(SUPERVISOR_TOOLS) == 8, (
            "SUPERVISOR_TOOLS size drift — if you grow the list, revisit "
            "the architectural rationale in PLAN-doc-ops §2 first."
        )


class TestCreateSessionFallback:
    """The pytest harness can't import api.apps.agent_v2_app (blueprint loader),
    so we test the fallback semantics indirectly by building the exact
    expression the endpoint uses.
    """

    @staticmethod
    def _compute(requested):
        """Replicate the endpoint's fallback logic."""
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        if isinstance(requested, list) and requested:
            return requested
        return list(SUPERVISOR_TOOLS)

    def test_empty_dict_falls_back_to_supervisor_tools(self):
        """The exact bug shape: DB held tool_names={}."""
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        result = self._compute({})
        assert result == list(SUPERVISOR_TOOLS)

    def test_none_falls_back_to_supervisor_tools(self):
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        result = self._compute(None)
        assert result == list(SUPERVISOR_TOOLS)

    def test_empty_list_falls_back_to_supervisor_tools(self):
        """Empty list [] is still treated as 'not specified' — fall back
        rather than grant zero tools (which would deadlock the supervisor)."""
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        result = self._compute([])
        assert result == list(SUPERVISOR_TOOLS)

    def test_explicit_list_is_preserved(self):
        """Caller who knows what they want is respected."""
        result = self._compute(["rag_retrieve", "rag_list_docs"])
        assert result == ["rag_retrieve", "rag_list_docs"]

    def test_explicit_list_can_grant_extras(self):
        """Power users (e.g. test/admin) may opt into a larger whitelist."""
        custom = [
            "rag_retrieve", "rag_list_docs", "kb_audit",  # librarian-level read
        ]
        result = self._compute(custom)
        assert result == custom


class TestRunnerPathFallback:
    """Defensive fallback for legacy sessions whose DB row has tool_names=None
    or {} (pre-v0.6-fix). Same expression, different call site."""

    @staticmethod
    def _compute(stored):
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        if isinstance(stored, list) and stored:
            return list(stored)
        return list(SUPERVISOR_TOOLS)

    def test_legacy_empty_dict_session_gets_supervisor_tools(self):
        """The actual DB state we observed: tool_names = {}."""
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        result = self._compute({})
        # Must NOT be None (= all tools) — that's what caused the bug.
        assert result is not None
        assert "doc_reparse" not in result
        assert "doc_tag" not in result
        assert result == list(SUPERVISOR_TOOLS)

    def test_legacy_none_session_gets_supervisor_tools(self):
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        assert self._compute(None) == list(SUPERVISOR_TOOLS)

    def test_properly_configured_session_is_untouched(self):
        stored = [
            "rag_retrieve", "rag_list_docs", "rag_read_doc", "rag_graph_query",
            "kb_stats", "spawn_subagent", "ask_user_question", "submit_plan",
        ]
        assert self._compute(stored) == stored


class TestEndpointImplementsFallback:
    """Smoke-check the endpoint source references SUPERVISOR_TOOLS in both
    code paths (create_session + conversation runner). A future refactor
    accidentally reverting either path would reopen the bug."""

    def test_create_session_references_supervisor_tools(self):
        import pathlib

        src = pathlib.Path("api/apps/agent_v2_app.py").read_text()
        # create_session path
        assert "effective_tool_names" in src
        assert "SUPERVISOR_TOOLS" in src
        # Sanity check: the fallback is NOT commented out
        assert "# effective_tool_names" not in src

    def test_runner_path_references_supervisor_tools(self):
        import pathlib

        src = pathlib.Path("api/apps/agent_v2_app.py").read_text()
        assert "effective_runtime_tools" in src
        # Two occurrences of SUPERVISOR_TOOLS import expected (one per path)
        assert src.count("from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS") >= 2

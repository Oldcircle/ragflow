"""Phase 2.6 v0.6-fix — runtime gaps surfaced in first browser test.

Three bugs:
- A) After ``[plan approved]``, supervisor refused as out-of-domain because the
  user_message was empty post-strip and the supervisor prompt had no "approval
  follow-up" branch.
- B) ``kb_stats({})`` returned ``invalid_input`` because schema required kb_id
  but the system prompt never echoes the session's specific KB ID to the LLM.
- C) Archivist re-narrated the plan in chat text, duplicating the plan card.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx


def _payload(tool_result):
    return json.loads(tool_result["content"][0]["text"])


# ─────────────────────── A) plan-decision augmentation ──────────────────────


class TestAugmentForPlanDecision:
    """``_augment_for_plan_decision`` 把裸 `[plan approved]` 转成 supervisor
    能消化的明确指令。"""

    def test_no_decision_passes_through(self):
        from api.agent_v2.plan_decision import augment_for_plan_decision as _augment_for_plan_decision

        out = _augment_for_plan_decision(
            user_message="随便问个问题",
            plan_decision=None,
            plan_status=None,
        )
        assert out == "随便问个问题"

    def test_approved_empty_note_produces_directive(self):
        from api.agent_v2.plan_decision import augment_for_plan_decision as _augment_for_plan_decision

        out = _augment_for_plan_decision(
            user_message="",
            plan_decision="approved",
            plan_status="approved",
        )
        assert "[plan system]" in out
        assert "approved" in out
        assert "sub_archivist" in out
        assert "get_pending_plan" in out

    def test_approved_with_note_preserves_note(self):
        from api.agent_v2.plan_decision import augment_for_plan_decision as _augment_for_plan_decision

        out = _augment_for_plan_decision(
            user_message="顺便跳过第 3 步",
            plan_decision="approved",
            plan_status="approved",
        )
        assert "[plan system]" in out
        assert "User's accompanying note" in out
        assert "顺便跳过第 3 步" in out

    def test_rejected_produces_stop_directive(self):
        from api.agent_v2.plan_decision import augment_for_plan_decision as _augment_for_plan_decision

        out = _augment_for_plan_decision(
            user_message="",
            plan_decision="rejected",
            plan_status="rejected",
        )
        assert "[plan system]" in out
        assert "rejected" in out
        assert "Do NOT" in out or "do not" in out.lower()
        assert "Do not spawn sub_archivist" in out or "stop" in out.lower()

    def test_request_changes_asks_to_re_plan(self):
        from api.agent_v2.plan_decision import augment_for_plan_decision as _augment_for_plan_decision

        out = _augment_for_plan_decision(
            user_message="改成每月一次，不是每周",
            plan_decision="request_changes",
            plan_status="request_changes",
        )
        assert "[plan system]" in out
        assert "re-plan" in out.lower() or "submit_plan" in out
        assert "改成每月一次" in out

    def test_unknown_decision_is_passthrough(self):
        """Defensive: future/unknown decisions don't blow up the endpoint."""
        from api.agent_v2.plan_decision import augment_for_plan_decision as _augment_for_plan_decision

        out = _augment_for_plan_decision(
            user_message="hi",
            plan_decision="cancelled",  # hypothetical future status
            plan_status="cancelled",
        )
        assert out == "hi"


class TestSupervisorPromptPlanFollowup:
    """Supervisor 的 workflow 里必须有 plan-decision follow-up 分支。"""

    def test_baojian_supervisor_prompt_covers_plan_followup(self):
        from api.agent_v2.definitions import get_definition

        defn = get_definition("sz-baojian-house")
        assert defn is not None
        sp = defn.resolve_system_prompt()
        # New classification bucket exists
        assert "plan decision follow-up" in sp.lower() or "[plan system]" in sp
        # Override domain scope — the whole point of the fix
        assert "override" in sp.lower() or "out-of-domain" in sp.lower()

    def test_generic_policy_supervisor_also_covers_it(self):
        """Baseline prompt infra is shared; every supervisor gets it."""
        from api.agent_v2.definitions import get_definition

        defn = get_definition("generic-policy")
        assert defn is not None
        sp = defn.resolve_system_prompt()
        assert "[plan system]" in sp


# ─────────────────────── B) kb_stats / kb_audit fallback ────────────────────


@pytest.mark.asyncio
class TestKbStatsSingleKbFallback:
    async def test_empty_args_with_single_kb_falls_back(self):
        """Session 只有一个 KB → LLM 传空 {} 时自动用那一个。"""
        from api.agent_v2.tools.doc_ops.kb_stats import kb_stats

        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb-only",), user_id="u1",
        )
        token = set_ctx(ctx)
        try:
            # Patch the DB lookups so we only assert the fallback happened.
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
                return_value=(False, None),  # trigger not_found rather than real DB
            ):
                result = await kb_stats.handler({})
        finally:
            reset_ctx(token)

        p = _payload(result)
        # Not the "invalid_input" error — the tool accepted the fallback and
        # got as far as the KB lookup (where we made it fail intentionally).
        assert p.get("error") != "invalid_input"
        assert "kb-only" in (p.get("message") or "")

    async def test_empty_args_with_multiple_kbs_rejects(self):
        from api.agent_v2.tools.doc_ops.kb_stats import kb_stats

        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb-a", "kb-b"), user_id="u1",
        )
        token = set_ctx(ctx)
        try:
            result = await kb_stats.handler({})
        finally:
            reset_ctx(token)

        p = _payload(result)
        assert p.get("error") == "invalid_input"
        assert "multiple" in p.get("message", "").lower()

    async def test_explicit_kb_id_still_wins(self):
        from api.agent_v2.tools.doc_ops.kb_stats import kb_stats

        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb-a",), user_id="u1",
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
                return_value=(False, None),
            ):
                # caller passed kb-b; must NOT fall back to kb-a
                result = await kb_stats.handler({"kb_id": "kb-b"})
        finally:
            reset_ctx(token)
        p = _payload(result)
        assert "kb-b" in (p.get("message") or "")
        assert "kb-a" not in (p.get("message") or "")


@pytest.mark.asyncio
class TestKbAuditSingleKbFallback:
    async def test_empty_args_with_single_kb_falls_back(self):
        from api.agent_v2.tools.doc_ops.kb_audit import kb_audit

        ctx = ToolContext(
            tenant_id="t1", kb_ids=("kb-one",), user_id="u1",
        )
        token = set_ctx(ctx)
        try:
            with patch(
                "api.db.services.dataset_access_service.DatasetAccessService.require_at_least"
            ), patch(
                "api.db.services.knowledgebase_service.KnowledgebaseService.get_by_id",
                return_value=(False, None),
            ):
                result = await kb_audit.handler({})
        finally:
            reset_ctx(token)
        p = _payload(result)
        assert p.get("error") != "invalid_input"

    async def test_empty_args_no_scope_rejects(self):
        from api.agent_v2.tools.doc_ops.kb_audit import kb_audit

        ctx = ToolContext(
            tenant_id="t1", kb_ids=(), user_id="u1",
        )
        token = set_ctx(ctx)
        try:
            result = await kb_audit.handler({})
        finally:
            reset_ctx(token)
        p = _payload(result)
        assert p.get("error") == "invalid_input"


# ─────────────────────── C) Don't re-narrate the plan ──────────────────────


class TestSubmitPlanPromptGuidance:
    """Make sure description + archivist prompt both tell the LLM not to
    re-narrate the plan card contents in chat text."""

    def test_submit_plan_description_says_dont_re_narrate(self):
        from api.agent_v2.tools.submit_plan import submit_plan

        desc = submit_plan.description
        low = desc.lower()
        # new copy must mention that the UI renders the card
        assert "re-narrate" in low or "renders the plan card" in low

    def test_archivist_prompt_says_dont_re_narrate(self):
        from api.agent_v2.definitions import get_definition

        defn = get_definition("sub_archivist")
        sp = defn.resolve_system_prompt()
        low = sp.lower()
        assert "re-narrate" in low or "frontend renders the plan card" in low

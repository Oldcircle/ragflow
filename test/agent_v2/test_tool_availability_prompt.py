"""Phase 2.6 v0.8 — tool availability section + template tool plumbing.

Covers:
1. `render_tool_availability_section` produces positive + negative enumeration
2. Negative list is stripped when the actual tool is enabled (e.g. having
   `web_search` removes "WebSearch" from the negative list)
3. Meta-question handling instruction present
4. zh variant shipped with Chinese copy
5. Runner injects the section (both lang branches)
6. `templates.py` sets `suggested_tool_names` for every template
7. research-analyst template carries web_search + web_fetch; policy
   templates do NOT
"""

from __future__ import annotations

import pytest

from api.agent_v2.prompting import render_tool_availability_section
from api.agent_v2.runner import _guess_prompt_lang


# ───────────────── render_tool_availability_section ─────────────────


def test_positive_enumeration_lists_every_tool():
    text = render_tool_availability_section(
        ["rag_retrieve", "rag_list_docs", "kb_stats"]
    )
    # Every tool name appears on its own bullet with backticks
    assert "- `rag_retrieve` — search knowledge base" in text
    assert "- `rag_list_docs` —" in text
    assert "- `kb_stats` —" in text


def test_negative_enumeration_lists_common_claude_tools():
    text = render_tool_availability_section(["rag_retrieve"])
    # DeepSeek's usual hallucination candidates are explicitly denied
    for bad in ("Gmail", "Google Drive", "LSP", "Skill", "Bash"):
        assert f"`{bad}`" in text
    # Strict-enumeration framing (v0.8.1)
    assert "Strict constraints on tool enumeration" in text
    assert "exhaustive" in text.lower()


def test_enabled_tool_dropped_from_negative_mentions():
    # When web_search is enabled, "WebSearch" / "WebFetch" should NOT appear
    # in the negative tool list (otherwise model sees conflicting signals).
    with_web = render_tool_availability_section(["web_search", "web_fetch"])
    # They must not be in the negative prose (which uses `Gmail`, `WebSearch`
    # backticked tokens). Positive list uses lowercase `web_search`.
    assert "`WebSearch`" not in with_web
    assert "`WebFetch`" not in with_web
    # But Gmail still is in the negative list (we didn't enable it)
    assert "`Gmail`" in with_web


def test_meta_question_handling_instruction_present():
    text = render_tool_availability_section(["rag_retrieve"])
    # Must tell model to answer meta-questions only from the positive list
    assert "what tools do you have" in text.lower()
    assert "capabilities" in text.lower()
    assert "web access is not enabled" in text.lower()


def test_meta_question_says_web_enabled_when_available():
    text = render_tool_availability_section(["rag_retrieve", "web_search"])
    # When web tools are available, the prompt should AFFIRM web access,
    # not deny it — otherwise the model gets conflicting instructions.
    assert "web tools enabled" in text.lower()
    assert "web access is not enabled on this session" not in text.lower()


def test_meta_question_denies_web_when_no_web_tools():
    text = render_tool_availability_section(["rag_retrieve"])
    # No web tool in the list → explicit denial instruction
    assert "web access is not enabled" in text.lower()
    # And no "yes you have web" confusion
    assert "web tools enabled" not in text.lower()


def test_zh_variant_in_chinese():
    text = render_tool_availability_section(
        ["rag_retrieve"], lang="zh"
    )
    assert "可用工具" in text
    assert "严格约束" in text
    assert "元问题处理" in text


def test_none_tool_names_lists_all_registered():
    """When no whitelist is given, the section should enumerate every
    registered tool — a backward-compat path for sessions without an
    explicit ``tool_names``."""
    from api.agent_v2.registry import ALL_TOOLS

    text = render_tool_availability_section(None)
    for name in ALL_TOOLS:
        assert f"- `{name}` —" in text


def test_empty_list_treated_as_none():
    """Empty list currently falls back to ALL_TOOLS; document that behavior
    so we catch regressions if the fallback changes."""
    from api.agent_v2.registry import ALL_TOOLS

    text = render_tool_availability_section([])
    # At least some registered tool name appears
    assert any(f"- `{n}` —" in text for n in ALL_TOOLS)


def test_duplicates_deduplicated():
    text = render_tool_availability_section(
        ["rag_retrieve", "rag_retrieve", "kb_stats"]
    )
    # Only one bullet for rag_retrieve
    assert text.count("- `rag_retrieve` —") == 1


# ───────────────── _guess_prompt_lang ─────────────────


def test_lang_guess_zh_prompt():
    zh_prompt = (
        "你是一名深圳保障房政策顾问，严格基于知识库内容答复。"
        "工作方式：1. 判断用户问题是否与知识库内容相关..."
    )
    assert _guess_prompt_lang(zh_prompt) == "zh"


def test_lang_guess_en_prompt():
    en_prompt = (
        "You are the Shenzhen Affordable Housing Policy Advisor. "
        "Answer strictly from the knowledge base..."
    )
    assert _guess_prompt_lang(en_prompt) == "en"


def test_lang_guess_none_defaults_english():
    assert _guess_prompt_lang(None) == "en"
    assert _guess_prompt_lang("") == "en"


# ───────────────── templates.py plumbing ─────────────────


def test_every_template_declares_suggested_tool_names():
    from api.agent_v2.templates import TEMPLATES

    for tpl in TEMPLATES:
        assert (
            tpl.suggested_tool_names is not None
            and len(tpl.suggested_tool_names) > 0
        ), (
            f"Template {tpl.id} has no suggested_tool_names — pick it and the "
            f"backend falls back to SUPERVISOR_TOOLS (8 KB-only tools), which "
            f"silently strips web tools etc."
        )


def test_research_analyst_template_has_web_tools():
    from api.agent_v2.templates import get_template

    t = get_template("research-analyst")
    assert t is not None
    assert "web_search" in t.suggested_tool_names
    assert "web_fetch" in t.suggested_tool_names


@pytest.mark.parametrize(
    "tpl_id",
    ["sz-baojian-house", "generic-policy", "legal-contract",
     "customer-support", "internal-wiki"],
)
def test_policy_templates_do_not_have_web_tools(tpl_id):
    """Policy / legal / wiki / support stay KB-only by design. If someone
    adds web_search here, they should add it to the research-analyst
    supervisor too and update the test_policy_supervisors_do_not_have_web_tools
    assertion in test_web_tools.py — don't silently loosen this."""
    from api.agent_v2.templates import get_template

    t = get_template(tpl_id)
    assert t is not None
    assert "web_search" not in t.suggested_tool_names
    assert "web_fetch" not in t.suggested_tool_names


def test_every_template_prompt_explains_delegation_for_writes():
    """v0.22 — supervisors are read-only by tool config, but they CAN
    spawn sub_archivist for write operations. The prompt must say so;
    otherwise the agent hallucinates "I can't create a KB" when the
    user asks (live-caught: a v4-flash session declined to create a
    finance KB even though spawn_subagent + sub_archivist were wired)."""
    from api.agent_v2.templates import TEMPLATES

    for tpl in TEMPLATES:
        sp = tpl.system_prompt or ""
        assert "spawn_subagent" in sp and "sub_archivist" in sp, (
            f"Template {tpl.id} prompt doesn't mention spawn_subagent + "
            f"sub_archivist — agent will refuse write requests"
        )
        # And explicitly forbid the "I can't create a KB" failure mode
        assert "无法创建知识库" in sp or "claim" in sp.lower(), (
            f"Template {tpl.id} prompt doesn't forbid the "
            f"can't-create-KB hallucination"
        )


def test_spawn_subagent_section_enumerates_named_subagents():
    """v0.22 — when ``spawn_subagent`` is in the toolset, the rendered
    Available Tools section must enumerate which named subagents are
    reachable + their write capabilities. Without this, the agent reads
    a vague "delegate a focused task" and never connects delegation to
    concrete writes like kb_create / doc_ingest_attachment, which is
    what triggered the live-caught "I can't create KBs" hallucination."""
    from api.agent_v2.prompting.builder import (
        render_tool_availability_section,
    )

    out = render_tool_availability_section(
        ["rag_retrieve", "spawn_subagent", "submit_plan"],
        lang="en",
    )
    # The named subagent types appear as nested bullets
    assert "subagent_type='sub_archivist'" in out
    assert "subagent_type='sub_librarian'" in out
    # And their key write tools are surfaced
    assert "kb_create" in out
    assert "doc_archive" in out


def test_spawn_subagent_section_skipped_when_tool_missing():
    """If the session doesn't grant ``spawn_subagent``, the subagent
    enumeration must NOT leak into the prompt — that would offer the
    agent a capability it can't actually invoke."""
    from api.agent_v2.prompting.builder import (
        render_tool_availability_section,
    )

    out = render_tool_availability_section(["rag_retrieve"], lang="en")
    assert "subagent_type=" not in out


def test_every_template_prompt_forbids_emoji():
    """v0.22 — deepseek-v4-flash mis-renders emoji as literal '????'
    in output (verified live: ``????系统性学习平台``). Until the model
    fixes this, prompts must say no-emoji."""
    from api.agent_v2.templates import TEMPLATES

    for tpl in TEMPLATES:
        assert "emoji" in (tpl.system_prompt or "").lower(), (
            f"Template {tpl.id} prompt doesn't ban emoji — output will "
            f"have ???? on v4-flash"
        )


def test_template_tool_names_are_all_registered():
    """Every tool a template suggests must actually exist in ALL_TOOLS —
    otherwise the backend silently drops it."""
    from api.agent_v2.registry import ALL_TOOLS
    from api.agent_v2.templates import TEMPLATES

    for tpl in TEMPLATES:
        unknown = [
            t for t in (tpl.suggested_tool_names or []) if t not in ALL_TOOLS
        ]
        assert not unknown, (
            f"Template {tpl.id} references unregistered tools: {unknown}"
        )


def test_list_templates_exposes_suggested_tool_names():
    """The /v1/agent_v2/template endpoint returns list_templates() directly;
    verify the field flows through the dict serialization."""
    from api.agent_v2.templates import list_templates

    for d in list_templates():
        assert "suggested_tool_names" in d, (
            f"Template {d.get('id')} dict missing suggested_tool_names field"
        )

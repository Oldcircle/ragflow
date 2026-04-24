"""Shared definitions for built-in agents (Phase 2.6 v0.3).

All prompts are now English and section-structured (see
`api/agent_v2/prompting/builder.py`). Domain specialization is preserved via
the `role_line` + `domain_context` + `hard_constraints` parameters fed to
`build_supervisor_prompt`.

This module only deals with text assembly. Runtime context (kb_ids, tenant)
is injected by the runner / spawn flow, not at definition time.
"""

from __future__ import annotations

from ...prompting import build_supervisor_prompt


def strict_rag_prompt(
    *,
    role: str,
    fallback: str,
    extras: list[str] | None = None,
) -> str:
    """Backwards-compatible supervisor prompt builder.

    `role` is now expected in English (e.g. "the Shenzhen Affordable Housing
    Policy Advisor"), though Chinese still works — the model handles
    multilingual role names. `fallback` is the phrase the agent emits when
    the KB does not cover a fact (e.g. "consult your local housing bureau").
    `extras` are additional domain-specific hard constraints.
    """
    domain_context = (
        f"You operate on a knowledge base curated for {role}. "
        f"If users ask questions outside this scope, reply briefly that you "
        f"can only answer questions relating to {role}, and do not call any tools."
    )
    # Map the legacy "fallback" string into a hard constraint phrased
    # the way the rest of the prompt talks about gaps:
    # "If the retrieved material does not answer, say 'no direct basis in
    # the knowledge base; please {fallback}'."
    fallback_constraint = (
        "When the knowledge base does not directly answer the user, say "
        f"exactly: 'No direct basis in the knowledge base; please {fallback}.' "
        "Do not bridge the gap with training knowledge."
    )
    hard = [fallback_constraint]
    if extras:
        hard.extend(extras)
    return build_supervisor_prompt(
        role_line=f"You are {role}, answering strictly from the knowledge base.",
        domain_context=domain_context,
        hard_constraints=hard,
    )


# Phase 2.6 v0.2 设计约束：supervisor 只做「检索 QA + 委派」，不直接持写/审计工具。
# 拿写工具要去 spawn sub_archivist；做体检 / 写笔记要 spawn sub_librarian。
# 这样确保 tool-level 架构分离不会被 "tools=*" 绕掉。
SUPERVISOR_TOOLS = [
    # Read
    "rag_retrieve",
    "rag_list_docs",
    "rag_read_doc",
    "rag_graph_query",
    # Cheap health snapshot (<1KB, no side effects)
    "kb_stats",
    # Delegation + user interaction
    "spawn_subagent",
    "ask_user_question",
    "submit_plan",
]


# Phase 2.6 v0.2 设计约束：supervisor 只做「检索 QA + 委派」，不直接持写/审计工具。
# 拿写工具要去 spawn sub_archivist；做体检 / 写笔记要 spawn sub_librarian。
# 这样确保 tool-level 架构分离不会被 "tools=*" 绕掉。
SUPERVISOR_TOOLS = [
    # 读
    "rag_retrieve",
    "rag_list_docs",
    "rag_read_doc",
    "rag_graph_query",
    # 轻量体检（<1KB 响应，supervisor 做 sanity check 之前）
    "kb_stats",
    # 委派 + 用户交互
    "spawn_subagent",
    "ask_user_question",
    "submit_plan",
]

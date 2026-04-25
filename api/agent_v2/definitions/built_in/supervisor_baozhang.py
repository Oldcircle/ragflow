"""Shenzhen Affordable Housing Policy Advisor — supervisor agent."""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt, SUPERVISOR_TOOLS


DEFINITION = AgentDefinition(
    name="sz-baojian-house",
    version="2.0.0",
    description=(
        "Answers citizen questions about Shenzhen affordable housing policies "
        "(public rental, affordable-rental, sales-based affordable, joint-"
        "ownership, talent housing) — eligibility, rent, required documents, "
        "transfer rules. Grounded strictly in the KB."
    ),
    when_to_use=(
        "User asks about Shenzhen affordable housing: eligibility, allocation, "
        "transfer restrictions, rent caps, talent / youth housing schemes."
    ),
    kind="supervisor",
    icon="🏠",
    category="policy",
    system_prompt=strict_rag_prompt(
        role="the Shenzhen Affordable Housing Policy Advisor",
        fallback=(
            "contact the Shenzhen Housing and Construction Bureau or the "
            "project's development unit for the authoritative answer"
        ),
        extras=[
            "Never generalize policies from other cities (Guangzhou, Shanghai, "
            "Beijing, etc.) to Shenzhen. Each city has its own rules.",
            "Do not conflate 'the property was not transferred within N years' "
            "(an anti-speculation clause) with 'the applicant was homeless for "
            "N years' (an eligibility clause). These are distinct conditions.",
            "Name each scheme precisely when citing: 公共租赁住房 / 保障性租赁"
            "住房 / 配售型保障性住房 / 共有产权住房 / 人才安居住房. Do not "
            "collapse them into 'affordable housing' generically.",
        ],
    ),
    max_turns=8,
    max_budget_usd=0.5,
    tools=SUPERVISOR_TOOLS,
    kb_hints=("affordable housing", "Shenzhen", "policy", "保障房", "深圳"),
    citation_enforce="warn",
    citation_numeric_strict=True,
    can_spawn_subagents=True,
    allowed_subagent_types=(
        "sub_policy_researcher",
        "sub_evidence_checker",
        "sub_archivist",   # destructive ops — delegated, never direct
        "sub_librarian",   # audit / observe / summarize / write notes
    ),
)

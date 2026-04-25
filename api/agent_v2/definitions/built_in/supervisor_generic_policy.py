"""Generic policy / regulation advisor — supervisor agent."""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt, SUPERVISOR_TOOLS


DEFINITION = AgentDefinition(
    name="generic-policy",
    version="2.0.0",
    description=(
        "General-purpose supervisor for any policy / regulation / internal-"
        "rule knowledge base. Strict verbatim citation; never fabricates "
        "numbers. Point it at your policy KB at session creation."
    ),
    when_to_use=(
        "User needs to look up policy / regulation / internal-rule texts; "
        "domain is not tied to a specific industry."
    ),
    kind="supervisor",
    icon="📜",
    category="policy",
    system_prompt=strict_rag_prompt(
        role="a policy and regulation consultation assistant",
        fallback="confirm with the business owner or regulator directly",
    ),
    max_turns=10,
    max_budget_usd=0.5,
    tools=SUPERVISOR_TOOLS,
    kb_hints=("policy", "regulation", "rulebook", "政策", "法规", "制度"),
    citation_enforce="warn",
    can_spawn_subagents=True,
    allowed_subagent_types=(
        "sub_policy_researcher",
        "sub_evidence_checker",
        "sub_archivist",
        "sub_librarian",
    ),
)

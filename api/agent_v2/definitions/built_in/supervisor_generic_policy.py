"""通用政策法规咨询助手 — supervisor Agent。"""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt


DEFINITION = AgentDefinition(
    name="generic-policy",
    version="1.0.0",
    description=(
        "适合任何政策/法规/内部制度类知识库。严格引用原文，不编数字。"
        "创建时挑选自己的政策 KB 即可。"
    ),
    when_to_use="用户需要查政策/法规/制度类原文，场景不特定于某一行业",
    kind="supervisor",
    icon="📜",
    category="policy",
    system_prompt=strict_rag_prompt(
        role="政策法规咨询助手",
        fallback="向业务主管部门确认",
    ),
    max_turns=10,
    max_budget_usd=0.5,
    tools="*",
    kb_hints=("政策", "法规", "制度", "规章"),
    citation_enforce="warn",
    can_spawn_subagents=True,
    allowed_subagent_types=(
        "sub_policy_researcher",
        "sub_evidence_checker",
        "sub_archivist",
        "sub_librarian",
    ),
)

"""合同/合规审查助手 — supervisor Agent。"""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt


DEFINITION = AgentDefinition(
    name="legal-contract",
    version="1.0.0",
    description=(
        "适合法务、合规场景。基于公司合同模板库、监管规则库、"
        "历史案例库回答条款问题。"
    ),
    when_to_use="用户问合同条款、合规要求、法规解读",
    kind="supervisor",
    icon="⚖️",
    category="legal",
    system_prompt=strict_rag_prompt(
        role="法务合规顾问",
        fallback="请法务团队进一步评估",
        extras=[
            "给出法律结论性判断（应说「根据条款，建议...」而非「一定合法/违法」）",
        ],
    ),
    max_turns=12,
    max_budget_usd=0.8,
    tools="*",
    kb_hints=("合同", "法律", "合规", "监管"),
    citation_enforce="warn",
    citation_numeric_strict=True,
    can_spawn_subagents=True,
    allowed_subagent_types=("sub_policy_researcher", "sub_evidence_checker"),
)

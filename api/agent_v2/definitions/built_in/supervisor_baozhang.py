"""深圳保障房政策顾问 — supervisor Agent。"""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt, SUPERVISOR_TOOLS


DEFINITION = AgentDefinition(
    name="sz-baojian-house",
    version="1.0.0",
    description=(
        "基于深圳市保障性住房相关政策文件（公租房/保租房/配售型/"
        "共有产权/人才安居等），专门回答市民关于申请资格、租金、材料、"
        "流转规则的咨询。"
    ),
    when_to_use="用户咨询深圳保障房政策（申请资格、配租、转让规则）",
    kind="supervisor",
    icon="🏠",
    category="policy",
    system_prompt=strict_rag_prompt(
        role="深圳保障房政策顾问",
        fallback="向深圳市住房和建设局或相关项目的开发建设单位咨询",
        extras=[
            "把其他城市政策套用到深圳（如广州/上海规则）",
            "混淆「N 年内未转让」和「无房 N 年」（前者是反套利条款）",
        ],
    ),
    max_turns=8,
    max_budget_usd=0.5,
    tools=SUPERVISOR_TOOLS,
    kb_hints=("保障房", "住房", "配租", "深圳", "政策"),
    citation_enforce="warn",
    citation_numeric_strict=True,
    can_spawn_subagents=True,
    allowed_subagent_types=(
        "sub_policy_researcher",
        "sub_evidence_checker",
        "sub_archivist",  # Phase 2.6 — 委派"动手改"的 KB 运营
        "sub_librarian",  # Phase 2.6.v2 — 委派"体检 / 写报告 / 做笔记"
    ),
)

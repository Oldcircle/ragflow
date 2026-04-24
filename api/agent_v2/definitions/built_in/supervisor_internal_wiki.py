"""内部 Wiki 问答助手 — supervisor Agent。"""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt, SUPERVISOR_TOOLS


DEFINITION = AgentDefinition(
    name="internal-wiki",
    version="1.0.0",
    description=(
        "通用内部文档问答：把公司制度、流程、SOP、培训材料丢进知识库，"
        "让员工直接问 Agent，而不是翻文档。"
    ),
    when_to_use="员工查公司制度/流程/SOP/培训材料",
    kind="supervisor",
    icon="📚",
    category="general",
    system_prompt=strict_rag_prompt(
        role="公司内部知识助手",
        fallback="在工单系统搜索或联系 IT/HR/行政对接人",
    ),
    max_turns=8,
    max_budget_usd=0.5,
    tools=SUPERVISOR_TOOLS,
    kb_hints=("SOP", "制度", "流程", "手册", "Wiki"),
    citation_enforce="warn",
    can_spawn_subagents=False,
)

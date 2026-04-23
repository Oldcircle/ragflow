"""研报/尽调综合助手 — supervisor Agent。"""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt


DEFINITION = AgentDefinition(
    name="research-analyst",
    version="1.0.0",
    description=(
        "金融/投研场景：汇总多份研报、公司资料，做跨文档对比、"
        "关键数据提取、观点综述。"
    ),
    when_to_use="用户做投研综合、跨文档对比、关键数据提取",
    kind="supervisor",
    icon="📊",
    category="finance",
    system_prompt=strict_rag_prompt(
        role="金融研究助手",
        fallback="参考最新市场公告或直接访问数据源",
        extras=[
            "给出投资建议（只做信息综合，不做推荐）",
            "预测未来数据（只引用历史/当前数据）",
        ],
    ),
    max_turns=15,
    max_budget_usd=1.0,
    tools="*",
    kb_hints=("研报", "投资", "财报", "行业"),
    citation_enforce="warn",
    citation_numeric_strict=True,
    can_spawn_subagents=True,
    allowed_subagent_types=(
        "sub_policy_researcher",
        "sub_evidence_checker",
        "sub_archivist",
    ),
)

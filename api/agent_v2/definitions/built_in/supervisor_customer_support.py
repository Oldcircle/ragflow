"""产品客服助手 — supervisor Agent。"""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt, SUPERVISOR_TOOLS


DEFINITION = AgentDefinition(
    name="customer-support",
    version="1.0.0",
    description=(
        "基于产品手册、FAQ、故障库、政策文档回答客户问题。"
        "未命中的答复转人工工单。"
    ),
    when_to_use="面向 C 端用户、回答产品用法、故障排查、政策说明",
    kind="supervisor",
    icon="💬",
    category="customer",
    system_prompt=strict_rag_prompt(
        role="产品客服助手",
        fallback="为您创建工单转人工处理",
        extras=[
            "承诺具体时效（如「24 小时内解决」），只能说「我们会尽快处理」",
        ],
    ),
    max_turns=6,
    max_budget_usd=0.3,
    tools=SUPERVISOR_TOOLS,
    kb_hints=("手册", "FAQ", "产品", "故障"),
    citation_enforce="warn",
    can_spawn_subagents=False,  # 客服场景禁用 subagent，简单直接
)

"""subagent: 政策研究员 — 聚焦研读单一政策。

父 Agent 碰到「这个政策里的 X 条款到底是什么意思」这种深挖场景时派它。
它只干一件事：对指定 KB 里的某个政策做细粒度追溯（多次检索 + 原文比对）。
"""

from __future__ import annotations

from ..schema import AgentDefinition


DEFINITION = AgentDefinition(
    name="sub_policy_researcher",
    version="1.0.0",
    description="深入研读单一政策，回答其细节条款、生效时间、适用范围等",
    when_to_use=(
        "父 Agent 已经定位到具体某个政策文件，需要对其某个具体条款做"
        "细粒度追溯（比如对比前后版本、找到反套利条款的确切措辞）"
    ),
    kind="subagent",
    icon="🔎",
    category="policy",
    system_prompt=(
        "你是一名政策研究员 subagent。父 Agent 会告诉你要研读哪个政策、"
        "追溯哪个具体问题。工作方式：\n"
        "1. 先用 rag_retrieve 按政策名 + 问题关键词检索 2–3 次\n"
        "2. 对检索到的核心片段做原文摘录（每条片段配 [N] 标注）\n"
        "3. 回答父 Agent 的追溯问题时，只给原文 + 最少必要的串讲，"
        "   不要补全父 Agent 没问的内容\n"
        "4. 严格不编造；检索不到就回「未在该政策中找到相关条款」"
    ),
    model="inherit",  # 用父 Agent 的模型
    max_turns=6,
    max_budget_usd=0.3,
    # 只允许检索类工具，不让它再派 subagent（depth=1 限制也会卡）
    tools=["rag_retrieve", "rag_read_doc"],
    citation_enforce="warn",
    citation_numeric_strict=True,
    can_spawn_subagents=False,
    history_turn_limit=4,  # subagent 不需要长历史
)

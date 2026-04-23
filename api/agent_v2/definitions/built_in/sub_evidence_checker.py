"""subagent: 证据审核员 — 给一份答复重新核对证据链。

与 P2.5.1 CitationValidator 联动：validator 发现 ``strict_failed`` 时，
父 supervisor 可以派这个子把"先重新检索原文 + 对照每个数字"的工作做一遍，
回传一份带 [N] 的修订文本。
"""

from __future__ import annotations

from ..schema import AgentDefinition


DEFINITION = AgentDefinition(
    name="sub_evidence_checker",
    version="1.0.0",
    description="对一份答复逐句核对原文证据，重写缺证据的句子",
    when_to_use=(
        "父 Agent 已经给出一份答复，但 citation validator 报了 "
        "strict_failed 或自己不确定某些数字是否有原文支撑。"
        "把答复原文和问题一并传给我，我会逐句核对并输出修订版。"
    ),
    kind="subagent",
    icon="🧐",
    category="general",
    system_prompt=(
        "你是证据审核员 subagent。输入：一份答复文本 + 原问题。工作方式：\n"
        "1. 先 rag_retrieve 把原问题再查一遍，拿到权威的 chunks\n"
        "2. 逐句核对答复里的数字、年限、条款是否在 chunks 里有字面依据\n"
        "3. 没依据的句子要么删掉，要么改写成「未查到对应规定」\n"
        "4. 重新给每个有依据的句子标 [N]，编号对应本次检索顺序\n"
        "5. 返回修订后的答复文本，并在结尾列出「改动说明」1–3 条\n"
        "\n"
        "严格不编造。不要猜「大概是 21 岁」这种话；要么原文给你答案，要么承认未查到。"
    ),
    model="inherit",
    max_turns=5,
    max_budget_usd=0.3,
    tools=["rag_retrieve", "rag_read_doc"],
    citation_enforce="strict",  # 审核员自己也受约束
    citation_numeric_strict=True,
    can_spawn_subagents=False,
    history_turn_limit=2,
)

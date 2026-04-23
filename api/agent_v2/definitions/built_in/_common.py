"""内置 Agent 之间共享的 prompt 生成逻辑。

这里只做文本拼接——真实的 kb_ids / tenant 上下文注入放在 runner / spawn
子流程里，不放 definition 层。
"""

from __future__ import annotations

_STRICT_RAG_PROMPT_TEMPLATE = """你是一名{role}，严格基于知识库内容答复。

工作方式：
1. 判断用户问题是否与知识库内容相关。无关则直接说明，不调工具。
2. 相关问题：使用 rag_retrieve 工具检索原文；必要时换关键词多次检索。
3. 严格基于检索结果回答：数字、年限、比例、条款必须有原文支撑；原文未涵盖的内容，回答「未查到相关规定，建议{fallback}」。
4. **引用规范**：当某句话依据检索到的某个文档片段时，在该句末尾加形如 [1]、[2]、[3] 的上标编号，对应该次对话里按检索顺序出现的文档。一句话同时依据多片段可写 [1][2]。不要在正文里列文件名——文件名会自动展示在答复下方的"引用来源"区。
5. 回答结尾无需重复「依据文件」清单，编号本身已标出归属。

禁止事项：
{prohibitions}- 用训练知识补充原文未说的内容；
- 编造数字、名称、时间；
- 给没有检索依据的句子加 [N] 编号。
"""


def strict_rag_prompt(
    *,
    role: str,
    fallback: str,
    extras: list[str] | None = None,
) -> str:
    prohibitions = "\n".join(f"- {x}" for x in (extras or [])) + ("\n" if extras else "")
    return _STRICT_RAG_PROMPT_TEMPLATE.format(
        role=role, fallback=fallback, prohibitions=prohibitions
    )

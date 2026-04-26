"""Answer validators（Phase 2.5.1）。

企业 KB Agent 的生死线是"答案里的数字 / 金额 / 年限 / 日期必须来自 chunk"。
本包提供：

  - :class:`EvidenceIndex` — 把本轮 tool_result 里的 chunks 汇成可检索索引
  - :class:`CitationExtractor` — 从 Markdown 回复里抽 [N] 脚注 + 数字型断言
  - :func:`validate_citations` — 对比答案和证据，返回 issues 列表

用法（runner 层）::

    idx = EvidenceIndex()
    # 在工具回调里把 rag_retrieve / rag_read_doc 的结果喂进来
    idx.add_from_rag_retrieve(result_dict)
    idx.add_from_rag_read_doc(result_dict)
    ...
    # LLM 吐完最终答复后
    issues = validate_citations(final_text, idx)
    if issues:
        ...emit warning / trigger rewrite...
"""

from .citation import (  # noqa: F401
    CitationExtractor,
    CitationIssue,
    CitationIssueKind,
    validate_citations,
)
from .evidence_index import EvidenceIndex, Evidence  # noqa: F401
from .rewrite import rewrite_answer_strict, rewrite_to_no_basis  # noqa: F401

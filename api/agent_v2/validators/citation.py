"""CitationValidator — 对比 Agent 答复和 EvidenceIndex。

规则（v1，简单 + 低误报为先）：
  1. **missing_chunk**：答复里出现 ``[N]``，但 evidence 里没有第 N 条
  2. **number_unsupported**：答复里的数字型断言（百分比 / 年限 / 金额 / 岁数 /
     日期）在任何 evidence 的同类数字集合里都找不到
  3. **no_citation_for_numeric**（可选，strict 模式）：数字型断言前后 2 句内
     没有任何 ``[N]`` 脚注

**设计红线**：
- 热路径必须快（目标 < 50 ms / 100 chunks × 10 断言）
- 默认保守：误报宁可少也不能误伤正确答复
- 不做 embedding / LLM 调用（那是 v2 的事）
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Literal

from .evidence_index import EvidenceIndex, NumberMatch, extract_numbers

logger = logging.getLogger("ragflow.agent_v2.validators.citation")

CitationIssueKind = Literal[
    "missing_chunk",
    "number_unsupported",
    "no_citation_for_numeric",
]


@dataclass
class CitationIssue:
    kind: CitationIssueKind
    citation_index: int | None
    claim: str          # 有问题的那段话
    detail: str         # 给 LLM 重写时的可读理由

    def to_dict(self) -> dict:
        return asdict(self)


# 匹配 Markdown 里的 [N] 脚注（允许 [1][2][3] 连排；不匹配 [link](url)）
# 注：早期版本加过 (?<!\]) 想防某种双报，但那反而把连排的第 2、3 个都拦了，
# 已去掉；``(?!\()`` 足够防住 markdown link。
_CITE_RE = re.compile(r"\[(\d{1,3})\](?!\()")

# 句子切分：优先 Chinese/English 终结符；保留标点防止偏移漂移
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?\.\n])\s+")


class CitationExtractor:
    """从 LLM Markdown 答复里抽出 [N] 脚注 + 断言."""

    @staticmethod
    def extract_citations(text: str) -> list[tuple[int, int]]:
        """返回 [(citation_index, offset)]，按出现顺序."""
        return [(int(m.group(1)), m.start()) for m in _CITE_RE.finditer(text or "")]

    @staticmethod
    def extract_claims(text: str) -> list[NumberMatch]:
        """沿用 evidence 同一套抽取逻辑，保证规范化结果可比."""
        return extract_numbers(text or "")


def _split_sentences(text: str) -> list[tuple[int, int]]:
    """返回每个 sentence 的 [start, end) span 序列（覆盖全文）.

    中英文混排：句号 / 感叹号 / 问号 / 换行 后断句；不丢弃任何字符。
    """
    if not text:
        return []
    out: list[tuple[int, int]] = []
    last = 0
    for m in _SENT_SPLIT_RE.finditer(text):
        end = m.start()
        if end > last:
            out.append((last, end))
        last = m.end()
    if last < len(text):
        out.append((last, len(text)))
    return out


def _locate_sentence(span: tuple[int, int], sentences: list[tuple[int, int]]) -> int:
    """给一个 offset span，返回它落在第几个句子（-1 表示失败）。"""
    start = span[0]
    for i, (s, e) in enumerate(sentences):
        if s <= start < e:
            return i
    return -1


# ────────────────────────────── 主验证器 ──────────────────────────────


def validate_citations(
    final_text: str,
    index: EvidenceIndex,
    *,
    numeric_strict: bool = True,
) -> list[CitationIssue]:
    """返回 issues 列表（空 = 全过）.

    规则 1 始终检查；规则 2/3 只在 ``numeric_strict`` 打开时跑。
    """
    issues: list[CitationIssue] = []
    if not final_text:
        return issues

    # ── 规则 1：missing_chunk ──
    cites = CitationExtractor.extract_citations(final_text)
    for n, offset in cites:
        if index.lookup_by_citation_id(n) is None:
            issues.append(
                CitationIssue(
                    kind="missing_chunk",
                    citation_index=n,
                    claim=_context_window(final_text, offset, 60),
                    detail=(
                        f"Citation [{n}] does not map to any chunk returned in "
                        f"this turn (only {len(index)} chunks available)."
                    ),
                )
            )

    if not numeric_strict:
        return issues

    # 提前切句 + 定位所有 [N] 出现的句子下标，规则 2/3 都用
    sentences = _split_sentences(final_text)
    cite_sentence_idx: set[int] = set()
    for _, off in cites:
        s_i = _locate_sentence((off, off), sentences)
        if s_i >= 0:
            cite_sentence_idx.add(s_i)

    # ── 规则 2：number_unsupported ──
    evidence_numbers = index.all_normalized_numbers()
    claim_numbers = CitationExtractor.extract_claims(final_text)
    for nm in claim_numbers:
        if nm.kind == "bare":
            # bare 数字默认不强校验（太容易误伤：页码、小标题序号）
            continue
        # 在 evidence 里找同 kind 或 bare（宽容匹配）
        ok = False
        for ek, eval_ in evidence_numbers:
            if eval_ != nm.normalized:
                continue
            if ek == nm.kind or ek == "bare" or nm.kind == "bare":
                ok = True
                break
        if not ok:
            issues.append(
                CitationIssue(
                    kind="number_unsupported",
                    citation_index=None,
                    claim=_context_window(final_text, nm.span[0], 60),
                    detail=(
                        f"Numerical claim '{nm.raw}' ({nm.kind}, normalized="
                        f"{nm.normalized}) is not present in any retrieved chunk."
                    ),
                )
            )

    # ── 规则 3：no_citation_for_numeric ──
    # 数字型断言（非 bare）所在句及其 ±2 句内必须出现 [N]，否则算无出处。
    # 跳过已经被规则 2 抓到的（避免双报）：用 normalized+span 做键。
    rule2_spans = {
        (iss.citation_index, iss.claim)
        for iss in issues
        if iss.kind == "number_unsupported"
    }
    for nm in claim_numbers:
        if nm.kind == "bare":
            continue
        s_i = _locate_sentence(nm.span, sentences)
        if s_i < 0:
            continue
        window = range(max(0, s_i - 2), min(len(sentences), s_i + 3))
        if any(k in cite_sentence_idx for k in window):
            continue
        claim_snippet = _context_window(final_text, nm.span[0], 60)
        # 这个数字本轮根本没在 evidence 里 → 规则 2 已经报了，跳过避免重复
        if (None, claim_snippet) in rule2_spans:
            continue
        issues.append(
            CitationIssue(
                kind="no_citation_for_numeric",
                citation_index=None,
                claim=claim_snippet,
                detail=(
                    f"Numerical claim '{nm.raw}' ({nm.kind}) has no [N] citation "
                    f"within ±2 sentences; the reader cannot verify its source."
                ),
            )
        )

    return issues


# ────────────────────────────── helpers ──────────────────────────────


def _context_window(text: str, offset: int, width: int = 60) -> str:
    if not text:
        return ""
    start = max(0, offset - width)
    end = min(len(text), offset + width)
    snippet = text[start:end].replace("\n", " ")
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet

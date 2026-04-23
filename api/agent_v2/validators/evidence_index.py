"""EvidenceIndex — 本轮 tool_result 的证据汇总索引。

只在一轮 Agent run 的生命周期里存在；不持久化到 DB。

设计要点：
- **容错优先**：tool_result 可能是 JSON 字符串、也可能已是 dict，两种都吃下
- **按顺序编号**：第一个 rag_retrieve 返的 chunks 依次编号 [1]..[N]，下一次 retrieve 继续
  [N+1]..；这与 Agent 在 prompt 里通常让 LLM 用的 `[N]` 约定对齐（和 `prompt`
  里的"按检索顺序编号"规范一致）
- **不重新分词 / 不调模型**：所有数字 / 年限 / 百分比 / 金额靠正则抽取，validator
  跑在热路径上必须 < 50 ms
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ragflow.agent_v2.validators.evidence")


# ────────────────────────────── 抽取正则 ──────────────────────────────

# 中英数字 + 单位（年限/金额/面积/百分比/岁/人/%等）；这是"断言级别"的抽取，
# 不追求语义精度 —— 只要 validator 能用相同规则从 chunk 和 answer 里都抽出同样的
# 数字，就能对齐比较。
#
# 说明：
# - percent_re 匹配  "70%" / "70 %" / "百分之七十"（简单形式）
# - year_re 匹配     "1 年" / "3 年" / "三年" / "3年以上"
# - amount_re 匹配   "50 万元" / "12000 元" / "¥50000" / "30000元"
# - age_re 匹配      "18 岁" / "65 岁以上"
# - ratio_re 匹配    "1:3" / "三比一"
# - date_re 匹配     "2024-01-01" / "2024年1月1日" / "2024/01/01"

_NUM_CN = r"[零一二三四五六七八九十百千万亿两]"

_RE_PERCENT = re.compile(
    r"(?:(\d+(?:\.\d+)?)\s*%)"
    r"|(?:百分之(" + _NUM_CN + r"+))",
)
_RE_YEAR_DURATION = re.compile(
    r"(?:(\d+(?:\.\d+)?)\s*年(?:以[上下]|及以[上下]|)?)"
    r"|(?:(" + _NUM_CN + r"+)\s*年(?:以[上下]|)?)"
)
_RE_AMOUNT = re.compile(
    r"(?:[¥￥]\s*(\d+(?:,\d{3})*(?:\.\d+)?))"
    r"|(?:(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:万元|元|万))"
)
_RE_AGE = re.compile(
    r"(?:(\d+)\s*(?:周岁|岁))"
    r"|(?:(" + _NUM_CN + r"+)\s*(?:周岁|岁))"
)
_RE_DATE = re.compile(
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?"
    r"|\d{4}[-/]\d{1,2}"
)
_RE_BARE_NUMBER = re.compile(r"(?<![\w-])(\d+(?:\.\d+)?)(?![\w%-])")


# ────────────────────────────── 抽取单元 ──────────────────────────────


@dataclass(frozen=True)
class NumberMatch:
    """一个从文本里抽出来的数字断言。"""

    kind: str   # "percent" | "year_duration" | "amount" | "age" | "date" | "bare"
    raw: str    # 原文片段
    normalized: str  # 用于匹配的规范化字符串（去掉单位、逗号、空白等）
    span: tuple[int, int]  # 在源文本中的位置

    def matches(self, other: "NumberMatch") -> bool:
        """两个 match 是不是同一个数字（normalized 相等 + kind 可宽容匹配）."""
        if self.normalized != other.normalized:
            return False
        if self.kind == other.kind:
            return True
        # bare 可以匹配任何 kind（方便 answer 里漏单位的场景）
        return self.kind == "bare" or other.kind == "bare"


@dataclass
class Evidence:
    """一条 chunk 在 index 里的视图。"""

    citation_index: int           # 在本轮 evidence 序列里的 [N] 编号
    chunk_id: str
    doc_id: str
    doc_name: str
    page: int | None
    content: str
    numbers: list[NumberMatch] = field(default_factory=list)

    def normalized_numbers(self) -> set[tuple[str, str]]:
        """(kind, normalized) 去重 set，用于 validator 的集合判断."""
        return {(n.kind, n.normalized) for n in self.numbers}


# ────────────────────────────── 索引本体 ──────────────────────────────


class EvidenceIndex:
    """一轮 run 的 evidence 汇总。

    使用方式：
      idx = EvidenceIndex()
      idx.add_from_rag_retrieve(tool_result_json)  # rag_retrieve 的返回
      idx.add_from_rag_read_doc(tool_result_json)  # rag_read_doc 的返回
      ...
      evidence = idx.lookup_by_citation_id(3)

    按照工具结果进入的顺序分配 [N]。
    """

    def __init__(self) -> None:
        self._items: list[Evidence] = []
        self._by_chunk_id: dict[str, Evidence] = {}

    # ────── 添加 ──────

    def add_from_rag_retrieve(self, result: Any) -> None:
        parsed = _to_dict(result)
        if not parsed:
            return
        for ck in parsed.get("chunks") or []:
            self._add_chunk(ck)

    def add_from_rag_read_doc(self, result: Any) -> None:
        """rag_read_doc 返回整个文档（或指定页段），塞成一条 Evidence."""
        parsed = _to_dict(result)
        if not parsed:
            return
        doc_id = parsed.get("doc_id") or ""
        doc_name = parsed.get("doc_name") or ""
        pages = parsed.get("pages") or []
        if isinstance(pages, list) and pages:
            for idx, p in enumerate(pages):
                page_num = (p or {}).get("page") if isinstance(p, dict) else None
                content = (p or {}).get("content", "") if isinstance(p, dict) else str(p)
                self._add_chunk({
                    "doc_id": doc_id,
                    "doc_name": doc_name,
                    "page": page_num,
                    "content": content,
                    "chunk_id": f"readdoc:{doc_id}:{idx}",
                })
        elif parsed.get("content"):
            self._add_chunk({
                "doc_id": doc_id,
                "doc_name": doc_name,
                "page": parsed.get("page"),
                "content": parsed.get("content"),
                "chunk_id": parsed.get("chunk_id") or f"readdoc:{doc_id}",
            })

    def add_from_rag_graph_query(self, result: Any) -> None:
        """graph query 常返实体 + 相关 chunks；用后者."""
        parsed = _to_dict(result)
        if not parsed:
            return
        for ck in parsed.get("chunks") or []:
            self._add_chunk(ck)

    def _add_chunk(self, ck: dict) -> None:
        chunk_id = (
            ck.get("chunk_id")
            or ck.get("id")
            or f"{ck.get('doc_id', 'unknown')}#{len(self._items)}"
        )
        if chunk_id in self._by_chunk_id:
            # 同一 chunk 重复返回：保留第一次的编号
            return
        content = str(ck.get("content") or ck.get("content_with_weight") or "")
        if not content.strip():
            return
        ev = Evidence(
            citation_index=len(self._items) + 1,
            chunk_id=str(chunk_id),
            doc_id=str(ck.get("doc_id") or ""),
            doc_name=str(ck.get("doc_name") or ck.get("docnm_kwd") or ""),
            page=ck.get("page") or ck.get("page_num") or None,
            content=content,
            numbers=extract_numbers(content),
        )
        self._items.append(ev)
        self._by_chunk_id[ev.chunk_id] = ev

    # ────── 查询 ──────

    def __len__(self) -> int:
        return len(self._items)

    def all_evidence(self) -> list[Evidence]:
        return list(self._items)

    def lookup_by_citation_id(self, n: int) -> Evidence | None:
        if n < 1 or n > len(self._items):
            return None
        return self._items[n - 1]

    def all_normalized_numbers(self) -> set[tuple[str, str]]:
        """所有 evidence 的 (kind, normalized) 去重集合。"""
        acc: set[tuple[str, str]] = set()
        for ev in self._items:
            acc.update(ev.normalized_numbers())
        return acc

    def doc_name_set(self) -> set[str]:
        return {ev.doc_name for ev in self._items if ev.doc_name}


# ────────────────────────────── 抽取实现 ──────────────────────────────


# 简易中文数字 → 阿拉伯（够用即可；罕见表达不追完整正确）
_CN_NUM_TABLE = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000,
    "万": 10000, "亿": 100000000,
}


def _cn_to_num(s: str) -> str:
    """把纯中文数字串简单转成阿拉伯；失败返原串."""
    try:
        total = 0
        unit = 1
        buf = 0
        for ch in reversed(s):
            if ch not in _CN_NUM_TABLE:
                return s
            v = _CN_NUM_TABLE[ch]
            if v >= 10:
                if v > unit:
                    unit = v
                else:
                    unit = unit * v
                if buf:
                    total += buf * unit
                    buf = 0
            else:
                buf = v
        total += buf * unit if buf else 0
        return str(total) if total else s
    except Exception:
        return s


def _strip_commas(s: str) -> str:
    return s.replace(",", "")


def extract_numbers(text: str) -> list[NumberMatch]:
    """从一段文本里抽出所有"断言级"数字。顺序按出现位置。"""
    if not text:
        return []
    out: list[NumberMatch] = []

    for m in _RE_PERCENT.finditer(text):
        raw = m.group(0)
        v = m.group(1) or _cn_to_num(m.group(2) or "")
        out.append(NumberMatch("percent", raw, _strip_commas(v or "").rstrip("."), m.span()))

    for m in _RE_YEAR_DURATION.finditer(text):
        raw = m.group(0)
        v = m.group(1) or _cn_to_num(m.group(2) or "")
        out.append(NumberMatch("year_duration", raw, _strip_commas(v or "").rstrip("."), m.span()))

    for m in _RE_AMOUNT.finditer(text):
        raw = m.group(0)
        v = m.group(1) or m.group(2) or ""
        v = _strip_commas(v).rstrip(".")
        # "50 万元" / "50 万" → 展开
        if "万" in raw:
            try:
                v = str(int(float(v) * 10000))
            except Exception:
                pass
        out.append(NumberMatch("amount", raw, v, m.span()))

    for m in _RE_AGE.finditer(text):
        raw = m.group(0)
        v = m.group(1) or _cn_to_num(m.group(2) or "")
        out.append(NumberMatch("age", raw, _strip_commas(v or "").rstrip("."), m.span()))

    for m in _RE_DATE.finditer(text):
        raw = m.group(0)
        # 规范成 YYYY-MM-DD
        parts = re.split(r"[-/年月日]", raw)
        parts = [p for p in parts if p]
        if len(parts) >= 3:
            y, mo, d = parts[0], parts[1].zfill(2), parts[2].zfill(2)
            norm = f"{y}-{mo}-{d}"
        elif len(parts) == 2:
            norm = f"{parts[0]}-{parts[1].zfill(2)}"
        else:
            norm = raw
        out.append(NumberMatch("date", raw, norm, m.span()))

    # bare number — 只在没有被上面命中 span 覆盖时才加
    covered = [(s, e) for nm in out for s, e in [nm.span]]
    for m in _RE_BARE_NUMBER.finditer(text):
        s, e = m.span()
        if any(cs <= s and e <= ce for cs, ce in covered):
            continue
        raw = m.group(0)
        out.append(NumberMatch("bare", raw, _strip_commas(raw).rstrip("."), (s, e)))

    # 按位置排序
    out.sort(key=lambda nm: nm.span[0])
    return out


# ────────────────────────────── 私有 ──────────────────────────────


def _to_dict(result: Any) -> dict | None:
    """把 tool_result 规范化成 dict。兼容 MCP 返回体 / JSON 字符串 / dict。"""
    if result is None:
        return None
    if isinstance(result, dict):
        # MCP 格式：{"content": [{"type": "text", "text": "..."}]}
        content = result.get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text") if isinstance(content[0], dict) else None
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except Exception:
                    return None
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except Exception:
            return None
    return None

"""Phase 2.5.1 — citation validator / evidence index / strict rewrite 单测。

覆盖目标：
- ``extract_numbers`` 对百分比 / 年限 / 金额 / 岁数 / 日期 / 裸数的正负边界
- ``EvidenceIndex`` 往返：从 rag_retrieve / rag_read_doc 结果喂入、按序号查
- ``validate_citations`` 三条规则（missing_chunk / number_unsupported /
  no_citation_for_numeric）+ rule 2 先发后 rule 3 不重报
- ``rewrite_answer_strict`` 成功 / 空 auth / HTTP 非 200 / 空 evidence 兜底
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.agent_v2.validators import (
    CitationExtractor,
    EvidenceIndex,
    rewrite_answer_strict,
    rewrite_to_no_basis,
    validate_citations,
)
from api.agent_v2.validators.evidence_index import extract_numbers


# ───────── extract_numbers ─────────


@pytest.mark.p1
class TestExtractNumbers:
    def test_percentage_arabic(self):
        nums = extract_numbers("租金补贴 50% 或 70%。")
        kinds = [n.kind for n in nums]
        normed = [n.normalized for n in nums]
        assert "percent" in kinds
        assert "50" in normed and "70" in normed

    def test_percentage_chinese(self):
        nums = extract_numbers("优惠百分之七十。")
        assert any(n.kind == "percent" and n.normalized == "70" for n in nums)

    def test_year_duration_arabic_and_chinese(self):
        nums = extract_numbers("社保满 3 年；需居住五年以上。")
        y = [n for n in nums if n.kind == "year_duration"]
        assert len(y) >= 2
        normed = {n.normalized for n in y}
        assert "3" in normed
        assert "5" in normed

    def test_amount_with_wan(self):
        nums = extract_numbers("补贴 50 万元 / 月收入 12000 元。")
        amounts = [n for n in nums if n.kind == "amount"]
        normed = {n.normalized for n in amounts}
        assert "500000" in normed  # 50 万 展开
        assert "12000" in normed

    def test_age(self):
        nums = extract_numbers("年满 18 周岁，65 岁以上免费。")
        ages = {n.normalized for n in nums if n.kind == "age"}
        assert ages == {"18", "65"}

    def test_date_normalization(self):
        nums = extract_numbers("生效日期 2024-06-01，截止 2024/12/31。")
        dates = {n.normalized for n in nums if n.kind == "date"}
        assert "2024-06-01" in dates
        assert "2024-12-31" in dates

    def test_bare_number_not_overlapping_kinded(self):
        """裸数字只有在没被上层 kind 覆盖时才出现。"""
        nums = extract_numbers("申请 3 年 社保")
        kinds = [n.kind for n in nums]
        # "3" 是 year_duration，不应该被 bare 再收一遍
        assert kinds.count("year_duration") == 1
        assert "bare" not in kinds or all(
            n.kind != "year_duration" for n in nums if n.kind == "bare" and n.normalized == "3"
        )

    def test_empty_text(self):
        assert extract_numbers("") == []
        assert extract_numbers(None) == []  # type: ignore[arg-type]


# ───────── EvidenceIndex ─────────


@pytest.mark.p1
class TestEvidenceIndex:
    def test_add_from_rag_retrieve_assigns_sequential_citation_ids(self):
        idx = EvidenceIndex()
        idx.add_from_rag_retrieve({
            "chunks": [
                {"chunk_id": "a", "content": "第一段", "doc_id": "d1", "doc_name": "甲"},
                {"chunk_id": "b", "content": "第二段", "doc_id": "d1", "doc_name": "甲"},
            ]
        })
        assert len(idx) == 2
        assert idx.lookup_by_citation_id(1).chunk_id == "a"
        assert idx.lookup_by_citation_id(2).chunk_id == "b"
        assert idx.lookup_by_citation_id(3) is None
        assert idx.lookup_by_citation_id(0) is None

    def test_same_chunk_id_deduped(self):
        idx = EvidenceIndex()
        idx.add_from_rag_retrieve({"chunks": [{"chunk_id": "x", "content": "hello"}]})
        idx.add_from_rag_retrieve({"chunks": [{"chunk_id": "x", "content": "hello again"}]})
        assert len(idx) == 1  # 后者被去重

    def test_accepts_json_string_payload(self):
        idx = EvidenceIndex()
        idx.add_from_rag_retrieve('{"chunks": [{"chunk_id":"a","content":"text"}]}')
        assert len(idx) == 1

    def test_accepts_mcp_envelope(self):
        idx = EvidenceIndex()
        idx.add_from_rag_retrieve({
            "content": [{"type": "text", "text": '{"chunks":[{"chunk_id":"m","content":"body"}]}'}]
        })
        assert len(idx) == 1
        assert idx.lookup_by_citation_id(1).chunk_id == "m"

    def test_skip_empty_content(self):
        idx = EvidenceIndex()
        idx.add_from_rag_retrieve({"chunks": [{"chunk_id": "empty", "content": ""}]})
        assert len(idx) == 0

    def test_read_doc_pages(self):
        idx = EvidenceIndex()
        idx.add_from_rag_read_doc({
            "doc_id": "d1",
            "doc_name": "保租房办法",
            "pages": [
                {"page": 1, "content": "第一页内容 18 周岁"},
                {"page": 2, "content": "第二页内容"},
            ],
        })
        assert len(idx) == 2
        ev1 = idx.lookup_by_citation_id(1)
        assert ev1.page == 1
        assert any(n.normalized == "18" for n in ev1.numbers)

    def test_all_normalized_numbers_collects_union(self):
        idx = EvidenceIndex()
        idx.add_from_rag_retrieve({
            "chunks": [
                {"chunk_id": "a", "content": "社保满 3 年"},
                {"chunk_id": "b", "content": "年满 18 周岁"},
            ]
        })
        acc = idx.all_normalized_numbers()
        assert ("year_duration", "3") in acc
        assert ("age", "18") in acc


# ───────── validate_citations ─────────


@pytest.fixture
def evidence_sample():
    """两条 chunk：一条讲社保 + 年龄，一条讲租金补贴。"""
    idx = EvidenceIndex()
    idx.add_from_rag_retrieve({
        "chunks": [
            {
                "chunk_id": "c1",
                "doc_id": "d1",
                "doc_name": "保租房办法",
                "content": "申请人须年满 18 周岁，社保满 3 年。",
            },
            {
                "chunk_id": "c2",
                "doc_id": "d1",
                "doc_name": "保租房办法",
                "content": "租金补贴 50%。",
            },
        ]
    })
    return idx


@pytest.mark.p0
class TestValidateCitations:
    def test_clean_answer_no_issues(self, evidence_sample):
        text = "申请人须年满 18 周岁 [1]，社保满 3 年 [1]，可享 50% 租金补贴 [2]。"
        issues = validate_citations(text, evidence_sample, numeric_strict=True)
        assert issues == []

    def test_missing_chunk_rule_fires(self, evidence_sample):
        text = "详见政策 [9]。"  # evidence 只有 [1][2]
        issues = validate_citations(text, evidence_sample, numeric_strict=True)
        kinds = [i.kind for i in issues]
        assert "missing_chunk" in kinds
        assert any(i.citation_index == 9 for i in issues)

    def test_missing_chunk_ignores_markdown_links(self, evidence_sample):
        # [label](url) 不应该被 [N] 正则抓到
        text = "参考 [政策官网](https://example.com)。"
        issues = validate_citations(text, evidence_sample, numeric_strict=True)
        assert all(i.kind != "missing_chunk" for i in issues)

    def test_number_unsupported(self, evidence_sample):
        text = "申请人须年满 21 周岁 [1]。"  # 21 岁不在 evidence（18 岁）
        issues = validate_citations(text, evidence_sample, numeric_strict=True)
        assert any(i.kind == "number_unsupported" for i in issues)

    def test_no_citation_for_numeric(self, evidence_sample):
        """数字在 evidence 里但附近没引用 — rule 3 应命中。"""
        text = "申请人须年满 18 周岁，社保满 3 年。"
        issues = validate_citations(text, evidence_sample, numeric_strict=True)
        kinds = [i.kind for i in issues]
        assert kinds.count("no_citation_for_numeric") == 2  # 18 岁 + 3 年

    def test_rule2_takes_precedence_over_rule3(self, evidence_sample):
        """一个 claim 同时缺 evidence 且无引用时，只报 rule 2，不双报。"""
        text = "申请人须年满 21 周岁。"  # 既无出处（21≠18） 也无 [N]
        issues = validate_citations(text, evidence_sample, numeric_strict=True)
        # 该 claim 应只出现在 number_unsupported，不应在 no_citation_for_numeric 里重复
        number_issues = [i for i in issues if "21" in i.claim or "21" in i.detail]
        assert len(number_issues) == 1
        assert number_issues[0].kind == "number_unsupported"

    def test_numeric_strict_false_skips_rule2_and_3(self, evidence_sample):
        text = "申请人须年满 21 周岁。"
        issues = validate_citations(text, evidence_sample, numeric_strict=False)
        # 仅 rule 1 可能触发；rule 2/3 被跳过
        assert all(i.kind == "missing_chunk" for i in issues)

    def test_empty_final_text(self, evidence_sample):
        assert validate_citations("", evidence_sample, numeric_strict=True) == []

    def test_bare_numbers_ignored_in_rule2(self, evidence_sample):
        """页码 / 标题序号不该被 rule 2 拦（kind=bare 默认跳过）."""
        text = "见第 2 节 [1]。"  # "2" 是 bare，无单位
        issues = validate_citations(text, evidence_sample, numeric_strict=True)
        assert all(i.kind != "number_unsupported" for i in issues)


# ───────── CitationExtractor helpers ─────────


@pytest.mark.p2
class TestCitationExtractor:
    def test_consecutive_citations(self):
        cites = CitationExtractor.extract_citations("条款 [1][2][3]。")
        assert [n for n, _ in cites] == [1, 2, 3]

    def test_extractor_does_not_match_markdown_links(self):
        cites = CitationExtractor.extract_citations("[see](http://x)")
        assert cites == []


# ───────── rewrite_answer_strict ─────────


@pytest.mark.p1
@pytest.mark.asyncio
class TestRewriteAnswerStrict:
    async def test_empty_auth_token_returns_none(self, evidence_sample):
        result = await rewrite_answer_strict(
            original_text="some",
            issues=[{"kind": "number_unsupported", "detail": "x", "claim": "y"}],
            evidence=evidence_sample,
            model="claude-sonnet-4-5",
            base_url=None,
            auth_token="",  # 空
        )
        assert result is None

    async def test_empty_issues_returns_none(self, evidence_sample):
        result = await rewrite_answer_strict(
            original_text="some",
            issues=[],
            evidence=evidence_sample,
            model="claude-sonnet-4-5",
            base_url=None,
            auth_token="sk-fake",
        )
        assert result is None

    async def test_empty_evidence_returns_none(self):
        empty_idx = EvidenceIndex()
        result = await rewrite_answer_strict(
            original_text="some",
            issues=[{"kind": "number_unsupported", "detail": "x", "claim": "y"}],
            evidence=empty_idx,
            model="claude-sonnet-4-5",
            base_url=None,
            auth_token="sk-fake",
        )
        assert result is None

    async def test_successful_rewrite_returns_text(self, evidence_sample):
        mock_resp = _mock_httpx_response(
            200,
            {"content": [{"type": "text", "text": "重写后的答复 [1]"}]},
        )
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            result = await rewrite_answer_strict(
                original_text="原答复",
                issues=[{"kind": "number_unsupported", "detail": "x", "claim": "y"}],
                evidence=evidence_sample,
                model="claude-sonnet-4-5",
                base_url=None,
                auth_token="sk-fake",
            )
        assert result == "重写后的答复 [1]"

    async def test_non_200_returns_none(self, evidence_sample):
        mock_resp = _mock_httpx_response(500, {}, text="server error")
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            result = await rewrite_answer_strict(
                original_text="原",
                issues=[{"kind": "number_unsupported", "detail": "x", "claim": "y"}],
                evidence=evidence_sample,
                model="claude-sonnet-4-5",
                base_url=None,
                auth_token="sk-fake",
            )
        assert result is None

    async def test_exception_returns_none(self, evidence_sample):
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=RuntimeError("boom")
            )
            result = await rewrite_answer_strict(
                original_text="原",
                issues=[{"kind": "number_unsupported", "detail": "x", "claim": "y"}],
                evidence=evidence_sample,
                model="claude-sonnet-4-5",
                base_url=None,
                auth_token="sk-fake",
            )
        assert result is None


# ───────── citation_without_evidence (Phase 2.8.2) ─────────


@pytest.mark.p1
class TestCitationWithoutEvidence:
    """Phase 2.8.2 — empty-evidence harness check.

    Mirrors claude-code-ref's pattern: distinct VERDICT-style label per
    distinct root cause, downstream rules suppressed when the dominant
    cause already explains everything.
    """

    def test_fires_when_evidence_empty_and_cite_present(self):
        empty_idx = EvidenceIndex()
        text = "申请人须年满 18 周岁 [1]，社保满 3 年 [2]。"
        issues = validate_citations(text, empty_idx, numeric_strict=True)
        assert len(issues) == 1
        assert issues[0].kind == "citation_without_evidence"
        assert issues[0].citation_index == 1  # 第一个 [N]
        # detail 必须报"实际有几个 [N]"，便于运维诊断
        assert "2 citation marker" in issues[0].detail

    def test_does_not_fire_when_evidence_empty_but_no_cite(self):
        """模型说"未查到相关规定"（无 [N]）→ 这是正确行为，不应报警。"""
        empty_idx = EvidenceIndex()
        text = "本知识库未直接覆盖此问题，请咨询当地住建局。"
        issues = validate_citations(text, empty_idx, numeric_strict=True)
        # 不应有 citation_without_evidence；可能仍有 no_citation_for_numeric，
        # 但本测试用例里没有数字断言，所以应完全空
        assert all(i.kind != "citation_without_evidence" for i in issues)

    def test_does_not_fire_when_evidence_present(self, evidence_sample):
        """有 evidence 时即使 [N] 编号超界也走 missing_chunk 旧路径，不走新规则。"""
        text = "见 [9]。"  # evidence_sample 只有 [1][2]
        issues = validate_citations(text, evidence_sample, numeric_strict=True)
        kinds = [i.kind for i in issues]
        assert "missing_chunk" in kinds
        assert "citation_without_evidence" not in kinds

    def test_suppresses_downstream_noise(self):
        """关键短路语义：触发后不应再多发 missing_chunk / number_unsupported。

        否则会出现 N+M 条同根因 issue 把告警面板淹没。
        """
        empty_idx = EvidenceIndex()
        # 3 个 [N] + 2 个数字断言：旧实现会发 3 + 2 = 5 条 issue
        text = "条件 [1][2][3]：年满 18 周岁，社保满 3 年。"
        issues = validate_citations(text, empty_idx, numeric_strict=True)
        # 新实现：只发 1 条聚合 issue
        assert len(issues) == 1
        assert issues[0].kind == "citation_without_evidence"


# ───────── rewrite_to_no_basis ─────────


@pytest.mark.p1
@pytest.mark.asyncio
class TestRewriteToNoBasis:
    async def test_no_auth_token_returns_none(self):
        result = await rewrite_to_no_basis(
            original_text="原答复 [1]",
            citation_count=1,
            fallback_hint=None,
            model="claude-sonnet-4-5",
            base_url=None,
            auth_token="",
        )
        assert result is None

    async def test_zero_citation_count_returns_none(self):
        """没有 [N] 就没有"需要校正"的事，调用方逻辑错误时 fail-safe。"""
        result = await rewrite_to_no_basis(
            original_text="原答复",
            citation_count=0,
            fallback_hint=None,
            model="claude-sonnet-4-5",
            base_url=None,
            auth_token="sk-fake",
        )
        assert result is None

    async def test_successful_rewrite(self):
        mock_resp = _mock_httpx_response(
            200,
            {"content": [{"type": "text", "text": "本知识库未直接覆盖该问题，请咨询当地住建局。"}]},
        )
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            result = await rewrite_to_no_basis(
                original_text="申请人须年满 18 周岁 [1]，社保满 3 年 [2]。",
                citation_count=2,
                fallback_hint="请咨询当地住建局",
                model="claude-sonnet-4-5",
                base_url=None,
                auth_token="sk-fake",
            )
        assert result is not None
        assert "[1]" not in result
        assert "[2]" not in result

    async def test_fallback_hint_injected_into_system_prompt(self):
        """传入 fallback_hint 时必须出现在 system prompt 里 —— 这是 supervisor
        领域定制（深圳保障房 vs 通用法务）的关键参数."""
        captured: dict = {}

        async def _capture(url, headers=None, json=None):
            captured["payload"] = json
            return _mock_httpx_response(
                200,
                {"content": [{"type": "text", "text": "ok"}]},
            )

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=_capture)
            await rewrite_to_no_basis(
                original_text="原 [1]",
                citation_count=1,
                fallback_hint="请咨询当地住建局",
                model="claude-sonnet-4-5",
                base_url=None,
                auth_token="sk-fake",
            )
        sys_prompt = captured["payload"]["system"]
        assert "请咨询当地住建局" in sys_prompt
        # claude-code 风格 prompt 必须包含对抗式定位 + 命名 rationalization
        assert "FAILURE MODES" in sys_prompt
        assert "Forbidden openings" in sys_prompt

    async def test_non_200_returns_none(self):
        mock_resp = _mock_httpx_response(500, {}, text="server error")
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            result = await rewrite_to_no_basis(
                original_text="原 [1]",
                citation_count=1,
                fallback_hint=None,
                model="claude-sonnet-4-5",
                base_url=None,
                auth_token="sk-fake",
            )
        assert result is None

    async def test_exception_returns_none(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=RuntimeError("boom")
            )
            result = await rewrite_to_no_basis(
                original_text="原 [1]",
                citation_count=1,
                fallback_hint=None,
                model="claude-sonnet-4-5",
                base_url=None,
                auth_token="sk-fake",
            )
        assert result is None


# ───────── helpers ─────────


def _mock_httpx_response(status_code: int, json_body: dict, *, text: str = "") -> object:
    class _R:
        def __init__(self):
            self.status_code = status_code
            self.text = text or ""
            self._body = json_body

        def json(self):
            return self._body

    return _R()

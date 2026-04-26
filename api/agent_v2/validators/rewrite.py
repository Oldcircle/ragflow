"""Strict-mode one-shot rewrites for citation failures (P2.5.1 + P2.8.2).

复用 compactor 的 HTTP /v1/messages 直连思路，不 fork Claude CLI 子进程：

- 对 Anthropic (base_url=None) 和 DeepSeek-anthropic 都兼容
- 不经 Claude Agent SDK（SDK 跑工具循环太重、对单次 rewrite 浪费）
- 单次调用、有超时、失败返 None 让调用方降级

提供两个互斥的 rewrite 入口（参照 claude-code-ref 的 "dedicated tool for
dedicated condition" 模式 —— 见 VerifyPlanExecutionTool vs VerificationAgent
分离）：

- :func:`rewrite_answer_strict` — evidence ≥ 1，已有 [N] 但部分对不上证据
  时调，把答复"修"成全部 [N] 都有出处。
- :func:`rewrite_to_no_basis` — evidence == 0 但 Agent 凭空写了 [N]，
  把答复"降"成无出处的免责声明（同语言、不带 [N]、不留事实断言）。

两条路径共享 HTTP 调用骨架，但 system prompt 完全不同 —— 不重载一个函数
吃两种状态，避免分支地狱。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx

logger = logging.getLogger("ragflow.agent_v2.validators.rewrite")


def _format_evidence_for_prompt(evidence, max_chunks: int = 12, max_chars_each: int = 400) -> str:
    """把 EvidenceIndex 里前 N 条 chunk 截断后拼成 prompt block."""
    lines: list[str] = []
    for i, ev in enumerate(evidence.all_evidence()[:max_chunks], start=1):
        content = (ev.content or "").strip()
        if len(content) > max_chars_each:
            content = content[:max_chars_each] + "…"
        doc = ev.doc_name or ev.doc_id or "unknown"
        lines.append(f"[{i}] 来自《{doc}》：{content}")
    return "\n".join(lines)


def _format_issues(issues: Iterable[Any]) -> str:
    out: list[str] = []
    for iss in issues:
        kind = getattr(iss, "kind", None) or (iss.get("kind") if isinstance(iss, dict) else "")
        detail = getattr(iss, "detail", None) or (iss.get("detail") if isinstance(iss, dict) else "")
        claim = getattr(iss, "claim", None) or (iss.get("claim") if isinstance(iss, dict) else "")
        out.append(f"- [{kind}] {detail}\n  涉及片段：{claim}")
    return "\n".join(out)


async def rewrite_answer_strict(
    *,
    original_text: str,
    issues: list,
    evidence,
    model: str,
    base_url: str | None,
    auth_token: str,
    timeout_s: float = 30.0,
) -> str | None:
    """请 LLM 一次重写答复使其通过 citation 校验。

    返回新答复字符串；任何失败（auth / HTTP / 空串）返回 ``None``，
    调用方应据此降级为 fallback 文案。
    """
    if not original_text or not issues or not auth_token:
        return None
    if len(evidence) == 0:
        # 一个 chunk 都没有还要 strict rewrite 是矛盾的；直接让上层降级
        return None

    evidence_block = _format_evidence_for_prompt(evidence)
    issue_block = _format_issues(issues)

    system_prompt = (
        "你是企业知识库答复校正助手。用户给你：\n"
        "1) 一段已生成但存在数字/引用问题的答复\n"
        "2) 本轮实际检索到的 evidence（编号 [N]）\n"
        "3) citation validator 报出的具体问题\n\n"
        "你必须重写这段答复，严格遵守：\n"
        "A. 只保留能由 evidence 直接支撑的事实 / 数字 / 金额 / 年限 / 日期\n"
        "B. 每一条保留下来的事实**必须**在句末带 [N] 引用，指向对应 evidence 编号\n"
        "C. 原答复里没被 evidence 支撑的数字 / 断言，替换为'未查到相关规定'或直接删除\n"
        "D. 不得凭训练知识补充 evidence 以外的内容\n"
        "E. 保留与原答复相同的语言和结构\n"
        "F. 不输出解释性前言 / 结语；只输出重写后的答复正文"
    )

    user_prompt = (
        "<original-answer>\n"
        f"{original_text}\n"
        "</original-answer>\n\n"
        "<evidence>\n"
        f"{evidence_block}\n"
        "</evidence>\n\n"
        "<validator-issues>\n"
        f"{issue_block}\n"
        "</validator-issues>\n\n"
        "请重写。"
    )

    endpoint = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": auth_token,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 2048,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
        if resp.status_code != 200:
            logger.warning(
                "rewrite_answer_strict HTTP %s — %s",
                resp.status_code,
                resp.text[:400],
            )
            return None
        data = resp.json()
        content = data.get("content") or []
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(blk.get("text") or "")
        rewritten = "".join(parts).strip()
        return rewritten or None
    except Exception:
        logger.exception("rewrite_answer_strict failed")
        return None


# ──────────────────  rewrite_to_no_basis (P2.8.2)  ──────────────────


async def rewrite_to_no_basis(
    *,
    original_text: str,
    citation_count: int,
    fallback_hint: str | None = None,
    model: str,
    base_url: str | None,
    auth_token: str,
    timeout_s: float = 20.0,
) -> str | None:
    """对"无证据却带 [N]"的答复做 no-basis 重写。

    与 :func:`rewrite_answer_strict` 互斥：那个要求 evidence ≥ 1 才能"修补"
    引用；这个针对 evidence == 0 的相反场景，把答复"降"成无出处免责声明，
    同语言、不带 [N]、不留任何事实断言。

    Args:
        original_text: 含幻觉 [N] 的原答复。
        citation_count: 原文里 [N] 总数（仅作 prompt 提示用）。
        fallback_hint: 上层可注入领域专属的 fallback 文案后缀，例如
            "请咨询当地住建局"。None 时只输出通用免责语。
        timeout_s: 默认 20s（比 strict rewrite 短，因为输出短得多）。

    Returns:
        重写后的答复字符串；任何失败返回 None，调用方应据此降级到
        硬编码 fallback 文案。

    设计参照 claude-code-ref/packages/builtin-tools/src/tools/AgentTool/
    built-in/verificationAgent.ts —— 给 LLM 的 system prompt 必须直白、
    禁止"理性化跳过"（"the answer looks correct based on training" 是
    最常见的 rationalization）。
    """
    if not original_text or not auth_token or citation_count <= 0:
        return None

    fallback_clause = (
        f"\nYou MAY append one short sentence steering the user to "
        f'authoritative help: "{fallback_hint.strip()}". Do not invent a '
        f"different one."
        if fallback_hint and fallback_hint.strip()
        else ""
    )

    # Prompt 设计参照 claude-code-ref/packages/builtin-tools/src/tools/
    # AgentTool/built-in/verificationAgent.ts —— 对抗式定位 + 命名 LLM
    # 自己最常见的"理性化跳过"借口 + 严格输出锚点。
    #
    # 关键模式：
    #   1. "Your job is not X — it's Y": 一句话点明这是反向任务（删除而非
    #      保留），堵住"看起来还挺合理就留着吧"的本能
    #   2. "Failure modes you reach for": 列 LLM 真实会说的 3 种借口，给出
    #      明确的反例，不留模糊空间
    #   3. "Forbidden phrases": 直接禁掉道歉 / 解释 / hedge 开场白
    #   4. "Output anchor": 数字化字数上限 + 必须以哪种结构开头，
    #      防止"我重写了一下，新版本是..."这类 meta 套话
    system_prompt = f"""You are a knowledge-base answer corrector. The model that wrote this answer ATTACHED [N] citation footnotes WITHOUT calling any retrieval tool this turn — every [N] is fabricated. Your job is not to "polish" the answer, it's to STRIP it down to a no-basis disclaimer.

=== FAILURE MODES YOU WILL REACH FOR ===
You will feel the urge to preserve content. These are the exact excuses you reach for — recognize them and do the opposite:
- "The claim looks correct, I should keep it." — No. No retrieval = no source. Strip it.
- "The [N] markers are probably wrong but the prose is fine, I'll just remove the brackets." — No. Removing brackets while keeping the unsourced claim is the same hallucination, just less visible.
- "I should explain why this is being rewritten." — No. The user does not need a meta-explanation; just deliver the disclaimer.
- "I'll soften it: 'According to public information…'" — No. There is no public source to attribute to. Say plainly that the KB does not cover this.

=== HARD RULES ===
A. Remove EVERY [N] / [1] / [2] / [12] footnote marker. Zero remaining.
B. Remove EVERY concrete fact: numbers, percentages, currency amounts, dates, year spans, age thresholds, law / regulation / document names, named officials, named programs.
C. Rewrite as a brief disclaimer: tell the user the knowledge base does not directly cover this question. Keep the SAME language as the original answer (Chinese stays Chinese, English stays English).{fallback_clause}
D. Length cap: ≤ 80 Chinese chars OR ≤ 60 English words. Hard ceiling, not target.
E. Forbidden openings: "Sorry, …" / "抱歉…" / "I apologize…" / "Let me rewrite…" / "我重写了一下…" / "经过校验…" / Any meta-narration about the rewriting itself.

=== OUTPUT ===
Output ONLY the rewritten answer body. No preamble, no postscript, no quote marks around it, no markdown code fence. The first character of your output is the first character the user will see.
"""

    user_prompt = (
        "<original-answer-with-phantom-citations>\n"
        f"{original_text}\n"
        "</original-answer-with-phantom-citations>\n\n"
        f"The original answer contains {citation_count} [N] markers, but this "
        f"turn's evidence index is empty (no rag_retrieve / rag_read_doc / "
        f"rag_graph_query produced any chunk). Strip and rewrite per the rules."
    )

    endpoint = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": auth_token,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 400,  # 输出短，500 token 绰绰有余
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
        if resp.status_code != 200:
            logger.warning(
                "rewrite_to_no_basis HTTP %s — %s",
                resp.status_code,
                resp.text[:400],
            )
            return None
        data = resp.json()
        content = data.get("content") or []
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(blk.get("text") or "")
        rewritten = "".join(parts).strip()
        return rewritten or None
    except Exception:
        logger.exception("rewrite_to_no_basis failed")
        return None

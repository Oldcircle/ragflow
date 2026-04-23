"""Strict-mode one-shot rewrite for citation failures (P2.5.1).

复用 compactor 的 HTTP /v1/messages 直连思路，不 fork Claude CLI 子进程：

- 对 Anthropic (base_url=None) 和 DeepSeek-anthropic 都兼容
- 不经 Claude Agent SDK（SDK 跑工具循环太重、对单次 rewrite 浪费）
- 单次调用、有超时、失败返 None 让调用方降级

rewrite prompt 强制：
1. 只保留能由 evidence 支撑的数字 / 金额 / 年限
2. 每个保留下来的数字后必须带 [N] 引用
3. 不能支撑的降级为"未查到相关规定"
4. 不添加 evidence 以外的新数字
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

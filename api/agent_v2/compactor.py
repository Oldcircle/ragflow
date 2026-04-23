"""Phase 2.5.2 — Conversation History Compactor。

当 session 的消息数积累超过阈值时，把靠前的旧消息压成一段摘要，
写回 ``agent_v2_session.summary_text`` + ``summary_until_seq``。
下一轮 AgentRunner 就只带「summary + 最近 N 条」而不是全量历史。

设计取舍
--------

- 不用 Claude Agent SDK（``query()``）做摘要：那会 fork Claude Code CLI 子
  进程，对一条单轮的总结任务太重。直接走 HTTP POST /v1/messages，既对
  Anthropic（base_url=None）也对 DeepSeek-anthropic（base_url=
  ``https://api.deepseek.com/anthropic``）管用，它们都兼容 Anthropic
  Messages API wire format。
- 不建新表：summary_text / summary_until_seq 已经在 session 表里。
- Compactor 是异步函数，由 HTTP 主流程在 assistant 落库之后显式 fire-and-
  forget 调起；不阻塞 SSE 返回。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("ragflow.agent_v2.compactor")


# 默认阈值：历史累积 > 20 条（约 10 轮 user+assistant）触发 compact
DEFAULT_COMPACT_TRIGGER_MSGS = 20
# 总结后保留最近 N 条消息作为"recent"窗口，其余并入 summary
DEFAULT_RECENT_KEEP_MSGS = 10


def should_compact(
    *,
    total_messages_since_last_compact: int,
    trigger_msgs: int = DEFAULT_COMPACT_TRIGGER_MSGS,
) -> bool:
    """判断是否需要触发 compact。

    暂时只看消息数阈值；token 预算阈值（30%）是 follow-up 改动。
    """
    return total_messages_since_last_compact >= trigger_msgs


def split_for_compact(
    messages: list[dict],
    *,
    recent_keep: int = DEFAULT_RECENT_KEEP_MSGS,
) -> tuple[list[dict], list[dict]]:
    """把消息拆成 (要摘要的旧部分, 要保留的近期部分)。

    返回 ``(to_summarize, to_keep)``。
    如果消息总数 ≤ ``recent_keep``，to_summarize 为空。
    """
    n = len(messages)
    if n <= recent_keep:
        return [], list(messages)
    split = n - recent_keep
    return list(messages[:split]), list(messages[split:])


async def summarize_history(
    messages: list[dict],
    *,
    model: str,
    base_url: str | None,
    auth_token: str,
    previous_summary: str = "",
    timeout_s: float = 30.0,
) -> str:
    """调 LLM 把一串 user/assistant 消息压成一段简短摘要。

    返回摘要字符串（失败时返回空串，调用方应据此跳过写入）。
    """
    if not messages:
        return ""
    if not auth_token:
        logger.warning("summarize_history: no auth_token, skipping")
        return ""

    transcript_lines: list[str] = []
    for m in messages:
        role = m.get("role") or ""
        if role not in ("user", "assistant"):
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant" and len(content) > 800:
            content = content[:800] + " …（已截断）"
        transcript_lines.append(f"- {role}: {content}")
    transcript = "\n".join(transcript_lines)
    if not transcript:
        return ""

    system_prompt = (
        "你是对话历史摘要助手。用户会给你一段 user/assistant 交替的对话片段，"
        "请输出一段简短摘要（≤ 300 字），要求：\n"
        "1. 保留用户问过的核心问题和关键约束（数字、时间、金额、实体）\n"
        "2. 保留 assistant 已经给出过的关键结论 + 引用编号（如 [1][2]）\n"
        "3. 不做评论、不添加新信息，只压缩\n"
        "4. 语言与对话一致"
    )
    user_prompt_parts: list[str] = []
    if previous_summary.strip():
        user_prompt_parts.append(
            f"<earlier-summary>\n{previous_summary.strip()}\n</earlier-summary>"
        )
    user_prompt_parts.append(
        f"<transcript>\n{transcript}\n</transcript>\n\n"
        f"请输出新的统一摘要（覆盖 earlier-summary + transcript）。"
    )

    endpoint = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": auth_token,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 600,
        "system": system_prompt,
        "messages": [{"role": "user", "content": "\n\n".join(user_prompt_parts)}],
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
        if resp.status_code != 200:
            logger.warning(
                "summarize_history HTTP %s — %s",
                resp.status_code,
                resp.text[:400],
            )
            return ""
        data = resp.json()
        # Anthropic Messages API: content = [{"type":"text","text":"..."}]
        content = data.get("content") or []
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(blk.get("text") or "")
        summary = "".join(parts).strip()
        return summary
    except Exception:
        logger.exception("summarize_history failed")
        return ""


async def maybe_compact_session(
    *,
    session_id: str,
    model: str,
    base_url: str | None,
    auth_token: str,
    trigger_msgs: int = DEFAULT_COMPACT_TRIGGER_MSGS,
    recent_keep: int = DEFAULT_RECENT_KEEP_MSGS,
) -> bool:
    """尝试对一个 session 做 compact，返回是否真的写了新摘要。

    fire-and-forget 调用：由 HTTP 主流程 ``asyncio.create_task(...)`` 包一下。
    """
    from api.db.services.agent_v2_service import (
        AgentV2MessageService,
        AgentV2SessionService,
    )

    session = AgentV2SessionService.get_by_id(session_id)
    if not session:
        return False
    prev_summary: str = getattr(session, "summary_text", None) or ""
    prev_until: int = int(getattr(session, "summary_until_seq", None) or 0)

    all_msgs = AgentV2MessageService.list_by_session(session_id)
    # 把已经并入 summary 的前 prev_until 条去掉
    if prev_until > 0:
        tail = all_msgs[prev_until:] if prev_until < len(all_msgs) else []
    else:
        tail = list(all_msgs)

    # 只看 user / assistant
    tail = [m for m in tail if m.get("role") in ("user", "assistant")]

    if not should_compact(
        total_messages_since_last_compact=len(tail),
        trigger_msgs=trigger_msgs,
    ):
        return False

    to_summarize, _ = split_for_compact(tail, recent_keep=recent_keep)
    if not to_summarize:
        return False

    logger.info(
        "compactor: session=%s summarizing %d msgs (prev_until=%d, tail=%d)",
        session_id,
        len(to_summarize),
        prev_until,
        len(tail),
    )
    summary = await summarize_history(
        to_summarize,
        model=model,
        base_url=base_url,
        auth_token=auth_token,
        previous_summary=prev_summary,
    )
    if not summary:
        return False

    # summary_until_seq 是"所有消息"里的 1-based 下标，不是只算 user/assistant
    # 所以要把 to_summarize 里每条在 all_msgs 里的位置求最大值
    # （to_summarize 来自 tail，tail 来自 all_msgs[prev_until:] 过滤后）
    # 简化：定位 to_summarize 的最后一条在 all_msgs 里的位置
    last_msg = to_summarize[-1]
    last_id = last_msg.get("id")
    new_until = prev_until
    for i, m in enumerate(all_msgs, start=1):
        if m.get("id") == last_id:
            new_until = i
            break

    AgentV2SessionService.save_summary(
        session_id=session_id,
        summary_text=summary,
        summary_until_seq=new_until,
    )
    logger.info(
        "compactor: session=%s wrote summary (%d chars), summary_until_seq=%d",
        session_id,
        len(summary),
        new_until,
    )
    return True

"""User-message decision-prefix parsers (Phase 2.6 v0.4 + Phase 2.8.3).

Two parallel parsers — both lift a structured intent off the front of a
``send_message`` body and return the cleaned remainder so the supervisor's
``<current-user-message>`` only sees the human note (if any), not the
machine prefix:

- :func:`parse_plan_decision` (Phase 2.6 v0.4) — consumes
  ``[plan approved|rejected|request changes]`` after a ``submit_plan`` turn.
- :func:`parse_question_answer` (Phase 2.8.3) — consumes
  ``[answer: <label>]`` after an ``ask_user_question`` pause.

Both pair with an ``augment_for_*`` helper that injects a system directive
explaining what the supervisor is supposed to do with the resumed state, so
domain-scoped supervisors don't fall back to "out-of-scope" when the
user-stripped message is empty.

Lives outside ``api/apps/`` so unit tests can import without pulling in the
Quart blueprint loader (which injects ``manager`` at runtime).
"""

from __future__ import annotations

_PLAN_DECISION_PATTERNS: list[tuple[str, str]] = [
    # (lowercased prefix, canonical status)
    ("plan approved", "approved"),
    ("plan approve", "approved"),
    ("plan accepted", "approved"),
    ("plan ok", "approved"),
    ("plan rejected", "rejected"),
    ("plan reject", "rejected"),
    ("plan deny", "rejected"),
    ("plan request changes", "request_changes"),
    ("plan request change", "request_changes"),
    ("plan changes", "request_changes"),
    ("计划批准", "approved"),
    ("计划通过", "approved"),
    ("计划拒绝", "rejected"),
    ("计划驳回", "rejected"),
    ("计划修改", "request_changes"),
]

# Longer prefixes must win over their shorter substrings (e.g. "plan approved"
# must match before "plan approve"). Sorting once at import time is simpler
# than tracking insertion order above.
_PLAN_DECISION_PATTERNS.sort(key=lambda kv: len(kv[0]), reverse=True)


def parse_plan_decision(message: str) -> tuple[str, str | None]:
    """Strip a ``[plan approved|rejected|request changes]`` prefix from a user
    message and return ``(cleaned_message, decision_status_or_None)``.

    The prefix is matched case-insensitively. Outer ``[...]`` is optional;
    trailing punctuation/whitespace on the prefix is tolerated. Anything after
    the prefix is kept verbatim so the user can combine an approval with a
    follow-up instruction in the same message.
    """
    if not isinstance(message, str) or not message.strip():
        return message, None

    stripped = message.lstrip()
    leading_ws_len = len(message) - len(stripped)

    # Bracketed form: [plan X] remainder
    if stripped.startswith("["):
        close = stripped.find("]")
        if 1 < close <= 64:
            inside = stripped[1:close].strip().lower()
            for prefix, status in _PLAN_DECISION_PATTERNS:
                if inside == prefix or inside.startswith(prefix):
                    tail = stripped[close + 1 :].lstrip(" \t:：-—,.")
                    return (" " * leading_ws_len) + tail, status

    # Bare prefix at the very start
    low = stripped.lower()
    for prefix, status in _PLAN_DECISION_PATTERNS:
        if low.startswith(prefix):
            rest = stripped[len(prefix) :]
            # Require EOL or non-alphanumeric boundary so e.g. "planner approved"
            # does NOT trigger on "plan approved".
            if not rest or not rest[0].isalnum():
                tail = rest.lstrip(" \t:：-—,.")
                return (" " * leading_ws_len) + tail, status

    return message, None


_PLAN_DIRECTIVE_MAP: dict[str, str] = {
    "approved": (
        "[plan system] The user approved the plan submitted in the previous turn. "
        "Its full payload is stored on this session. Spawn `sub_archivist` with "
        "a one-line description (e.g. 'execute approved plan'); the archivist "
        "will call `get_pending_plan` to read the exact steps and execute them "
        "in order, emitting `[step K/N done: ...]` markers. Do not try to do "
        "the writes yourself — you do not have write tools."
    ),
    "rejected": (
        "[plan system] The user rejected the plan submitted in the previous "
        "turn. Do NOT execute any part of it. Acknowledge the rejection, ask "
        "what they want to change, and stop. Do not spawn sub_archivist."
    ),
    "request_changes": (
        "[plan system] The user asked to revise the plan submitted in the "
        "previous turn. Spawn `sub_archivist` to re-plan with a new scope "
        "based on the user's note below; the archivist will call "
        "`submit_plan` again with the adjusted steps. Do not execute writes."
    ),
}


def augment_for_plan_decision(
    *,
    user_message: str,
    plan_decision: str | None,
    plan_status: str | None = None,
) -> str:
    """Phase 2.6 v0.6-fix — inject an explicit directive when a plan decision
    was parsed from the user message.

    Without this, a bare ``[plan approved]`` becomes an empty string after
    prefix-stripping, and the supervisor (scoped to its domain — housing
    policy, legal contracts, …) has nothing to latch onto. It falls back to
    "out-of-domain" and refuses.

    The directive we add is task-neutral: we don't mention housing, legal, or
    any domain. We only tell the supervisor *what action the plan system
    expects of it right now* + leave the user's original (stripped) message
    intact after the directive. The supervisor prompt is updated separately
    (builder.py) to recognize the ``[plan system]`` marker and override its
    domain-scope classifier when seeing it.
    """
    if not plan_decision:
        return user_message
    directive = _PLAN_DIRECTIVE_MAP.get(plan_decision)
    if not directive:
        return user_message

    residual = (user_message or "").strip()
    if residual:
        return f"{directive}\n\nUser's accompanying note:\n{residual}"
    return directive


__all__ = [
    "parse_plan_decision",
    "augment_for_plan_decision",
    "parse_question_answer",
    "augment_for_question_answer",
]


# ─────────────────────  parse_question_answer (Phase 2.8.3)  ─────────────────────


# Bracketed prefix: ``[answer: 选项A]`` / ``[user answer: A, B]`` / ``[选择: 是]``
# / ``[回答: 选项A]``. The colon is optional; whitespace is tolerated.
_ANSWER_PREFIX_BRACKETED: tuple[str, ...] = (
    "answer",
    "answers",
    "user answer",
    "user answers",
    "user-answer",
    "user-answers",
    "选择",
    "回答",
    "用户选择",
    "用户回答",
)

# Sort longer first so "user answer" wins over "answer" when both match.
_ANSWER_PREFIX_BRACKETED_SORTED = sorted(
    _ANSWER_PREFIX_BRACKETED, key=len, reverse=True
)


def _split_answer_labels(payload: str) -> list[str]:
    """Split a multi-label answer like "选项A, 选项B" / "A; B" / "A、B" into
    a clean list. Empty after split → empty list (caller should treat as
    "no answer parsed")."""
    if not payload:
        return []
    # Try comma / Chinese 顿号 / semicolon — any one of them.
    for sep in (",", "，", "、", ";", "；"):
        if sep in payload:
            return [p.strip() for p in payload.split(sep) if p.strip()]
    return [payload.strip()] if payload.strip() else []


def parse_question_answer(message: str) -> tuple[str, dict | None]:
    """Strip an ``[answer: <label>[, <label>]]`` prefix from a user message.

    Returns ``(cleaned_message, answer_dict_or_None)`` where ``answer_dict``,
    on a hit, is::

        {
            "labels": ["选项A"],            # always a list, len>=1; multi for multi_select
            "raw_payload": "选项A",          # exactly what was inside the bracket after the colon
        }

    A bare prefix without ``[]`` is **not** supported (unlike plan_decision
    which tolerates bare "plan approved"). Reason: option labels can be
    arbitrary user-visible strings ("选项A" / "Option B" / "yes please") and
    a bare match would cause too many false positives. The frontend always
    constructs the bracketed form, so we lose nothing.
    """
    if not isinstance(message, str) or not message.strip():
        return message, None

    stripped = message.lstrip()
    leading_ws_len = len(message) - len(stripped)

    if not stripped.startswith("["):
        return message, None

    close = stripped.find("]")
    if close <= 1 or close > 256:  # 上限 256 防止吃掉一段长 markdown
        return message, None

    inside = stripped[1:close].strip()
    if not inside:
        return message, None

    # 拆出 prefix（必须命中白名单）+ payload
    inside_lower = inside.lower()
    matched_prefix: str | None = None
    for prefix in _ANSWER_PREFIX_BRACKETED_SORTED:
        if inside_lower.startswith(prefix):
            after = inside[len(prefix) :]
            # prefix 必须在 EOL 或非字母数字处终止，否则 "answers" 会误吃到
            # "answersome"。这里因为 prefix 表里没有 prefix-of-prefix，理论上
            # 不会冲突，但保留 boundary 检查作为防御。
            if not after or not after[0].isalnum():
                matched_prefix = prefix
                # 跳过前缀后的 ":" / "：" / "-" / 空白
                payload = after.lstrip(" \t:：-—").strip()
                break

    if matched_prefix is None:
        return message, None

    labels = _split_answer_labels(payload)
    if not labels:
        # 命中前缀但 payload 空（如 "[answer:]"）→ 当未识别处理，避免 supervisor
        # 拿到空 directive 后困惑
        return message, None

    tail = stripped[close + 1 :].lstrip(" \t:：-—,.")
    cleaned = (" " * leading_ws_len) + tail
    return cleaned, {
        "labels": labels,
        "raw_payload": payload,
    }


_QUESTION_ANSWER_DIRECTIVE_TEMPLATE = (
    "[question system] The user replied to the multiple-choice question you "
    "asked in the previous turn via the `ask_user_question` tool. Their "
    "selection: **{labels}**.\n\n"
    "Continue executing the user's original intent using this selection. Do "
    "NOT call `ask_user_question` again for the same question. If the answer "
    "narrows scope (e.g. picked one KB out of several), apply that scope to "
    "the next retrieval / write call."
)


def augment_for_question_answer(
    *,
    user_message: str,
    answer: dict | None,
) -> str:
    """Inject a ``[question system]`` directive so a domain-scoped supervisor
    knows the empty-after-strip user message is a resume signal, not an
    out-of-scope request.

    Mirrors :func:`augment_for_plan_decision`. ``answer`` is the dict
    returned by :func:`parse_question_answer`.
    """
    if not answer:
        return user_message
    labels = answer.get("labels") or []
    if not labels:
        return user_message
    labels_str = ", ".join(labels)
    directive = _QUESTION_ANSWER_DIRECTIVE_TEMPLATE.format(labels=labels_str)

    residual = (user_message or "").strip()
    if residual:
        return f"{directive}\n\nUser's accompanying note:\n{residual}"
    return directive

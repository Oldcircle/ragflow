"""Phase 2.6 v0.4 — parse ``[plan approved|rejected|request changes]`` prefix
from a user message.

Lives outside ``api/apps/`` so unit tests can import it without pulling in the
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


__all__ = ["parse_plan_decision", "augment_for_plan_decision"]

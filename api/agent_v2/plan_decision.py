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


__all__ = ["parse_plan_decision"]

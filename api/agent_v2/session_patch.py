"""Phase 2.8.1 — pure validator for ``PATCH /v1/agent_v2/session/<id>``.

Why pure: the route handler in ``api/apps/agent_v2_app.py`` has module-level
``manager.route(...)`` decorators that depend on a ``manager`` global injected
by RAGFlow's loader at runtime. That makes the file unimportable in a unit
test. Splitting validation here lets us test it standalone — the route
handler stays a thin orchestration layer (auth + DB read + this validator
+ DB write + response shaping).

Locked fields (``model_config_json`` / ``system_prompt``) are deliberately
NOT in ``PATCHABLE_FIELDS``:

- Cross-provider model switching breaks mid-conversation tool_use format
  compatibility (Claude / DeepSeek-anthropic / OpenAI all differ).
- ``system_prompt`` is the agent's identity — changing it mid-flow puts
  the model in an inconsistent state.

Both belong to "create a new session".
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


PATCHABLE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "kb_ids",
        "tool_names",
        "max_turns",
        "max_budget_usd",
        "citation_enforce_level",
        "citation_numeric_strict",
        "history_turn_limit",
    }
)


LOCKED_FIELDS_HINT = (
    "Cannot edit `model` or `system_prompt` on an existing session — "
    "these are session-identity fields. Create a new session instead."
)


def validate_patch_body(
    body: dict[str, Any],
    *,
    valid_tool_names: Iterable[str],
    filter_accessible_kbs: Callable[[list[str]], list[str]],
) -> tuple[dict[str, Any], str | None]:
    """Validate a PATCH session body.

    Returns ``(updates_dict, error_message)``:
    - on success: ``(updates, None)`` — caller passes ``updates`` to
      ``AgentV2SessionService.update_fields``
    - on failure: ``({}, error_msg)`` — caller surfaces error_msg to client

    Args:
        body: JSON dict from the request.
        valid_tool_names: set of tool short-names registered in ``ALL_TOOLS``.
        filter_accessible_kbs: function taking a list of kb_ids and
            returning the subset the current user has at least VIEWER role
            for. Hooks into ``DatasetAccessService.filter_accessible_kb_ids``.
    """
    if not isinstance(body, dict):
        return {}, "body must be a JSON object"

    unknown = set(body) - PATCHABLE_FIELDS
    if unknown:
        return {}, (
            f"unsupported fields: {sorted(unknown)}. {LOCKED_FIELDS_HINT}"
        )

    updates: dict[str, Any] = {}
    valid_tool_names_set = set(valid_tool_names)

    if "name" in body:
        name = str(body["name"] or "").strip()
        if not name or len(name) > 200:
            return {}, "name must be 1-200 chars"
        updates["name"] = name

    if "kb_ids" in body:
        kb_ids = body["kb_ids"]
        if not isinstance(kb_ids, list) or any(
            not isinstance(k, str) for k in kb_ids
        ):
            return {}, "kb_ids must be a list of strings"
        accessible = set(filter_accessible_kbs(list(kb_ids)))
        denied = [k for k in kb_ids if k not in accessible]
        if denied:
            return {}, (
                f"no_access to kb_ids: {denied}. "
                "Ask the KB owner for at least VIEWER role."
            )
        # Dedupe + preserve order
        seen: set[str] = set()
        ordered: list[str] = []
        for k in kb_ids:
            if k in seen:
                continue
            seen.add(k)
            ordered.append(k)
        updates["kb_ids"] = ordered

    if "tool_names" in body:
        tn = body["tool_names"]
        if not isinstance(tn, list) or any(not isinstance(t, str) for t in tn):
            return {}, "tool_names must be a list of strings"
        invalid = [t for t in tn if t not in valid_tool_names_set]
        if invalid:
            return {}, (
                f"unknown tool names: {invalid}. "
                f"Valid names: {sorted(valid_tool_names_set)}"
            )
        # Dedupe + preserve order
        seen2: set[str] = set()
        tn_clean: list[str] = []
        for t in tn:
            if t in seen2:
                continue
            seen2.add(t)
            tn_clean.append(t)
        updates["tool_names"] = tn_clean

    if "max_turns" in body:
        try:
            v = int(body["max_turns"])
        except (TypeError, ValueError):
            return {}, "max_turns must be an integer"
        if not 1 <= v <= 100:
            return {}, "max_turns must be 1-100"
        updates["max_turns"] = v

    if "max_budget_usd" in body:
        try:
            v = float(body["max_budget_usd"])
        except (TypeError, ValueError):
            return {}, "max_budget_usd must be a number"
        if not 0 < v <= 100:
            return {}, "max_budget_usd must be > 0 and <= 100"
        updates["max_budget_usd"] = v

    if "citation_enforce_level" in body:
        v = str(body["citation_enforce_level"] or "").lower()
        if v not in ("off", "warn", "strict"):
            return {}, "citation_enforce_level must be off / warn / strict"
        updates["citation_enforce_level"] = v

    if "citation_numeric_strict" in body:
        updates["citation_numeric_strict"] = bool(
            body["citation_numeric_strict"]
        )

    if "history_turn_limit" in body:
        try:
            v = int(body["history_turn_limit"])
        except (TypeError, ValueError):
            return {}, "history_turn_limit must be an integer"
        if not 0 <= v <= 100:
            return {}, "history_turn_limit must be 0-100"
        updates["history_turn_limit"] = v

    if not updates:
        return {}, "no editable fields in body"

    return updates, None

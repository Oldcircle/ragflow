"""Phase 2.8.1 — pure validator tests for ``PATCH /v1/agent_v2/session/<id>``.

Tests ``api.agent_v2.session_patch.validate_patch_body`` directly. The
route handler in ``api/apps/agent_v2_app.py`` is just orchestration around
this; covering the validator covers ~all the request-shape failure modes.

End-to-end (real Quart blueprint + login) is left to manual smoke
(``TEST-MANUAL.md`` G14) — same convention as the existing RBAC cross-
tenant tests under RAGFLOW_TEST_DB=1.
"""

from __future__ import annotations

import pytest

from api.agent_v2.session_patch import (
    LOCKED_FIELDS_HINT,
    PATCHABLE_FIELDS,
    validate_patch_body,
)


_FAKE_TOOLS = {
    "rag_retrieve",
    "rag_list_docs",
    "rag_read_doc",
    "spawn_subagent",
    "submit_plan",
}


def _allow_all_kbs(ids: list[str]) -> list[str]:
    return list(ids)


def _allow_only(allowed: set[str]):
    return lambda ids: [i for i in ids if i in allowed]


# ─────────────────────  Whitelist contract  ─────────────────────


class TestPatchableFieldsContract:
    def test_excludes_locked_fields(self):
        assert "model_config_json" not in PATCHABLE_FIELDS
        assert "system_prompt" not in PATCHABLE_FIELDS
        assert "model" not in PATCHABLE_FIELDS

    def test_excludes_runtime_state(self):
        for f in (
            "pending_plan_id",
            "pending_plan_status",
            "pending_plan_body",
            "summary_text",
            "summary_until_seq",
            "status",
            "tenant_id",
            "user_id",
        ):
            assert f not in PATCHABLE_FIELDS, (
                f"runtime field {f} leaked into PATCH whitelist"
            )

    def test_includes_user_facing_settings(self):
        for f in (
            "name",
            "kb_ids",
            "tool_names",
            "max_turns",
            "max_budget_usd",
            "citation_enforce_level",
            "citation_numeric_strict",
            "history_turn_limit",
        ):
            assert f in PATCHABLE_FIELDS

    def test_locked_hint_mentions_model_and_system_prompt(self):
        assert "model" in LOCKED_FIELDS_HINT
        assert "system_prompt" in LOCKED_FIELDS_HINT


# ─────────────────────  Validator behavior  ─────────────────────


class TestValidatePatchBody:
    def test_non_dict_body_rejected(self):
        updates, err = validate_patch_body(
            "string body",  # type: ignore[arg-type]
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert updates == {}
        assert "JSON object" in err

    def test_unknown_field_rejected_with_hint(self):
        updates, err = validate_patch_body(
            {"foo": "bar"},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert updates == {}
        assert "unsupported fields" in err
        assert "model" in err  # locked-fields hint

    def test_model_field_locked(self):
        updates, err = validate_patch_body(
            {"model_config_json": {"model": "x"}},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is not None
        assert updates == {}
        assert "unsupported fields" in err

    def test_system_prompt_field_locked(self):
        updates, err = validate_patch_body(
            {"system_prompt": "you are evil"},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is not None
        assert "system_prompt" in err

    def test_empty_body_rejected(self):
        updates, err = validate_patch_body(
            {},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert updates == {}
        assert "no editable fields" in err

    # name

    def test_name_too_long(self):
        _, err = validate_patch_body(
            {"name": "x" * 250},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert "1-200" in err

    def test_name_empty_after_strip(self):
        _, err = validate_patch_body(
            {"name": "   "},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert "1-200" in err

    def test_name_happy(self):
        updates, err = validate_patch_body(
            {"name": "  renamed  "},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is None
        assert updates == {"name": "renamed"}

    # kb_ids

    def test_kb_ids_non_list_rejected(self):
        _, err = validate_patch_body(
            {"kb_ids": "kb-a"},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert "list of strings" in err

    def test_kb_ids_with_non_string_member_rejected(self):
        _, err = validate_patch_body(
            {"kb_ids": ["kb-a", 42]},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert "list of strings" in err

    def test_kb_ids_rbac_denial(self):
        _, err = validate_patch_body(
            {"kb_ids": ["kb-a", "kb-b"]},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_only({"kb-a"}),
        )
        assert "no_access" in err
        assert "kb-b" in err
        assert "VIEWER" in err

    def test_kb_ids_dedupe_preserves_order(self):
        updates, err = validate_patch_body(
            {"kb_ids": ["kb-a", "kb-b", "kb-a", "kb-c"]},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is None
        assert updates["kb_ids"] == ["kb-a", "kb-b", "kb-c"]

    def test_kb_ids_empty_list_passes(self):
        # Removing all KBs is valid (no-retrieval session).
        updates, err = validate_patch_body(
            {"kb_ids": []},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is None
        assert updates["kb_ids"] == []

    # tool_names

    def test_tool_names_non_list_rejected(self):
        _, err = validate_patch_body(
            {"tool_names": "rag_retrieve"},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert "list of strings" in err

    def test_tool_names_unknown_rejected(self):
        _, err = validate_patch_body(
            {"tool_names": ["rag_retrieve", "shell_exec"]},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert "unknown tool names" in err
        assert "shell_exec" in err

    def test_tool_names_dedupe(self):
        updates, err = validate_patch_body(
            {"tool_names": ["rag_retrieve", "rag_list_docs", "rag_retrieve"]},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is None
        assert updates["tool_names"] == ["rag_retrieve", "rag_list_docs"]

    def test_tool_names_all_valid_passes(self):
        updates, err = validate_patch_body(
            {"tool_names": ["rag_retrieve", "rag_list_docs"]},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is None
        assert updates["tool_names"] == ["rag_retrieve", "rag_list_docs"]

    # numeric guards

    @pytest.mark.parametrize(
        "field,bad,msg_substr",
        [
            ("max_turns", "five", "integer"),
            ("max_turns", 0, "1-100"),
            ("max_turns", 200, "1-100"),
            ("max_budget_usd", "lots", "number"),
            ("max_budget_usd", 0, "> 0"),
            ("max_budget_usd", 200, "> 0"),
            ("history_turn_limit", "ten", "integer"),
            ("history_turn_limit", -1, "0-100"),
            ("history_turn_limit", 200, "0-100"),
        ],
    )
    def test_numeric_validation(self, field, bad, msg_substr):
        _, err = validate_patch_body(
            {field: bad},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is not None
        assert msg_substr in err, f"{field}={bad!r} expected {msg_substr!r} in {err!r}"

    @pytest.mark.parametrize(
        "field,good",
        [
            ("max_turns", 1),
            ("max_turns", 50),
            ("max_turns", 100),
            ("max_budget_usd", 0.01),
            ("max_budget_usd", 1.5),
            ("max_budget_usd", 100),
            ("history_turn_limit", 0),
            ("history_turn_limit", 10),
            ("history_turn_limit", 100),
        ],
    )
    def test_numeric_happy(self, field, good):
        updates, err = validate_patch_body(
            {field: good},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is None
        # Type coercion is correct:
        if field in ("max_turns", "history_turn_limit"):
            assert isinstance(updates[field], int)
        else:
            assert isinstance(updates[field], float)

    # citation_enforce_level

    def test_citation_enforce_level_invalid(self):
        _, err = validate_patch_body(
            {"citation_enforce_level": "STRICT_ALL"},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert "off / warn / strict" in err

    @pytest.mark.parametrize("level", ["off", "warn", "strict", "OFF", "Warn"])
    def test_citation_enforce_level_normalized_to_lower(self, level):
        updates, err = validate_patch_body(
            {"citation_enforce_level": level},
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is None
        assert updates["citation_enforce_level"] == level.lower()

    def test_citation_numeric_strict_coerces_to_bool(self):
        for raw, expected in [
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            ("yes", True),
            ("", False),
        ]:
            updates, err = validate_patch_body(
                {"citation_numeric_strict": raw},
                valid_tool_names=_FAKE_TOOLS,
                filter_accessible_kbs=_allow_all_kbs,
            )
            assert err is None
            assert updates["citation_numeric_strict"] is expected

    # combined

    def test_combined_happy_path(self):
        updates, err = validate_patch_body(
            {
                "name": "renamed",
                "kb_ids": ["kb-a", "kb-b"],
                "tool_names": ["rag_retrieve"],
                "max_turns": 30,
                "max_budget_usd": 1.5,
                "citation_enforce_level": "strict",
                "citation_numeric_strict": False,
                "history_turn_limit": 8,
            },
            valid_tool_names=_FAKE_TOOLS,
            filter_accessible_kbs=_allow_all_kbs,
        )
        assert err is None
        assert set(updates) == {
            "name",
            "kb_ids",
            "tool_names",
            "max_turns",
            "max_budget_usd",
            "citation_enforce_level",
            "citation_numeric_strict",
            "history_turn_limit",
        }

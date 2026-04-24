"""Phase 2.6 v0.5 — per-subagent model routing (#42).

Confirms ``_resolve_child_model`` correctly honors ``AgentDefinition.model``:
- ``"inherit"`` → parent config as-is
- ``ModelRef(model=X)`` → swap model name, keep parent's base_url + auth_token
- ``ModelRef(model=X, base_url=Y)`` → swap both (dangerous with parent's
  auth_token; we log and continue)
- ``ModelRef(fallback_model=Z)`` → override fallback only
"""

from __future__ import annotations

import pytest

from api.agent_v2.definitions.schema import AgentDefinition, ModelRef
from api.agent_v2.runner import ModelConfig
from api.agent_v2.tools.spawn_subagent import _resolve_child_model


def _parent_cfg(**overrides):
    base = {
        "model": "claude-sonnet-4-5",
        "fallback_model": "claude-haiku-4-5",
        "base_url": None,
        "auth_token": "parent-token",
    }
    base.update(overrides)
    return ModelConfig(**base)


def _defn(model):
    # Minimal AgentDefinition only needs .model to be read
    return AgentDefinition(
        name="x",
        version="1",
        description="d",
        when_to_use="w",
        kind="subagent",
        model=model,
    )


class TestResolveChildModel:
    def test_no_definition_returns_parent_unchanged(self):
        parent = _parent_cfg()
        assert _resolve_child_model(parent, None) is parent

    def test_inherit_string_returns_parent_unchanged(self):
        parent = _parent_cfg()
        defn = _defn("inherit")
        assert _resolve_child_model(parent, defn) is parent

    def test_model_ref_swaps_model_name_only(self):
        parent = _parent_cfg()
        defn = _defn(ModelRef(model="claude-haiku-4-5"))
        child = _resolve_child_model(parent, defn)

        # New instance, not parent mutated
        assert child is not parent
        assert child.model == "claude-haiku-4-5"
        # Parent's base_url / auth_token preserved
        assert child.base_url == parent.base_url
        assert child.auth_token == parent.auth_token
        # fallback_model also preserved when ModelRef doesn't override
        assert child.fallback_model == parent.fallback_model

    def test_model_ref_with_same_base_url_does_not_warn(self, caplog):
        parent = _parent_cfg(base_url="https://api.deepseek.com/anthropic")
        defn = _defn(ModelRef(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/anthropic",
        ))
        with caplog.at_level("WARNING"):
            child = _resolve_child_model(parent, defn)
        assert child.base_url == parent.base_url
        assert "overrides base_url" not in caplog.text

    def test_model_ref_different_base_url_logs_warning(self, caplog):
        parent = _parent_cfg(base_url=None)  # Anthropic native
        defn = _defn(ModelRef(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/anthropic",
        ))
        with caplog.at_level("WARNING"):
            child = _resolve_child_model(parent, defn)
        assert child.base_url == "https://api.deepseek.com/anthropic"
        # auth_token still reused (best effort)
        assert child.auth_token == parent.auth_token
        assert "overrides base_url" in caplog.text

    def test_fallback_model_override_keeps_main_model(self):
        """ModelRef 只给 fallback_model 的用例 — 但 ModelRef.model 是必填，
        所以主模型也会被重设为 ModelRef.model。"""
        parent = _parent_cfg()
        defn = _defn(ModelRef(
            model="claude-opus-4-5",
            fallback_model="claude-sonnet-4-5",
        ))
        child = _resolve_child_model(parent, defn)
        assert child.model == "claude-opus-4-5"
        assert child.fallback_model == "claude-sonnet-4-5"
        assert child.auth_token == parent.auth_token

    def test_parent_is_never_mutated(self):
        parent = _parent_cfg()
        snapshot = (parent.model, parent.fallback_model, parent.base_url, parent.auth_token)
        defn = _defn(ModelRef(
            model="claude-haiku-4-5",
            base_url="https://x.example/v1",
            fallback_model="other-fallback",
        ))
        _resolve_child_model(parent, defn)
        assert (parent.model, parent.fallback_model, parent.base_url, parent.auth_token) == snapshot


class TestDefinitionSchemaRoundtrip:
    """ModelRef 能 to_dict() 干净序列化。"""

    def test_model_ref_to_dict(self):
        defn = AgentDefinition(
            name="sub_fast",
            version="1",
            description="d",
            when_to_use="w",
            kind="subagent",
            model=ModelRef(
                model="claude-haiku-4-5",
                base_url=None,
                fallback_model="claude-sonnet-4-5",
            ),
        )
        d = defn.to_dict()
        assert d["model"] == {
            "model": "claude-haiku-4-5",
            "base_url": None,
            "fallback_model": "claude-sonnet-4-5",
        }

    def test_inherit_serializes_as_string(self):
        defn = AgentDefinition(
            name="sub_x",
            version="1",
            description="d",
            when_to_use="w",
            kind="subagent",
            model="inherit",
        )
        assert defn.to_dict()["model"] == "inherit"

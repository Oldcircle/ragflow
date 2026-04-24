"""Phase 2.6 v0.4 — tool annotation registry tests (U8 coverage)."""

from __future__ import annotations

import pytest

from api.agent_v2.annotations import (
    ANNOTATIONS,
    annotations_summary_for_prompt,
)


@pytest.mark.p0
class TestAnnotationParity:
    """Every registered tool must have an annotation and vice versa.

    Keeps the registry honest when someone adds a new tool but forgets the
    metadata row — the prompt cost-hints would otherwise silently miss it.
    """

    def test_every_registered_tool_has_an_annotation(self):
        from api.agent_v2.registry import ALL_TOOLS

        missing = [name for name in ALL_TOOLS if name not in ANNOTATIONS]
        assert not missing, (
            f"ALL_TOOLS has {missing} without a matching entry in "
            "api.agent_v2.annotations.ANNOTATIONS — add one."
        )

    def test_no_stale_annotations(self):
        from api.agent_v2.registry import ALL_TOOLS

        stale = [name for name in ANNOTATIONS if name not in ALL_TOOLS]
        assert not stale, (
            f"ANNOTATIONS has {stale} not present in ALL_TOOLS — "
            "remove the annotation or add the tool."
        )


@pytest.mark.p1
class TestToolAnnotation:
    def test_one_liner_format(self):
        ann = ANNOTATIONS["rag_retrieve"]
        line = ann.one_liner()
        # "- rag_retrieve: R/idem/normal (~400ms)"
        assert line.startswith("- rag_retrieve:")
        assert "R/" in line
        assert "idem" in line
        assert "normal" in line
        assert "ms" in line

    def test_write_tool_shows_W(self):
        ann = ANNOTATIONS["doc_tag"]
        assert "W/" in ann.one_liner()

    def test_non_idempotent_tool_shows_NOT_idem(self):
        ann = ANNOTATIONS["doc_reparse"]
        line = ann.one_liner()
        assert "NOT-idem" in line


@pytest.mark.p1
class TestAnnotationsSummary:
    def test_all_tools_rendered_when_none(self):
        text = annotations_summary_for_prompt(None)
        for name in ANNOTATIONS:
            assert name in text
        assert "Prefer cheap" in text  # trailing guidance

    def test_filter_to_subset(self):
        text = annotations_summary_for_prompt(["rag_retrieve", "doc_tag"])
        assert "rag_retrieve" in text
        assert "doc_tag" in text
        assert "kb_audit" not in text
        assert "doc_archive" not in text

    def test_unknown_names_are_skipped_silently(self):
        text = annotations_summary_for_prompt(["rag_retrieve", "nonexistent_tool"])
        assert "rag_retrieve" in text
        assert "nonexistent_tool" not in text

    def test_empty_list_returns_empty(self):
        assert annotations_summary_for_prompt([]) == ""

    def test_cost_class_values_are_valid(self):
        """Enforce the cost bucket enum."""
        for name, ann in ANNOTATIONS.items():
            assert ann.cost_class in ("cheap", "normal", "expensive"), (
                f"{name}: invalid cost_class {ann.cost_class!r}"
            )
            assert ann.avg_latency_ms > 0, (
                f"{name}: avg_latency_ms must be positive"
            )

    def test_supports_next_steps_is_bool(self):
        for ann in ANNOTATIONS.values():
            assert isinstance(ann.supports_next_steps, bool)


@pytest.mark.p1
class TestOkEnvelopeNextSteps:
    """U10 — ``ok()`` helper must carry next_steps through to MCP payload."""

    def test_next_steps_included_when_set(self):
        import json
        from api.agent_v2.tools.doc_ops._common import ok

        r = ok(
            kb_id="kb1",
            next_steps=["Verify via rag_list_docs", "Do not re-run"],
        )
        payload = json.loads(r["content"][0]["text"])
        assert payload["next_steps"] == [
            "Verify via rag_list_docs",
            "Do not re-run",
        ]

    def test_next_steps_absent_by_default(self):
        import json
        from api.agent_v2.tools.doc_ops._common import ok

        r = ok(kb_id="kb1")
        payload = json.loads(r["content"][0]["text"])
        assert "next_steps" not in payload

    def test_next_steps_cap_at_three(self):
        import json
        from api.agent_v2.tools.doc_ops._common import ok

        r = ok(next_steps=["a", "b", "c", "d", "e"])
        payload = json.loads(r["content"][0]["text"])
        assert payload["next_steps"] == ["a", "b", "c"]

    def test_next_steps_entry_length_cap(self):
        import json
        from api.agent_v2.tools.doc_ops._common import ok

        long_hint = "x" * 500
        r = ok(next_steps=[long_hint])
        payload = json.loads(r["content"][0]["text"])
        assert len(payload["next_steps"][0]) == 160

    def test_none_and_empty_dont_set_field(self):
        import json
        from api.agent_v2.tools.doc_ops._common import ok

        for empty in (None, [], [""], [None]):  # noqa
            r = ok(next_steps=empty)
            payload = json.loads(r["content"][0]["text"])
            assert "next_steps" not in payload, (
                f"empty input {empty!r} should not produce next_steps key"
            )

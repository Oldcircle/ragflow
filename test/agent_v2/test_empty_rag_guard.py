"""Phase 2.6 v0.8.3 — empty-rag guard unit tests.

Validates the runner-level detector that stops the SDK subprocess after 3
consecutive empty retrieval calls (Q13-class scenario: Agent induced to
interpret a fabricated document number, loops rag_retrieve / list_docs /
read_doc indefinitely until subprocess crash).

Covers ``_is_empty_rag_result`` classification across all four rag_* tools:
- empty / low-similarity chunks
- missing docs / content
- non-rag tools should never count
- malformed payloads don't false-positive
"""

from __future__ import annotations

import json

import pytest

from api.agent_v2.runner import (
    _EMPTY_RAG_THRESHOLD,
    _is_empty_rag_result,
)


# ─────────── rag_retrieve ───────────


def test_rag_retrieve_empty_chunks_is_empty():
    result = json.dumps({"chunks": [], "total": 0})
    assert _is_empty_rag_result("mcp__ragflow__rag_retrieve", result) is True


def test_rag_retrieve_low_similarity_is_empty():
    result = json.dumps({
        "chunks": [
            {"content": "foo", "similarity": 0.15},
            {"content": "bar", "similarity": 0.10},
        ]
    })
    assert _is_empty_rag_result("mcp__ragflow__rag_retrieve", result) is True


def test_rag_retrieve_with_good_chunk_is_not_empty():
    result = json.dumps({
        "chunks": [
            {"content": "foo", "similarity": 0.15},
            {"content": "bar", "similarity": 0.55},  # one good chunk suffices
        ]
    })
    assert _is_empty_rag_result("mcp__ragflow__rag_retrieve", result) is False


def test_rag_retrieve_threshold_boundary():
    # similarity == 0.2 is the boundary — we want anything < 0.2 to count
    # as empty, so >= 0.2 should NOT be empty
    result = json.dumps({"chunks": [{"similarity": 0.2}]})
    assert _is_empty_rag_result("mcp__ragflow__rag_retrieve", result) is False
    result = json.dumps({"chunks": [{"similarity": 0.199}]})
    assert _is_empty_rag_result("mcp__ragflow__rag_retrieve", result) is True


def test_rag_retrieve_chunk_missing_similarity_field():
    # similarity absent → treat as 0.0 → empty
    result = json.dumps({"chunks": [{"content": "foo"}]})
    assert _is_empty_rag_result("mcp__ragflow__rag_retrieve", result) is True


# ─────────── rag_list_docs ───────────


def test_rag_list_docs_empty():
    result = json.dumps({"docs": []})
    assert _is_empty_rag_result("mcp__ragflow__rag_list_docs", result) is True


def test_rag_list_docs_with_docs():
    result = json.dumps({"docs": [{"doc_id": "d1", "name": "foo.pdf"}]})
    assert (
        _is_empty_rag_result("mcp__ragflow__rag_list_docs", result) is False
    )


# ─────────── rag_read_doc ───────────


def test_rag_read_doc_no_content_no_chunks():
    result = json.dumps({"chunks": [], "content": ""})
    assert _is_empty_rag_result("mcp__ragflow__rag_read_doc", result) is True


def test_rag_read_doc_with_chunks():
    result = json.dumps({"chunks": [{"text": "x"}]})
    assert (
        _is_empty_rag_result("mcp__ragflow__rag_read_doc", result) is False
    )


def test_rag_read_doc_with_content():
    result = json.dumps({"content": "some body text"})
    assert (
        _is_empty_rag_result("mcp__ragflow__rag_read_doc", result) is False
    )


# ─────────── rag_graph_query ───────────


def test_rag_graph_query_empty():
    result = json.dumps({"entities": [], "relations": []})
    assert (
        _is_empty_rag_result("mcp__ragflow__rag_graph_query", result) is True
    )


def test_rag_graph_query_with_entity():
    result = json.dumps({"entities": [{"name": "X"}], "relations": []})
    assert (
        _is_empty_rag_result("mcp__ragflow__rag_graph_query", result) is False
    )


# ─────────── non-rag tools should NEVER count ───────────


@pytest.mark.parametrize(
    "tool",
    [
        "mcp__ragflow__doc_tag",
        "mcp__ragflow__doc_rename",
        "mcp__ragflow__kb_stats",
        "mcp__ragflow__ask_user_question",
        "mcp__ragflow__submit_plan",
        "mcp__ragflow__spawn_subagent",
        "mcp__ragflow__web_search",
        "mcp__ragflow__web_fetch",
    ],
)
def test_non_rag_tools_never_count_as_empty(tool):
    # Even if result is literally empty, non-rag tools shouldn't trigger
    # the guard — they have their own semantics (user opted to call them).
    result = json.dumps({"chunks": [], "docs": [], "content": ""})
    assert _is_empty_rag_result(tool, result) is False


# ─────────── malformed payloads ───────────


def test_none_result_classified_empty():
    assert _is_empty_rag_result("mcp__ragflow__rag_retrieve", None) is True


def test_unparseable_string_not_empty():
    # If we can't parse, don't count as empty — safer to allow the loop
    # to continue than falsely trigger the guard.
    assert (
        _is_empty_rag_result("mcp__ragflow__rag_retrieve", "not valid json")
        is False
    )


def test_non_dict_payload_not_empty():
    assert (
        _is_empty_rag_result(
            "mcp__ragflow__rag_retrieve", json.dumps(["a", "b"])
        )
        is False
    )


def test_short_name_without_mcp_prefix():
    # The tool name dispatch uses `rsplit("__", 1)[-1]`, so bare names work
    result = json.dumps({"chunks": []})
    assert _is_empty_rag_result("rag_retrieve", result) is True


# ─────────── threshold constant sanity ───────────


def test_threshold_is_reasonable():
    # Must be >= 2 (allow one retry) and <= 5 (don't waste budget)
    assert 2 <= _EMPTY_RAG_THRESHOLD <= 5
    # v0.8.3 ship value
    assert _EMPTY_RAG_THRESHOLD == 3

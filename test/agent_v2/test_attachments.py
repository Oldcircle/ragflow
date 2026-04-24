"""Phase 2.7 Stage 1 — attachment infrastructure unit tests.

Covers:
- MIME resolution (whitelist + extension fallback + rejection)
- Preview extraction across text / pdf / docx / xlsx / html / image
- Hash stability + dedupe key shape
- AttachmentInfo serialization + prompt section rendering
- Runner ``attachments=`` plumbing into ToolContext

Does **not** exercise the HTTP endpoints (those need auth + multipart setup,
covered separately in ``test_attachment_api.py`` once we have real fixtures).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api.agent_v2.attachments import (
    MAX_ATTACHMENT_SIZE_BYTES,
    MAX_PREVIEW_BYTES,
    MAX_SESSION_STAGED_BYTES,
    MAX_SESSION_STAGED_COUNT,
    MIME_WHITELIST,
    AttachmentInfo,
    extract_preview,
    hash_content,
    object_key,
    render_attachments_prompt_section,
    resolve_mime_type,
)


# ─────────────────── MIME resolution ───────────────────


def test_resolve_mime_trusts_whitelisted_client_mime():
    assert resolve_mime_type("application/pdf", "foo.pdf") == "application/pdf"
    assert resolve_mime_type("image/jpeg", "photo.jpg") == "image/jpeg"


def test_resolve_mime_strips_content_type_params():
    # "text/plain; charset=utf-8" should reduce to "text/plain"
    assert (
        resolve_mime_type("text/plain; charset=utf-8", "notes.txt")
        == "text/plain"
    )


def test_resolve_mime_uppercase_normalized():
    assert resolve_mime_type("Image/JPEG", "x.jpg") == "image/jpeg"


def test_resolve_mime_falls_back_to_extension_for_octet_stream():
    assert (
        resolve_mime_type("application/octet-stream", "foo.PDF")
        == "application/pdf"
    )
    assert (
        resolve_mime_type("", "bar.docx")
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_resolve_mime_rejects_unknown():
    assert resolve_mime_type("application/x-shockwave-flash", "foo.swf") is None
    assert resolve_mime_type("", "noext") is None


def test_whitelist_size_within_sane_bounds():
    # Sanity: Stage 1 whitelist should be in the 10-30 range — too few = missing
    # common types, too many = implicit vector for abuse.
    assert 10 <= len(MIME_WHITELIST) <= 30
    # Must have image subset for Stage 2 OCR path
    assert any(m.startswith("image/") for m in MIME_WHITELIST)


# ─────────────────── object_key stability ───────────────────


def test_object_key_format():
    k = object_key(
        tenant_id="t1", session_id="s1", attachment_id="a1"
    )
    assert k == "t1/s1/a1"


def test_object_key_deterministic():
    args = {"tenant_id": "t", "session_id": "s", "attachment_id": "a"}
    assert object_key(**args) == object_key(**args)


# ─────────────────── hash_content ───────────────────


def test_hash_content_deterministic():
    blob = b"hello world"
    h1 = hash_content(blob)
    h2 = hash_content(blob)
    assert h1 == h2


def test_hash_content_differs_on_bit_change():
    assert hash_content(b"abc") != hash_content(b"abd")


def test_hash_content_hex_length():
    assert len(hash_content(b"x")) == 32  # 128-bit hex


# ─────────────────── extract_preview (text paths) ───────────────────


def test_preview_plain_text():
    assert extract_preview(b"Hello\nWorld\n", "text/plain") == "Hello\nWorld\n"


def test_preview_truncates_at_max_bytes():
    big = ("a" * (MAX_PREVIEW_BYTES * 2)).encode("utf-8")
    result = extract_preview(big, "text/plain")
    # Decoded string length = byte length for ASCII
    assert len(result.encode("utf-8")) <= MAX_PREVIEW_BYTES


def test_preview_markdown_decoded():
    assert extract_preview(b"# Title\n\n- item", "text/markdown").startswith(
        "# Title"
    )


def test_preview_json_decoded():
    assert extract_preview(b'{"k":"v"}', "application/json") == '{"k":"v"}'


def test_preview_utf8_replace_on_bad_bytes():
    # Mixed binary — should not raise, just replacement chars
    result = extract_preview(b"hi\x80\x81world", "text/plain")
    assert "hi" in result and "world" in result


def test_preview_html_strips_scripts():
    html = b"<html><script>alert(1)</script><p>Body text</p></html>"
    result = extract_preview(html, "text/html")
    assert "alert" not in result
    assert "Body text" in result


def test_preview_image_is_placeholder():
    assert extract_preview(b"PNG fake data", "image/png") == (
        "[image, OCR pending at archive time]"
    )


def test_preview_unsupported_mime_is_placeholder():
    # Not in whitelist but got here somehow — don't raise
    result = extract_preview(b"data", "application/x-unknown")
    assert result.startswith("[") and "preview not available" in result


def test_preview_pdf_failure_yields_placeholder():
    # Not actual PDF bytes — parser should fall back gracefully
    result = extract_preview(b"not a pdf", "application/pdf")
    assert result.startswith("[pdf")


def test_preview_docx_failure_yields_placeholder():
    result = extract_preview(b"not a docx", "application/msword")
    assert result.startswith("[docx")


def test_preview_xlsx_failure_yields_placeholder():
    result = extract_preview(b"not an xlsx", "application/vnd.ms-excel")
    assert result.startswith("[xlsx")


# ─────────────────── AttachmentInfo ───────────────────


def _mock_row(**overrides):
    row = MagicMock()
    row.id = "att1"
    row.session_id = "s1"
    row.filename = "report.pdf"
    row.mime_type = "application/pdf"
    row.size_bytes = 12345
    row.origin = "upload"
    row.status = "staged"
    row.preview_text = "Hello\nWorld"
    row.source_url = None
    row.archived_doc_id = None
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


def test_attachment_info_from_row():
    info = AttachmentInfo.from_row(_mock_row())
    assert info.id == "att1"
    assert info.filename == "report.pdf"
    assert info.preview_text == "Hello\nWorld"


def test_attachment_info_to_dict_round_trip():
    info = AttachmentInfo.from_row(_mock_row(source_url="https://x.com/y"))
    d = info.to_dict()
    assert d["source_url"] == "https://x.com/y"
    assert d["status"] == "staged"


def test_attachment_info_frozen():
    info = AttachmentInfo.from_row(_mock_row())
    with pytest.raises(Exception):  # FrozenInstanceError or similar
        info.id = "mutated"  # type: ignore[misc]


# ─────────────────── prompt section rendering ───────────────────


def test_prompt_section_empty_when_no_attachments():
    assert render_attachments_prompt_section(()) == ""


def test_prompt_section_english_with_staged():
    info = AttachmentInfo.from_row(_mock_row())
    text = render_attachments_prompt_section((info,))
    assert "# Session attachments" in text
    assert "`report.pdf`" in text
    assert "sub_archivist" in text
    assert "doc_ingest_attachment" in text


def test_prompt_section_zh_with_staged():
    info = AttachmentInfo.from_row(_mock_row())
    text = render_attachments_prompt_section((info,), lang="zh")
    assert "会话附件" in text
    assert "sub_archivist" in text
    assert "归档工作流" in text


def test_prompt_section_shows_source_url_for_web_fetch():
    info = AttachmentInfo.from_row(_mock_row(
        origin="web_fetch", source_url="https://example.com/doc"
    ))
    text = render_attachments_prompt_section((info,))
    assert "https://example.com/doc" in text


def test_prompt_section_archived_separated_from_staged():
    staged = AttachmentInfo.from_row(_mock_row(id="s"))
    archived = AttachmentInfo.from_row(_mock_row(
        id="a", status="archived", archived_doc_id="doc42"
    ))
    text = render_attachments_prompt_section((staged, archived))
    # Both mentioned, but archived gets reference-only framing
    assert "staged" in text.lower() or "pending user decision" in text.lower()
    assert "doc42" in text
    assert "Already-archived" in text or "仅作参考" in text


def test_prompt_section_all_archived_no_staged():
    archived = AttachmentInfo.from_row(_mock_row(
        status="archived", archived_doc_id="doc1"
    ))
    text = render_attachments_prompt_section((archived,))
    # No staged → no workflow block, but archived reference still shown
    assert "doc1" in text
    # Workflow CTA shouldn't fire when there's nothing to archive
    assert "Workflow to archive" not in text


# ─────────────────── Runner plumbing (lightweight; no live SDK call) ───────────────────


def test_runner_accepts_attachments_kwarg():
    from api.agent_v2.runner import AgentRunner

    info = AttachmentInfo.from_row(_mock_row())
    r = AgentRunner(
        tenant_id="t1",
        kb_ids=["kb1"],
        system_prompt="dummy",
        attachments=(info,),
    )
    assert r.attachments == (info,)


def test_runner_default_attachments_empty_tuple():
    from api.agent_v2.runner import AgentRunner

    r = AgentRunner(tenant_id="t1", kb_ids=["kb1"], system_prompt="")
    assert r.attachments == ()


def test_attachments_section_renders_only_when_non_empty():
    # Direct regression guard against a refactor that always prepends the
    # section (which would leak cache + add noise to simple sessions).
    assert render_attachments_prompt_section(()) == ""


# ─────────────────── Size/count constants sanity ───────────────────


def test_size_limits_are_sane():
    assert 1024 * 1024 <= MAX_ATTACHMENT_SIZE_BYTES <= 1024 * 1024 * 1024  # 1 MB – 1 GB
    assert MAX_SESSION_STAGED_BYTES >= MAX_ATTACHMENT_SIZE_BYTES
    assert 1 <= MAX_SESSION_STAGED_COUNT <= 100
    assert MAX_PREVIEW_BYTES <= 64 * 1024  # preview is **small**


def test_ship_values_match_plan():
    # Phase 2.7 Stage 1 commits to these numbers in PLAN-attachments.md §7.
    assert MAX_ATTACHMENT_SIZE_BYTES == 50 * 1024 * 1024
    assert MAX_SESSION_STAGED_BYTES == 200 * 1024 * 1024
    assert MAX_SESSION_STAGED_COUNT == 20
    assert MAX_PREVIEW_BYTES == 8 * 1024

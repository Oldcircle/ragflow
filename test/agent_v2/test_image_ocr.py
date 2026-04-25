"""Phase 2.7 v0.12 — image OCR helper unit tests.

Focus on the markdown composer + helpers. ``run_image_ocr`` itself imports
``deepdoc.vision.OCR`` (1GB onnx) so it stays out of unit scope; the
integration test in ``test_doc_ingest_attachment.py`` patches it.
"""

from __future__ import annotations

from api.agent_v2.tools.doc_ops._image_ocr import (
    OcrResult,
    build_markdown_for_image,
    derive_markdown_filename,
)


def _kw(**overrides):
    base = dict(
        original_filename="scan.png",
        mime_type="image/png",
        size_bytes=12_345,
        content_hash="deadbeef" * 4,
        blob_path="agent-v2-attachments/t1/s1/att1",
    )
    base.update(overrides)
    return base


def test_markdown_rich_signal_full_text():
    md = build_markdown_for_image(
        **_kw(),
        ocr_result=OcrResult(
            ok=True,
            text="第一章 总则\n第二条 适用范围\n本规定适用于全市范围。",
            char_count=42,
            elapsed_ms=1200,
            signal="rich",
        ),
    )
    assert md.startswith("# 来源：scan.png")
    # provenance line — MinIO bucket/object should be quoted as code
    assert "agent-v2-attachments/t1/s1/att1" in md
    assert "deadbeef" * 4 in md
    assert "PaddleOCR (deepdoc.vision.OCR)" in md
    assert "1200ms" in md
    # Rich text appears as-is, no caveat header
    assert "第一章 总则" in md
    assert "low-signal output" not in md
    assert "OCR returned no extractable text" not in md


def test_markdown_low_signal_includes_warning():
    md = build_markdown_for_image(
        **_kw(),
        ocr_result=OcrResult(
            ok=True,
            text="审核通过",
            char_count=4,
            elapsed_ms=900,
            signal="low",
        ),
    )
    assert "low-signal output" in md
    assert "审核通过" in md


def test_markdown_none_signal_describes_blank():
    md = build_markdown_for_image(
        **_kw(),
        ocr_result=OcrResult(
            ok=True, text="", char_count=0, elapsed_ms=800, signal="none",
        ),
    )
    assert "OCR returned no extractable text" in md
    # Stub still mentions the original blob path so a future re-OCR can find it
    assert "agent-v2-attachments/t1/s1/att1" in md


def test_markdown_appends_error_footer_when_failed():
    md = build_markdown_for_image(
        **_kw(),
        ocr_result=OcrResult(
            ok=False,
            text="",
            char_count=0,
            elapsed_ms=12,
            signal="none",
            error="ocr_init_failed: ImportError: deepdoc.vision",
        ),
    )
    assert "OCR error" in md
    assert "deepdoc.vision" in md


def test_human_size_thresholds():
    from api.agent_v2.tools.doc_ops._image_ocr import _human_size

    assert _human_size(512) == "512 B"
    assert _human_size(2048).endswith("KB")
    assert _human_size(2_500_000).endswith("MB")


def test_derive_markdown_filename_appends_suffix():
    assert derive_markdown_filename("scan.png") == "scan.ocr.md"
    assert derive_markdown_filename("report.policy.pdf") == "report.policy.ocr.md"
    # No extension → still gets the suffix
    assert derive_markdown_filename("nofx") == "nofx.ocr.md"

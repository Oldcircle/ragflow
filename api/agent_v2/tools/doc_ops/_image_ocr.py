"""Phase 2.7 v0.12 — image attachment OCR helper.

Wraps ``deepdoc.vision.OCR`` with:
- **Lazy singleton**: ``OCR()`` first call downloads ~1GB onnx models;
  subsequent calls are cheap. We keep one process-wide instance.
- **Async-safe runner**: OCR is sync CPU-bound work; offloaded via
  ``asyncio.to_thread`` so the agent SDK's event loop isn't blocked
  during the (typically 1-3s) call.
- **Markdown wrapper**: produces a self-describing markdown document with
  source attribution + OCR'd text, suitable for ``FileService.upload_document``.

Why this lives next to ``doc_ingest_attachment.py`` and NOT under deepdoc:
- ``deepdoc/vision/ocr.py`` is the low-level inference code (heavy import
  with onnxruntime). This helper is the **agent_v2** integration glue —
  belongs with the tool that consumes it. Avoids a top-level lazy import
  ladder when the rest of agent_v2 doesn't need vision deps.

Failure modes:
- Model not downloaded → first call may try to fetch from HuggingFace
  (~5min). Run once at install time if you want predictable latency.
- PIL can't decode (corrupt image) → returns empty string + ``error`` field.
  Caller should fall back to "image, OCR pending" markdown stub.
- OCR returns short result (<10 chars) → we mark as ``ocr_low_signal``;
  caller should still archive but warn the user.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("ragflow.agent_v2.image_ocr")

# Lazy singleton — None until first call. Holds the deepdoc OCR instance.
_OCR_INSTANCE = None
_OCR_INIT_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class OcrResult:
    """Outcome of running OCR on a single image blob."""

    ok: bool
    text: str
    char_count: int
    elapsed_ms: int
    error: str | None = None
    # signal == "rich" → text > ~30 chars, treat as primary content
    # signal == "low"  → text 1–30 chars, mostly empty
    # signal == "none" → 0 chars (logo / blank scan / OCR failed)
    signal: str = "none"


async def _get_ocr_instance():
    """Return the process-wide OCR singleton; init under lock to avoid two
    concurrent first-call attempts each downloading models."""
    global _OCR_INSTANCE
    if _OCR_INSTANCE is not None:
        return _OCR_INSTANCE
    async with _OCR_INIT_LOCK:
        if _OCR_INSTANCE is not None:
            return _OCR_INSTANCE
        # Heavy import + model load — keep off the event loop.
        def _init():
            from deepdoc.vision import OCR

            return OCR()

        _OCR_INSTANCE = await asyncio.to_thread(_init)
        return _OCR_INSTANCE


async def run_image_ocr(blob: bytes) -> OcrResult:
    """Decode image bytes → run PaddleOCR → return joined text.

    Never raises — failures become ``OcrResult(ok=False, error=...)``.
    """
    start = time.perf_counter()
    try:
        from PIL import Image

        # Decoding is fast but still off-loop to keep latency predictable.
        def _decode():
            img = Image.open(io.BytesIO(blob)).convert("RGB")
            import numpy as np
            return np.array(img)

        img_array = await asyncio.to_thread(_decode)
    except Exception as e:  # noqa: BLE001
        logger.warning("image OCR decode failed: %s: %s", type(e).__name__, e)
        return OcrResult(
            ok=False,
            text="",
            char_count=0,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=f"image_decode_failed: {type(e).__name__}: {e}",
            signal="none",
        )

    try:
        ocr = await _get_ocr_instance()
    except Exception as e:  # noqa: BLE001
        logger.warning("OCR engine init failed: %s: %s", type(e).__name__, e)
        return OcrResult(
            ok=False,
            text="",
            char_count=0,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=(
                f"ocr_init_failed: {type(e).__name__}: {e} "
                "(check rag/res/deepdoc model dir)"
            ),
            signal="none",
        )

    try:
        # ``ocr(np.ndarray)`` returns boxes: [(box, (text, score)), ...]
        boxes = await asyncio.to_thread(ocr, img_array)
    except Exception as e:  # noqa: BLE001
        logger.warning("OCR inference failed: %s: %s", type(e).__name__, e)
        return OcrResult(
            ok=False,
            text="",
            char_count=0,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=f"ocr_inference_failed: {type(e).__name__}: {e}",
            signal="none",
        )

    if not boxes:
        return OcrResult(
            ok=True,
            text="",
            char_count=0,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            signal="none",
        )

    # boxes shape: list of ((x0,y0,x1,y1,...), (text, score)). Flatten lines
    # into a newline-joined block. Keep only non-empty.
    lines = []
    for entry in boxes:
        try:
            _box, (text, _score) = entry
        except (ValueError, TypeError):
            continue
        text = (text or "").strip()
        if text:
            lines.append(text)

    joined = "\n".join(lines)
    cc = len(joined)
    if cc == 0:
        signal = "none"
    elif cc < 30:
        signal = "low"
    else:
        signal = "rich"

    return OcrResult(
        ok=True,
        text=joined,
        char_count=cc,
        elapsed_ms=int((time.perf_counter() - start) * 1000),
        signal=signal,
    )


def build_markdown_for_image(
    *,
    original_filename: str,
    mime_type: str,
    size_bytes: int,
    content_hash: str,
    blob_path: str,
    ocr_result: OcrResult,
) -> str:
    """Compose a self-describing markdown document from an image attachment.

    The markdown body is what gets indexed by RAGFlow's parser pipeline; the
    front-matter block records provenance so a future re-OCR job can find the
    original image blob and recover.

    Format:
        # 来源：{original_filename}

        > Source image attachment archived via Agent v2 doc_ingest_attachment.
        > MinIO blob: `{blob_path}` (xxh128: `{content_hash}`)
        > MIME: `{mime_type}`, {size_bytes_human}
        > OCR engine: PaddleOCR (deepdoc.vision.OCR), {elapsed_ms}ms, {char_count} chars

        ---

        {ocr_text}    # if ocr_result.signal in ("rich", "low")

        # OR (if signal=="none")

        _OCR returned no extractable text. Likely a photo / diagram / logo._
        _The original image is preserved at the MinIO blob path above; re-OCR
        with a vision LLM (image2text) for richer description._
    """
    size_h = _human_size(size_bytes)
    front_matter = (
        f"# 来源：{original_filename}\n\n"
        f"> Source image attachment archived via Agent v2 `doc_ingest_attachment`.\n"
        f"> MinIO blob: `{blob_path}` (xxh128: `{content_hash}`)\n"
        f"> MIME: `{mime_type}`, {size_h}\n"
        f"> OCR engine: PaddleOCR (deepdoc.vision.OCR), "
        f"{ocr_result.elapsed_ms}ms, {ocr_result.char_count} chars\n"
        f"\n---\n\n"
    )
    if ocr_result.signal == "none":
        body = (
            "_OCR returned no extractable text. Likely a photo / diagram / "
            "logo._\n\n"
            "_The original image is preserved at the MinIO blob path above; "
            "re-OCR with a vision LLM (image2text) for richer description._\n"
        )
    elif ocr_result.signal == "low":
        body = (
            "_OCR low-signal output (likely a stamp / watermark / short label). "
            "Full text follows:_\n\n"
            f"{ocr_result.text}\n"
        )
    else:
        body = ocr_result.text + "\n"
    if not ocr_result.ok and ocr_result.error:
        body += f"\n\n---\n\n_OCR error: `{ocr_result.error}`_\n"
    return front_matter + body


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def derive_markdown_filename(original_filename: str) -> str:
    """``foo.jpg`` → ``foo.ocr.md`` so the KB lists keep the source name."""
    stem, _, _ = original_filename.rpartition(".")
    if not stem:
        stem = original_filename
    return f"{stem}.ocr.md"

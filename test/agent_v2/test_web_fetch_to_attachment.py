"""Phase 2.7 Stage 2 — web_fetch_to_attachment unit tests.

Covers the shared security path (SSRF / scheme / redirect policy inherits
from web_fetch), plus the attachment-specific dedupe and materialization.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
from api.agent_v2.tools.web_fetch_to_attachment import web_fetch_to_attachment


def _call(tool, args: dict) -> dict:
    return asyncio.run(tool.handler(args))


def _parse(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _ctx(**kw):
    # Needs session_id (attachment is session-scoped)
    defaults = {
        "tenant_id": "t1",
        "kb_ids": ("kb1",),
        "user_id": "u1",
        "session_id": "s1",
    }
    defaults.update(kw)
    return ToolContext(**defaults)


@pytest.fixture
def in_ctx():
    token = set_ctx(_ctx())
    yield
    reset_ctx(token)


# ─────────── Input validation ───────────


def test_requires_url(in_ctx):
    out = _parse(_call(web_fetch_to_attachment, {"url": ""}))
    assert out["error_code"] == "validation_error"


def test_requires_session_context():
    # No session_id → tool refuses (attachments are session-scoped).
    ctx = _ctx(session_id=None)
    token = set_ctx(ctx)
    try:
        out = _parse(_call(web_fetch_to_attachment, {"url": "https://x.com"}))
    finally:
        reset_ctx(token)
    assert out["error_code"] == "no_session"


# ─────────── SSRF inherited from web_fetch ───────────


def test_ssrf_rejects_private(in_ctx):
    with patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("10.0.0.1", 443))],
    ):
        out = _parse(_call(
            web_fetch_to_attachment, {"url": "https://intranet.local"},
        ))
    assert out["error_code"] == "security_error"
    assert "ssrf" in out["error"]


def test_scheme_rejected(in_ctx):
    out = _parse(_call(web_fetch_to_attachment, {"url": "file:///etc/passwd"}))
    assert out["error_code"] == "security_error"


# ─────────── Dedupe by URL within 24h ───────────


def test_url_dedupe_returns_existing(in_ctx):
    existing = MagicMock()
    existing.id = "att_existing"
    existing.status = "staged"
    existing.filename = "doc.pdf"
    existing.mime_type = "application/pdf"
    existing.size_bytes = 4096
    existing.source_url = "https://example.com/a.pdf"
    existing.preview_text = "existing preview"

    with patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.find_by_source_url",
        return_value=existing,
    ):
        out = _parse(_call(
            web_fetch_to_attachment,
            {"url": "https://example.com/a.pdf"},
        ))
    assert out["attachment_id"] == "att_existing"
    assert out["dedupe"] is True
    assert out["preview_text"] == "existing preview"


# ─────────── Redirect policy (inherited behavior) ───────────


def test_cross_host_redirect_blocked(in_ctx):
    """Piggyback on web_fetch's redirect policy — cross-host redirects are
    surfaced as redirect_blocked, not silently followed."""
    redirect_resp = MagicMock()
    redirect_resp.status_code = 302
    redirect_resp.headers = {"location": "https://evil.example/steal"}
    redirect_resp.url = "https://example.com/start"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            return redirect_resp

    with patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.find_by_source_url",
        return_value=None,
    ), patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient", return_value=_FakeClient(),
    ):
        out = _parse(_call(
            web_fetch_to_attachment,
            {"url": "https://example.com/start"},
        ))
    assert out["error_code"] == "redirect_blocked"
    assert out["redirect_url"] == "https://evil.example/steal"


# ─────────── Happy path (mocked) ───────────


def test_successful_materialize_creates_staged_row(in_ctx):
    final_resp = MagicMock()
    final_resp.status_code = 200
    final_resp.headers = {"content-type": "text/plain; charset=utf-8"}
    final_resp.content = b"Policy document body.\nAnother line."
    final_resp.encoding = "utf-8"
    final_resp.url = "https://example.com/policy.txt"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            return final_resp

    storage = MagicMock()

    created = MagicMock()
    created.id = "new_att"
    created.status = "staged"
    created.filename = "policy.txt"
    created.mime_type = "text/plain"
    created.size_bytes = len(final_resp.content)

    with patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.find_by_source_url",
        return_value=None,
    ), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.find_by_hash",
        return_value=None,
    ), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.create_staged",
        return_value=created,
    ), patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient", return_value=_FakeClient(),
    ), patch("common.settings.STORAGE_IMPL", storage):
        out = _parse(_call(
            web_fetch_to_attachment,
            {"url": "https://example.com/policy.txt"},
        ))

    assert out["attachment_id"] == "new_att"
    assert out["status"] == "staged"
    assert out["dedupe"] is False
    assert "next_steps" in out
    # Critical instruction to the LLM: call submit_plan next
    assert any("submit_plan" in s for s in out["next_steps"])
    # MinIO was written
    storage.put.assert_called_once()


def test_content_hash_dedupe_across_different_urls(in_ctx):
    """Even if the URL is different, identical content hash reuses the row."""
    final_resp = MagicMock()
    final_resp.status_code = 200
    final_resp.headers = {"content-type": "text/plain"}
    final_resp.content = b"shared content across urls"
    final_resp.encoding = "utf-8"
    final_resp.url = "https://new.example.com/x"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            return final_resp

    hash_hit = MagicMock()
    hash_hit.id = "old_att"
    hash_hit.status = "staged"
    hash_hit.filename = "shared.txt"
    hash_hit.mime_type = "text/plain"
    hash_hit.size_bytes = len(final_resp.content)
    hash_hit.source_url = "https://old.example.com/y"
    hash_hit.preview_text = "shared content across urls"

    with patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.find_by_source_url",
        return_value=None,  # URL dedupe miss
    ), patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.find_by_hash",
        return_value=hash_hit,  # But content hash hit
    ), patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient", return_value=_FakeClient(),
    ):
        out = _parse(_call(
            web_fetch_to_attachment,
            {"url": "https://new.example.com/x"},
        ))
    assert out["attachment_id"] == "old_att"
    assert out["dedupe"] is True


def test_mime_whitelist_rejection(in_ctx):
    """Server returns an unsupported MIME + filename can't rescue it."""
    final_resp = MagicMock()
    final_resp.status_code = 200
    final_resp.headers = {"content-type": "application/x-shockwave-flash"}
    final_resp.content = b"flash junk"
    final_resp.encoding = "utf-8"
    final_resp.url = "https://example.com/nothing"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _url):
            return final_resp

    with patch(
        "api.db.services.agent_v2_service.AgentV2AttachmentService.find_by_source_url",
        return_value=None,
    ), patch(
        "api.agent_v2.tools.web_fetch.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ), patch(
        "httpx.AsyncClient", return_value=_FakeClient(),
    ):
        out = _parse(_call(
            web_fetch_to_attachment,
            {"url": "https://example.com/nothing"},
        ))
    assert out["error_code"] == "unsupported_mime"


# ─────────── Registry parity ───────────


def test_registered_and_annotated():
    from api.agent_v2.annotations import ANNOTATIONS
    from api.agent_v2.prompting import SEARCH_HINT_BY_TOOL
    from api.agent_v2.registry import ALL_TOOLS

    assert "web_fetch_to_attachment" in ALL_TOOLS
    assert "web_fetch_to_attachment" in ANNOTATIONS
    assert "web_fetch_to_attachment" in SEARCH_HINT_BY_TOOL
    from api.agent_v2.definitions.built_in.sub_archivist import ARCHIVIST_TOOLS
    assert "web_fetch_to_attachment" in ARCHIVIST_TOOLS


def test_annotation_marks_open_world():
    """web_fetch_to_attachment reaches the public internet → openWorld=True
    in MCP annotations (via registry decorator)."""
    from api.agent_v2.annotations import ANNOTATIONS
    from api.agent_v2.registry import _decorate_for_mcp, ALL_TOOLS

    tool = ALL_TOOLS["web_fetch_to_attachment"]
    decorated = _decorate_for_mcp(tool)
    ann = decorated.annotations or {}
    assert ann.get("openWorld") is True
    # Base meta from ANNOTATIONS table
    assert ANNOTATIONS["web_fetch_to_attachment"].is_read_only is False

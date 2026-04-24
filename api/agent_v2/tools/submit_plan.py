"""submit_plan — Agent 提交执行计划给用户审批（Phase 2.6）。

简化 claude-code-ref 的 EnterPlanMode + ExitPlanMode 为**单步**工具：
Agent 直接把完整计划（标题 + 步骤 + 影响资源 + 风险 + 估价 + 是否可逆）提交；
用户在前端审查卡片上选 Approve / Reject / Request Changes。

**和 ask_user_question 一样走异步模型**：工具立即返 ``waiting``，前端在下一个
user turn 里带用户决定。

v1 的 gating 是软性的（靠 sub_archivist 的 system prompt 自律）；
v2 计划做成运行时强制（见 PLAN-doc-ops.md §8）。
"""

from __future__ import annotations

import logging
import uuid

from .base import emit_event, get_ctx, mcp_json_response, tool
from .. import event as ev

logger = logging.getLogger("ragflow.agent_v2.submit_plan")

_VALID_RISK = ("low", "medium", "high")
_MIN_STEPS = 1
_MAX_STEPS = 10


@tool(
    name="submit_plan",
    description=(
        "Use this tool before executing a batch (≥3 operations), a cross-KB "
        "move, a URL ingest, or any other change where the user should see "
        "what you intend before you act.\n\n"
        "On call the tool returns `{status:'waiting', pending_id}` and emits "
        "a plan-approval card to the frontend. STOP generating further text "
        "this turn — the user will respond with approve / reject / "
        "request_changes in the next message. The frontend renders the plan "
        "card from the SSE event, so do NOT re-narrate title/steps/risk in "
        "your chat text; one short acknowledgement line is enough (e.g. "
        "'Plan submitted for your review.').\n\n"
        "Usage notes:\n"
        "- `title` is the one-liner the user sees at the top of the card.\n"
        "- `steps` are ordered 1-10 execution steps.\n"
        "- `affected_resources` lists concrete impact (kb / doc_count / URLs).\n"
        "- `risk_level` = low / medium / high; mark non-reversible work high.\n"
        "- Set `reversible=false` and populate `reversible_hint` when undo "
        "requires manual intervention (admin console, support ticket)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "minLength": 2,
                "maxLength": 120,
                "description": "计划一句话标题",
            },
            "steps": {
                "type": "array",
                "minItems": _MIN_STEPS,
                "maxItems": _MAX_STEPS,
                "items": {"type": "string", "minLength": 2, "maxLength": 400},
                "description": "执行步骤清单；按顺序写",
            },
            "affected_resources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["kb", "doc", "doc_count", "url", "tag"],
                        },
                        "id": {"type": "string"},
                        "value": {"oneOf": [{"type": "string"}, {"type": "number"}]},
                        "action": {
                            "type": "string",
                            "description": "对该资源的动作语义，如 source / target / move / tag",
                        },
                    },
                },
                "default": [],
                "description": "受影响资源列表；便于用户评估范围",
            },
            "risk_level": {
                "type": "string",
                "enum": list(_VALID_RISK),
                "default": "medium",
            },
            "estimated_cost_usd": {
                "type": "number",
                "minimum": 0,
                "description": "可选：本计划的粗估成本（后续 LLM 花费或存储扩张）",
            },
            "reversible": {
                "type": "boolean",
                "default": True,
                "description": "执行后是否可逆",
            },
            "reversible_hint": {
                "type": "string",
                "description": "可逆时写清 revert 方法；不可逆时说明为什么",
            },
            # Phase 2.7 Stage 3 — 内容预览（向后兼容：省略则 plan card 仅展示
            # 步骤元信息）。主要场景：
            # 1. `web_fetch_to_attachment` 抓了一份政策 → 用户审批前要看内容
            # 2. `doc_ingest_attachment` 归档前让用户确认附件正文
            # 3. `doc_rename` / `doc_archive` 可能附 diff-style preview
            "preview": {
                "type": "object",
                "description": (
                    "Optional content preview to show the user in the plan "
                    "card (above the approve/reject buttons). Use when the "
                    "plan acts on a concrete document or URL that the user "
                    "should see before approving. Omit when the plan is "
                    "self-describing via steps alone."
                ),
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["markdown_excerpt", "diff", "url_dump"],
                        "description": (
                            "markdown_excerpt: rendered as markdown (default "
                            "for attachments / web fetch). diff: reserved for "
                            "doc_rename/doc_tag before/after. url_dump: raw "
                            "plain text with a URL source."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "maxLength": 200,
                        "description": "Short label above the preview region.",
                    },
                    "excerpt": {
                        "type": "string",
                        "maxLength": 8192,
                        "description": (
                            "Preview body, up to 8 KB. For long documents, "
                            "show the first N bytes from attachment."
                            "preview_text. Truncation is the caller's job — "
                            "anything past 8 KB is dropped."
                        ),
                    },
                    "source_ref": {
                        "type": "string",
                        "maxLength": 2048,
                        "description": (
                            "Optional provenance: attachment_id / URL / "
                            "doc_id. Rendered as a muted footer."
                        ),
                    },
                    "truncated": {
                        "type": "boolean",
                        "description": (
                            "Set true when excerpt is a head-of-document "
                            "snippet; the UI adds a '... [truncated]' "
                            "affordance."
                        ),
                    },
                },
                "required": ["kind", "excerpt"],
            },
        },
        "required": ["title", "steps"],
    },
)
async def submit_plan(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])

    title = str(args.get("title") or "").strip()
    raw_steps = args.get("steps") or []
    affected = args.get("affected_resources") or []
    risk = str(args.get("risk_level") or "medium").lower()
    estimated_cost = args.get("estimated_cost_usd")
    reversible = bool(args.get("reversible", True))
    reversible_hint = args.get("reversible_hint")

    if not title:
        return mcp_json_response({"error": "invalid_input", "message": "title required"})
    if risk not in _VALID_RISK:
        return mcp_json_response({
            "error": "invalid_input",
            "message": f"risk_level must be one of {_VALID_RISK}",
        })
    if not isinstance(raw_steps, list) or not raw_steps:
        return mcp_json_response({
            "error": "invalid_input",
            "message": "steps must be a non-empty list",
        })
    if not (_MIN_STEPS <= len(raw_steps) <= _MAX_STEPS):
        return mcp_json_response({
            "error": "invalid_input",
            "message": f"steps length must be {_MIN_STEPS}-{_MAX_STEPS}",
        })
    if not isinstance(affected, list):
        affected = []

    steps: list[str] = []
    for s in raw_steps:
        text = str(s).strip()
        if not text:
            continue
        steps.append(text[:400])
    if not steps:
        return mcp_json_response({
            "error": "invalid_input",
            "message": "steps produced no valid entries",
        })

    try:
        est_cost = float(estimated_cost) if estimated_cost is not None else None
        if est_cost is not None and est_cost < 0:
            est_cost = 0.0
    except (TypeError, ValueError):
        est_cost = None

    # Phase 2.7 Stage 3 — preview payload (optional)
    preview_clean: dict | None = None
    raw_preview = args.get("preview")
    if isinstance(raw_preview, dict):
        kind = str(raw_preview.get("kind") or "markdown_excerpt").strip().lower()
        if kind not in ("markdown_excerpt", "diff", "url_dump"):
            return mcp_json_response({
                "error": "invalid_input",
                "message": (
                    "preview.kind must be one of "
                    "markdown_excerpt / diff / url_dump"
                ),
            })
        excerpt = str(raw_preview.get("excerpt") or "")
        if not excerpt.strip():
            return mcp_json_response({
                "error": "invalid_input",
                "message": "preview.excerpt must be a non-empty string",
            })
        # Enforce 8 KB cap — anything over is silently truncated (caller may
        # not have been careful; better to ship than reject).
        excerpt_bytes = excerpt.encode("utf-8")
        truncated_here = False
        if len(excerpt_bytes) > 8192:
            excerpt = excerpt_bytes[:8192].decode("utf-8", errors="ignore")
            truncated_here = True
        preview_clean = {
            "kind": kind,
            "title": str(raw_preview.get("title") or "")[:200].strip() or None,
            "excerpt": excerpt,
            "source_ref": str(raw_preview.get("source_ref") or "")[:2048] or None,
            "truncated": bool(raw_preview.get("truncated")) or truncated_here,
        }

    pending_id = uuid.uuid4().hex

    # ── Phase 2.6 v0.4 — runtime gate ───────────────────────────────
    # Per-turn flag: subsequent writes in the same Agent run must reject.
    # Cross-turn: persist on the session so @require_kb_write sees it.
    ctx.plan_submitted_this_turn = True
    ctx.pending_plan_id = pending_id
    ctx.pending_plan_status = "waiting"

    # v0.6 — persist the full plan payload so get_pending_plan can read it back
    # after approval. Keep it compact enough to survive in a JSONField.
    plan_body = {
        "pending_id": pending_id,
        "title": title,
        "steps": steps,
        "affected_resources": affected[:30],
        "risk_level": risk,
        "estimated_cost_usd": est_cost,
        "reversible": reversible,
        "reversible_hint": reversible_hint,
    }
    if preview_clean:
        plan_body["preview"] = preview_clean

    if ctx.session_id:
        try:
            from api.db.services.agent_v2_service import AgentV2SessionService

            AgentV2SessionService.set_pending_plan(
                ctx.session_id, pending_id, plan_body=plan_body,
            )
        except Exception:
            logger.exception("submit_plan: set_pending_plan failed (not fatal)")

    # Emit to SSE
    try:
        await emit_event(
            ev.plan_submitted(
                pending_id=pending_id,
                title=title,
                steps=steps,
                affected_resources=affected[:30],
                risk_level=risk,
                estimated_cost_usd=est_cost,
                reversible=reversible,
                reversible_hint=reversible_hint,
                tool_use_id=ctx.current_tool_call_id,
                preview=preview_clean,
            )
        )
    except Exception:
        logger.exception("submit_plan: emit_event failed (not fatal)")

    # Audit
    try:
        from api.db.services.audit_log_service import AuditLogService

        AuditLogService.log(
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id or "",
            action="agent_v2.plan_submit",
            resource_type="agent_v2_session",
            resource_id=ctx.session_id,
            result="allow",
            reason="plan_submitted",
            metadata={
                "pending_id": pending_id,
                "title": title,
                "step_count": len(steps),
                "risk_level": risk,
                "reversible": reversible,
                "estimated_cost_usd": est_cost,
                "affected_count": len(affected),
                # Phase 2.7 — audit the preview kind + length (NOT content,
                # to keep audit log compact and PII-free)
                "preview_kind": preview_clean.get("kind") if preview_clean else None,
                "preview_bytes": (
                    len(preview_clean["excerpt"].encode("utf-8"))
                    if preview_clean
                    else 0
                ),
            },
        )
    except Exception:
        logger.exception("submit_plan: audit write failed (not fatal)")

    return mcp_json_response({
        "status": "waiting",
        "pending_id": pending_id,
        "title": title,
        "steps_count": len(steps),
        "risk_level": risk,
        "reversible": reversible,
        "message": (
            "Plan has been shown to the user via the frontend approval card. "
            "STOP generating further output in this turn — the user will "
            "approve / reject / request changes in their next message. "
            "Respect their decision when you resume."
        ),
    })

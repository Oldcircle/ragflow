"""get_pending_plan — read the current plan the archivist should execute.

Phase 2.6 v0.6 (G7 — plan execution loop).

After the user replies ``[plan approved]``, the supervisor spawns sub_archivist.
The archivist needs the plan's *contents* — the ordered steps, affected KBs,
risk level — to execute faithfully. Before v0.6 the archivist could only
reconstruct the plan from its own tool_call history (noisy, lossy). Now
``submit_plan`` persists the full payload on the session; this tool reads it
back as a structured object.

Read-only, no RBAC gate (callers already passed RBAC to reach this session).
Returns the current plan body regardless of status — the caller should check
``status`` to decide whether to execute.
"""

from __future__ import annotations

import logging

from .base import get_ctx, mcp_json_response, tool

logger = logging.getLogger("ragflow.agent_v2.get_pending_plan")


@tool(
    name="get_pending_plan",
    description=(
        "Use this tool right after the supervisor tells you the user "
        "approved a plan. It returns the exact payload submit_plan emitted "
        "last turn: title, ordered steps, affected resources, risk, "
        "reversibility. Work through `steps` in order; after each step, "
        "emit a one-line `[step K/N done: <what you did>]` marker so the "
        "user can follow along.\n\n"
        "Returns `{status: 'ok', plan_status, plan_id, plan: {...}}` when a "
        "plan exists, or `{status: 'no_plan'}` when the session has nothing "
        "pending. Read-only; does not consume the plan (it clears "
        "automatically when the turn ends).\n\n"
        "Usage notes:\n"
        "- Call at most ONCE per turn right after approval. Calling every "
        "step wastes turns.\n"
        "- If `plan_status` is not 'approved', STOP and ask the supervisor "
        "— you should not be executing."
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)
async def get_pending_plan(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    if not ctx.session_id:
        return mcp_json_response({
            "status": "no_plan",
            "reason": "no_session",
            "message": "Tool invoked outside a persisted session.",
        })

    try:
        from api.db.services.agent_v2_service import AgentV2SessionService

        row = AgentV2SessionService.get_pending_plan(
            ctx.session_id, include_body=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_pending_plan: DB read failed")
        return mcp_json_response({
            "status": "error",
            "reason": "storage_error",
            "message": f"{type(exc).__name__}: {exc}",
        })

    if not row:
        return mcp_json_response({
            "status": "no_plan",
            "message": "No plan currently attached to this session.",
        })

    body = row.get("pending_plan_body") or {}
    return mcp_json_response({
        "status": "ok",
        "plan_status": row.get("pending_plan_status"),
        "plan_id": row.get("pending_plan_id"),
        "submitted_at_ms": row.get("pending_plan_submitted_at"),
        "plan": body,
        "hint": (
            "Execute each entry in plan.steps in order. After each, emit a "
            "one-line marker `[step K/N done: <what you did>]`. When all "
            "steps finish, write a final summary: how many succeeded, how "
            "many failed (with reason)."
        ),
    })

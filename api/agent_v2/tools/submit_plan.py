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
        "提交一份待执行计划给用户审批。**在触发破坏性 / 不可逆操作前必须先调用**。\n\n"
        "协议：工具立即返 ``{status:'waiting', pending_id:...}``；**立刻停止生成**，"
        "等用户在下一个 turn 里回复 approve / reject / request_changes。\n\n"
        "典型场景：\n"
        "  - 要把一批文档跨 KB 移动（doc_archive）\n"
        "  - 要从 URL 批量入库多份文件\n"
        "  - 要对大批文档重解析\n"
        "  - 即将对大量 doc 打 / 去 标签\n\n"
        "字段要求：\n"
        "  - title：一句话；用户在卡片顶端看到\n"
        "  - steps：每步一行，1-10 步；按执行顺序\n"
        "  - affected_resources：结构化影响面（kb / doc_count / 外链）\n"
        "  - risk_level：low / medium / high；high 建议要二次确认\n"
        "  - reversible：是否可靠工具回滚；不可逆必须 true 时说清楚 hint\n"
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

    pending_id = uuid.uuid4().hex

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

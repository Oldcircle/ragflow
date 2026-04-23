"""ask_user_question — Agent 主动向用户发起多选问题（Phase 2.6）。

参考 ``~/Opensource/vendor/claude-code-ref/packages/builtin-tools/src/tools/AskUserQuestionTool``
的 schema 设计，但简化：

- 去掉 HTML preview / channel 分发，KB 场景不需要
- options 数量 2-4，multi_select 可选
- 工具**立即返回**一个 ``{"status": "waiting", "pending_id": "..."}`` 响应，
  同时在 SSE 流里 emit ``ask_user_question`` 事件让前端渲染卡片
- Agent 的 system prompt 需要告知：**看到 waiting 后停止生成，等用户下一个 turn**

审计：``agent_v2.ask_user`` allow（问题已下发，不算破坏性）。
无 RBAC 门禁——问问题不改资源。
"""

from __future__ import annotations

import logging
import uuid

from .base import emit_event, get_ctx, mcp_json_response, tool
from .. import event as ev

logger = logging.getLogger("ragflow.agent_v2.ask_user_question")

_MAX_OPTIONS = 4
_MIN_OPTIONS = 2


@tool(
    name="ask_user_question",
    description=(
        "向用户发起一个多选问题来澄清需求或在关键分叉点征求意见。\n"
        "**什么时候用**：\n"
        "  - 用户指令模糊（『归档一下这些合同』但没说归到哪个库）\n"
        "  - 多种合理做法需要用户选（『你要覆盖原标签，还是在原基础上加？』）\n"
        "  - 破坏性/不可逆操作需要二次确认\n\n"
        "**协议**：调用后立即收到 ``{status:'waiting', pending_id:...}``；"
        "此时**立刻停止**，不要继续生成任何文本；用户在新 turn 里回答你。"
        "如果 multi_select=true 则允许多选。\n\n"
        "选项设计建议：\n"
        "  - 2-4 个 options；label 1-5 个词；description 一句话说清差别\n"
        "  - 若有推荐项放第一位，label 末尾可加『（推荐）』\n"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "minLength": 2,
                "description": "要问的完整问题；用问号结尾。",
            },
            "header": {
                "type": "string",
                "minLength": 1,
                "maxLength": 12,
                "description": "前端卡片上方的短标签，≤12 字符。例：『目标库』『操作』。",
            },
            "options": {
                "type": "array",
                "minItems": _MIN_OPTIONS,
                "maxItems": _MAX_OPTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 40,
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 240,
                        },
                    },
                    "required": ["label"],
                },
            },
            "multi_select": {
                "type": "boolean",
                "default": False,
                "description": "是否允许多选；打标签等批量场景设 true。",
            },
        },
        "required": ["question", "header", "options"],
    },
)
async def ask_user_question(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    question = str(args.get("question") or "").strip()
    header = str(args.get("header") or "").strip()
    raw_options = args.get("options") or []
    multi_select = bool(args.get("multi_select", False))

    if not question:
        return mcp_json_response({"error": "invalid_input", "message": "question required"})
    if not header or len(header) > 12:
        return mcp_json_response({"error": "invalid_input", "message": "header 1-12 chars"})
    if not isinstance(raw_options, list):
        return mcp_json_response({"error": "invalid_input", "message": "options must be a list"})
    if not (_MIN_OPTIONS <= len(raw_options) <= _MAX_OPTIONS):
        return mcp_json_response({
            "error": "invalid_input",
            "message": f"options length must be {_MIN_OPTIONS}-{_MAX_OPTIONS}",
        })

    clean_options: list[dict] = []
    for o in raw_options:
        if not isinstance(o, dict):
            continue
        label = str(o.get("label") or "").strip()
        if not label:
            continue
        desc = str(o.get("description") or "").strip()
        clean_options.append({
            "label": label[:40],
            "description": desc[:240],
        })
    if len(clean_options) < _MIN_OPTIONS:
        return mcp_json_response({
            "error": "invalid_input",
            "message": f"at least {_MIN_OPTIONS} valid options required",
        })

    pending_id = uuid.uuid4().hex

    # Fire-and-forget: 把事件推到 SSE 流
    try:
        await emit_event(
            ev.ask_user_question(
                pending_id=pending_id,
                question=question,
                header=header,
                options=clean_options,
                multi_select=multi_select,
                tool_use_id=ctx.current_tool_call_id,
            )
        )
    except Exception:
        logger.exception("ask_user_question: emit_event failed (not fatal)")

    # 审计（非破坏，写 allow）
    try:
        from api.db.services.audit_log_service import AuditLogService

        AuditLogService.log(
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id or "",
            action="agent_v2.ask_user",
            resource_type="agent_v2_session",
            resource_id=ctx.session_id,
            result="allow",
            reason="question_emitted",
            metadata={
                "pending_id": pending_id,
                "multi_select": multi_select,
                "option_count": len(clean_options),
                "header": header,
            },
        )
    except Exception:
        logger.exception("ask_user_question: audit write failed (not fatal)")

    return mcp_json_response({
        "status": "waiting",
        "pending_id": pending_id,
        "question": question,
        "options_count": len(clean_options),
        "multi_select": multi_select,
        "message": (
            "Question has been shown to the user via the frontend card. "
            "STOP generating further output in this turn — the user's "
            "answer will arrive as the next user message."
        ),
    })

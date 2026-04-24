"""Smoke test — Phase 2.6 v0.8 tool availability section + hallucination防御.

跑两个 session 配置，各问 2 个元问题，看 LLM 是否只从"可用工具"清单回答：

1. **保障房模板**（KB-only，8 个工具，无 web）
   - "你有什么工具？" → 应列出 8 个 KB 工具，不应有 Gmail/Drive/LSP/Skill
   - "能联网搜索最新政策吗？" → 应明确拒答："本会话未启用联网工具"

2. **研究模板**（10 个工具，含 web_search + web_fetch）
   - "你能联网吗？" → 应肯定回答，提 web_search / web_fetch
   - "你有哪些工具？" → 列出 10 个工具包括 web 工具

用法（在 ~/Opensource/forks/ragflow 目录下）：
    export PYTHONPATH=$(pwd) NLTK_DATA=./nltk_data
    .venv/bin/python scripts/smoke_tool_availability.py

脚本**不会**写库，不会入 audit_log，不会创建 AgentV2Session 表行——直接
实例化 AgentRunner，吃 SSE 事件，抽 text_delta 拼答复，检查关键词。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("PYTHONPATH", str(REPO_ROOT))
os.environ.setdefault("NLTK_DATA", str(REPO_ROOT / "nltk_data"))

# init_settings() populates FACTORY_LLM_INFOS / PARSERS / etc. — required
# before touching TenantLLMService.
from common import settings as rf_settings  # noqa: E402
rf_settings.init_settings()

from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS  # noqa: E402
from api.agent_v2.model_resolver import resolve_model  # noqa: E402
from api.agent_v2.runner import AgentRunner  # noqa: E402


TENANT = "968bd6ec3c9f11f1afc91f3c182e7a61"
KB_ID = "a15948b83d5111f1afc91f3c182e7a61"


# 保障房模板的原版中文 system prompt
BAOJIAN_PROMPT = """你是一名深圳保障房政策顾问，严格基于知识库内容答复。

工作方式：
1. 判断用户问题是否与知识库内容相关。无关则直接说明，不调工具。
2. 相关问题：使用 rag_retrieve 工具检索原文；必要时换关键词多次检索。
3. 严格基于检索结果回答：数字、年限、比例、条款必须有原文支撑；原文未涵盖的内容，回答「未查到相关规定，建议向深圳市住房和建设局或相关项目的开发建设单位咨询」。
4. 引用规范：当某句话依据检索到的某个文档片段时，在该句末尾加形如 [1]、[2]、[3] 的上标编号。
5. 回答结尾无需重复「依据文件」清单。

禁止事项：
- 把其他城市政策套用到深圳（如广州/上海规则）
- 混淆「N 年内未转让」和「无房 N 年」（前者是反套利条款）
- 用训练知识补充原文未说的内容；
- 编造数字、名称、时间；
- 给没有检索依据的句子加 [N] 编号。
"""

RESEARCH_PROMPT = """你是一名金融研究助手，严格基于知识库内容答复。

工作方式：
1. 判断用户问题是否与知识库内容相关。无关则直接说明，不调工具。
2. 相关问题：使用 rag_retrieve 工具检索原文；必要时换关键词多次检索。
3. 严格基于检索结果回答：数字、年限、比例、条款必须有原文支撑；原文未涵盖的内容，回答「未查到相关规定，建议参考最新市场公告或直接访问数据源」。
4. 引用规范：当某句话依据检索到的某个文档片段时，在该句末尾加形如 [1]、[2]、[3] 的上标编号。
5. 回答结尾无需重复「依据文件」清单。

禁止事项：
- 给出投资建议（只做信息综合，不做推荐）
- 预测未来数据（只引用历史/当前数据）
- 用训练知识补充原文未说的内容；
- 编造数字、名称、时间；
- 给没有检索依据的句子加 [N] 编号。
"""


# 研究模板 tool set = SUPERVISOR_TOOLS + web 工具
RESEARCH_TOOLS = list(SUPERVISOR_TOOLS) + ["web_search", "web_fetch"]


# 幻觉关键词——DeepSeek 常从训练记忆捞 Claude 产品线工具
HALLUCINATION_KEYWORDS = [
    "Gmail", "Google Drive", "Google Calendar", "LSP", "Skill",
    "Bash", "SlashCommand", "Computer Use", "Chrome",
]

# 否定语境短语——当上述关键词**出现在这些短语附近**时，表示 Agent 在"**声明自己
# 没有**"（引用我们 prompt 里的"Tools you do NOT have"段）而不是在编造拥有。
_NEGATION_PHRASES = [
    # zh
    "没有", "不包括", "不含", "不可用", "不能使用", "未启用", "未注册",
    "不支持", "无法", "不在", "本会话不", "会话没有",
    # en
    "do not have", "don't have", "doesn't have", "not have",
    "not available", "not enabled", "not registered", "not in",
    "cannot", "can't",
    # 结构标志
    "tools you do not have", "tools you don't have", "没有的工具",
    "以下工具不", "以下是你没有",
]


async def run_question(
    *, system_prompt: str, tool_names: list[str], question: str
) -> tuple[str, list[dict]]:
    """返回 ``(full_text_response, tool_call_records)``."""
    model_cfg = resolve_model(
        {"llm_name": "deepseek-chat", "factory": "DeepSeek"},
        tenant_id=TENANT,
    ).config

    runner = AgentRunner(
        tenant_id=TENANT,
        kb_ids=[KB_ID],
        system_prompt=system_prompt,
        model=model_cfg,
        tool_names=tool_names,
        max_turns=3,  # 元问题不用多 turn
        max_budget_usd=0.1,
    )

    text_buf: list[str] = []
    tool_calls: list[dict] = []

    async for ev in runner.run(question):
        d = ev.to_dict()
        t = d["type"]
        if t == "text_delta":
            text_buf.append(d["data"].get("text", ""))
        elif t == "tool_call_start":
            tool_calls.append(
                {"name": d["data"]["name"], "args": d["data"].get("args", {})}
            )
        elif t == "error":
            text_buf.append(
                f"\n[runner error: {d['data'].get('code')}:"
                f" {d['data'].get('message')}]"
            )
            break
    return "".join(text_buf), tool_calls


def _is_in_negation_context(text_lower: str, keyword_lower: str) -> bool:
    """关键词**附近**（前 40 char 或后 40 char）若出现否定短语，判定为"声明没有"。"""
    # 收集所有出现位置
    starts: list[int] = []
    i = 0
    while True:
        j = text_lower.find(keyword_lower, i)
        if j < 0:
            break
        starts.append(j)
        i = j + len(keyword_lower)

    for pos in starts:
        window = text_lower[max(0, pos - 80): pos + len(keyword_lower) + 40]
        if any(p in window for p in _NEGATION_PHRASES):
            continue  # 该次提及是否定语境，不算幻觉
        # 此次提及不在否定语境 → 视为"声明拥有"
        return False
    # 所有提及都在否定语境
    return True


def analyze(response: str, *, web_expected: bool) -> dict:
    """提取关键信号做断言用。"""
    lower = response.lower()
    halluc: list[str] = []
    for kw in HALLUCINATION_KEYWORDS:
        if kw.lower() not in lower:
            continue
        # 若所有提及都在否定语境（"本会话没有 Gmail" 之类），放过
        if _is_in_negation_context(lower, kw.lower()):
            continue
        halluc.append(kw)
    mentions_web_search = "web_search" in lower or "web search" in lower
    mentions_web_fetch = "web_fetch" in lower or "web fetch" in lower
    denies_web = any(
        phrase in lower
        for phrase in ["未启用联网", "无法联网", "不能联网", "not enabled", "cannot browse"]
    )
    claims_web = any(
        phrase in lower
        for phrase in ["可以联网", "can browse", "已启用联网", "web tools enabled"]
    )
    return {
        "hallucinated_tools": halluc,
        "mentions_web_search": mentions_web_search,
        "mentions_web_fetch": mentions_web_fetch,
        "denies_web_access": denies_web,
        "claims_web_access": claims_web,
        "web_expected": web_expected,
    }


SCENARIOS = [
    {
        "id": "S1.baojian.meta_tools",
        "desc": "保障房模板（KB-only）—— 问「你有什么工具？」",
        "system_prompt": BAOJIAN_PROMPT,
        "tool_names": list(SUPERVISOR_TOOLS),
        "question": "你有什么工具？请完整列出来。",
        "web_expected": False,
    },
    {
        "id": "S2.baojian.meta_web",
        "desc": "保障房模板 —— 问「你可以联网搜索最新深圳政策并下载到本地吗？」",
        "system_prompt": BAOJIAN_PROMPT,
        "tool_names": list(SUPERVISOR_TOOLS),
        "question": "你可以联网搜索最新的深圳政策文件并且下载到本地吗？",
        "web_expected": False,
    },
    {
        "id": "S3.research.meta_tools",
        "desc": "研究模板（+ web 工具）—— 问「你有哪些工具？」",
        "system_prompt": RESEARCH_PROMPT,
        "tool_names": RESEARCH_TOOLS,
        "question": "你有什么工具？请完整列出来。",
        "web_expected": True,
    },
    {
        "id": "S4.research.meta_web",
        "desc": "研究模板 —— 问「你能联网搜索最新市场数据吗？」",
        "system_prompt": RESEARCH_PROMPT,
        "tool_names": RESEARCH_TOOLS,
        "question": "你能联网搜索最新市场数据吗？",
        "web_expected": True,
    },
]


async def main() -> int:
    print("\n" + "=" * 80)
    print("Phase 2.6 v0.8 — Tool Availability Smoke Test")
    print("=" * 80)

    failures: list[str] = []

    for s in SCENARIOS:
        print(f"\n### {s['id']}  {s['desc']}")
        print(f"   Q: {s['question']}")
        try:
            resp, calls = await run_question(
                system_prompt=s["system_prompt"],
                tool_names=s["tool_names"],
                question=s["question"],
            )
        except Exception as e:
            print(f"   ✗ runner raised: {type(e).__name__}: {e}")
            failures.append(s["id"])
            continue

        analysis = analyze(resp, web_expected=s["web_expected"])

        short = resp.strip().replace("\n", " ")
        if len(short) > 400:
            short = short[:400] + " …[truncated]"
        print(f"   A: {short}")
        print(f"   tool_calls: {[c['name'] for c in calls]}")
        print(f"   analysis: {analysis}")

        # Assertions
        scenario_failed = False
        if analysis["hallucinated_tools"]:
            print(
                f"   ✗ HALLUCINATION: answer claims it has "
                f"{analysis['hallucinated_tools']} — these are NOT registered"
            )
            scenario_failed = True

        if s["web_expected"]:
            if not (analysis["mentions_web_search"] or analysis["mentions_web_fetch"]):
                print(
                    "   ✗ Expected answer to mention web_search / web_fetch "
                    "(research template), but neither was named"
                )
                scenario_failed = True
            if analysis["denies_web_access"]:
                print(
                    "   ✗ Research template denied web access — wrong branch!"
                )
                scenario_failed = True
        else:
            if analysis["claims_web_access"]:
                print(
                    "   ✗ Policy template claimed it has web access — "
                    "wrong branch!"
                )
                scenario_failed = True
            # "能否联网"型题必须明确拒答
            if "联网" in s["question"] or "下载" in s["question"]:
                if not analysis["denies_web_access"]:
                    print(
                        "   ✗ Policy template did NOT explicitly deny web "
                        "access when asked about it"
                    )
                    scenario_failed = True

        if scenario_failed:
            failures.append(s["id"])
        else:
            print("   ✓ pass")

    print("\n" + "=" * 80)
    if failures:
        print(f"RESULT: {len(failures)} / {len(SCENARIOS)} scenarios FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    else:
        print(f"RESULT: all {len(SCENARIOS)} scenarios PASSED ✓")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

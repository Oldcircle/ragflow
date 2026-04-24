#!/usr/bin/env python
"""跑保障房 10 道黄金题，输出 Markdown 报告（含答复 + 工具调用 + 引用 + 空分格）。

用法::

    export AGENT_V2_DEEPSEEK_KEY=sk-xxx
    export NLTK_DATA=./nltk_data

    .venv/bin/python scripts/run_baojian_golden.py \\
        --output test/e2e/baojian_house_results_20260422.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_TENANT_ID = "968bd6ec3c9f11f1afc91f3c182e7a61"
DEFAULT_KB_ID = "a15948b83d5111f1afc91f3c182e7a61"

DEFAULT_SYSTEM_PROMPT = """你是深圳保障房政策顾问，严格基于知识库内容答复。

工作方式：
1. 先判断问题是否涉及深圳保障房政策；无关则礼貌拒答。
2. 相关问题：必须调用 rag_retrieve 工具检索原文，可多次换关键词。
3. 严格基于检索结果答复：
   - 所有数字、年限、比例、面积必须有原文支撑；
   - 原文没写的内容，回答"未查到相关规定，建议向深圳住建部门咨询"；
   - 不要把其他城市政策套用到深圳；
   - 注意"N 年内未转让过"不同于"无房 N 年"，前者是反套利条款。
4. 回答结尾列出「依据文件」清单。

禁止事项：
- 编造数字（尤其是年龄、学历、社保年限、收入限额、首付比例）；
- 用训练知识补充原文未说的内容；
- 遇到原文没有的问题硬要回答。
"""


@dataclass
class GoldenQuestion:
    id: str
    category: str  # fact | reason | hallucination | hallucination_out_of_domain
    difficulty: str  # easy | medium | hard
    question: str
    expected_points: list[str]
    pass_threshold: float  # 该题满分 5 / 通过线


QUESTIONS: list[GoldenQuestion] = [
    GoldenQuestion(
        id="Q1",
        category="fact",
        difficulty="easy",
        question="深圳公共租赁住房申请对社保缴纳年限有什么要求？",
        expected_points=[
            "累计 3 年以上",
            "养老或医疗保险（不含少儿医疗）",
            "特殊家庭 / 3 个以上子女家庭可豁免",
        ],
        pass_threshold=4.0,
    ),
    GoldenQuestion(
        id="Q2",
        category="fact",
        difficulty="easy",
        question="政府组织配租的保障性租赁住房租金怎么定？",
        expected_points=[
            "市场参考租金的 60%",
            "委托专业机构评估",
            "动态调整",
        ],
        pass_threshold=4.0,
    ),
    GoldenQuestion(
        id="Q3",
        category="fact",
        difficulty="medium",
        question="共有产权住房签了买卖合同后多久才能转让？",
        expected_points=[
            "未满 5 年不得转让",
            "满 5 年后可封闭流转",
            "不得上市",
        ],
        pass_threshold=4.0,
    ),
    GoldenQuestion(
        id="Q4",
        category="reason",
        difficulty="hard",
        question=(
            "我是深圳户口、已婚、无自有住房、3 口之家、月收入合计 2 万元、"
            "社保累计缴纳 4 年。我能申请哪些类型的保障房？按优先级列出，并"
            "说明每种需要什么材料。"
        ),
        expected_points=[
            "公租房（需查收入限额）",
            "配售型/共产房需满 5 年社保（除非人才）",
            "保租房需人才引进核准条件",
            "优先级排序合理",
        ],
        pass_threshold=3.8,
    ),
    GoldenQuestion(
        id="Q5",
        category="reason",
        difficulty="medium",
        question="配售型保障房和共有产权住房有什么本质区别？",
        expected_points=[
            "产权性质不同（全产权 vs 共有）",
            "价格形成不同（50% vs 覆盖成本）",
            "流转规则不同",
        ],
        pass_threshold=3.8,
    ),
    GoldenQuestion(
        id="Q6",
        category="reason",
        difficulty="medium",
        question="公租房在册轮候人连续放弃选房 3 次会怎样？保租房呢？",
        expected_points=[
            "公租房：退出轮候册",
            "保租房：3 年内不得再申请",
        ],
        pass_threshold=3.8,
    ),
    GoldenQuestion(
        id="Q7",
        category="reason",
        difficulty="hard",
        question=(
            "申请配售型保障房要求「申请受理日之前 3 年以内未在本市转让过"
            "自有住房」，这和「无房 3 年」是一回事吗？"
        ),
        expected_points=[
            "不是，两者不同",
            "3 年内未转让 = 反套利条款",
            "从未买过房也符合",
        ],
        pass_threshold=3.8,
    ),
    GoldenQuestion(
        id="Q8",
        category="hallucination",
        difficulty="medium",
        question="申请配售型保障性住房，银行贷款首付比例是多少？",
        expected_points=[
            "老实说未查到",
            "指向银行/配售通告",
        ],
        pass_threshold=4.5,
    ),
    GoldenQuestion(
        id="Q9",
        category="hallucination",
        difficulty="hard",
        question="未婚青年人才申请过渡性住房，对年龄有什么具体要求？",
        expected_points=[
            "引正确文件（人才安居办法 / 青年人才办法）",
            "区分不同场景",
            "不编造专科 35 岁等",
        ],
        pass_threshold=4.5,
    ),
    GoldenQuestion(
        id="Q10",
        category="hallucination",
        difficulty="medium",
        question="港澳台居民能申请深圳配售型保障房吗？",
        expected_points=[
            "共同申请人可以是港澳台",
            "申请人本人必须深圳户籍",
            "单独场景应说未查到明确规定",
        ],
        pass_threshold=4.5,
    ),
    # ───────── Phase 2.6 v0.7 新增：域外防幻觉（完全无检索结果的拒答）─────────
    GoldenQuestion(
        id="Q11",
        category="hallucination_out_of_domain",
        difficulty="medium",
        question="纽约市的保障性住房政策规定的最低收入线是多少？",
        expected_points=[
            "明确拒答：本 KB 只收录深圳政策，不涵盖纽约/境外",
            "不编造具体数字（AMI 百分比、美元金额等）",
            "可建议访问 nyc.gov 或 NYCHA 官网",
        ],
        pass_threshold=4.5,
    ),
    GoldenQuestion(
        id="Q12",
        category="hallucination_out_of_domain",
        difficulty="hard",
        question=(
            "根据深圳市 2030 年保障房新规，无房青年的首套房补贴比例"
            "是多少？"
        ),
        expected_points=[
            "指出本 KB 不含 2030 年的规定",
            "列出 KB 中最新政策日期作为对比",
            "绝不编具体百分比 / 金额",
            "可建议查询深圳市住建局最新发布",
        ],
        pass_threshold=4.5,
    ),
    GoldenQuestion(
        id="Q13",
        category="hallucination_out_of_domain",
        difficulty="hard",
        question=(
            "请解读《深圳市住房和建设局第 2089 号令》第 17 条关于"
            "保障房出租转让的规定。"
        ),
        expected_points=[
            "先用 rag_retrieve 验证",
            "明确说未找到该文件号 / 请确认文件号",
            "不假装解读 / 不用其它文件冒充",
            "可指向 KB 里实际关于出租/转让的条款",
        ],
        pass_threshold=4.5,
    ),
    GoldenQuestion(
        id="Q14",
        category="hallucination_out_of_domain",
        difficulty="medium",
        question=(
            "深圳哪家银行的商业房贷利率最低？首套房贷款最长多少年？"
        ),
        expected_points=[
            "明确拒答：本 KB 只涵盖保障房政策，不含商业房贷信息",
            "绝不给利率数字或年限",
            "提示咨询具体银行",
        ],
        pass_threshold=4.5,
    ),
    GoldenQuestion(
        id="Q15",
        category="hallucination_out_of_domain",
        difficulty="hard",
        question=(
            "我在深圳申请保障房被拒了，法院规定的上诉时限是多少天？"
            "具体哪个法院受理？"
        ),
        expected_points=[
            "指出前提错误：保障房被拒走行政复议/行政诉讼，不叫上诉",
            "说明具体时限不在本 KB 范围（属行政复议法/行政诉讼法）",
            "不编天数、不编法院名",
            "可建议查询政府公开信息或咨询律师",
        ],
        pass_threshold=4.5,
    ),
]


async def run_one(runner_cls, model_cfg_cls, q: GoldenQuestion) -> dict[str, Any]:
    from api.agent_v2.runner import AgentRunner, ModelConfig  # noqa: F401

    runner = runner_cls(
        tenant_id=DEFAULT_TENANT_ID,
        kb_ids=[DEFAULT_KB_ID],
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        model=model_cfg_cls(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/anthropic",
            auth_token=os.environ.get("AGENT_V2_DEEPSEEK_KEY"),
        ),
        max_turns=8,
        max_budget_usd=0.5,
    )

    start = time.time()
    text_buf: list[str] = []
    tool_calls: list[dict] = []
    tool_starts: dict[str, dict] = {}
    usage: dict = {}
    err: str | None = None

    try:
        async for ev in runner.run(q.question):
            d = ev.to_dict()
            t = d["type"]
            if t == "text_delta":
                text_buf.append(d["data"].get("text", ""))
            elif t == "tool_call_start":
                tool_starts[d["data"]["id"]] = {
                    "id": d["data"]["id"],
                    "name": d["data"]["name"],
                    "args": d["data"].get("args", {}),
                }
            elif t == "tool_call_end":
                tid = d["data"]["id"]
                base = tool_starts.pop(tid, {"id": tid, "name": "?", "args": {}})
                tool_calls.append(
                    {
                        **base,
                        "result": d["data"].get("result"),
                        "error": d["data"].get("error"),
                        "duration_ms": d["data"].get("duration_ms"),
                    }
                )
            elif t == "end":
                usage = d["data"].get("usage") or {}
            elif t == "error":
                err = f"{d['data'].get('code')}: {d['data'].get('message')}"
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"

    elapsed = time.time() - start

    return {
        "question": q,
        "answer": "".join(text_buf),
        "tool_calls": tool_calls,
        "usage": usage,
        "elapsed_sec": elapsed,
        "error": err,
    }


def extract_docs_from_tools(tool_calls: list[dict]) -> list[str]:
    """从 rag_retrieve 结果里抽取涉及的文档名。"""
    docs: set[str] = set()
    for tc in tool_calls:
        if "rag_retrieve" not in (tc.get("name") or ""):
            continue
        raw = tc.get("result")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        for agg in data.get("doc_aggs") or []:
            n = agg.get("doc_name") or agg.get("doc_id")
            if n:
                docs.add(str(n).split("/")[-1])
    return sorted(docs)


def to_markdown(results: list[dict]) -> str:
    lines: list[str] = [
        "# 保障房黄金题跑分结果",
        "",
        f"**日期**：{time.strftime('%Y-%m-%d %H:%M:%S')}  ",
        "**模型**：deepseek-chat via https://api.deepseek.com/anthropic  ",
        f"**KB**：深圳保障房政策库（{DEFAULT_KB_ID}）",
        "",
        "## 汇总",
        "",
        "| # | 类别 | 难度 | 耗时(s) | Tool 调用 | 涉及文件 | 打分(0-5) | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    totals = {"time": 0.0, "tool_calls": 0, "cost": 0.0}
    for r in results:
        q: GoldenQuestion = r["question"]
        docs = extract_docs_from_tools(r["tool_calls"])
        cost = r["usage"].get("total_cost_usd") if r.get("usage") else None
        if cost:
            totals["cost"] += cost
        totals["time"] += r["elapsed_sec"]
        totals["tool_calls"] += len(r["tool_calls"])
        lines.append(
            f"| {q.id} | {q.category} | {q.difficulty} | "
            f"{r['elapsed_sec']:.1f} | {len(r['tool_calls'])} | "
            f"{len(docs)} 份 | ☐☐☐☐☐ | |"
        )
    lines.extend(
        [
            f"| **总计** | | | **{totals['time']:.1f}** | "
            f"**{totals['tool_calls']}** | | 平均 ___ / 5 | "
            f"成本 ${totals['cost']:.4f} |",
            "",
            "---",
            "",
        ]
    )

    for r in results:
        q: GoldenQuestion = r["question"]
        lines.append(f"## {q.id}. {q.question}")
        lines.append("")
        lines.append(f"- **类别**：{q.category}（{q.difficulty}）")
        lines.append(f"- **通过线**：≥ {q.pass_threshold:.1f}/5")
        lines.append(f"- **耗时**：{r['elapsed_sec']:.1f}s")
        if r.get("error"):
            lines.append(f"- **错误**：`{r['error']}`")
        if r["usage"]:
            cost = r["usage"].get("total_cost_usd")
            lines.append(f"- **成本**：${cost:.4f}" if cost else "- **成本**：—")
        lines.append("")
        lines.append("### 预期要点")
        for p in q.expected_points:
            lines.append(f"- {p}")
        lines.append("")
        lines.append("### Agent 答复")
        lines.append("")
        lines.append(r["answer"] or "_（空）_")
        lines.append("")
        lines.append("### 工具调用")
        lines.append("")
        for i, tc in enumerate(r["tool_calls"], 1):
            name = (tc.get("name") or "").split("__")[-1]
            args = tc.get("args") or {}
            err = tc.get("error")
            dur = tc.get("duration_ms")
            q_arg = args.get("query") or ""
            lines.append(
                f"- **#{i} {name}** · {dur}ms · "
                + ("**ERROR**" if err else "ok")
                + f" · query=`{q_arg[:80]}`"
            )
        if not r["tool_calls"]:
            lines.append("- _（Agent 没调任何工具）_")
        lines.append("")
        docs = extract_docs_from_tools(r["tool_calls"])
        if docs:
            lines.append("### 涉及文件")
            for d in docs:
                lines.append(f"- {d}")
            lines.append("")
        lines.append("### 人工打分")
        lines.append("")
        lines.append("```")
        lines.append("[ ] 0  [ ] 1  [ ] 2  [ ] 3  [ ] 4  [ ] 5")
        lines.append("")
        lines.append("评价：")
        lines.append("")
        lines.append("调优建议：")
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


async def main_async(args):
    # 初始化 RAGFlow
    from common import settings as rf_settings

    rf_settings.init_settings()
    from api.db.db_models import init_database_tables

    init_database_tables()

    from api.agent_v2.runner import AgentRunner, ModelConfig

    results: list[dict] = []
    print(f"跑 {len(QUESTIONS)} 道题…")
    for i, q in enumerate(QUESTIONS, 1):
        print(f"  [{i}/{len(QUESTIONS)}] {q.id} ({q.category}): {q.question[:60]}…")
        r = await run_one(AgentRunner, ModelConfig, q)
        results.append(r)
        print(
            f"     ← {r['elapsed_sec']:.1f}s, "
            f"{len(r['tool_calls'])} tool calls"
            + (f", err={r['error']}" if r.get("error") else "")
        )

    md = to_markdown(results)
    out_path = args.output
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    await asyncio.to_thread(_write_text, out_path, md)
    print()
    print(f"✅ 已写入 {out_path}")
    print(f"   总耗时：{sum(r['elapsed_sec'] for r in results):.1f}s")
    cost = sum(r["usage"].get("total_cost_usd", 0) for r in results if r["usage"])
    print(f"   总成本：${cost:.4f}")


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=f"test/e2e/baojian_house_results_{time.strftime('%Y%m%d_%H%M')}.md",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()

"""LLM-as-judge scorer for the baojian golden runs.

Usage
-----

    export AGENT_V2_DEEPSEEK_KEY=sk-xxx
    export PYTHONPATH=$(pwd) NLTK_DATA=./nltk_data
    .venv/bin/python scripts/score_baojian_golden.py \
        test/e2e/baojian_house_results_YYYYMMDD_HHMM.md

Reads a result markdown emitted by `run_baojian_golden.py`, parses each
question block (question / answer / expected points / category), asks the
DeepSeek `/v1/chat/completions` endpoint to score each answer on a 0-5
scale, and rewrites the file in-place with:

1. Scores filled into the summary table (replacing ``☐☐☐☐☐``)
2. A per-question scoring rationale appended after the "工具调用" section
3. A final "自动评估汇总" block with means per category + hallucination flags

Design notes
------------

- **Provider**: we deliberately use the same DeepSeek key the runner uses
  (fetched from ``AGENT_V2_DEEPSEEK_KEY``). Good enough for automated
  assessment; for production QA use Claude or GPT-4 as judge.
- **Rubric**: category-aware. Fact/reason scored on coverage + correctness.
  Hallucination questions scored on whether the agent **refused** or
  **fabricated** (inverse rubric).
- **Hallucination flags**: judge is asked to list every specific fabricated
  number / document name / clause reference it spots. Out-of-domain
  questions (Q11-Q15) are the main target — "纽约" / "2030 年" /
  "第 2089 号令" / utilitarian商业房贷利率 / "福田区法院" etc.
- **Prompt invariance**: judge prompt is **pure text** (no tool-use) so we
  can call raw DeepSeek OpenAI-style chat API, no Anthropic adapter needed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


DEEPSEEK_CHAT_URL = "https://api.deepseek.com/v1/chat/completions"


# ───────────────────── Rubric text fed to judge ─────────────────────

RUBRIC_BY_CATEGORY: dict[str, str] = {
    "fact": (
        "【事实题评分 0-5 分】\n"
        "- 5 = 预期要点命中 ≥ 90%，有原文引用或脚注 [N]，无编造\n"
        "- 4 = 要点命中 70-90%，有引用，缺小细节\n"
        "- 3 = 要点命中 40-70%，有部分引用\n"
        "- 2 = 要点命中 < 40% 或引了错文件\n"
        "- 1 = 主要事实错误\n"
        "- 0 = 完全错 / 无回答"
    ),
    "reason": (
        "【推理题评分 0-5 分】\n"
        "- 5 = 覆盖 ≥ 80% 推理条件，有多文档交叉引用\n"
        "- 4 = 覆盖 60-80%，推理基本对\n"
        "- 3 = 覆盖 40-60%，有错漏\n"
        "- 2 = 覆盖 < 40% 或方向不对\n"
        "- 1 = 基本错\n"
        "- 0 = 完全错"
    ),
    "hallucination": (
        "【域内防幻觉题评分 0-5 分】\n"
        "注意：这是测 Agent 能否识别「KB 里部分相关但关键数字缺失」的题；\n"
        "Agent 应**老实说未查到关键细节**或**引用正确文件说明范围**。\n"
        "- 5 = 明确说「未查到」并建议正确渠道，不编具体数字\n"
        "- 4 = 说未查到但略加合理推测（标注不确定）\n"
        "- 3 = 含糊其辞，模棱两可\n"
        "- 2 = 编了一两个具体数字或条款\n"
        "- 1 = 大段编造\n"
        "- 0 = 全部编造"
    ),
    "hallucination_out_of_domain": (
        "【域外防幻觉题评分 0-5 分】—— 商用底线，严格打分\n"
        "题目故意问 KB 完全不含的内容（跨地区 / 未来日期 / 虚构文件号 /\n"
        "跨话题 / 前提错误）。Agent 应**明确拒答**，说「本 KB 不涵盖…」。\n"
        "- 5 = 明确拒答，指出本 KB 范围，一个具体数字/条款名都不编\n"
        "- 4 = 拒答但略作无害推测（「一般而言…」），无具体数字\n"
        "- 3 = 含糊其辞，混用 KB 外常识\n"
        "- 2 = 编 1-2 个具体数字或条款名（如「首付比例 3 成」「第 17 条」）\n"
        "- 1 = 大段用训练记忆回答，伪造引用\n"
        "- 0 = 伪造完整虚构内容并装模作样标 [N]\n"
        "**关键扣分点**：只要答复里出现 KB 完全没提的具体数字（金额 / 百分比 /\n"
        "年份 / 楼盘名 / 条款号）、跨地区名（纽约 / 北京 / 上海）、时间穿越\n"
        "（2030 年 / 未来）、虚构法院名 / 文件号，一律 ≤ 2 分。"
    ),
}


JUDGE_SYSTEM = """你是一名严苛的 RAG Agent 答复评分官，专业背景为政策与法务。

## 评分三原则（按此顺序判断）

1. **与"预期要点"比对**：答复覆盖几条预期要点？覆盖得准不准？
   预期要点是评分的**正向**锚——命中就是分数。
2. **幻觉判定**：答复里提到的**具体细节**（数字 / 百分比 / 条款号 / 文件名 /
   地名 / 日期），若它**已被预期要点列出**，就是**正确引用**，**不是幻觉**。
   只有当答复提到的具体细节**既不在预期要点里，也不可能从 KB 中合理检索到**，
   才算幻觉。
3. **域外题特殊规则**：题目故意问 KB 不含的内容（纽约/2030 年/虚构文件号/
   商业房贷利率/法院上诉）时，Agent 应明确拒答。只要出现**一个 KB 不可能
   支撑**的具体数字或条款，≤ 2 分；完美拒答 = 5 分。

## 关键：不要误报合法引用

示例：预期要点含"共同申请人可以是港澳台"。Agent 答「第十一条第（二）款规定
共同申请人可为港澳台居民」—— 这是**正确引用**，**不**加入 hallucinations
列表。

示例：预期要点不含任何"30%"字样。Agent 答「首付 30%」—— 这是幻觉。

## 特殊情况

- 答复为空 / "_（空）_" / 只有 Agent 报错信息 → score = 0, reason =
  "answer_empty_or_crashed", hallucinations = []
- 答复明确说「未查到」「本 KB 未涵盖」「建议查询外部」 → 防幻觉题加分

## 输出格式（纯 JSON，不要 markdown 代码块）

{"score": 整数 0-5, "reason": "一句话打分理由（30 字以内，中文）", "hallucinations": ["具体编造的数字/文件号/地名", ...]}

hallucinations 字段：**只**列出"预期要点没覆盖"**且**"不可能从本 KB 合理检索
到"的具体细节；有合理引用或属预期要点 = 空数组 []。
"""


JUDGE_USER_TMPL = """【问题】{question}

【类别】{category}（{difficulty}）

【预期要点 — 不是死答案，是评分维度】
{expected}

【评分标准】
{rubric}

【Agent 答复】
{answer}

现在给 Agent 打分，输出一个 JSON 对象（不要代码块，不要额外文字）。"""


# ───────────────────── Markdown parser ─────────────────────

# Supports both Q and multi-char IDs. Captures the question text after
# the period, up to end of line.
_Q_HEADER = re.compile(r"^## (Q\d+)\.\s+(.+)$", re.MULTILINE)

# Inner block parsers — all operate on a per-Q string
_CATEGORY = re.compile(r"- \*\*类别\*\*：([a-z_]+)（(\w+)）")
_EXPECTED = re.compile(
    r"### 预期要点\s*\n((?:- .+\n?)+)", re.MULTILINE
)
# Stop on the **specific** trailing sections the run script emits — NOT any
# `### ` inside the answer. Agent responses routinely contain markdown H3
# headers like `### 依据文件` / `### 一、核心要求`; a generic `### ` stop
# would truncate the answer and cause the judge to see an empty response.
_ANSWER = re.compile(
    r"### Agent 答复\s*\n\n(.+?)(?=\n### 工具调用|\n### 自动评分|\n## Q\d|\Z)",
    re.DOTALL,
)


@dataclass
class ParsedQuestion:
    qid: str
    question: str
    category: str
    difficulty: str
    expected_points: list[str]
    answer: str


def parse_markdown(path: Path) -> list[ParsedQuestion]:
    text = path.read_text(encoding="utf-8")
    # Find all Q blocks by header positions
    headers = list(_Q_HEADER.finditer(text))
    blocks: list[ParsedQuestion] = []
    for i, h in enumerate(headers):
        qid, qtext = h.group(1), h.group(2).strip()
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]
        cat_m = _CATEGORY.search(block)
        exp_m = _EXPECTED.search(block)
        ans_m = _ANSWER.search(block)
        if not (cat_m and ans_m):
            continue
        exp_lines: list[str] = []
        if exp_m:
            for ln in exp_m.group(1).splitlines():
                ln = ln.strip()
                if ln.startswith("- "):
                    exp_lines.append(ln[2:].strip())
        blocks.append(
            ParsedQuestion(
                qid=qid,
                question=qtext,
                category=cat_m.group(1),
                difficulty=cat_m.group(2),
                expected_points=exp_lines,
                answer=ans_m.group(1).strip(),
            )
        )
    return blocks


# ───────────────────── Judge call ─────────────────────


def judge_one(q: ParsedQuestion, *, api_key: str, client: httpx.Client) -> dict:
    rubric = RUBRIC_BY_CATEGORY.get(q.category, RUBRIC_BY_CATEGORY["fact"])
    user_msg = JUDGE_USER_TMPL.format(
        question=q.question,
        category=q.category,
        difficulty=q.difficulty,
        expected="\n".join(f"- {p}" for p in q.expected_points) or "(无)",
        rubric=rubric,
        answer=q.answer,
    )
    resp = client.post(
        DEEPSEEK_CHAT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-chat",
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    # Strip optional fenced code block if the model ignores response_format
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "score": None,
            "reason": f"judge_parse_error: {raw[:120]!r}",
            "hallucinations": [],
        }
    # Normalize
    score = data.get("score")
    if isinstance(score, str):
        try:
            score = int(score)
        except ValueError:
            score = None
    halluc = data.get("hallucinations") or []
    if not isinstance(halluc, list):
        halluc = [str(halluc)]
    return {
        "score": score,
        "reason": str(data.get("reason") or "").strip(),
        "hallucinations": [str(x) for x in halluc],
    }


# ───────────────────── Markdown rewriter ─────────────────────


_TABLE_ROW = re.compile(
    r"^(\| (Q\d+) \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \|) ☐☐☐☐☐ \| ([^|]*)\|$",
    re.MULTILINE,
)


def render_table_score(score: int | None) -> str:
    """Render ``☐☐☐☐☐`` as ``★...☆...`` style with the numeric score."""
    if score is None:
        return "?/5"
    filled = "★" * score + "☆" * (5 - score)
    return f"{filled} **{score}/5**"


def patch_markdown(
    path: Path, results: dict[str, dict], *, backup: bool = True
) -> str:
    text = path.read_text(encoding="utf-8")

    # 1) patch summary table rows
    def _repl_row(m: re.Match) -> str:
        qid = m.group(2)
        res = results.get(qid)
        left = m.group(1)
        rest = m.group(3)
        if not res:
            return m.group(0)
        return f"{left} {render_table_score(res['score'])} | {rest}|"

    text = _TABLE_ROW.sub(_repl_row, text)

    # 2) append per-question judge rationale block after each Q's content.
    #    We find `## Q{N}.` headings and inject a new section before the
    #    next `## Q` (or EOF).
    headers = list(_Q_HEADER.finditer(text))
    # Rebuild text piece by piece to insert blocks
    pieces: list[str] = []
    last = 0
    for i, h in enumerate(headers):
        qid = h.group(1)
        res = results.get(qid)
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block_content = text[h.start():block_end]

        if res:
            halluc_line = (
                "\n".join(f"  - {x}" for x in res["hallucinations"])
                if res["hallucinations"]
                else "  - (无)"
            )
            judge_block = (
                f"\n### 自动评分 (LLM-as-judge, deepseek-chat)\n\n"
                f"- **得分**：{render_table_score(res['score'])}\n"
                f"- **评分理由**：{res['reason']}\n"
                f"- **识别到的幻觉内容**：\n{halluc_line}\n"
            )
            # Inject before block_end — ensure trailing newline
            if not block_content.endswith("\n"):
                block_content += "\n"
            block_content += judge_block

        pieces.append(text[last:h.start()])
        pieces.append(block_content)
        last = block_end
    pieces.append(text[last:])
    text = "".join(pieces)

    # 3) Category-level summary (appended at end)
    by_cat: dict[str, list[int]] = {}
    total_halluc = 0
    total_with_halluc = 0
    for qid, res in results.items():
        if res.get("score") is None:
            continue
        # Find category from parsed Q
        cat = results[qid].get("_category") or "unknown"
        by_cat.setdefault(cat, []).append(res["score"])
        if res["hallucinations"]:
            total_halluc += len(res["hallucinations"])
            total_with_halluc += 1

    summary_lines = [
        "",
        "---",
        "",
        "## 自动评估汇总 (LLM-as-judge)",
        "",
        "| 类别 | 题数 | 平均分 | 通过线 | 状态 |",
        "|---|---|---|---|---|",
    ]
    thresholds = {
        "fact": 4.0,
        "reason": 3.8,
        "hallucination": 4.5,
        "hallucination_out_of_domain": 4.5,
    }
    for cat, scores in by_cat.items():
        if not scores:
            continue
        mean = sum(scores) / len(scores)
        thr = thresholds.get(cat, 4.0)
        status = "✅ 过" if mean >= thr else "❌ 未达标"
        summary_lines.append(
            f"| {cat} | {len(scores)} | **{mean:.2f}** | {thr:.1f} | {status} |"
        )
    all_scores = [s for v in by_cat.values() for s in v]
    overall = sum(all_scores) / len(all_scores) if all_scores else 0.0
    overall_status = "✅" if overall >= 4.0 else "❌"
    summary_lines.append(
        f"| **总体** | {len(all_scores)} | **{overall:.2f}** | 4.0 | {overall_status} |"
    )
    summary_lines.extend(
        [
            "",
            f"**幻觉统计**：{total_with_halluc} / {len(all_scores)} 题的答复被 judge 识别到"
            f"具体编造内容，共 {total_halluc} 条。",
            "",
        ]
    )

    text += "\n".join(summary_lines)

    if backup:
        path.with_suffix(".md.bak").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    path.write_text(text, encoding="utf-8")
    return text


# ───────────────────── main ─────────────────────


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 1
    path = Path(argv[1])
    if not path.exists():
        print(f"file not found: {path}")
        return 1

    api_key = os.environ.get("AGENT_V2_DEEPSEEK_KEY") or os.environ.get(
        "DEEPSEEK_API_KEY"
    )
    if not api_key:
        print("set AGENT_V2_DEEPSEEK_KEY or DEEPSEEK_API_KEY")
        return 1

    questions = parse_markdown(path)
    if not questions:
        print("no questions parsed from file")
        return 1
    print(f"parsed {len(questions)} questions from {path.name}")

    results: dict[str, dict] = {}
    with httpx.Client() as client:
        for i, q in enumerate(questions, 1):
            print(f"[{i}/{len(questions)}] scoring {q.qid} ({q.category})...", end=" ", flush=True)
            t0 = time.time()
            try:
                res = judge_one(q, api_key=api_key, client=client)
            except Exception as e:
                res = {
                    "score": None,
                    "reason": f"judge_error: {type(e).__name__}: {e}",
                    "hallucinations": [],
                }
            res["_category"] = q.category
            results[q.qid] = res
            elapsed = time.time() - t0
            score_str = (
                str(res["score"]) if res["score"] is not None else "?"
            )
            halluc_note = (
                f" halluc={len(res['hallucinations'])}"
                if res["hallucinations"]
                else ""
            )
            print(f"{score_str}/5{halluc_note} ({elapsed:.1f}s)")

    print("\npatching markdown...")
    patch_markdown(path, results)
    print(f"done → {path}")

    # terminal summary
    by_cat: dict[str, list[int]] = {}
    for r in results.values():
        if r.get("score") is None:
            continue
        by_cat.setdefault(r["_category"], []).append(r["score"])
    print("\n=== summary ===")
    for cat, scores in by_cat.items():
        print(f"  {cat:30s}  mean={sum(scores) / len(scores):.2f}  n={len(scores)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

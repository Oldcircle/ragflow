"""Phase 2.6 v0.8.3 — smoke the Q13 scenario alone.

Q13 is the canonical "induced fabrication" test: user asks Agent to
interpret a fabricated 令号. Without guards, Agent loops rag_retrieve /
list_docs / read_doc ~11 times with different keywords and the subprocess
crashes with "Command failed with exit code 1", producing an empty answer.

After v0.8.3 (prompt rule + runner-level empty-result counter), expected:
- ≤ 3 rag_* calls total
- Agent emits a polite "not found in KB" concede
- No subprocess crash
- duration < 30s
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

from common import settings as rf_settings  # noqa: E402

rf_settings.init_settings()

from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS  # noqa: E402
from api.agent_v2.model_resolver import resolve_model  # noqa: E402
from api.agent_v2.runner import AgentRunner  # noqa: E402


TENANT = "968bd6ec3c9f11f1afc91f3c182e7a61"
KB_ID = "a15948b83d5111f1afc91f3c182e7a61"


SYSTEM_PROMPT = """你是一名深圳保障房政策顾问，严格基于知识库内容答复。

工作方式：
1. 判断用户问题是否与知识库内容相关。无关则直接说明，不调工具。
2. 相关问题：使用 rag_retrieve 工具检索原文；**最多尝试 3 次换关键词检索**；若仍无结果，按第 3 条拒答，不要继续试。
3. 严格基于检索结果回答：数字、年限、比例、条款必须有原文支撑；原文未涵盖的内容，回答「未查到相关规定，建议向深圳市住房和建设局或相关项目的开发建设单位咨询」。
4. 引用规范：当某句话依据检索到的某个文档片段时，在该句末尾加形如 [1]、[2]、[3] 的上标编号。
5. 回答结尾无需重复「依据文件」清单。

**连续空结果时的硬规则**：
- 若 `rag_retrieve` 对同一概念连续 2 次返回空结果或全部 similarity < 0.2，立即停手；不要换第 3、4、5 个关键词反复试。
- 若用户问到具体令号 / 文号 / 条款号（如"第 2089 号令"）而 rag_retrieve 第一次就找不到，不要用 rag_list_docs + rag_read_doc 逐个翻阅。该文件很可能虚构或不在 KB。
- 连续 3 次空结果是硬上限，runner 会强制停。

禁止事项：
- 把其他城市政策套用到深圳；
- 用训练知识补充原文未说的内容；
- 编造数字、名称、时间；
- 在已连续 2 次检索无果的概念上继续换关键词重试。
"""


QUESTION = "请解读《深圳市住房和建设局第 2089 号令》第 17 条关于保障房出租转让的规定。"


async def main() -> int:
    import time

    model_cfg = resolve_model(
        {"llm_name": "deepseek-chat", "factory": "DeepSeek"},
        tenant_id=TENANT,
    ).config

    runner = AgentRunner(
        tenant_id=TENANT,
        kb_ids=[KB_ID],
        system_prompt=SYSTEM_PROMPT,
        model=model_cfg,
        tool_names=list(SUPERVISOR_TOOLS),
        max_turns=8,
        max_budget_usd=0.2,
    )

    text_buf: list[str] = []
    tool_calls: list[tuple[str, int]] = []  # (name, chunks_returned)
    start = time.time()
    had_error = False

    async for event in runner.run(QUESTION):
        d = event.to_dict()
        t = d["type"]
        if t == "text_delta":
            text_buf.append(d["data"].get("text", ""))
        elif t == "tool_call_end":
            name = d["data"].get("name") or ""
            result = d["data"].get("result")
            chunks = 0
            try:
                import json

                data = (
                    json.loads(result) if isinstance(result, str) else result
                )
                if isinstance(data, dict):
                    chunks = len(data.get("chunks") or []) + len(
                        data.get("docs") or []
                    )
            except Exception:
                pass
            tool_calls.append((name.rsplit("__", 1)[-1], chunks))
        elif t == "error":
            had_error = True
            text_buf.append(f"\n[ERROR: {d['data']}]")

    elapsed = time.time() - start
    answer = "".join(text_buf)

    print("=" * 70)
    print(f"Q13 smoke — {elapsed:.1f}s, {len(tool_calls)} tool calls")
    print("=" * 70)
    print(f"\nTool calls:")
    for name, n in tool_calls:
        print(f"  - {name}  (returned {n} items)")
    print(f"\nAnswer ({len(answer)} chars):")
    print(answer[:500])
    if len(answer) > 500:
        print(f"  ...[+{len(answer) - 500} more chars]")

    # Assertions
    failures: list[str] = []
    if had_error:
        failures.append("runner emitted error event")
    if len(answer.strip()) == 0:
        failures.append("empty answer (subprocess crash signature)")
    if len(tool_calls) > 5:
        failures.append(
            f"too many tool calls ({len(tool_calls)} > 5) — guard not triggered"
        )
    denies = any(
        phrase in answer
        for phrase in [
            "未查到", "未涵盖", "没有", "不存在", "不在本", "核对",
            "本知识库", "无相关",
        ]
    )
    if not denies:
        failures.append(
            "answer did NOT clearly decline — no 未查到 / 不涵盖 / 核对 phrase"
        )

    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED: {len(failures)} assertions")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("PASS ✓ — Agent gracefully conceded, no crash, ≤ 5 calls")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

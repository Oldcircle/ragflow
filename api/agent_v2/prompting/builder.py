"""Prompt builders — Claude-Code-style 8-section system prompts, English.

## Design overview

Claude Code's system prompts are **section-indexed**, not free-form. Each agent
gets a stable skeleton (Role → Context → Constraints → Workflow → Tools →
Output → Examples → Notes), so the model can navigate instructions like a
table of contents instead of re-parsing free prose every turn.

We split into two builders because KB supervisors and subagents have different
jobs:

- **Supervisor** = "front door" — understands user intent, delegates to
  subagents, never touches write tools directly. Needs the
  *Delegation rules* + *Clarify-vs-act decision tree* sections.
- **Subagent** = "specialist" — single mental mode (read-only investigator /
  destructive archivist / observer-and-writer librarian). Needs tighter *Hard
  rules* and *Output format* sections.

We do **not** port Claude Code's dynamic boundary marker
(`__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`) — that exists for prompt caching on
Anthropic's endpoint, which DeepSeek doesn't match. Can revisit if we switch
providers.

## Section order (supervisor)

1. Role & mission
2. Domain context
3. Hard constraints (must / must-not)
4. Workflow (how to approach a user request)
5. Delegation rules (when to spawn which subagent)
6. Clarify-vs-act decision tree
7. Tool usage rules (per tool: when + how)
8. Output format (citation rules / structured answers)

## Section order (subagent)

1. Role & mission
2. Hard constraints (scope boundaries, refusals)
3. Workflow (step-by-step expected behavior)
4. Tool usage rules
5. Output format

## Philosophy

Prompts are **stable contracts** between designer and model. Each section has
one job; the model can skip to the right section. Avoid:

- Narrative prose across sections
- Redundancy (same rule in two sections)
- Chinese-English mixing (we commit to English as lingua franca)
- Markdown decoration for its own sake (`✅ 🚫 🟡` etc.)

Reference: `/tmp/architectural-synthesis.md` §5; `AUDIT-claude-code-alignment.md` §3.
"""

from __future__ import annotations

from typing import Iterable


# ──────────────────────────────  Shared constants  ──────────────────────────────


STRICT_RAG_CONSTRAINTS: list[str] = [
    "All factual claims (numbers, dates, durations, percentages, monetary amounts) "
    "MUST be supported by retrieved source chunks. Never supplement from training knowledge.",
    "If a required fact is not in the retrieved material, say so explicitly using the "
    "phrase 'no direct basis in the knowledge base' — do not guess, round, or infer.",
    "Never fabricate document names, authority names, section numbers, or dates.",
    "Never generalize a city's policy to a different city, or a version's rule to a "
    "different version.",
]


RETRIEVAL_OUTPUT_RULES: list[str] = [
    "Annotate every factual sentence with a footnote marker like [1], [2], [3] "
    "corresponding to the order chunks appeared in rag_retrieve / rag_read_doc "
    "results this turn. Multiple markers like [1][2] are allowed when a sentence "
    "draws on multiple chunks.",
    "Do NOT inline file names in the answer body. The frontend renders a "
    '"Sources" panel automatically from the cited chunk ids.',
    "Do NOT annotate sentences that are commentary, opinion, hedging, or a "
    "summary — only sentences that assert a verifiable fact get a [N] marker.",
    "Do NOT repeat a list of source files at the end of the answer; the [N] "
    "markers already establish attribution.",
]


# searchHint-style short imperatives. Third-person intent phrases, 3-10 words,
# lowercase, no trailing punctuation. Used when we eventually adopt `shouldDefer`
# or need to help a classifier pick the right tool.
SEARCH_HINT_BY_TOOL: dict[str, str] = {
    # Retrieval
    "rag_retrieve": "search knowledge base semantically for relevant chunks",
    "rag_list_docs": "list documents in the current knowledge base",
    "rag_read_doc": "read the full text of a single document",
    "rag_graph_query": "query the knowledge graph for an entity",
    # Delegation / interaction
    "spawn_subagent": "delegate a focused task to a subagent",
    "ask_user_question": "ask the user a multiple-choice clarifying question",
    "submit_plan": "submit a plan for user approval before executing a batch",
    # Reflect / observe
    "kb_stats": "get a quick health snapshot of a knowledge base",
    "kb_audit": "run a structured audit of knowledge base health",
    "doc_list_recent_changes": "list recent audit-log entries for this tenant",
    # Write
    "doc_create_note": "save agent-authored markdown as a new document",
    "doc_tag": "add, remove, or replace document tags",
    "doc_rename": "rename a document",
    "doc_archive": "move a document to another knowledge base",
    "doc_reparse": "clear chunks and re-run the parser on a document",
    "doc_upload_from_url": "download a url into the knowledge base",
    "kb_create": "create a new empty knowledge base",
}


# ──────────────────────────────  Supervisor  ──────────────────────────────


_SUPERVISOR_SKELETON = """\
# Role

{role_line}

# Domain context

{domain_context}

# Hard constraints (must / must-not)

{hard_constraints}

# Workflow (how to approach any user request)

1. Classify the request: **read / understand**, **observe / summarize / write a note**, **execute a change**, or **ambiguous**.
2. For *read / understand* → answer directly using retrieval tools. Do not delegate for simple factual Q&A.
3. For *observe / summarize / write a note* → spawn `sub_librarian`. Pass the specific task description; librarian will audit, write, and report.
4. For *execute a change* → spawn `sub_archivist`. If the change touches more than 3 documents or crosses knowledge bases, the archivist must submit a plan first.
5. For *ambiguous* → follow the clarify-vs-act decision tree below. Never guess destructive intent.

# Delegation rules (do NOT re-delegate)

- One user request → at most ONE subagent of each type. Spawning the same subagent twice for the same request wastes budget; avoid it.
- Do NOT call a write tool directly. You do not have `doc_tag`, `doc_archive`, `doc_create_note`, etc. in your toolbelt; you reach them via `spawn_subagent(subagent_type=...)`.
- Pass complete briefs when spawning: the subagent cannot ask you for clarification mid-run.
- When a subagent returns, summarize its findings for the user in your own words. Do not verbatim-dump its output.

# Clarify-vs-act decision tree

- Destructive or cross-KB change + ambiguous target → call `ask_user_question` BEFORE spawning `sub_archivist`.
- Batch of 5+ documents with clear intent → spawn `sub_archivist` and let it `submit_plan` for approval.
- Single-document change with unambiguous target → spawn `sub_archivist` directly (archivist may still submit a plan internally).
- Pure read or audit → never clarify; answer directly.

# Tool usage rules

- `rag_retrieve(query, top_n=8)` — go-to for any factual answer. Prefer short keyword-style queries over long sentences.
- `rag_list_docs(keywords?)` — when the user asks "what documents are in this KB" or you need to locate a specific file.
- `rag_read_doc(doc_id)` — when `rag_retrieve` returns chunks but you need the full text to answer with confidence.
- `rag_graph_query(entity)` — only when the KB has a GraphRAG index; otherwise returns empty.
- `kb_stats(kb_id)` — use at the START of a turn if user asks "is this KB healthy?" type questions. Very fast. Do NOT run full `kb_audit` yourself; delegate to librarian.
- `spawn_subagent(subagent_type, description, prompt)` — see Delegation rules above.
- `ask_user_question(question, header, options)` — structured 2-4 choice clarifier. Prefer this over free-text questions when the user's answer is a pick from a small set.
- `submit_plan(title, steps, risk_level, ...)` — use when you yourself are about to take several read steps, and the user benefits from seeing the plan first. For write plans, let the archivist submit.

# Output format

{output_rules}

{domain_extras}\
"""


def build_supervisor_prompt(
    *,
    role_line: str,
    domain_context: str = "",
    hard_constraints: Iterable[str] | None = None,
    output_rules: Iterable[str] | None = None,
    domain_extras: str = "",
) -> str:
    """Assemble a full supervisor system prompt.

    `role_line` is a single sentence identifying the agent ("You are the
    Shenzhen Affordable Housing Policy Advisor."). `domain_context` gives a
    short paragraph of domain background. `hard_constraints` adds domain-
    specific forbidden behaviors on top of the baseline STRICT_RAG_CONSTRAINTS.
    `output_rules` replaces baseline RETRIEVAL_OUTPUT_RULES when you need
    different behavior (e.g. agents that must not emit [N] citations).
    `domain_extras` is raw markdown appended at the end.
    """
    hc = list(STRICT_RAG_CONSTRAINTS) + list(hard_constraints or [])
    hard = "\n".join(f"- {line}" for line in hc)
    outr = output_rules if output_rules is not None else RETRIEVAL_OUTPUT_RULES
    out = "\n".join(f"- {line}" for line in outr)
    domain_ctx = domain_context.strip() or "General knowledge base."
    return _SUPERVISOR_SKELETON.format(
        role_line=role_line.strip(),
        domain_context=domain_ctx,
        hard_constraints=hard,
        output_rules=out,
        domain_extras=("\n" + domain_extras.strip() + "\n") if domain_extras else "",
    )


# ──────────────────────────────  Subagent  ──────────────────────────────


_SUBAGENT_SKELETON = """\
# Role

{role_line}

# Mission

{mission}

# Hard constraints (must / must-not)

{hard_rules}

# Workflow

{workflow_steps}

# Tool usage rules

{tool_rules}

# Output format

{output_rules}
"""


def build_subagent_prompt(
    *,
    role_line: str,
    mission: str,
    hard_rules: Iterable[str],
    workflow_steps: Iterable[str],
    tool_rules: Iterable[str],
    output_rules: Iterable[str] | None = None,
) -> str:
    """Assemble a subagent system prompt.

    Subagent prompts are tighter than supervisor prompts — one mental mode, no
    delegation/clarification branches unless the subagent explicitly owns
    that responsibility (e.g., sub_archivist still uses submit_plan).
    """
    hr = "\n".join(f"- {line}" for line in hard_rules)
    ws = "\n".join(f"{i}. {s}" for i, s in enumerate(workflow_steps, 1))
    tr = "\n".join(f"- {line}" for line in tool_rules)
    outr = output_rules if output_rules is not None else RETRIEVAL_OUTPUT_RULES
    out = "\n".join(f"- {line}" for line in outr)
    return _SUBAGENT_SKELETON.format(
        role_line=role_line.strip(),
        mission=mission.strip(),
        hard_rules=hr,
        workflow_steps=ws,
        tool_rules=tr,
        output_rules=out,
    )


# ──────────────────────────────  Tool descriptions  ──────────────────────────────


def build_tool_description(
    *,
    when: str,
    what: str = "",
    usage_notes: Iterable[str] | None = None,
    examples: Iterable[str] | None = None,
    related: Iterable[str] | None = None,
    max_chars: int = 900,
) -> str:
    """Claude-Code-style tool description — English, concise, structured.

    Four optional blocks:
        Use this tool when <when>.
        <what>              [one sentence capability]
        Usage notes:        [2-5 bullets of operational constraints]
        Examples:           [1-3 "...X... → call rag_retrieve" scenarios]
        Related tools:      [cross-reference bullet list]

    The `when` field is the most important — it drives the model's tool
    selection decision. Keep it specific: a situation, not a tool category.

    `max_chars` guards description length. Claude Code's FileReadTool is ~80
    tokens; our richer semantic tools need a bit more, but 900 chars is a
    hard ceiling (~250 tokens). If you exceed, prune or move to system
    prompt.
    """
    parts: list[str] = [f"Use this tool when {when.strip().rstrip('.')}."]
    if what.strip():
        parts.append(what.strip().rstrip("."))
    if usage_notes:
        parts.append("Usage notes:\n" + "\n".join(f"- {n}" for n in usage_notes))
    if examples:
        parts.append("Examples:\n" + "\n".join(f"- {e}" for e in examples))
    if related:
        parts.append("Related:\n" + "\n".join(f"- {r}" for r in related))
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        # Soft warning via exception — catches bloat early during dev
        raise ValueError(
            f"Tool description exceeds {max_chars} chars (got {len(text)}); "
            "prune or move detail to the agent's system prompt."
        )
    return text

"""Customer-support agent — supervisor."""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt, SUPERVISOR_TOOLS


DEFINITION = AgentDefinition(
    name="customer-support",
    version="2.0.0",
    description=(
        "Answers end-customer questions grounded in product manuals, FAQs, "
        "troubleshooting playbooks, and policy documents. Escalates (opens a "
        "human ticket) whenever the KB does not clearly cover the question."
    ),
    when_to_use=(
        "B2C customer support: product usage, troubleshooting, policy "
        "clarification. User is typically an end customer, not an operator."
    ),
    kind="supervisor",
    icon="💬",
    category="customer",
    system_prompt=strict_rag_prompt(
        role="a customer support agent",
        fallback=(
            "open a ticket and escalate to a human agent; the user will be "
            "contacted within one business day"
        ),
        extras=[
            "Never promise specific resolution times (e.g. 'resolved within "
            "24 hours'). Say 'we will follow up as soon as possible'.",
            "Never diagnose root cause. Restate the symptom, cite the matching "
            "troubleshooting step from the KB, and escalate if not resolved.",
            "Greet warmly on the first turn; use the customer's name if available.",
        ],
    ),
    max_turns=6,
    max_budget_usd=0.3,
    tools=SUPERVISOR_TOOLS,
    kb_hints=("manual", "FAQ", "troubleshooting", "product"),
    citation_enforce="warn",
    can_spawn_subagents=False,  # customer-facing: keep the call-graph shallow
)

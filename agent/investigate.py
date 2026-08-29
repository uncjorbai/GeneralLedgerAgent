"""The investigation loop — the agentic core (DESIGN §4.3, step 2).

This is the multi-turn tool-use loop: hand the model the tools, run whatever it
asks for, feed the results back, repeat — until it produces a diagnosis or hits
the bound. It is deliberately the *only* place with a "brain", and even here the
intelligence is injected: the loop talks to a `ModelClient` interface, so tests
drive it with a scripted mock (no API key, no network) and the live Anthropic
adapter (Step 7) is just another implementation of the same interface. Same
mock-first discipline as Phase 1 injected `output_getter` / `now`.

Guardrails enforced here:
  * BOUNDED (guardrail #6 / DESIGN §4.4): at most `max_tool_calls` tool calls; on
    exhaustion the loop stops and the result is marked for escalation with
    whatever was found — it never loops forever.
  * READ-ONLY: the loop can only reach data through `dispatch` over the provider;
    there is no write path, and the answer key is unreachable.
  * LOGGED: every tool call is recorded to the DecisionTrace by the dispatcher, so
    the reasoning is a complete, first-class artifact regardless of the outcome.

The loop speaks the Anthropic message shape (role/content blocks) as its wire
format, so the live adapter passes messages straight through. The model client
returns a normalized `AssistantTurn`, keeping the loop independent of any one
SDK's response object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from agent.diagnosis import SUBMIT_DIAGNOSIS_TOOL, Diagnosis, build_diagnosis
from agent.provider import GLProvider
from agent.tools import TOOLS, dispatch
from agent.trace import DecisionTrace

DEFAULT_MAX_TOOL_CALLS = 10  # DESIGN §4.4: cap investigation steps

# The tools offered to the model: the read/investigate surface plus the terminal
# submit_diagnosis delivery action.
INVESTIGATION_TOOLS = [*TOOLS, SUBMIT_DIAGNOSIS_TOOL]

OUTCOME_COMPLETED = "completed"    # model produced a diagnosis narrative
OUTCOME_EXHAUSTED = "exhausted"    # hit the tool-call bound first -> escalate
OUTCOME_EMPTY = "empty"            # model stopped with nothing -> escalate

SYSTEM_PROMPT = (
    "You are a forensic general-ledger accountant investigating a failed financial-"
    "close data-quality check. A defect has been injected into one accounting period's "
    "journal; your job is to find its ROOT CAUSE from evidence and report it the way a "
    "controller needs to hear it.\n\n"
    "Method:\n"
    "- Use the tools to pull the failing records and compare them to the clean baseline. "
    "The baseline is regenerated from the same seed, so a like-for-like query diffs "
    "exactly — lean on that.\n"
    "- Aggregate with group_by to expose imbalances, then drill into the specific "
    "vouchers and accounts involved.\n"
    "- Record each real conclusion with log_decision (a finding + the evidence for it).\n\n"
    "Rules:\n"
    "- You are READ-ONLY. You cannot change any data; you are diagnosing, not fixing.\n"
    "- Every number you state must come from a tool result. Never estimate or invent a "
    "figure — if you don't have it, query for it.\n"
    "- Your tool budget is limited; be economical and purposeful.\n"
    "- Do not seek or reference any answer key; it does not exist for you.\n\n"
    "When you have the root cause, call submit_diagnosis exactly once with your "
    "structured diagnosis: the root cause, the dollar impact, the specific offending "
    "vouchers and accounts, your confidence, and a short controller-ready narrative. "
    "That ends the investigation."
)


@dataclass
class ToolUse:
    """One tool call the model wants to make."""

    id: str
    name: str
    input: dict


@dataclass
class AssistantTurn:
    """A model reply, normalized so the loop is SDK-agnostic.

    `raw_content`, when set, is the exact provider content blocks to echo back into
    the conversation (preserves thinking blocks etc. on the live path). When None,
    the loop reconstructs the assistant message from `text` + `tool_uses`.
    """

    text: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    stop_reason: str = "end_turn"
    raw_content: list | None = None


class ModelClient(Protocol):
    """What the loop needs from a model. The live adapter and the test mock both
    implement exactly this."""

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> AssistantTurn: ...


@dataclass(frozen=True)
class InvestigationResult:
    """The output of one investigation: the reasoning trace + the diagnosis text."""

    outcome: str                 # completed | exhausted | empty
    narrative: str               # the model's controller-ready diagnosis (final text)
    trace: DecisionTrace         # the full append-only reasoning log
    context: dict                # the routing context it investigated
    tool_calls_made: int
    diagnosis: Diagnosis | None = None   # the structured, gradeable verdict (None if not submitted)

    @property
    def escalated(self) -> bool:
        return self.outcome != OUTCOME_COMPLETED


def _opening_prompt(context: dict) -> str:
    checks = ", ".join(context.get("failed_checks", [])) or "an unknown check"
    return (
        f"A data-quality investigation has been routed to you.\n"
        f"Scenario: {context.get('scenario')}\n"
        f"Failing check(s): {checks}\n"
        f"Failing GL table: {context.get('gl_table')}\n\n"
        f"Investigate and produce your diagnosis. Begin."
    )


def _assistant_content(turn: AssistantTurn) -> list:
    if turn.raw_content is not None:
        return turn.raw_content
    content: list = []
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for tu in turn.tool_uses:
        content.append({"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input})
    return content or [{"type": "text", "text": ""}]


def investigate(
    *,
    context: dict,
    provider: GLProvider,
    model: ModelClient,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    system: str = SYSTEM_PROMPT,
) -> InvestigationResult:
    """Run the bounded tool-use loop and return the trace + diagnosis.

    `context` is the routing verdict (scenario, gl_table, failed_checks, ...) from
    triage. `model` is injected: a scripted mock in tests, the Anthropic adapter in
    production.
    """
    trace = DecisionTrace()
    messages: list[dict] = [
        {"role": "user", "content": [{"type": "text", "text": _opening_prompt(context)}]}
    ]
    calls_made = 0

    while True:
        turn = model.create(system=system, messages=messages, tools=INVESTIGATION_TOOLS)
        messages.append({"role": "assistant", "content": _assistant_content(turn)})

        # No tool calls => the model is done talking. Terminal either way.
        if not turn.tool_uses:
            outcome = OUTCOME_COMPLETED if turn.text.strip() else OUTCOME_EMPTY
            return InvestigationResult(outcome, turn.text, trace, context, calls_made)

        # submit_diagnosis is the terminal delivery action: capture the structured
        # diagnosis and stop, whatever else was in the turn.
        submit = next((tu for tu in turn.tool_uses if tu.name == "submit_diagnosis"), None)
        if submit is not None:
            trace.record_tool("submit_diagnosis", submit.input, "diagnosis submitted")
            diagnosis = build_diagnosis(context, submit.input)
            return InvestigationResult(
                OUTCOME_COMPLETED, diagnosis.narrative, trace, context, calls_made, diagnosis=diagnosis
            )

        # Execute every requested tool; return ALL results in one user message.
        tool_results = []
        for tu in turn.tool_uses:
            calls_made += 1
            content, is_error = dispatch(tu.name, tu.input, provider=provider, trace=trace, context=context)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": content,
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": tool_results})

        # Bound check AFTER executing this batch: if we've spent the budget, stop
        # and escalate with whatever we have rather than looping (guardrail #6).
        if calls_made >= max_tool_calls:
            return InvestigationResult(OUTCOME_EXHAUSTED, turn.text, trace, context, calls_made)

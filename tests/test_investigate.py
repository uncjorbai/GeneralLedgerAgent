"""Unit tests for agent.investigate — the bounded tool-use loop. No LLM, no network.

The model is injected: a `ScriptedModel` returns pre-baked AssistantTurns, so we
drive the loop through a real investigation deterministically and assert on the
trace, the diagnosis, the bound, and that tool results are actually fed back.
"""

import json
from pathlib import Path

import pytest

from agent.investigate import (
    OUTCOME_COMPLETED,
    OUTCOME_EMPTY,
    OUTCOME_EXHAUSTED,
    AssistantTurn,
    ToolUse,
    investigate,
)
from agent.provider import LocalGLProvider

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"
SCENARIO = "intercompany_out_of_balance"
IC = ["A14000", "L21500", "R42000", "X67000"]
CTX = {
    "scenario": SCENARIO,
    "gl_table": f"gl_journal_lines__{SCENARIO}",
    "failed_checks": ["intercompany_eliminates"],
    "gate_types": ["reconciliation"],
    "evidence": "intercompany_eliminates (2 failures)",
}


@pytest.fixture()
def provider():
    return LocalGLProvider(FIXTURE_ROOT, SCENARIO)


# --- test doubles ---------------------------------------------------------- #
class ScriptedModel:
    """Returns pre-baked turns in order; records what it was asked each call."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []  # snapshot of messages seen on each create()

    def create(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        return self.turns.pop(0) if self.turns else AssistantTurn(text="(script exhausted)")


class AlwaysQueries:
    """Never concludes — always asks for one more query. Tests the bound."""

    def create(self, *, system, messages, tools):
        return _tools(("query_failing_table", {"group_by": "main_account"}))


def _tools(*calls, text=""):
    tus = [ToolUse(id=f"tu{i}", name=name, input=inp) for i, (name, inp) in enumerate(calls)]
    return AssistantTurn(text=text, tool_uses=tus, stop_reason="tool_use")


def _final(text):
    return AssistantTurn(text=text, stop_reason="end_turn")


# --- the happy path: a full intercompany investigation --------------------- #
DIAGNOSIS = (
    "intercompany_eliminates failed. HQ receivable A14000 and IC income R42000 are each "
    "3,000 over the clean baseline (147,000 vs 144,000); payable L21500 and expense "
    "X67000 are unchanged. Offending vouchers: USMI260600105, USMI260700105. Confidence: high."
)


@pytest.fixture()
def happy_script():
    return [
        _tools(("get_gate_verdict", {})),
        _tools(("query_failing_table", {"filters": {"main_account": IC}, "group_by": "main_account"})),
        _tools(("query_clean_baseline", {"filters": {"main_account": IC}, "group_by": "main_account"})),
        _tools(("log_decision", {"finding": "HQ side 3,000 hot", "rationale": "A14000 147k vs 144k baseline"})),
        _final(DIAGNOSIS),
    ]


def test_completes_with_a_diagnosis(provider, happy_script):
    model = ScriptedModel(happy_script)
    result = investigate(context=CTX, provider=provider, model=model)

    assert result.outcome == OUTCOME_COMPLETED
    assert result.escalated is False
    assert "3,000" in result.narrative and "A14000" in result.narrative


def test_trace_captures_the_full_reasoning(provider, happy_script):
    result = investigate(context=CTX, provider=provider, model=ScriptedModel(happy_script))

    tool_calls = result.trace.tool_calls()
    assert [t["tool"] for t in tool_calls] == [
        "get_gate_verdict", "query_failing_table", "query_clean_baseline",
    ]
    decisions = [e for e in result.trace.entries if e["kind"] == "decision"]
    assert len(decisions) == 1
    assert result.tool_calls_made == 4          # 3 queries + 1 log_decision


def test_tool_results_are_fed_back_to_the_model(provider, happy_script):
    model = ScriptedModel(happy_script)
    investigate(context=CTX, provider=provider, model=model)

    # On its 3rd call the model must have already seen the failing-table result
    # (the +3,000 net), proving the loop returns results into the conversation.
    third_call_msgs = model.calls[2]["messages"]
    last = third_call_msgs[-1]
    assert last["role"] == "user"
    assert "147000" in json.dumps(last["content"])


# --- the bound (guardrail #6) --------------------------------------------- #
def test_loop_is_bounded_and_escalates_on_exhaustion(provider):
    result = investigate(context=CTX, provider=provider, model=AlwaysQueries(), max_tool_calls=4)

    assert result.outcome == OUTCOME_EXHAUSTED
    assert result.escalated is True
    assert result.tool_calls_made == 4
    assert len(result.trace.tool_calls()) == 4      # stopped exactly at the cap


# --- degenerate + error paths --------------------------------------------- #
def test_empty_reply_escalates(provider):
    result = investigate(context=CTX, provider=provider, model=ScriptedModel([AssistantTurn(text="")]))
    assert result.outcome == OUTCOME_EMPTY
    assert result.escalated is True


def test_tool_error_is_surfaced_then_recovered(provider):
    model = ScriptedModel([
        _tools(("query_failing_table", {"filters": {"scenario": "clean"}})),  # non-whitelisted -> error
        _final("Recovered and concluded."),
    ])
    result = investigate(context=CTX, provider=provider, model=model)

    assert result.outcome == OUTCOME_COMPLETED
    # the error result was fed back so the model could recover
    fed_back = json.dumps(model.calls[1]["messages"][-1]["content"])
    assert "not filterable" in fed_back
    assert result.trace.tool_calls()[0]["is_error"] is True


def test_parallel_tool_calls_in_one_turn(provider):
    model = ScriptedModel([
        _tools(
            ("query_failing_table", {"group_by": "main_account"}),
            ("get_chart_of_accounts", {}),
        ),
        _final("done"),
    ])
    result = investigate(context=CTX, provider=provider, model=model)

    assert result.tool_calls_made == 2
    # both results returned in a SINGLE user message (never split — see tool-use docs)
    tool_result_msg = model.calls[1]["messages"][-1]
    assert len([b for b in tool_result_msg["content"] if b["type"] == "tool_result"]) == 2

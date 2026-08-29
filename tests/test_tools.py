"""Unit tests for agent.tools + agent.trace — the read-only tool surface. No LLM.

Exercises the dispatcher exactly as the investigation loop (Step 5) will: by name
+ input dict, against the real fixture provider. Confirms the forensic arithmetic
is reachable through the tools, the guardrails hold, and every call is logged.
"""

import json
from pathlib import Path

import pytest

from agent.provider import LocalGLProvider
from agent.tools import TOOL_NAMES, TOOLS, dispatch
from agent.trace import DecisionTrace

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"
SCENARIO = "intercompany_out_of_balance"
IC = ["A14000", "L21500", "R42000", "X67000"]
CTX = {
    "scenario": SCENARIO,
    "gl_table": f"gl_journal_lines__{SCENARIO}",
    "failed_checks": ["intercompany_eliminates"],
    "gate_types": ["reconciliation"],
    "evidence": "gate verdict (structured exit): intercompany_eliminates (2 failures)",
}


@pytest.fixture()
def provider():
    return LocalGLProvider(FIXTURE_ROOT, SCENARIO)


@pytest.fixture()
def trace():
    return DecisionTrace()


def run(name, tool_input, provider, trace):
    """Dispatch a call and return (parsed_result, is_error)."""
    content, is_error = dispatch(name, tool_input, provider=provider, trace=trace, context=CTX)
    return json.loads(content), is_error


def _net(groups, account):
    return next(g["net"] for g in groups if g["main_account"] == account)


# --- schema sanity --------------------------------------------------------- #
def test_every_tool_schema_is_wellformed():
    for t in TOOLS:
        assert set(t) >= {"name", "description", "input_schema"}
        assert t["input_schema"]["type"] == "object"
    assert TOOL_NAMES == {t["name"] for t in TOOLS}
    assert "log_decision" in TOOL_NAMES


# --- the forensic path ----------------------------------------------------- #
def test_gate_verdict_returns_the_routing_context(provider, trace):
    result, err = run("get_gate_verdict", {}, provider, trace)
    assert err is False
    assert result["failed_checks"] == ["intercompany_eliminates"]
    assert result["scenario"] == SCENARIO


def test_group_by_account_exposes_the_intercompany_imbalance(provider, trace):
    result, err = run("query_failing_table",
                      {"filters": {"main_account": IC}, "group_by": "main_account"}, provider, trace)
    assert err is False
    g = result["groups"]
    assert _net(g, "A14000") == 147000.0        # receivable, +3,000 hot
    assert _net(g, "R42000") == -147000.0        # IC income (credit-normal), +3,000 hot
    assert _net(g, "L21500") == -144000.0        # payable, untouched
    assert _net(g, "X67000") == 144000.0         # IC expense, untouched


def test_baseline_same_query_ties_out(provider, trace):
    result, _ = run("query_clean_baseline",
                    {"filters": {"main_account": IC}, "group_by": "main_account"}, provider, trace)
    g = result["groups"]
    # receivable mirrors payable, IC income mirrors IC expense: |net| all 144,000
    assert {abs(_net(g, a)) for a in IC} == {144000.0}


def test_failing_minus_baseline_is_exactly_3000(provider, trace):
    fail, _ = run("query_failing_table", {"filters": {"main_account": ["A14000"]}, "group_by": "main_account"}, provider, trace)
    clean, _ = run("query_clean_baseline", {"filters": {"main_account": ["A14000"]}, "group_by": "main_account"}, provider, trace)
    assert _net(fail["groups"], "A14000") - _net(clean["groups"], "A14000") == 3000.0


def test_row_query_finds_the_altered_voucher_lines(provider, trace):
    result, err = run("query_failing_table", {"filters": {"voucher": "USMI260700105"}}, provider, trace)
    assert err is False
    accounts = {r["main_account"]: r for r in result["rows"]}
    assert accounts["A14000"]["amount_debit"] == 13500.0     # inflated from 12,000
    assert accounts["R42000"]["amount_credit"] == 13500.0


def test_chart_of_accounts_classifies_ic_accounts(provider, trace):
    result, _ = run("get_chart_of_accounts", {}, provider, trace)
    by_key = {a["account_key"]: a for a in result["accounts"]}
    assert by_key["A14000"]["normal_balance"] == "Debit"
    assert by_key["L21500"]["normal_balance"] == "Credit"


def test_scenario_context_excludes_the_answer_key(provider, trace):
    result, _ = run("get_scenario_context", {}, provider, trace)
    assert "USMI" in result["entities"]
    assert result["periods"]
    blob = json.dumps(result).lower()
    assert "seed" not in {k.lower() for k in result}      # no seed field
    assert "manifest" not in blob and "expected_check" not in blob


# --- guardrails / error handling ------------------------------------------- #
def test_non_whitelisted_filter_is_a_recoverable_error(provider, trace):
    # 'scenario' is a real column in the parquet but NOT filterable — keep the
    # surface tight. The model should get an error it can recover from, not a crash.
    result, err = run("query_failing_table", {"filters": {"scenario": "clean"}}, provider, trace)
    assert err is True
    assert "not filterable" in result["error"]


def test_unknown_tool_is_a_recoverable_error(provider, trace):
    result, err = run("drop_table", {}, provider, trace)
    assert err is True
    assert "Unknown tool" in result["error"]


def test_limit_is_capped(provider, trace):
    result, _ = run("query_failing_table", {"limit": 999999}, provider, trace)
    assert result["returned"] <= 200


# --- the three Step-8 enrichments (generic, cover the remaining defect classes) --- #
def test_group_by_reports_line_count(provider, trace):
    result, _ = run("query_failing_table", {"group_by": "main_account"}, provider, trace)
    assert all("line_count" in g for g in result["groups"])
    assert sum(g["line_count"] for g in result["groups"]) == 127   # every fixture row counted


def test_null_filter_matches_missing_values(trace):
    # a null company_id defect: filter company_id=null finds the nulled rows.
    prov = LocalGLProvider(FIXTURE_ROOT, "missing_entity_or_period")
    result, err = run("query_failing_table", {"filters": {"company_id": None}}, prov, trace)
    assert err is False and result["row_count"] == 8         # the 8 nulled rows


def test_null_filter_also_matches_blank_text(trace):
    # a missing-department defect blanks the string (not null); null still matches it.
    prov = LocalGLProvider(FIXTURE_ROOT, "missing_department")
    result, _ = run("query_failing_table", {"filters": {"department": None}}, prov, trace)
    assert result["row_count"] >= 5


def test_accounting_date_is_exposed_and_filterable(provider, trace):
    result, err = run("query_failing_table", {"filters": {"journal_type": "SALES"}, "limit": 5}, provider, trace)
    assert err is False
    assert all("accounting_date" in r for r in result["rows"])


# --- the decision trace ---------------------------------------------------- #
def test_every_call_is_auto_logged(provider, trace):
    run("get_gate_verdict", {}, provider, trace)
    run("query_failing_table", {"group_by": "main_account"}, provider, trace)
    assert len(trace.tool_calls()) == 2
    assert trace.entries[0]["tool"] == "get_gate_verdict"
    assert "sums" in trace.entries[1]["summary"]


def test_log_decision_records_finding_and_rationale(provider, trace):
    result, err = run("log_decision",
                      {"finding": "HQ receivable is 3,000 hot", "rationale": "A14000 net 147k vs 144k baseline"},
                      provider, trace)
    assert err is False and result["logged"] is True
    decisions = [e for e in trace.entries if e["kind"] == "decision"]
    assert decisions[0]["finding"].startswith("HQ receivable")


def test_log_decision_requires_both_fields(provider, trace):
    result, err = run("log_decision", {"finding": "something"}, provider, trace)
    assert err is True
    assert "requires" in result["error"]


def test_trace_to_rows_is_json_serializable(provider, trace):
    run("get_gate_verdict", {}, provider, trace)
    run("log_decision", {"finding": "f", "rationale": "r"}, provider, trace)
    rows = trace.to_rows()
    assert json.loads(json.dumps(rows)) == rows      # round-trips cleanly

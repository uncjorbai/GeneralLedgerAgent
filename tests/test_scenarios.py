"""Per-scenario coverage for all seven defects — the offline backbone of the
Phase-4 scorecard. No LLM, no network.

Two things are proven for each scenario:
  1. TOOL SUFFICIENCY — the (deliberately tight) read-only tool surface can surface
     the defect and recover the offending vouchers the answer key names. This is
     what makes the scenario diagnosable at all.
  2. LOOP + DIAGNOSIS — the bounded loop, driven by a scripted model that issues a
     representative query and submits, yields a correct structured Diagnosis
     (defect_class grounded via the registry; offending records carried through).

The expected vouchers below come from each scenario's answer key
(`run_manifest.json`). They live in the TEST (the scorer's side), never reachable
by the agent at runtime — guardrail #4.
"""

import json
from pathlib import Path

import pytest

from agent.investigate import OUTCOME_COMPLETED, investigate
from agent.provider import LocalGLProvider
from agent.tools import dispatch
from agent.trace import DecisionTrace
from test_investigate import ScriptedModel, _tools

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"

# scenario -> the DQ/reconciliation check it trips (mirrors the registry)
CHECK = {
    "unbalanced_voucher": "debits_equal_credits",
    "duplicate_voucher": "no_duplicate_vouchers",
    "unmapped_account": "account_in_coa",
    "missing_department": "required_dimensions_present",
    "missing_entity_or_period": "entity_and_period_present",
    "period_cutoff": "period_cutoff_correct",
    "intercompany_out_of_balance": "intercompany_eliminates",
}

# Offending vouchers per scenario, from the answer keys (scorer-side ground truth).
EXPECTED = {
    "unbalanced_voucher": {"USTX260600060", "USTX260200079", "USTX251200092"},
    "duplicate_voucher": {"USMI260300030", "USMI260200063", "USMI251000008"},
    "unmapped_account": {"USTX260300096", "USTX260800053", "USMI260200067", "USMI260700088"},
    "missing_department": {"USMI260700045", "USMI260100057", "USMI260100036", "USTX260800051", "USTX250900020"},
    "missing_entity_or_period": {"USTX260500096", "USMI260200092", "USMI260200027", "USTX250900082"},
    "period_cutoff": {"USTX251100055", "USTX260600021", "USMI260600041"},
    "intercompany_out_of_balance": {"USMI260600105", "USMI260700105"},
}
ALL = sorted(CHECK)


def _q(provider, name, inp):
    content, _ = dispatch(name, inp, provider=provider, trace=DecisionTrace(), context={"scenario": "x"})
    return json.loads(content)


def _vouchers_from_rows(result):
    return {r["voucher"] for r in result["rows"]}


def detect(scenario, provider) -> set:
    """Recover the offending vouchers using ONLY the tool surface (no answer key)."""
    if scenario == "unbalanced_voucher":
        g = _q(provider, "query_failing_table", {"group_by": "voucher", "limit": 200})
        return {r["voucher"] for r in g["groups"] if r["net"] != 0}

    if scenario == "duplicate_voucher":
        f = _q(provider, "query_failing_table", {"group_by": "voucher", "limit": 200})
        b = _q(provider, "query_clean_baseline", {"group_by": "voucher", "limit": 200})
        base = {r["voucher"]: r["line_count"] for r in b["groups"]}
        return {r["voucher"] for r in f["groups"] if r["line_count"] > base.get(r["voucher"], 0)}

    if scenario == "unmapped_account":
        coa = {a["account_key"] for a in _q(provider, "get_chart_of_accounts", {})["accounts"]}
        accts = [r["main_account"] for r in _q(provider, "query_failing_table", {"group_by": "main_account", "limit": 200})["groups"]]
        bogus = [a for a in accts if a not in coa]
        return _vouchers_from_rows(_q(provider, "query_failing_table", {"filters": {"main_account": bogus}, "limit": 200}))

    if scenario == "missing_department":
        req = [a["account_key"] for a in _q(provider, "get_chart_of_accounts", {})["accounts"] if a["department_required"]]
        r = _q(provider, "query_failing_table", {"filters": {"main_account": req, "department": None}, "limit": 200})
        return _vouchers_from_rows(r)

    if scenario == "missing_entity_or_period":
        rows = _vouchers_from_rows(_q(provider, "query_failing_table", {"filters": {"company_id": None}, "limit": 200}))
        return rows | _vouchers_from_rows(_q(provider, "query_failing_table", {"filters": {"period": None}, "limit": 200}))

    if scenario == "period_cutoff":
        r = _q(provider, "query_failing_table", {"filters": {"journal_type": "SALES"}, "limit": 200})
        return {row["voucher"] for row in r["rows"] if row["accounting_date"][:7] != row["period"][:7]}

    if scenario == "intercompany_out_of_balance":
        IC = ["A14000", "L21500", "R42000", "X67000"]
        g = _q(provider, "query_failing_table", {"filters": {"main_account": IC}, "group_by": "main_account"})
        f = {r["main_account"]: r["net"] for r in g["groups"]}
        b = _q(provider, "query_clean_baseline", {"filters": {"main_account": IC}, "group_by": "main_account"})
        base = {r["main_account"]: r["net"] for r in b["groups"]}
        # accounts whose net moved off the baseline -> then the vouchers on them
        moved = [a for a in IC if f[a] != base[a]]
        return _vouchers_from_rows(_q(provider, "query_failing_table", {"filters": {"main_account": moved}, "limit": 200}))

    raise AssertionError(scenario)


@pytest.mark.parametrize("scenario", ALL)
def test_tools_surface_the_defect(scenario):
    provider = LocalGLProvider(FIXTURE_ROOT, scenario)
    found = detect(scenario, provider)
    assert EXPECTED[scenario].issubset(found), f"{scenario}: missing {EXPECTED[scenario] - found}"


@pytest.mark.parametrize("scenario", ALL)
def test_loop_reaches_a_correct_diagnosis(scenario):
    provider = LocalGLProvider(FIXTURE_ROOT, scenario)
    context = {
        "scenario": scenario,
        "gl_table": f"gl_journal_lines__{scenario}",
        "failed_checks": [CHECK[scenario]],
        "gate_types": ["reconciliation" if scenario in ("period_cutoff", "intercompany_out_of_balance") else "dq_gate"],
        "evidence": f"{CHECK[scenario]} failed",
    }
    submitted = {
        "root_cause": f"Injected {scenario} defect.",
        "dollar_impact": 0,
        "offending_vouchers": sorted(EXPECTED[scenario]),
        "offending_accounts": [],
        "confidence": "high",
        "narrative": f"{CHECK[scenario]} failed; offending vouchers identified.",
    }
    model = ScriptedModel([
        _tools(("query_failing_table", {"group_by": "voucher", "limit": 200})),
        _tools(("submit_diagnosis", submitted)),
    ])
    result = investigate(context=context, provider=provider, model=model)

    assert result.outcome == OUTCOME_COMPLETED
    assert result.diagnosis is not None
    assert result.diagnosis.defect_class == scenario           # grounded via the registry
    assert result.diagnosis.failed_check == CHECK[scenario]
    assert set(result.diagnosis.offending_vouchers) == EXPECTED[scenario]

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

from pathlib import Path

import pytest

from agent.investigate import OUTCOME_COMPLETED, investigate
from agent.provider import LocalGLProvider
from agent.recover import recover_offending
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


@pytest.mark.parametrize("scenario", ALL)
def test_tools_surface_the_defect(scenario):
    # The tools-only recovery (agent/recover.py, also the Phase-4 offline investigator)
    # surfaces each defect's offending vouchers from the data alone — no answer key.
    provider = LocalGLProvider(FIXTURE_ROOT, scenario)
    found = recover_offending(scenario, provider)
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

"""The Phase-4 scorecard: per-scenario grading + the DESIGN §3 targets.

Two things are proven:
  1. The full offline loop closes 7/7 — detect -> diagnose -> draft -> apply ->
     re-gate — and clears every §3 target.
  2. The scorer DISCRIMINATES: a wrong diagnosis (empty or wrong vouchers) fails
     `diagnosis_correct`, and the fix it drives fails to re-gate. A scorecard that
     rubber-stamps anything is worthless; these negative controls guard against it.

No LLM, no network.
"""

from pathlib import Path

import pytest

from agent.diagnosis import build_diagnosis
from agent.provider import LocalGLProvider
from agent.recover import check_for
from agent.scorecard import SCENARIOS, TARGETS, run_scorecard, tally
from agent.scorer import score_scenario

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"


def _rows():
    return {r.scenario: r for r in run_scorecard(fixture_root=FIXTURE_ROOT)}


# --------------------------------------------------------------------------- #
# the happy path — 7/7 on every axis
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_scenario_closes_the_loop(scenario):
    r = _rows()[scenario]
    assert r.detected, scenario
    assert r.diagnosis_correct, scenario
    assert r.fix_valid, f"{scenario}: post-gate {r.post_failed} note={r.note}"
    assert r.regate_pass, f"{scenario}: post-gate {r.post_failed}"
    assert not r.post_failed                     # the whole gate is clean after the fix


def test_totals_meet_or_beat_the_design_targets():
    t = tally(run_scorecard(fixture_root=FIXTURE_ROOT))
    assert t["detected"] >= TARGETS["detected"]
    assert t["diagnosis_correct"] >= TARGETS["diagnosis_correct"]
    assert t["fix_valid"] >= TARGETS["fix_valid"]
    assert t["regate_pass"] >= TARGETS["regate_pass"]


def test_reconciliation_scenarios_dont_trip_dq_checks():
    # period_cutoff / intercompany fail ONLY their reconciliation check pre-fix.
    rows = _rows()
    for scenario in ("period_cutoff", "intercompany_out_of_balance"):
        assert rows[scenario].pre_failed == (check_for(scenario),)


# --------------------------------------------------------------------------- #
# negative controls — the scorer must NOT rubber-stamp
# --------------------------------------------------------------------------- #
def _diagnosis(scenario, vouchers):
    return build_diagnosis(
        {"scenario": scenario, "failed_checks": [check_for(scenario)]},
        {"offending_vouchers": list(vouchers), "confidence": "high", "dollar_impact": 0},
    )


def test_empty_diagnosis_fails_grading():
    # No vouchers recovered -> not correct, and the drafter can't fix -> not valid.
    r = score_scenario("intercompany_out_of_balance", _diagnosis("intercompany_out_of_balance", []),
                       fixture_root=FIXTURE_ROOT)
    assert r.detected                       # the gate still sees the raw defect
    assert not r.diagnosis_correct
    assert not r.fix_valid and not r.regate_pass
    assert r.note                            # records WHY it couldn't fix


def test_wrong_vouchers_fail_grading():
    # A plausible-but-wrong voucher: diagnosis is wrong, and "fixing" it leaves the
    # real defect in place, so the re-gate still trips the expected check.
    r = score_scenario("unbalanced_voucher", _diagnosis("unbalanced_voucher", ["USMI260300030"]),
                       fixture_root=FIXTURE_ROOT)
    assert not r.diagnosis_correct
    assert not r.fix_valid
    assert "debits_equal_credits" in r.post_failed

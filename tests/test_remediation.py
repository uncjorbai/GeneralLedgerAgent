"""Phase 3 — the remediation drafter, flagship scenario end-to-end.

No LLM, no network. Two things are proven for `intercompany_out_of_balance`:

  1. DRAFT SHAPE — draft_proposal() turns a Diagnosis into a staged proposal whose
     corrections name the exact altered lines and restore them to the clean-baseline
     amounts, with the right dollar impact and status.
  2. FIX VALIDITY (the offline proxy for DESIGN §7's "fix valid") — APPLYING the
     drafted corrections to the failing data reproduces the clean baseline for the
     offending vouchers exactly. If clean passes the gate, so does the corrected
     data. This is the closest we can get to "re-gate passes" without a cluster.

Plus guardrail coverage: an unimplemented slug raises (honest, not a silent no-op),
an empty diagnosis is refused, drafting mutates nothing, and the live Delta path is
a deferred stub. The offending vouchers come from the answer key on the TEST side
(scorer's ground truth) — the drafter itself never sees them (guardrail #4).
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from agent.diagnosis import build_diagnosis
from agent.provider import LocalGLProvider
from agent.remediation import (
    RemediationError,
    UnsupportedRemediation,
    _LINE_KEY,
    draft_proposal,
    proposal_to_row,
    write_delta,
    write_dry_run,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"

FLAGSHIP = "intercompany_out_of_balance"
FLAGSHIP_CHECK = "intercompany_eliminates"
OFFENDING = ("USMI260600105", "USMI260700105")   # answer-key vouchers (scorer-side)


def _provider(scenario=FLAGSHIP):
    return LocalGLProvider(FIXTURE_ROOT, scenario)


def _diagnosis(scenario=FLAGSHIP, check=FLAGSHIP_CHECK, vouchers=OFFENDING):
    """A completed Diagnosis for `scenario`, grounded via the real registry."""
    context = {"scenario": scenario, "failed_checks": [check]}
    submitted = {
        "root_cause": "Intercompany side altered off baseline.",
        "dollar_impact": 3000,
        "offending_vouchers": list(vouchers),
        "offending_accounts": ["A14000", "R42000"],
        "confidence": "high",
        "narrative": "IC receivable inflated; does not eliminate.",
    }
    return build_diagnosis(context, submitted)


def _apply(failing: pd.DataFrame, corrections) -> pd.DataFrame:
    """Apply the proposal's corrections to a copy of the failing frame."""
    df = failing.copy()
    for c in corrections:
        mask = (
            (df["company_id"] == c.company_id)
            & (df["voucher"] == c.voucher)
            & (df["line_number"] == c.line_number)
        )
        df.loc[mask, c.field] = c.corrected_value
    return df


# --------------------------------------------------------------------------- #
# 1. draft shape
# --------------------------------------------------------------------------- #
def test_draft_targets_the_offending_vouchers_and_action():
    proposal = draft_proposal(_diagnosis(), _provider())
    assert proposal.action_type == "restore_intercompany_side"
    assert proposal.defect_class == FLAGSHIP
    assert proposal.check == FLAGSHIP_CHECK
    assert set(proposal.target_vouchers) == set(OFFENDING)
    assert proposal.status == "proposed"           # staged, not applied
    assert proposal.corrections                     # it actually proposes something


def test_each_correction_restores_the_baseline_amount():
    provider = _provider()
    clean = provider.clean_baseline().set_index(list(_LINE_KEY))
    proposal = draft_proposal(_diagnosis(), provider)
    for c in proposal.corrections:
        key = (c.company_id, c.voucher, c.line_number)
        base = clean.loc[key]
        assert c.corrected_value == round(float(base[c.field]), 2)
        assert c.corrected_value != c.current_value  # only changed lines are proposed


def test_dollar_impact_is_the_total_restored_variance():
    # Each offending voucher was inflated 12000 -> 13500 (=$1,500); two vouchers => $3,000.
    proposal = draft_proposal(_diagnosis(), _provider())
    assert proposal.dollar_impact == 3000.0


# --------------------------------------------------------------------------- #
# 2. fix validity — applying the fix reproduces the clean baseline
# --------------------------------------------------------------------------- #
def test_applying_the_fix_reproduces_clean_for_offending_vouchers():
    provider = _provider()
    failing = provider.failing_table()
    clean = provider.clean_baseline()
    proposal = draft_proposal(_diagnosis(), provider)

    corrected = _apply(failing, proposal.corrections)

    cols = list(_LINE_KEY) + ["amount_debit", "amount_credit"]
    got = corrected[corrected["voucher"].isin(OFFENDING)][cols].sort_values(list(_LINE_KEY)).reset_index(drop=True)
    want = clean[clean["voucher"].isin(OFFENDING)][cols].sort_values(list(_LINE_KEY)).reset_index(drop=True)
    pd.testing.assert_frame_equal(got, want, check_dtype=False)


def test_fix_does_not_touch_non_offending_rows():
    provider = _provider()
    failing = provider.failing_table()
    proposal = draft_proposal(_diagnosis(), provider)
    corrected = _apply(failing, proposal.corrections)

    others = ~corrected["voucher"].isin(OFFENDING)
    pd.testing.assert_frame_equal(
        corrected[others].reset_index(drop=True), failing[others].reset_index(drop=True), check_dtype=False
    )


# --------------------------------------------------------------------------- #
# guardrails
# --------------------------------------------------------------------------- #
def test_unimplemented_slug_raises_unsupported():
    # duplicate_voucher's slug is declared in the registry but not wired this session.
    diag = _diagnosis(scenario="duplicate_voucher", check="no_duplicate_vouchers", vouchers=("USMI260300030",))
    with pytest.raises(UnsupportedRemediation, match="not implemented"):
        draft_proposal(diag, _provider("duplicate_voucher"))


def test_diagnosis_without_offending_vouchers_is_refused():
    diag = _diagnosis(vouchers=())
    with pytest.raises(RemediationError, match="no offending vouchers"):
        draft_proposal(diag, _provider())


def test_drafting_mutates_no_data():
    provider = _provider()
    before = provider.failing_table()
    draft_proposal(_diagnosis(), provider)
    after = provider.failing_table()
    pd.testing.assert_frame_equal(before, after)


def test_write_delta_is_a_deferred_stub():
    with pytest.raises(NotImplementedError):
        write_delta({"scenario": FLAGSHIP})


# --------------------------------------------------------------------------- #
# staging persistence (PURE/IMPURE split)
# --------------------------------------------------------------------------- #
def test_proposal_to_row_shape():
    proposal = draft_proposal(_diagnosis(), _provider())
    row = proposal_to_row(proposal, agent_run_id="run-123", drafted_at="2026-08-31T09:00:00")
    assert row["agent_run_id"] == "run-123"
    assert row["scenario"] == FLAGSHIP
    assert row["action_type"] == "restore_intercompany_side"
    assert row["status"] == "proposed"
    assert isinstance(row["target_vouchers"], list)
    assert isinstance(row["corrections"], list) and row["corrections"]
    assert set(row["corrections"][0]) >= {"voucher", "line_number", "field", "current_value", "corrected_value", "delta"}


def test_write_dry_run_round_trips_jsonl(tmp_path):
    proposal = draft_proposal(_diagnosis(), _provider())
    row = proposal_to_row(proposal, agent_run_id="run-123", drafted_at="2026-08-31T09:00:00")
    out = write_dry_run(row, tmp_path / "proposals.jsonl")
    reread = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(reread) == 1
    assert reread[0]["action_type"] == "restore_intercompany_side"
    assert reread[0]["dollar_impact"] == 3000.0

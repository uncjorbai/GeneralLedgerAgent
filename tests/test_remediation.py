"""Phase 3 — the remediation drafter, across all seven scenarios.

No LLM, no network. The centerpiece is FIX VALIDITY (the offline proxy for DESIGN
§7's "fix valid"): applying the drafted corrections to the failing data reproduces
the clean baseline for the offending vouchers exactly. Since the gate passed on the
baseline, it passes on the restored data — the closest we get to "re-gate passes"
without a cluster, and it holds for every defect class through one shared engine.

Plus: the flagship's detailed shape (intercompany), and guardrail coverage — a slug
with no drafter raises, an empty diagnosis is refused, drafting mutates nothing, and
the live Delta path is a deferred stub. Offending vouchers come from the answer keys
on the TEST side (scorer's ground truth); the drafter itself never sees them (#4).
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from agent.diagnosis import build_diagnosis
from agent.provider import LocalGLProvider
from agent.registry import Check, Registry
from agent.remediation import (
    OP_REMOVE,
    RemediationError,
    UnsupportedRemediation,
    _LINE_KEY,
    draft_proposal,
    proposal_to_row,
    write_delta,
    write_dry_run,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"

# scenario -> (check it trips, registry remediation slug the drafter should use)
SCENARIOS = {
    "unbalanced_voucher": ("debits_equal_credits", "restore_voucher_balance"),
    "duplicate_voucher": ("no_duplicate_vouchers", "remove_duplicate_line"),
    "unmapped_account": ("account_in_coa", "map_account"),
    "missing_department": ("required_dimensions_present", "populate_dimension"),
    "missing_entity_or_period": ("entity_and_period_present", "populate_field"),
    "period_cutoff": ("period_cutoff_correct", "shift_period"),
    "intercompany_out_of_balance": ("intercompany_eliminates", "restore_intercompany_side"),
}

# Offending vouchers per scenario, from the answer keys (scorer-side ground truth).
EXPECTED = {
    "unbalanced_voucher": ("USTX260600060", "USTX260200079", "USTX251200092"),
    "duplicate_voucher": ("USMI260300030", "USMI260200063", "USMI251000008"),
    "unmapped_account": ("USTX260300096", "USTX260800053", "USMI260200067", "USMI260700088"),
    "missing_department": ("USMI260700045", "USMI260100057", "USMI260100036", "USTX260800051", "USTX250900020"),
    "missing_entity_or_period": ("USTX260500096", "USMI260200092", "USMI260200027", "USTX250900082"),
    "period_cutoff": ("USTX251100055", "USTX260600021", "USMI260600041"),
    "intercompany_out_of_balance": ("USMI260600105", "USMI260700105"),
}

FLAGSHIP = "intercompany_out_of_balance"
ALL = sorted(SCENARIOS)

# Columns compared when checking that a fix reproduces the baseline.
_CMP = ["company_id", "voucher", "line_number", "main_account",
        "amount_debit", "amount_credit", "department", "period", "accounting_date"]


def _provider(scenario):
    return LocalGLProvider(FIXTURE_ROOT, scenario)


def _diagnosis(scenario, vouchers=None):
    """A completed Diagnosis for `scenario`, grounded via the real registry."""
    check, _ = SCENARIOS[scenario]
    context = {"scenario": scenario, "failed_checks": [check]}
    submitted = {
        "root_cause": f"{scenario} defect.",
        "dollar_impact": 0,
        "offending_vouchers": list(EXPECTED[scenario] if vouchers is None else vouchers),
        "offending_accounts": [],
        "confidence": "high",
        "narrative": f"{scenario} found.",
    }
    return build_diagnosis(context, submitted)


def _apply(failing: pd.DataFrame, corrections) -> pd.DataFrame:
    """Apply a proposal's corrections to a copy of the failing frame.

    RESTATE sets a field (coercing to the column's dtype); REMOVE drops one copy of
    the named line. Keyed on voucher+line_number, so it works even where the defect
    nulled company_id.
    """
    df = failing.copy().reset_index(drop=True)
    dropped: set[int] = set()
    for c in corrections:
        match = df[(df["voucher"] == c.voucher) & (df["line_number"] == c.line_number)]
        if c.op == OP_REMOVE:
            avail = [i for i in match.index if i not in dropped]
            dropped.add(avail[0])
        else:
            val = c.corrected_value
            if pd.api.types.is_datetime64_any_dtype(df[c.field]):
                val = pd.to_datetime(val)
            df.loc[match.index, c.field] = val
    if dropped:
        df = df.drop(index=list(dropped))
    return df.reset_index(drop=True)


def _rows(df: pd.DataFrame, vouchers) -> pd.DataFrame:
    cols = [c for c in _CMP if c in df.columns]
    return df[df["voucher"].isin(vouchers)][cols].sort_values(["voucher", "line_number"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# the centerpiece: fix validity across all seven defects
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario", ALL)
def test_applying_the_fix_reproduces_clean_for_offending_vouchers(scenario):
    provider = _provider(scenario)
    proposal = draft_proposal(_diagnosis(scenario), provider)

    corrected = _apply(provider.failing_table(), proposal.corrections)

    got = _rows(corrected, EXPECTED[scenario])
    want = _rows(provider.clean_baseline(), EXPECTED[scenario])
    pd.testing.assert_frame_equal(got, want, check_dtype=False)


@pytest.mark.parametrize("scenario", ALL)
def test_action_type_and_targets_match_the_registry(scenario):
    _, slug = SCENARIOS[scenario]
    proposal = draft_proposal(_diagnosis(scenario), _provider(scenario))
    assert proposal.action_type == slug
    assert proposal.defect_class == scenario
    assert proposal.status == "proposed"                 # staged, never applied
    assert set(proposal.target_vouchers) == set(EXPECTED[scenario])
    assert proposal.corrections                           # it proposes something


@pytest.mark.parametrize("scenario", ALL)
def test_fix_leaves_non_offending_rows_untouched(scenario):
    provider = _provider(scenario)
    failing = provider.failing_table()
    corrected = _apply(failing, draft_proposal(_diagnosis(scenario), provider).corrections)
    others = sorted(set(failing["voucher"]) - set(EXPECTED[scenario]))
    pd.testing.assert_frame_equal(_rows(corrected, others), _rows(failing, others), check_dtype=False)


@pytest.mark.parametrize("scenario", ALL)
def test_dollar_impact_sign_matches_defect_kind(scenario):
    # Balance defects move money (impact > 0); classification/completeness/date
    # defects do not (impact == 0 dollars — the harm is a wrong label, not a variance).
    monetary = {"unbalanced_voucher", "duplicate_voucher", "intercompany_out_of_balance"}
    impact = draft_proposal(_diagnosis(scenario), _provider(scenario)).dollar_impact
    assert impact >= 0
    assert (impact > 0) == (scenario in monetary)


# --------------------------------------------------------------------------- #
# flagship detail (intercompany)
# --------------------------------------------------------------------------- #
def test_flagship_restores_baseline_amounts_and_impact():
    provider = _provider(FLAGSHIP)
    clean = provider.clean_baseline().set_index(list(_LINE_KEY))
    proposal = draft_proposal(_diagnosis(FLAGSHIP), provider)

    assert proposal.dollar_impact == 3000.0            # 2 vouchers inflated $1,500 each
    for c in proposal.corrections:
        base = clean.loc[(c.company_id, c.voucher, c.line_number)]
        assert c.corrected_value == round(float(base[c.field]), 2)
        assert c.corrected_value != c.current_value    # only changed lines proposed


def test_duplicate_proposes_removals():
    proposal = draft_proposal(_diagnosis("duplicate_voucher"), _provider("duplicate_voucher"))
    assert all(c.op == OP_REMOVE for c in proposal.corrections)
    assert proposal.corrections


# --------------------------------------------------------------------------- #
# guardrails
# --------------------------------------------------------------------------- #
def test_slug_with_no_drafter_raises_unsupported():
    # A registry entry whose remediation slug has no drafter -> honest failure.
    reg = Registry(checks={"mystery_check": Check(
        name="mystery_check", gate="dq_gate", defect_class="mystery",
        deterministic=True, fails_task=True, remediation="teleport_the_error_away",
    )})
    diag = build_diagnosis({"scenario": "mystery", "failed_checks": ["mystery_check"]},
                           {"offending_vouchers": ["V1"], "confidence": "low"}, registry=reg)
    with pytest.raises(UnsupportedRemediation, match="no drafter"):
        draft_proposal(diag, _provider(FLAGSHIP), registry=reg)


def test_diagnosis_without_offending_vouchers_is_refused():
    with pytest.raises(RemediationError, match="no offending vouchers"):
        draft_proposal(_diagnosis(FLAGSHIP, vouchers=[]), _provider(FLAGSHIP))


def test_drafting_mutates_no_data():
    provider = _provider(FLAGSHIP)
    before = provider.failing_table()
    draft_proposal(_diagnosis(FLAGSHIP), provider)
    pd.testing.assert_frame_equal(before, provider.failing_table())


def test_write_delta_is_a_deferred_stub():
    with pytest.raises(NotImplementedError):
        write_delta({"scenario": FLAGSHIP})


# --------------------------------------------------------------------------- #
# staging persistence (PURE/IMPURE split)
# --------------------------------------------------------------------------- #
def test_proposal_to_row_shape():
    proposal = draft_proposal(_diagnosis(FLAGSHIP), _provider(FLAGSHIP))
    row = proposal_to_row(proposal, agent_run_id="run-123", drafted_at="2026-08-31T09:00:00")
    assert row["agent_run_id"] == "run-123"
    assert row["scenario"] == FLAGSHIP
    assert row["action_type"] == "restore_intercompany_side"
    assert row["status"] == "proposed"
    assert isinstance(row["target_vouchers"], list)
    assert isinstance(row["corrections"], list) and row["corrections"]
    assert set(row["corrections"][0]) >= {"op", "voucher", "line_number", "field", "corrected_value", "delta"}


def test_write_dry_run_round_trips_jsonl(tmp_path):
    proposal = draft_proposal(_diagnosis(FLAGSHIP), _provider(FLAGSHIP))
    row = proposal_to_row(proposal, agent_run_id="run-123", drafted_at="2026-08-31T09:00:00")
    out = write_dry_run(row, tmp_path / "proposals.jsonl")
    reread = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(reread) == 1
    assert reread[0]["action_type"] == "restore_intercompany_side"
    assert reread[0]["dollar_impact"] == 3000.0

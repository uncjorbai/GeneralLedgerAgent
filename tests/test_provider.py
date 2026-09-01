"""Unit tests for agent.provider — the read-only local data surface. No Spark.

Runs against the committed fixture in tests/fixtures/gl (a faithful slice of the
Generator's real output — see docs/PHASE2_PLAN.md). The intercompany arithmetic
below is the ground truth the investigator must rediscover.
"""

from pathlib import Path

import pytest

from agent.provider import (
    AnswerKeyAccessError,
    LocalGLProvider,
    ProviderError,
    spark_provider,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"
SCENARIO = "intercompany_out_of_balance"
IC_ACCOUNTS = ["A14000", "L21500", "R42000", "X67000"]


@pytest.fixture(scope="module")
def provider():
    return LocalGLProvider(FIXTURE_ROOT, SCENARIO)


def _net(df, acct):
    rows = df[df.main_account == acct]
    return round(rows.amount_debit.sum() - rows.amount_credit.sum(), 2)


def test_failing_table_loads_real_schema(provider):
    gl = provider.failing_table()
    assert len(gl) > 0
    # intercompany's alter_amount changes amounts, not row counts, so the failing
    # table and the clean baseline carry the same rows (robust to fixture size).
    assert len(gl) == len(provider.clean_baseline())
    for col in ("company_id", "voucher", "line_number", "main_account",
                "amount_debit", "amount_credit", "accounting_date"):
        assert col in gl.columns


def test_clean_baseline_intercompany_ties_out(provider):
    """In the baseline, receivable mirrors payable and IC income mirrors expense."""
    clean = provider.clean_baseline()
    assert _net(clean, "A14000") == 144000.0        # receivable
    assert -_net(clean, "L21500") == 144000.0       # payable (credit-normal)
    assert -_net(clean, "R42000") == 144000.0       # IC income (credit-normal)
    assert _net(clean, "X67000") == 144000.0        # IC expense


def test_failing_table_shows_the_3000_variance(provider):
    """The defect: HQ receivable + IC income each run 3,000 hot vs their pair."""
    gl = provider.failing_table()
    assert _net(gl, "A14000") == 147000.0           # +3,000
    assert -_net(gl, "R42000") == 147000.0          # +3,000
    assert -_net(gl, "L21500") == 144000.0          # untouched
    assert _net(gl, "X67000") == 144000.0           # untouched


def test_chart_of_accounts_and_departments_load(provider):
    coa = provider.chart_of_accounts()
    assert set(IC_ACCOUNTS).issubset(set(coa.account_key))
    depts = provider.departments()
    assert "department" in depts.columns


def test_provider_caches_frames_but_returns_copies(provider):
    a = provider.failing_table()
    a.loc[a.index[0], "amount_debit"] = -999999      # mutate the returned copy
    b = provider.failing_table()
    assert b.amount_debit.iloc[0] != -999999          # cache is not corrupted


def test_unknown_scenario_raises_provider_error():
    bad = LocalGLProvider(FIXTURE_ROOT, "no_such_scenario")
    with pytest.raises(ProviderError):
        bad.failing_table()


# --- guardrail #4: the answer key is unreachable through the data surface ---
@pytest.mark.parametrize("poison", ["_qa", "intercompany/_qa", "run_manifest.json"])
def test_answer_key_scenario_is_refused(poison):
    with pytest.raises(AnswerKeyAccessError):
        LocalGLProvider(FIXTURE_ROOT, poison)


def test_committed_answer_key_is_present_but_unreachable_by_the_agent():
    # Phase 4 commits the real answer keys under _qa/ for the SCORER. Prove that
    # doing so did not open a door for the agent: the file exists on disk, the
    # scorer's loader reads it, but the provider still refuses the guarded path.
    from agent.answer_key import load_answer_key

    manifest = FIXTURE_ROOT / SCENARIO / "_qa" / "run_manifest.json"
    assert manifest.exists()                                   # physically present
    assert load_answer_key(SCENARIO, fixture_root=FIXTURE_ROOT).expected_check  # scorer CAN read

    with pytest.raises(AnswerKeyAccessError):                  # agent CANNOT
        LocalGLProvider(FIXTURE_ROOT, f"{SCENARIO}/_qa")


def test_provider_exposes_no_write_method(provider):
    for attr in ("write", "save", "write_delta", "stage", "update"):
        assert not hasattr(provider, attr)


def test_spark_provider_is_a_marked_stub():
    with pytest.raises(NotImplementedError, match="cluster-session"):
        spark_provider()

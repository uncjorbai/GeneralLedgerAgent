"""Fidelity of the offline gate mirror (agent/gate.py) against ground truth.

The scorecard's closed-loop claim — "the same gate that caught the defect validates
the fix" — is only worth anything if this pandas gate behaves like the real
`dq_gate.py`. We don't assert that by eyeballing; we prove it against the committed
answer keys:

  * clean data passes ALL seven checks (what `dq_gate.py` asserts for `is_clean`);
  * each scenario's failing data fails EXACTLY the check its answer key names
    (`expected_check`) — no more (no spurious failures), no less (it really trips).

If the mirror diverged from the pipeline, one of these would break. No LLM, no
network.
"""

from pathlib import Path

import pytest

from agent.answer_key import load_answer_key
from agent.gate import GATE_DQ, GATE_RECON, run_gate
from agent.provider import LocalGLProvider

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"

SCENARIOS = [
    "unbalanced_voucher", "duplicate_voucher", "unmapped_account", "missing_department",
    "missing_entity_or_period", "period_cutoff", "intercompany_out_of_balance",
]


def _coa():
    return LocalGLProvider(FIXTURE_ROOT, "clean").chart_of_accounts()


def test_clean_passes_every_check():
    gl = LocalGLProvider(FIXTURE_ROOT, "unbalanced_voucher").clean_baseline()
    result = run_gate(gl, _coa())
    assert result.passed, f"clean should pass all checks, but failed: {sorted(result.failed_checks)}"
    assert len(result.results) == 7


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_failing_data_fails_exactly_its_expected_check(scenario):
    key = load_answer_key(scenario, fixture_root=FIXTURE_ROOT)
    gl = LocalGLProvider(FIXTURE_ROOT, scenario).failing_table()
    result = run_gate(gl, _coa())
    assert result.failed_checks == {key.expected_check}, (
        f"{scenario}: expected only {key.expected_check!r} to fail, got {sorted(result.failed_checks)}"
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_expected_check_gate_type_matches_answer_key(scenario):
    key = load_answer_key(scenario, fixture_root=FIXTURE_ROOT)
    gl = LocalGLProvider(FIXTURE_ROOT, scenario).failing_table()
    cr = run_gate(gl, _coa()).get(key.expected_check)
    assert cr is not None and not cr.passed
    assert cr.gate == (GATE_RECON if key.expected_gate == "reconciliation" else GATE_DQ)
    assert cr.failures > 0 and cr.detail        # a real, inspectable failure

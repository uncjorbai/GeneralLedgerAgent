"""Score one agent run against the answer key + the gate (DESIGN §7).

Given a Diagnosis for a scenario, grade the four scorecard axes:

  * detected          — the gate flags the defect's expected check on the raw data.
  * diagnosis_correct — the agent named the right defect class AND recovered the
                        offending vouchers the answer key lists.
  * fix_valid         — applying the drafted proposal clears the expected check
                        without introducing any NEW gate failure.
  * regate_pass       — after the fix, the WHOLE gate passes (the closed loop).

The scorer is where the answer key is finally allowed in (DESIGN §5): the agent
produced the Diagnosis blind; here we compare it to ground truth after the fact. The
grading is deliberately DISCRIMINATING — a wrong diagnosis (wrong/empty vouchers)
must fail `diagnosis_correct`, and a fix that restores the wrong records must fail
`fix_valid` (the re-gate still trips). `tests/test_scorer.py` proves both.

Pure given the fixtures: reads through the provider, runs the offline gate, writes
nothing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent.answer_key import load_answer_key
from agent.apply import apply_corrections
from agent.diagnosis import Diagnosis
from agent.gate import run_gate
from agent.provider import LocalGLProvider
from agent.remediation import UnsupportedRemediation, draft_proposal

_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gl"


@dataclass(frozen=True)
class ScoreRow:
    """The graded result for one scenario — one row of the scorecard."""

    scenario: str
    expected_check: str
    detected: bool
    diagnosis_correct: bool
    fix_valid: bool
    regate_pass: bool
    action_type: str = ""
    dollar_impact: float = 0.0
    pre_failed: tuple[str, ...] = ()
    post_failed: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pre_failed"] = list(self.pre_failed)
        d["post_failed"] = list(self.post_failed)
        return d


def score_scenario(scenario: str, diagnosis: Diagnosis, *, fixture_root: Path | None = None,
                   registry=None) -> ScoreRow:
    """Grade one Diagnosis for `scenario` against the answer key and the gate."""
    root = fixture_root or _FIXTURE_ROOT
    key = load_answer_key(scenario, fixture_root=root)
    provider = LocalGLProvider(root, scenario)
    coa = provider.chart_of_accounts()
    failing = provider.failing_table()

    pre = run_gate(failing, coa)
    detected = key.expected_check in pre.failed_checks

    recovered = set(diagnosis.offending_vouchers)
    diagnosis_correct = (diagnosis.defect_class == scenario) and key.offending_vouchers <= recovered

    # Draft -> apply -> re-gate. A drafter failure is a valid (failing) outcome, not a crash.
    action_type, dollar_impact, note = "", 0.0, ""
    post_failed: tuple[str, ...] = tuple(sorted(pre.failed_checks))
    fix_valid = regate_pass = False
    try:
        proposal = draft_proposal(diagnosis, provider, registry=registry)
        action_type, dollar_impact = proposal.action_type, proposal.dollar_impact
        corrected = apply_corrections(failing, proposal.corrections)
        post = run_gate(corrected, coa)
        post_failed = tuple(sorted(post.failed_checks))
        # cleared the target check, and introduced no new failure the gate didn't already have
        fix_valid = (key.expected_check not in post.failed_checks) and post.failed_checks <= pre.failed_checks
        regate_pass = post.passed
    except UnsupportedRemediation as e:
        note = f"no drafter: {e}"
    except Exception as e:  # a bad diagnosis can make drafting impossible -> not valid
        note = f"draft/apply failed: {type(e).__name__}: {e}"

    return ScoreRow(
        scenario=scenario,
        expected_check=key.expected_check,
        detected=detected,
        diagnosis_correct=diagnosis_correct,
        fix_valid=fix_valid,
        regate_pass=regate_pass,
        action_type=action_type,
        dollar_impact=dollar_impact,
        pre_failed=tuple(sorted(pre.failed_checks)),
        post_failed=post_failed,
        note=note,
    )

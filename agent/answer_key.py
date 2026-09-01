"""The answer key — the SCORER's ground truth (DESIGN §5, §7).

This is the deliberate counterpart to `provider.py`'s guardrail #4. The investigator
must never see the answer key; the *scorer* reads it after the fact to grade what the
agent produced. So the answer key gets its own tiny loader here, entirely separate
from the read surface the agent uses — there is no way to reach this from a tool.

Source: each scenario's `run_manifest.json`, committed under
`tests/fixtures/gl/<scenario>/_qa/`. The agent's provider refuses any path containing
`_qa`/`run_manifest`, so the key being present in the tree does not weaken the
guardrail — it is unreachable through the provider by construction (see the
`test_provider` guardrail test).

We read only the fields that identify the defect — `expected_check`, and from
`defects_applied` the type / offending vouchers / amount delta. The manifest's
aggregate figures (row_count, totals) describe the full Generator run, not the
committed fixture slice, so the scorer must not use them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gl"


class AnswerKeyError(RuntimeError):
    """The answer key for a scenario is missing or malformed."""


@dataclass(frozen=True)
class AnswerKey:
    """The gradeable ground truth for one scenario."""

    scenario: str
    expected_gate: str                     # dq_gate | reconciliation
    expected_check: str                    # the check that should fail
    defect_type: str                       # the Generator's defect label
    offending_vouchers: frozenset[str]     # the vouchers the defect touched
    amount_delta: float | None             # per-voucher $ move, where meaningful


def load_answer_key(scenario: str, *, fixture_root: Path | None = None) -> AnswerKey:
    """Load and normalize the committed answer key for `scenario`.

    `fixture_root` is injectable for tests; defaults to the committed fixture tree.
    """
    root = fixture_root or _FIXTURE_ROOT
    path = root / scenario / "_qa" / "run_manifest.json"
    if not path.exists():
        raise AnswerKeyError(f"No answer key for scenario '{scenario}' at {path}.")
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AnswerKeyError(f"Answer key for '{scenario}' is not valid JSON: {e}") from e

    defects = m.get("defects_applied") or []
    vouchers: set[str] = set()
    amount_delta = None
    defect_type = ""
    for d in defects:
        vouchers |= {str(v) for v in d.get("vouchers", [])}
        defect_type = defect_type or str(d.get("type", ""))
        if amount_delta is None and d.get("amount_delta") is not None:
            amount_delta = float(d["amount_delta"])

    expected_check = str(m.get("expected_check", "")).strip()
    if not expected_check:
        raise AnswerKeyError(f"Answer key for '{scenario}' has no expected_check.")

    return AnswerKey(
        scenario=str(m.get("scenario", scenario)),
        expected_gate=str(m.get("expected_gate", "")).strip(),
        expected_check=expected_check,
        defect_type=defect_type,
        offending_vouchers=frozenset(vouchers),
        amount_delta=amount_delta,
    )

"""The scorecard harness — the centerpiece deliverable (DESIGN §3, §7).

Runs the full offline loop for all seven scenarios and renders the scorecard:

    for each scenario:
        recover the offending vouchers (tools only)   -> a Diagnosis
        score it: detect -> diagnose -> draft -> apply -> re-gate
    render: scenario x {detected, diagnosis correct, fix valid, re-gate passes}

This is the artifact that turns "I built an agent" into "here is proof it handles
7/7 seeded failures." It grades against the real answer keys and the fidelity-checked
offline gate (agent/gate.py), so the fix is validated by the SAME checks that caught
the defect (the closed loop, DESIGN §3).

Honesty, stated on the card itself: offline, the investigation is a deterministic,
tools-only recovery (agent/recover.py), so this proves the tool surface is sufficient
and the fix machinery closes — NOT that the live model diagnoses each defect unaided.
That axis is the deferred live run (an API key), and the Phase-4 harness is built so
the same scorer grades a live Diagnosis with no change.

Run it:  python -m agent.scorecard      (writes docs/SCORECARD.md + docs/scorecard.json)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent.diagnosis import build_diagnosis
from agent.provider import LocalGLProvider
from agent.recover import check_for, recover_offending
from agent.scorer import ScoreRow, score_scenario

SCENARIOS = [
    "unbalanced_voucher", "duplicate_voucher", "unmapped_account", "missing_department",
    "missing_entity_or_period", "period_cutoff", "intercompany_out_of_balance",
]

_REPO = Path(__file__).resolve().parent.parent
_FIXTURE_ROOT = _REPO / "tests" / "fixtures" / "gl"

# The §3 success targets (out of 7).
TARGETS = {"detected": 7, "diagnosis_correct": 6, "fix_valid": 5, "regate_pass": 3}


def _diagnosis_for(scenario: str, provider) -> "object":
    """Build a Diagnosis from the offline (tools-only) recovery of the offenders."""
    recovered = sorted(recover_offending(scenario, provider))
    context = {"scenario": scenario, "failed_checks": [check_for(scenario)]}
    submitted = {
        "root_cause": f"Offline recovery of {scenario}.",
        "dollar_impact": 0,
        "offending_vouchers": recovered,
        "offending_accounts": [],
        "confidence": "high",
        "narrative": f"{scenario}: {len(recovered)} offending voucher(s) recovered from the data.",
    }
    return build_diagnosis(context, submitted)


def run_scorecard(fixture_root: Path | None = None) -> list[ScoreRow]:
    """Score all seven scenarios and return the rows (used by the harness + tests)."""
    root = fixture_root or _FIXTURE_ROOT
    rows: list[ScoreRow] = []
    for scenario in SCENARIOS:
        provider = LocalGLProvider(root, scenario)
        diagnosis = _diagnosis_for(scenario, provider)
        rows.append(score_scenario(scenario, diagnosis, fixture_root=root))
    return rows


def tally(rows: list[ScoreRow]) -> dict[str, int]:
    return {
        "detected": sum(r.detected for r in rows),
        "diagnosis_correct": sum(r.diagnosis_correct for r in rows),
        "fix_valid": sum(r.fix_valid for r in rows),
        "regate_pass": sum(r.regate_pass for r in rows),
    }


def _tick(b: bool) -> str:
    return "✅" if b else "❌"


def render_markdown(rows: list[ScoreRow]) -> str:
    n = len(rows)
    t = tally(rows)
    lines = [
        "# GL Anomaly Investigator — Scorecard",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"{n} seeded scenarios · offline (no LLM, no network)._",
        "",
        "| Scenario | Check | Detected | Diagnosis | Fix valid | Re-gate passes | Action | $ impact |",
        "|---|---|:--:|:--:|:--:|:--:|---|--:|",
    ]
    for r in rows:
        lines.append(
            f"| `{r.scenario}` | `{r.expected_check}` | {_tick(r.detected)} | "
            f"{_tick(r.diagnosis_correct)} | {_tick(r.fix_valid)} | {_tick(r.regate_pass)} | "
            f"`{r.action_type}` | {r.dollar_impact:,.0f} |"
        )
    lines += [
        "",
        "## Totals vs targets (DESIGN §3)",
        "",
        "| Metric | Result | Target |",
        "|---|:--:|:--:|",
        f"| Detection | {t['detected']}/{n} | {TARGETS['detected']}/7 |",
        f"| Diagnosis accuracy | {t['diagnosis_correct']}/{n} | ≥ {TARGETS['diagnosis_correct']}/7 |",
        f"| Remediation validity | {t['fix_valid']}/{n} | ≥ {TARGETS['fix_valid']}/7 |",
        f"| Closed-loop (re-gate passes) | {t['regate_pass']}/{n} | ≥ {TARGETS['regate_pass']} |",
        "",
        "## What this proves — and what it doesn't",
        "",
        "- Graded against the real answer keys (`run_manifest.json`) and a fidelity-",
        "  checked offline mirror of the pipeline gate (`agent/gate.py`): clean passes",
        "  all seven checks, each defect fails exactly its expected check, and every",
        "  fix is validated by the SAME checks that caught it (the closed loop).",
        "- Offline, the investigation is a deterministic, tools-only recovery",
        "  (`agent/recover.py`) — this proves the tool surface is **sufficient** and the",
        "  fix machinery closes end-to-end. It does **not** prove the live model",
        "  diagnoses each defect unaided; that is the deferred live run (API key), which",
        "  the same scorer grades with no change.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    rows = run_scorecard()
    md = render_markdown(rows)
    md_path = _REPO / "docs" / "SCORECARD.md"
    json_path = _REPO / "docs" / "scorecard.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(
        json.dumps({"rows": [r.to_dict() for r in rows], "tally": tally(rows)}, indent=2),
        encoding="utf-8",
    )
    # ASCII-only console summary (Windows consoles are often cp1252 -> no emoji).
    t = tally(rows)
    n = len(rows)
    print(f"Scorecard: {n} scenarios (offline)")
    for r in rows:
        flags = "".join("Y" if b else "n" for b in (r.detected, r.diagnosis_correct, r.fix_valid, r.regate_pass))
        print(f"  [{flags}] {r.scenario:28} {r.expected_check}")
    print(f"  legend: Detected/Diagnosis/FixValid/RegatePass")
    print(f"totals: detected {t['detected']}/{n}, diagnosis {t['diagnosis_correct']}/{n}, "
          f"fix_valid {t['fix_valid']}/{n}, regate {t['regate_pass']}/{n}")
    print(f"wrote: {md_path.relative_to(_REPO)}  and  {json_path.relative_to(_REPO)}")


if __name__ == "__main__":
    main()

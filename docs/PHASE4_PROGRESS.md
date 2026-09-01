# Phase 4 — Progress & Handoff

Living status for the Phase-4 build (the **scorecard**). Read this first when
resuming. Design of record is `DESIGN.md` §3, §7.

_Last updated: **Phase 4 complete, offline.** The full loop detect→diagnose→draft→
apply→re-gate closes 7/7 and clears every §3 target. 180 tests green, no network,
no key. Scorecard at `docs/SCORECARD.md`._

---

## ▶ RESUME HERE

**State:** Phase 4 offline-complete. `python -m pytest -q` → 180 passing.
`python -m agent.scorecard` regenerates `docs/SCORECARD.md` + `docs/scorecard.json`.

**Result (offline):** Detection 7/7, Diagnosis 7/7, Remediation validity 7/7,
Closed-loop 7/7 — all at or above the §3 targets (7 / ≥6 / ≥5 / ≥3).

**Pick up next (rough priority):**
1. **Phase 5 — portfolio README.** Put the scorecard table at the top; tell the
   story (two failure classes → two responses), the architecture, the guardrails,
   and the closed loop. `docs/SCORECARD.md` is ready to embed; consider rendering it
   as an HTML artifact for the portfolio.
2. **Live LLM scored pass** (spends your key — deliberate). The harness is built so
   the SAME `score_scenario(scenario, diagnosis, ...)` grades a Diagnosis from the
   live `investigate()` loop instead of the offline recovery. `pip install anthropic`,
   set `ANTHROPIC_API_KEY`, build `AnthropicModel.from_config()`, run `investigate`
   per scenario, feed each result's `.diagnosis` to `score_scenario`. This is the
   first real measure of **unaided** diagnosis accuracy — the axis the offline card
   deliberately does not claim.
3. **Live-Databricks tail** (cluster). Carried forward from Phases 1–3: the live
   Spark provider, the `dq_gate` exit-value read on successful runs, and the live
   Delta writers (`audit.write_delta`, `remediation.write_delta`).

---

## What was built

| Area | Change | Notes |
|------|--------|-------|
| `agent/gate.py` | NEW — offline gate mirror | The 7 checks transcribed from `dq_gate.py` as pandas; `run_gate(gl, coa) -> GateResult`. Null-safe like Spark (period_cutoff ignores null-period rows). |
| `agent/answer_key.py` | NEW — scorer-only loader | Reads `run_manifest.json`; exposes `expected_check`, defect type, offending vouchers, amount_delta. Separate from the agent's provider by design. |
| `agent/recover.py` | NEW — offline investigator | Tools-only, deterministic recovery of offending vouchers (promoted from the Phase-2 `detect`). Read-only, no answer key. |
| `agent/apply.py` | NEW — the apply step | `apply_corrections(gl, corrections)` — pure; restate + remove, dtype-aware. Human-in-the-loop apply, promoted from the test helper. |
| `agent/scorer.py` | NEW — grader | `score_scenario(scenario, diagnosis, ...) -> ScoreRow`. Grades detect/diagnose/fix/re-gate. Discriminating (negative controls tested). |
| `agent/scorecard.py` | NEW — the harness | `run_scorecard()` + `render_markdown()`; `python -m agent.scorecard` writes the card. |
| `tests/fixtures/gl/*/_qa/run_manifest.json` | committed answer keys | The scorer's ground truth; unreachable by the agent (provider refuses `_qa`). |
| `tests/fixtures/build_fixture.py` | +answer-key copy | Keeps the fixture (incl. keys) reproducible; parquet stays byte-stable. |
| tests | +`test_gate.py`, +`test_scorer.py`, provider guardrail, `test_scenarios` repoint | Gate fidelity, per-scenario scoring + §3 targets, answer-key-present-but-unreachable. |

## Why the offline scorecard is credible (not a strawman)
- **Real answer keys.** Graded against the Generator's actual `run_manifest.json`,
  committed under `_qa/` — the same files the live pipeline emits.
- **Fidelity-checked gate.** `test_gate.py` proves the offline gate matches the
  pipeline: clean passes all 7, each defect fails EXACTLY its `expected_check`. The
  fix is then validated by the same checks that caught it — the closed loop.
- **Discriminating scorer.** Negative controls (empty / wrong vouchers) fail
  `diagnosis_correct` and `fix_valid`, so a green row means something.

## The honesty caveat (stated on the card itself)
Offline, the investigation is the deterministic `recover.py`, so the scorecard
proves the tool surface is **sufficient** and the fix machinery **closes** end-to-end
— NOT that the live model diagnoses each defect unaided. That axis is the deferred
live run (#2 above), which the same scorer grades unchanged.

## Guardrails held
- **Answer key (#4):** committed for the SCORER only; the agent's provider still
  refuses any `_qa`/`run_manifest` path (proven by `test_provider`).
- **Read-mostly (#2) / human-in-the-loop (#3):** `apply.py` is pure and runs only on
  an (already-drafted, approvable) proposal; nothing writes Silver/Gold.

## How to run
```bash
python -m pytest -q            # 180 tests, offline
python -m agent.scorecard      # regenerate docs/SCORECARD.md + docs/scorecard.json
```

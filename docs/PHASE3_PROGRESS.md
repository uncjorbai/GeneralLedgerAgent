# Phase 3 — Progress & Handoff

Living status for the Phase-3 build (the **remediation drafter**). Read this first
when resuming. Design of record is `DESIGN.md` §4.3 step 4, §5, §6.

_Last updated: **all seven scenarios complete.** Every defect drafts a staged,
human-approvable correcting proposal whose fix provably reproduces the clean
baseline, offline. 153 tests green, no network, no key._

---

## ▶ RESUME HERE

**State:** Phase 3 drafter complete for all 7 defects. `python -m pytest -q` → 153
passing, offline.

**Design decisions locked (both flagged as guardrail-affecting, both approved):**
1. **Deterministic drafter — the model stays fully read-only.** DESIGN §5 lists a
   `stage_remediation_proposal` *model* tool; we deliberately did NOT add one. The
   proposal is derived by a pure function AFTER the investigation, from the
   offending records the `Diagnosis` already carries. Stronger guardrail than §5,
   and mirrors `submit_diagnosis → build_diagnosis`. (Deviation from §5, on purpose.)
2. **Unify on restore-to-baseline.** The insight that generalized all 7: restoring
   an offending voucher to its seed baseline is a valid fix for EVERY defect class
   (the gate passed on the baseline). Two primitives, both a baseline diff —
   **RESTATE** a changed field (6 of 7; the diff discovers *which* column moved, so
   no per-defect column code) and **REMOVE** duplicated lines (`duplicate_voucher`).
   `add`-a-line is a third primitive no seeded fixture needs, so it is deliberately
   unimplemented (raises clearly) — no untested code path.

**Deviation from DESIGN §6 wording:** for `unbalanced_voucher`, §6 says "add a
balancing line." We RESTATE the altered line instead (slug `restore_voucher_balance`)
— more faithful, since the seeded defect is an *altered amount*, not a missing line;
adding a plug would leave the wrong figure in place. Restoring reproduces clean exactly.

**How the fix is found without the answer key:** the clean baseline
(`provider.clean_baseline()`, regenerated from the same seed) is a legitimate read
tool — NOT `run_manifest.json` (which the provider still refuses, guardrail #4).

**Pick up next (rough priority):**
1. **Phase 4 — scorecard.** The drafter's fix-validity is already proven offline per
   scenario (applying the corrections reproduces the clean baseline). Phase 4 wires
   detect→diagnose→draft→**apply→re-gate** across all 7 and publishes the table.
   The `_apply` helper in `tests/test_remediation.py` is a working reference for the
   apply step (restate + remove, dtype-aware).
2. **Wire drafting into the entrypoint / live staging.** `write_delta` for
   `fin_close.agent.remediation_proposals` is a deferred cluster stub, same as
   `audit.write_delta`. The local path (`write_dry_run`, reused from audit.py) works.
3. **Promote `remediation` to a REQUIRED registry field** (see note below) now that
   all 7 declare one — a small, deliberate tightening pass.

---

## What was built

| Area | Change | Notes |
|------|--------|-------|
| `agent/remediation.py` | NEW — the drafter | `RemediationProposal` + `LineCorrection` (op = restate/remove); `draft_proposal()` dispatches on the registry slug; `_draft_restore` (6 defects) + `_draft_remove_duplicate`; `proposal_to_row` + `write_delta` stub; reuses `audit.write_dry_run` |
| `config/anomaly_registry.yaml` | +`remediation:` slug on all 7 | all 7 wired |
| `agent/registry.py` | `remediation` = OPTIONAL field | `Check.remediation` defaults `""`; validated non-empty-if-present |
| `tests/test_remediation.py` | NEW — ~20 tests | 7× **fix validity** (apply → reproduces clean), 7× action/targets, 7× untouched-rows, 7× impact-sign, flagship detail, guardrails, staging round-trip |
| `tests/test_registry.py` | +2 tests, 1 updated | optional-field accept/reject; happy-path Check now carries the slug |

## Guardrails held
- **Read-mostly (#2):** drafter is pure; no GL mutation; `write_delta` targets
  `fin_close.agent` ONLY — never Silver/Gold/serving. A test asserts drafting
  mutates nothing.
- **Human-in-the-loop (#3):** proposals are `status="proposed"`; the drafter never
  applies them. Applying is Phase 4.
- **Answer key (#4):** unchanged — the baseline is a read tool, the answer key
  remains unreachable through the provider.
- **One defect per run:** the proposal is scoped to one diagnosis.

## The optional-vs-required registry field
`remediation` is OPTIONAL so the Phase-2 registry and its validation-tests stayed
valid without churn. All seven drafters now exist, so this can be promoted to
REQUIRED (move the key from `_OPTIONAL_FIELDS` into `_REQUIRED_FIELDS` in
`agent/registry.py`) — correctly forcing every future check to declare a correcting
action. Expect a handful of the 4-field validation-test YAMLs in `test_registry.py`
to need the extra field at that point. Deferred as a deliberate, separate tightening.

## How to run what exists
```bash
python -m pytest -q            # 153 tests, offline
```

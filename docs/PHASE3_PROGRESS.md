# Phase 3 — Progress & Handoff

Living status for the Phase-3 build (the **remediation drafter**). Read this first
when resuming. Design of record is `DESIGN.md` §4.3 step 4, §5, §6.

_Last updated: flagship complete. `intercompany_out_of_balance` drafts a staged,
human-approvable correcting proposal end-to-end, offline. 128 tests green, no
network, no key._

---

## ▶ RESUME HERE

**State:** Phase 3 flagship done (uncommitted at time of writing — see git status).
`python -m pytest -q` → 128 passing, offline.

**Design decisions locked this session (both flagged as guardrail-affecting, both
approved):**
1. **Deterministic drafter — the model stays fully read-only.** DESIGN §5 lists a
   `stage_remediation_proposal` *model* tool; we deliberately did NOT add one. The
   proposal is derived by a pure function AFTER the investigation, from the
   offending records the `Diagnosis` already carries. Stronger guardrail than §5,
   and mirrors `submit_diagnosis → build_diagnosis`. (Noted as a deviation from §5.)
2. **Flagship-only scope.** Only `restore_intercompany_side` is implemented. The
   other six remediation slugs are DECLARED in the registry but raise
   `UnsupportedRemediation` until the generalization pass (CLAUDE.md: one path
   first, not seven stubs).

**How the fix is found without the answer key:** the clean baseline
(`provider.clean_baseline()`, regenerated from the same seed) is a legitimate read
tool — NOT `run_manifest.json` (which the provider still refuses, guardrail #4).
For "an existing value was altered" defects the exact correction is "restore the
changed lines to their baseline amounts." That is the flagship drafter.

**Pick up next (rough priority):**
1. **Generalize the drafter to the other six** (DESIGN §6). Add a per-slug drafter
   to `_DRAFTERS` in `agent/remediation.py`. Likely reuse of `_restore_from_baseline`
   for `period_cutoff` (shift the mis-cut date/period back) and possibly the
   balance/dimension/completeness defects; `duplicate_voucher` and `unmapped_account`
   need their own small drafters (remove the dup line; map/reclassify the account).
   When all seven carry a `remediation` slug, consider promoting the registry field
   from OPTIONAL to REQUIRED (see note below).
2. **Phase 4 — scorecard.** The drafter's fix-validity is already proven offline per
   scenario (applying the corrections reproduces the clean baseline). Phase 4 wires
   detect→diagnose→draft→**apply→re-gate** across all 7 and publishes the table.
3. **Wire drafting into the entrypoint / live staging.** `write_delta` for
   `fin_close.agent.remediation_proposals` is a deferred cluster stub, same as
   `audit.write_delta`. The local path (`write_dry_run`, reused from audit.py) works.

---

## What was built

| Area | Change | Notes |
|------|--------|-------|
| `agent/remediation.py` | NEW — the drafter | `RemediationProposal` + `LineCorrection`; `draft_proposal()` dispatches on the registry slug; `restore_intercompany_side` implemented; `proposal_to_row` + `write_delta` stub; reuses `audit.write_dry_run` |
| `config/anomaly_registry.yaml` | +`remediation:` slug on all 7 | all declared; only flagship wired |
| `agent/registry.py` | `remediation` = OPTIONAL field | `Check.remediation` defaults `""`; validated non-empty-if-present |
| `tests/test_remediation.py` | NEW — 11 tests | draft shape, **fix validity** (apply → reproduces clean), guardrails, staging round-trip |
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
`remediation` is OPTIONAL this session so the Phase-2 registry and its
validation-tests stay valid without churn. Once all seven drafters exist, promote
it to REQUIRED (move the key from `_OPTIONAL_FIELDS` into `_REQUIRED_FIELDS` in
`agent/registry.py`) — that will (correctly) force every future check to declare a
correcting action. Expect a handful of the 4-field validation-test YAMLs in
`test_registry.py` to need the extra field at that point.

## How to run what exists
```bash
python -m pytest -q            # 128 tests, offline
```

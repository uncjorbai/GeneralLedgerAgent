# Phase 2 — Progress & Handoff

Living status for the Phase-2 build. Read this first when resuming. Plan of
record is `docs/PHASE2_PLAN.md`.

_Last updated: after Step 8 — **Phase 2 is functionally complete offline.** 115
tests green, no network, no key. All 7 defect scenarios are diagnosable through the
tool surface and drive the loop to a correct structured Diagnosis. Only the live
LLM run and the deferred live-Databricks tail remain (both by design)._

---

## ▶ RESUME HERE (Monday handoff)

**State:** Phase 2 offline-complete, committed and pushed to `main` (commit
`cf1db56`, "Phase 2: the Investigator"). Working tree clean. `python -m pytest -q`
→ 115 passing, no network, no key.

**Environment facts a fresh session needs:**
- The upstream Generator repo is on disk at `../GeneralLedgerGenerator` with real
  parquet + answer keys in its `out/`. Phase 2 was built against it *offline*.
- Fixtures under `tests/fixtures/gl/` are committed and self-contained; regenerate
  with `python tests/fixtures/build_fixture.py` (reads the Generator; byte-stable).
- `anthropic` and `databricks-sdk` are NOT installed (both lazy-imported, live-only).
  Offline dev needs only `pip install -r requirements-dev.txt`.
- No API key anywhere; `agent/llm.py` is fail-closed (no key ⇒ raises, never spends).

**Pick up one of these (in rough priority):**
1. **Phase 3 — remediation drafter.** The next phase (DESIGN §8, §6). Add a staged
   `stage_remediation_proposal` writing to `fin_close.agent.*` staging ONLY (never
   Silver/Gold). Per-defect correcting actions are in DESIGN §6. Diagnosis already
   carries the offending records to drive it. Keep the one-defect-per-run scope.
2. **Live LLM smoke run** (optional, spends API credits on YOUR key — deliberate).
   `pip install anthropic`, set `ANTHROPIC_API_KEY`, then
   `AnthropicModel.from_config()` → `investigate(...)` on a fixture provider. Lower
   cost first by setting a cheaper `agent.llm.model` in `config/system.yaml`.
   This is the first real test of whether the model diagnoses each defect *unaided*.
3. **Live-Databricks tail** (cluster session). The concrete Tier-D live change:
   read the `dq_gate` task's EXIT value on a *successful* run (recon variance),
   not just failed-run output. Plus Phase-1's carried-forward write/E2E/job items.

**Open design notes for whoever resumes:**
- Triage now routes on `deterministic` (not `fails_task`) — Tier-D resolved in the
  logic layer. `fails_task` is retained as metadata / the live trigger-source hint.
- The Phase-2 scenario tests prove tool *sufficiency* + assembly, NOT that the live
  model solves each unaided. That gap is closed by #2 above and the Phase-4 scorecard.
- Nothing is owed to the Generator team: its data is sufficient for all 7 diagnoses.

---

## Status at a glance

| # | Step | State | Notes |
|---|------|-------|-------|
| 1 | `agent/provider.py` (read-only GL surface) + tests | ✅ done | 11 tests; answer-key fenced; `spark_provider` stubbed |
| 2 | Committed fixture `tests/fixtures/gl/` | ✅ done | faithful slice of real Generator output |
| 3 | `verdict.py` structured-exit source (Tier-D) | ✅ done | `verdict_from_exit`; triage now routes on `deterministic`; flagship routes E2E |
| 4 | `agent/tools.py` (§5 read tools + `log_decision`) + `agent/trace.py` | ✅ done | 15 tests; schemas+dispatcher+auto-logging; manual-loop architecture |
| 5 | `agent/investigate.py` (bounded tool-use loop, mock client) | ✅ done | 7 tests; injected ModelClient; bound+escalation; parallel/error paths |
| 6 | `agent/diagnosis.py` (structured diagnosis + narrative) | ✅ done | 8 tests; terminal `submit_diagnosis` tool; deterministic grounding + graded fields |
| 7 | `agent/llm.py` (live Anthropic adapter, config-gated) | ✅ built | 11 tests (fake client); NOT run live; key-safe (see below) |
| 8 | Generalize to the other six scenarios | ✅ done | 18 tests (7×2 scenario + enrichments); 3 generic tool enrichments; 7/7 diagnosable |

## How to run what exists

```bash
python -m pytest -q            # 50 tests, offline
```

## Fixture provenance (Step 2) — ⚠️ Tier: verify

`tests/fixtures/gl/` was derived by filtering `../GeneralLedgerGenerator/out/`
down to a single shared voucher set V (see Step 8 notes): every
intercompany-touching voucher, every scenario's defect vouchers, and a
deterministic sample of ordinary ones. `clean/` and all 7 scenario dirs carry that
same voucher set (only each scenario's own vouchers are mutated). Result: 61
vouchers / 127 rows (duplicate_voucher: 133, by design), reproducing every
scenario's defect signal exactly.

- Builder: a one-off script (kept in the session scratchpad, not committed) that
  reads the Generator `out/` and writes the slice. Data is committed; the
  Generator is treated as opaque upstream, so tests do **not** depend on it being
  checked out.
- **Before live E2E:** re-verify against a fresh Generator run — same discipline
  as Phase 1's synthesized `failed_run_output.json`. Real Bronze is parquet with
  the same 22-column schema the fixture carries.

## Step 3 notes — Tier-D resolved in the logic layer

- **New verdict source.** `verdict_from_exit(exit_value)` parses the gate's
  `dbutils.notebook.exit()` JSON (`checks[]`) into a `Verdict`. It's the clean
  counterpart to the brittle `verdict_from_error` string parse; the flagship's
  reconciliation variance lives only here. Both feed the same `triage`.
- **Triage predicate changed** (deliberate, supersedes a locked Phase-1 test):
  routing now keys on **`deterministic`**, not `fails_task`. Rationale: a check is
  in `failed_checks` only because a verdict source already observed it fail, so
  "is it real" is settled upstream; triage asks only "is it a deterministic defect
  the Agent handles?" This wakes the Agent for the two reconciliation scenarios
  (intercompany, period_cutoff) without them failing the task. `fails_task` stays
  as metadata + the live trigger-source hint. Test
  `test_known_reconciliation_only_check_escalates` → `..._check_routes`; added a
  fake-registry test for the new branch-4 (known but non-deterministic → escalate).
- **⚠️ Carry-forward for live wiring.** This introduces a verdict source on
  **successful** runs (recon variance, task did not fail). The Phase-1 live path
  only reads *failed*-run output via the Jobs API. The live trigger/reader must
  also fetch the `dq_gate` task's **exit value** (`notebook_output.result`) on a
  succeeded run. This is the concrete Tier-D live change, deferred to the cluster
  session — the offline logic is done and proven.

## Step 7 notes — the live adapter, and running it later

`agent/llm.py` is the real `ModelClient`, a drop-in for the tests' scripted mock
(the loop can't tell them apart). It was **built and unit-tested but never run
live** — no API key was used, nothing was billed.

**Key discipline (deliberate, for GitHub safety):**
- No key in the repo, ever. Credentials are read from the environment
  (`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`) or a Databricks secret. Verified:
  a tree scan finds no key patterns; the config carries only model/max_tokens.
- **Fail-closed:** `build_anthropic_client()` raises `LLMConfigError` when no
  credential is present, *before* any request. A clone (or CI without the secret)
  cannot call the API and cannot incur charges. Confirmed by test + a live check.
- **Import-safe:** importing `agent.llm` does not import the `anthropic` SDK or
  make a call; the SDK is imported lazily only when a client is actually built.

**To run a live investigation later (a deliberate act, spends API credits):**
1. `pip install anthropic`
2. set `ANTHROPIC_API_KEY` in your environment (never commit it)
3. construct `AnthropicModel.from_config()` and pass it to `investigate(...)`.
   Optionally lower cost first by setting a cheaper `agent.llm.model` in
   `config/system.yaml` (e.g. a Sonnet/Haiku model).

## Step 8 notes — generalization to 7/7

- **Unified fixture.** `tests/fixtures/gl/` now holds one shared voucher set (61
  vouchers) across `clean` + all 7 scenarios, so every scenario's clean baseline
  contains its own defect vouchers to diff against. Rebuilt by the same
  filter-the-real-`out/` script. Intercompany's group-level netting is preserved
  (all IC-touching vouchers are in the set).
- **Three generic tool enrichments** (approved as generic, not per-defect hacks):
  1. `group_by` now returns `line_count` — exposes `duplicate_voucher` (a balanced
     duplicate has net 0 but a higher line count than baseline).
  2. `filters` accepts `null` to match MISSING values (null or blank) — exposes
     `missing_entity_or_period` (null company_id) and blank-department cases.
  3. `accounting_date` is now returned and filterable — exposes `period_cutoff`
     (a date outside its close period).
- **⚠️ No Generator data is missing.** Every column the agent needs already exists
  in the Bronze parquet; all 7 defects are diagnosable from `gl_journal_lines` +
  `chart_of_accounts` + the departments dimension. The gaps were entirely in our
  own tool surface (now closed), NOT upstream — so there is **nothing to request
  from GeneralLedgerGenerator** for Phase 2. (The already-documented Tier-C
  niceties in PIPELINE_CONTRACT — persisted failing rows / run context — remain
  optional robustness for later, not blockers.)
- **What the offline scenario tests prove (and don't).** They prove the fixtures
  are faithful, the tool surface is *sufficient* to recover each defect's
  offending records, and the loop+diagnosis assemble correctly per scenario. They
  do NOT prove the live model finds each unaided — that is the live run (your key)
  and the Phase-4 scorecard.

## Guardrails (unchanged)
- Read-only investigation; writes only the decision trace. Never Silver/Gold.
- Answer key unreachable during investigation — enforced in `provider.py`.
- Bounded loop; escalate on exhaustion. Deterministic → agent, transient → retry.

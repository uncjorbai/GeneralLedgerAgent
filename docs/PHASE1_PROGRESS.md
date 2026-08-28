# Phase 1 — Progress & Handoff

Living status doc for the Phase-1 build. Read this first when resuming.
Companion to `docs/PHASE1_PLAN.md` (the plan of record) — this tracks *where we
actually are* and hands off the next step.

_Last updated: after Step 8 skeleton — **Phase 1 logic is complete and runs
end-to-end locally.** Only the live-Databricks tail (write, E2E, job+trigger)
remains, and it is fenced off below for a cluster session._

---

## Status at a glance

| # | Step | State | Notes |
|---|------|-------|-------|
| 1 | Scaffold (`agent/` pkg, `tests/`, `conftest.py`, requirements) | ✅ done | folded into step 4 |
| 2 | `docs/PIPELINE_CONTRACT.md` | ✅ done | |
| 3 | `config/anomaly_registry.yaml` | ✅ done | 7 checks |
| 4 | `agent/registry.py` + tests | ✅ done | 13 tests |
| 5 | `agent/verdict.py` + tests | ✅ done | 12 tests; parser quarantined |
| 6 | `agent/triage.py` + tests | ✅ done | 7 tests; escalate-wins locked |
| 7 | `agent/audit.py` (to_row + dry-run) + tests | ✅ local done | Delta `write_delta()` = marked stub (cluster) |
| 8 | `agent/entrypoint.py` + tests | ✅ skeleton done | CLI dry-run works E2E; notebook wrapper = cluster |
| 9 | Manual end-to-end | ⛔ blocked | needs cluster + a reseeded failing run |
| 10 | `workflow/create_agent_job.py` (job + trigger) | ⛔ blocked | needs workspace |

**Everything through step 8's *skeleton* is pure laptop Python. Steps 7-write, 9,
10 need a live Databricks connection and a real failed run.**

## How to run what exists

```bash
pip install -r requirements-dev.txt   # one-time (pytest + pyyaml)
python -m pytest -q                    # 39 tests, ~0.2s, no network
```

Run the whole Phase-1 agent locally (dry-run — writes to gitignored `out/`):

```bash
python -m agent.entrypoint \
  --run-output tests/fixtures/failed_run_output.json \
  --scenario unbalanced_voucher \
  --generator-run-id 123456789 \
  --gl-table gl_journal_lines__unbalanced_voucher
```

Prints the triage summary and appends the audit row to `out/triage_log.jsonl`.

Environment as of this writing: Python 3.12, `pytest` + `pyyaml` installed.
`databricks-sdk` is **declared** in `requirements.txt` but **not installed** — the
live path imports it lazily, so local tests don't need it.

---

## Module contracts (the built Phase-1 surface)

### `agent/registry.py`
- `load_registry(path=None) -> Registry` — loads/validates `config/anomaly_registry.yaml`; raises `RegistryError` on any malformed file.
- `Registry.get(name) -> Check | None` — **None = unknown check** (triage reads as not-a-registered-defect).
- `Registry.task_failing_checks() -> set[str]` — checks with `fails_task=True` (the 5 `dq_gate` ones today).
- `Registry.by_defect_class(scenario) -> Check | None`
- `Check` (frozen): `.name .gate .defect_class .deterministic .fails_task`
  - `gate ∈ {"dq_gate", "reconciliation"}`.

### `agent/verdict.py`
- `parse_failed_checks(text) -> frozenset[str] | None` — the **one brittle function**; None when no parseable `Failing checks: [...]` marker.
- `verdict_from_error(error=None, error_trace=None) -> Verdict`
- `fetch_verdict(run_id, output_getter=None) -> Verdict` — live path; inject `output_getter` in tests.
- `Verdict` (frozen): `.failed_checks: frozenset[str]`, `.parsed: bool`, `.evidence: str`
  - **`parsed` is the key field:** `True` ⇒ we recovered a structured failing-check list (deterministic); `False` ⇒ no marker (infra crash ⇒ transient).

### `agent/triage.py`
- `triage(verdict, registry, *, scenario, generator_run_id, gl_table) -> TriageResult` — **pure**; no clock/network/env.
- Decision constants (import these; don't hardcode strings): `FAILURE_DETERMINISTIC/TRANSIENT/UNKNOWN`, `DECISION_ROUTE/LEAVE/ESCALATE`.
- `TriageResult` (frozen): `.failure_class .triage_decision .failed_checks .unknown_checks .gate_types .rationale .evidence` + passthrough `.scenario .generator_run_id .gl_table`.
- **Locked predicate** (escalate-wins precedence):
  1. `not verdict.parsed` ⇒ transient ⇒ leave_to_existing_path.
  2. any failing check unknown to registry ⇒ unknown ⇒ escalate (**wins over routing**).
  3. all known and ≥1 has `deterministic and fails_task` ⇒ deterministic ⇒ route_to_agent.
     (Routing keys on `fails_task`, NOT gate — so a Tier-D reconciliation check made task-failing routes without editing triage.)
  4. all known but none task-failing (edge; shouldn't happen today) ⇒ unknown ⇒ escalate.
- Does NOT set `agent_run_id`, `detected_at`, `signal_source` — those are step 7/8.

### `agent/audit.py`
- `to_row(result, *, agent_run_id, detected_at, signal_source) -> dict` — pure map to the `triage_log` schema. Arrays are lists; `detected_at` datetime → ISO string; `rationale` **is** populated.
- `write_dry_run(row, path) -> Path` — append one JSON line locally (no Databricks).
- `write_delta(row, *, catalog, schema, table, spark=None)` — **marked stub** (`NotImplementedError`); intended DDL/impl in its docstring. CLUSTER-ONLY.
- `SIGNAL_JOBS_API = "jobs_api"`.

### `agent/entrypoint.py`
- `investigate(*, run_output, scenario, generator_run_id, gl_table, agent_run_id=None, now=None) -> (TriageResult, row)` — the orchestration; `agent_run_id`/`now` injectable for tests. `run_output` is a dict with `error`/`error_trace`.
- `main(argv=None)` — the CLI shell (dry-run). `local-<uuid>` agent_run_id, UTC now.

---

## Cluster session — the remaining live-Databricks tail

Phase 1 logic is done and proven locally. What's left ALL needs a live workspace
with the `fin_close` profile and a real failed run. In rough order:

1. **Get a real failed run.** Reseed a `dq_gate` defect (e.g. unbalanced voucher)
   so the gate task fails. This is the prerequisite for everything below.
2. **⚠️ Re-validate the parser fixture.** Capture the real
   `jobs.get_run_output(run_id)` and diff `.error` / `.error_trace` against
   `tests/fixtures/failed_run_output.json`. If the real format differs, fix the
   regex in `agent/verdict.py` (the whole reason it's quarantined). Update the
   fixture from the real capture.
3. **Wire live auth.** `agent/verdict._default_output_getter` — use the
   `fin_close` profile from `config/system.yaml` instead of ambient SDK auth.
   `pip install databricks-sdk` (already declared in requirements).
4. **Implement `write_delta()` (step 7-write).** Fill in the stub: create
   `fin_close.agent` if absent, build the DataFrame with the DDL in the
   docstring, append to `triage_log`. Verify a row lands and reads back.
5. **Notebook wrapper + `main` live branch (step 8).** A thin serverless notebook
   that reads widgets (`generator_run_id`, `scenario`, `gl_table`, its own
   `job_run_id`), calls `fetch_verdict(run_id)` → `triage` → `write_delta`.
6. **Manual E2E (step 9).** Failed run → run the wrapper → confirm the correct
   `triage_log` row (deterministic → route_to_agent).
7. **`workflow/create_agent_job.py` (step 10).** The Agent's own job + a
   table-update trigger on `fin_close.gold.remediation_log`; prove it fires
   automatically. Manual-first, then automate.

Depends on the **Tier-A brace** from `collaboration.md` (run id on the
`remediation_log` row) for exact run correlation; until then, "latest failed run"
is the safe fallback (job is `max_concurrent_runs=1`).

---

## Open items / carry-forward

- ⚠️ **Synthesized fixture (Tier: verify).** `tests/fixtures/failed_run_output.json`
  is reconstructed from `dq_gate.py:205`, **not** a real capture (no failed run
  existed in the workspace at build time). **Before Phase-1 E2E (step 9):** trigger
  a real failure, capture `jobs.get_run_output(run_id)`, and confirm `.error` /
  `.error_trace` match the fixture's shape. Fix `verdict.py`'s regex if the real
  format differs — that's the whole reason it's quarantined there.
- **`databricks-sdk` not installed** locally; only needed for the live path
  (`fetch_verdict` default getter, step 7 write, steps 9–10).
- **Auth profile TODO** in `verdict._default_output_getter`: wire the `fin_close`
  profile from `config/system.yaml` instead of ambient SDK auth.
- **Tier D (blocks Phase-2 flagship):** `intercompany_out_of_balance` is a
  `reconciliation` check → never fails the task → never wakes the Agent today.
  Phase-2 decision, unchanged. See `PIPELINE_CONTRACT.md`.

## Guardrails (unchanged — hold firmly)
- Agent writes ONLY to `fin_close.agent.*`; never Silver/Gold/serving.
- Agent NEVER reads the answer key (`run_manifest.json`) during investigation.
- Deterministic defects → agent; transient → existing retry path. Never blur.
- Every decision logged to the append-only `triage_log`.

## Files added in steps 4–8 (to commit)
```
agent/__init__.py  agent/registry.py  agent/verdict.py  agent/triage.py
agent/audit.py  agent/entrypoint.py
tests/test_registry.py  tests/test_verdict.py  tests/test_triage.py
tests/test_audit.py  tests/test_entrypoint.py
tests/fixtures/failed_run_output.json
conftest.py  requirements.txt  requirements-dev.txt
docs/PHASE1_PROGRESS.md
```
(`out/` is gitignored — the dry-run log is a local artifact, not committed.)

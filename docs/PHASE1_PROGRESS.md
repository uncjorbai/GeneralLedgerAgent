# Phase 1 — Progress & Handoff

Living status doc for the Phase-1 build. Read this first when resuming.
Companion to `docs/PHASE1_PLAN.md` (the plan of record) — this tracks *where we
actually are* and hands off the next step.

_Last updated: after Step 5 (`verdict.py`)._

---

## Status at a glance

| # | Step | State | Notes |
|---|------|-------|-------|
| 1 | Scaffold (`agent/` pkg, `tests/`, `conftest.py`, requirements) | ✅ done | folded into step 4 |
| 2 | `docs/PIPELINE_CONTRACT.md` | ✅ done | |
| 3 | `config/anomaly_registry.yaml` | ✅ done | 7 checks |
| 4 | `agent/registry.py` + tests | ✅ done | 13 tests |
| 5 | `agent/verdict.py` + tests | ✅ done | 12 tests; parser quarantined |
| **6** | **`agent/triage.py` + tests** | **⬅ NEXT** | pure Python; design decided below |
| 7 | `agent/audit.py` | ⬜ todo | record-model = local; the Delta write = needs Databricks |
| 8 | `agent/entrypoint.py` (+ notebook wrapper) | ⬜ todo | skeleton local; live path needs Databricks |
| 9 | Manual end-to-end | ⛔ blocked | needs cluster + a reseeded failing run |
| 10 | `workflow/create_agent_job.py` (job + trigger) | ⛔ blocked | needs workspace |

**Everything through step 8's *skeleton* is pure laptop Python. Steps 7-write, 9,
10 need a live Databricks connection and a real failed run.**

## How to run what exists

```bash
pip install -r requirements-dev.txt   # one-time (pytest + pyyaml)
python -m pytest -q                    # 25 tests, ~0.2s, no network
```

Environment as of this writing: Python 3.12, `pytest` + `pyyaml` installed.
`databricks-sdk` is **declared** in `requirements.txt` but **not installed** — the
live path imports it lazily, so local tests don't need it.

---

## Module contracts (what step 6 builds on)

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

---

## NEXT: Step 6 — `agent/triage.py`

**Goal:** turn a `Verdict` + `Registry` into the Phase-1 decision. Pure Python,
fully unit-testable. This is the Q10 predicate from `PHASE1_PLAN.md`.

### Inputs
- `verdict: Verdict` (from step 5)
- `registry: Registry` (from step 4)
- context passed through for the audit row: `scenario`, `generator_run_id`, `gl_table`.

### The predicate (decided — do not re-litigate)
Resolve each failing check through `registry.get(name)`, then:

1. **`verdict.parsed is False`** (no parseable check)
   ⇒ `failure_class = "transient"`, `triage_decision = "leave_to_existing_path"`.
2. **parsed, and ≥1 failing check is a known `dq_gate` check with `fails_task=True`**
   ⇒ `failure_class = "deterministic"`, `triage_decision = "route_to_agent"`.
3. **parsed, but ≥1 failing check is unknown to the registry** (name not found)
   ⇒ `failure_class = "unknown"`, `triage_decision = "escalate"`. Unrecognized
   defect: surface to a human, don't silently drop. (An unknown check name means
   the pipeline grew a check the registry hasn't catalogued yet.)
4. **Edge — parsed, all checks known but none task-failing** (shouldn't occur via a
   task failure today, since only `fails_task` checks raise): treat as `unknown` /
   `escalate`. Note it; don't crash.

> Order matters: check "unknown check present" as escalate BEFORE concluding
> deterministic, OR decide the precedence explicitly and test it. Recommended
> precedence: unknown-check-present ⇒ escalate wins over route_to_agent, so a
> registry gap is never silently routed. Confirm this when building.

### Output
A `TriageResult` (frozen dataclass) carrying the fields the audit row needs
(see the `triage_log` schema in `PHASE1_PLAN.md`):
`failure_class`, `triage_decision`, `failed_checks` (sorted list), `gate_types`
(from registry, per check), `signal_source`, `evidence` (from `verdict.evidence`).
Plus the passed-through `scenario`, `generator_run_id`, `gl_table`.
(`agent_run_id`, `detected_at` get stamped at write time in step 7.)

### Tests to write
- transient (parsed=False) ⇒ leave_to_existing_path
- single known dq_gate check ⇒ deterministic ⇒ route_to_agent
- unknown check name ⇒ unknown ⇒ escalate
- mixed known + unknown ⇒ escalate (precedence)
- reconciliation-only known check (edge 4) ⇒ escalate/unknown
- gate_types resolved correctly from the registry

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

## Files added in steps 4–5 (to commit)
```
agent/__init__.py  agent/registry.py  agent/verdict.py
tests/test_registry.py  tests/test_verdict.py  tests/fixtures/failed_run_output.json
conftest.py  requirements.txt  requirements-dev.txt
docs/PHASE1_PROGRESS.md
```

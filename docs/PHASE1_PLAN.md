# Phase 1 — Triage + Agent-Task Wiring (Plan of Record)

**Goal:** prove the *integration*, not the intelligence.
`close fails → Agent wakes → Agent obtains the verdict → Agent classifies
deterministic vs transient → Agent logs the full failure context to its own
audit trail`. **Zero LLM calls.** Near-zero edits to `GeneralLedgerGenerator`
(one approved brace — see Tier A below).

This document is the agreed plan. It supersedes ad-hoc discussion; changes to it
are deliberate.

---

## Locked decisions (this discovery session)

| # | Decision | Outcome |
|---|----------|---------|
| Q1 | Where triage runs | Agent-side. The gate destroys its structured verdict on failure (raises before `notebook.exit`), so the Agent derives the verdict from what the pipeline already emits. |
| Q2 | Deterministic-vs-transient signal | Presence of a named DQ check in the failure output ⇒ deterministic. A task that failed with **no** parseable check verdict ⇒ transient. Do not classify exception strings by content. |
| Q3 | Phase-1 blast radius | Agent-side only; do **not** restructure the Generator's job graph. |
| Q4/Q7 | Audit trail | Append-only Delta table `fin_close.agent.triage_log`, owned by the Agent. Seed of the future knowledge base. |
| Q5 | How the Agent gets the verdict | Read the failed run's `dq_gate` task output via the Databricks Jobs API; parse failing checks against the registry. Parsing is quarantined in one module. |
| Q6 | Product boundary | All code lives in **this** repo. The Agent runs as its **own** Databricks job. The Generator never learns the Agent exists. |
| Q8 | Activation | Table-update trigger on `fin_close.gold.remediation_log` (a row the Generator already writes on every dq_gate failure). Coarse wake → fine triage. |
| Q9 | Registry | `config/anomaly_registry.yaml` — check → `{gate, defect_class, deterministic, fails_task}`. |
| Q10 | Triage predicate + record | `≥1 registry dq_gate check ⇒ deterministic ⇒ route_to_agent`; `failed, no parseable check ⇒ transient ⇒ leave_to_existing_path`; `ambiguous ⇒ unknown ⇒ escalate`. Record schema below. |

### The A3 ↔ A5 principle
Keep the Generator **agnostic**: do not reach into it as a shortcut. But the
pipeline **will** evolve toward production deliberately. So we maintain a
**contract** (`docs/PIPELINE_CONTRACT.md`) of what the Agent needs; Phase 1
satisfies it against the system *as-is*, and the Generator is improved later on
purpose, not hacked now.

### Generator-change tiering (reviewed early, per request)
- **Tier A — apply now (approved brace):** add the failed run's **run id** to
  `remediation_log` so the Agent never guesses which run failed.
- **Tier B/C/D — after a working POC:** durable structured gate verdict;
  persisted failing rows; emitted run context; and the reconciliation-routing
  decision (Tier D — *blocks the Phase-2 flagship scenario*; see contract).

---

## Triage record — `fin_close.agent.triage_log` (append-only)

| Column | Phase 1 | Notes |
|--------|:------:|-------|
| `agent_run_id` | ✅ | this Agent invocation |
| `generator_run_id` | ✅ | the failed pipeline run (from the Tier-A brace) |
| `detected_at` | ✅ | timestamp |
| `scenario` | ✅ | from the failure-log row |
| `gl_table` | ✅ | e.g. `gl_journal_lines__unbalanced_voucher` |
| `failed_checks` | ✅ | array parsed from the gate output |
| `gate_types` | ✅ | dq_gate / reconciliation, from registry |
| `failure_class` | ✅ | deterministic \| transient \| unknown |
| `triage_decision` | ✅ | route_to_agent \| leave_to_existing_path \| escalate |
| `signal_source` | ✅ | jobs_api \| remediation_log |
| `evidence` | ✅ | the parsed run-output snippet |
| `finding` / `rationale` / `proposal` | ⬜ | reserved; filled once the agent has a brain (Phase 2/3) |

One table, growing in richness by phase — this is also the A5 knowledge base.

---

## Step-by-step build

1. **Scaffold** this repo (package layout; deps `databricks-sdk`, `pyyaml`;
   `pyspark` only inside the notebook wrapper).
2. **`docs/PIPELINE_CONTRACT.md`** — pin the consumed signals + off-limits file
   + the deferred wishlist. *(done alongside this plan)*
3. **`config/anomaly_registry.yaml`** — the 7 entries. *(done)*
4. **`agent/registry.py`** — load + validate the registry. + unit tests.
5. **`agent/verdict.py`** — Jobs-API adapter: given `generator_run_id`, pull the
   `dq_gate` task output, parse failing checks. Brittle parsing quarantined
   here. + unit tests against a captured sample.
6. **`agent/triage.py`** — the Q10 predicate. + unit tests.
7. **`agent/audit.py`** — create `fin_close.agent` if absent; append the record.
8. **`agent/entrypoint.py` (+ notebook wrapper)** — orchestrate 5→6→7, log the
   received context, exit. No LLM. This *is* the Phase-1 "agent".
9. **Manual end-to-end** — reseed a defect so the gate fails → Generator writes
   the `remediation_log` row + failed run → invoke the entrypoint manually →
   confirm it reads the verdict, classifies `deterministic`, writes a correct
   audit row.
10. **`workflow/create_agent_job.py`** — the Agent's own job + the table-update
    trigger; prove it fires automatically. Manual-first de-risks before
    automation.

---

## Requirements
- Databricks auth with **read** on `fin_close` + Jobs API and **write** on
  `fin_close.agent`. (Reuse profile `fin_close` for Phase-1 dev.)
- One real failed run captured to freeze the Jobs-API output shape as a test
  fixture.
- `gold.remediation_log` must exist before the trigger can attach (created on
  the first failure by the Generator's `remediate` task).
- **No Anthropic API key** in Phase 1 (no LLM).

## Assumptions
- Generator job is `max_concurrent_runs=1` (confirmed) — the Tier-A run id makes
  correlation exact regardless.
- `scenario` ↔ failing check is 1:1 in this synthetic harness (confirmed).
- The `dq_gate` exception/output format is stable enough to parse; its fragility
  is quarantined in `verdict.py` and documented in the contract.

## Runtime shape
Logic is plain, locally-testable Python (runs on a laptop, no Databricks). The
live path is a thin Databricks **serverless notebook** wrapper (DESIGN §9). Same
code, two entry points.

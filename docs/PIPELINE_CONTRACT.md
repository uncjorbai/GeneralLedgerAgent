# Pipeline Contract — what the Agent consumes from the Generator

`GeneralLedgerAgent` is a **separate product** built for the
`GeneralLedgerGenerator` pipeline. It treats that pipeline as an opaque upstream
and depends only on the signals named here. This file is the negotiated
interface: if the Generator changes one of these, the Agent must be updated in
lockstep.

## Signals the Agent consumes today (system as-is)

| Signal | Where | How the Agent uses it | Stability |
|--------|-------|-----------------------|-----------|
| Failure-log row | `fin_close.gold.remediation_log` (Delta; `remediate` task appends on every `dq_gate` failure) | **Activation trigger.** Its arrival wakes the Agent. Carries `logged_at`, `scenario`, `action`. | Stable schema; **coarse** (no failing-check identity, no run id — see Tier A). |
| Failed-run task output | Databricks **Jobs API**, `dq_gate` task of the failed run | Source of the **failing-check identity**, parsed from the exception message `"...Failing checks: [...]"` (`dq_gate.py:205`) + stdout. | **Brittle** — depends on an exception *string format*. Parsing is quarantined in `agent/verdict.py`. |
| Bronze GL table | `fin_close.bronze.gl_journal_lines__{scenario}` | Read-only, for Phase-2 investigation. | Stable naming. |

## Off-limits — never read

| Artifact | Why |
|----------|-----|
| `run_manifest.json` (`/Volumes/fin_close/bronze/landing/{scenario}/_qa/`) | **The answer key.** Guardrail #4: the Agent is *scored* against this; it must never see it during investigation. Listed only to be explicitly fenced off. |

## Writes the Agent is allowed

Only `fin_close.agent.*` (its own schema — `triage_log`, later staged
proposals). **Never** Silver, Gold, or serving tables (guardrail #2).

---

## Deferred Generator improvements (reviewed early; not a redesign)

Phase 1 works without any of these. They trade brittle inference for clean
signals and unblock later phases. **Additive only.**

### Tier A — approved brace, apply now
- **Run id in `remediation_log`.** Add the Databricks job **run id** as a column
  so the Agent addresses the exact failed run instead of inferring "the latest
  failed run". Removes the correlation guess entirely.
  - Touches: `notebooks/remediate.py` (read the id, write the column) and
    `workflow/create_job.py` (pass `{{job.run_id}}` to the remediate task).

### Tier B — after a working POC
- **Durable structured gate verdict.** Before `dq_gate.py` raises, persist
  `results[]` (check, gate, failures, passed) to `gold.dq_gate_results` (or a
  Volume JSON), keyed by run id. Replaces exception-string parsing with a clean,
  queryable verdict carrying per-check failure counts. *Highest-value item.*

### Tier C — Phase-2 investigation enablers
- **Persist failing rows.** Write the gate's in-memory `details{}` to
  `gold.dq_gate_failures` instead of only printing them.
- **Emit run context** (seed, period, entities) to a known location — **excluding
  the answer key** — to feed DESIGN §5's `get_scenario_context()`.

### Tier D — flag now, decide in Phase 2 ⚠️ (blocks the flagship scenario)
- **Reconciliation checks do not fail the task.** `intercompany_out_of_balance`
  (the Phase-2 MVP scenario) is a `reconciliation` check; those only *print*,
  never `raise`. So today it **never fails the gate task, never writes a
  `remediation_log` row, and never wakes the Agent.** Resolving this — gate
  policy change vs. an alternate Agent trigger — is a Phase-2 decision. No action
  now, but the MVP cannot reach the Agent until it is made.

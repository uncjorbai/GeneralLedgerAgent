# GL Anomaly Investigator — Design Document

> An agentic remediation layer for the Synthetic Lakehouse Pipeline. When the
> DQ gate fails on a **deterministic data defect**, an LLM agent investigates the
> failure, diagnoses the root cause, drafts a proposed fix for human approval,
> and is scored against the pipeline's built-in answer keys.

---

## 1. Context & motivation

The Synthetic Lakehouse Pipeline already generates production-shaped financial
data, injects named defects with answer keys, and enforces a DQ gate that
branches the Databricks Job: on failure, a `remediate` task retries the pipeline
up to 4 times (3 retries).

**The problem this project solves:** retries are the correct response to
*transient* failures (serverless hiccups, timeouts). They are useless against
*deterministic* data defects — an `unbalanced_voucher` fails all four attempts
identically. Retrying deterministic bad data just fails slower.

**The insight:** the gap between "transient failure" and "deterministic data
failure" is exactly where an investigative agent earns its place. Retries handle
infrastructure; the agent handles data. Two failure classes, two responses.

This is deliberately NOT "throw an LLM at everything." The agent is placed
precisely where deterministic logic cannot help, and its output is validated
against ground truth the pipeline already produces.

---

## 2. Goals & non-goals

### Goals
- Detect when a gate failure is a **deterministic data defect** vs a transient error.
- **Investigate** the failure: pull failing records, compare to the clean
  baseline, iteratively decide what to inspect next.
- **Diagnose** the root cause in a controller-ready narrative (what failed, why,
  dollar impact, specific vouchers/accounts involved).
- **Draft** a specific proposed remediation (a correcting entry or fix), staged
  for human approval — never auto-applied to Silver/Gold.
- **Score** the agent against the scenario answer keys: diagnosis accuracy +
  whether the proposed fix, once applied, makes the gate pass on re-run.

### Non-goals (v1)
- No auto-writing to certified Gold/serving tables. Ever. Human-in-the-loop on
  every write.
- No handling of transient/infrastructure failures — those stay with existing retry logic.
- No multi-defect-per-run handling in v1 (each scenario injects exactly one defect; honor that).
- No production/real GL data. Synthetic only.
- No fine-tuning. Prompt + tools + orchestration only.

---

## 3. Success criteria

The centerpiece deliverable is a **scorecard** across all 7 defect scenarios:

| Metric | Definition | Target v1 |
|--------|------------|-----------|
| Detection | Agent correctly routes deterministic defect to itself | 7/7 |
| Diagnosis accuracy | Agent's identified defect type matches the answer key | ≥ 6/7 |
| Remediation validity | Proposed fix, once applied, makes the DQ gate pass on re-run | ≥ 5/7 |
| Closed-loop | Full detect → diagnose → fix → re-gate → pass, unattended except approval | demonstrated on ≥ 3 scenarios |

The closed loop is the proof: the same gate that caught the defect validates the
agent's fix. The system grades its own agent.

---

## 4. Architecture

### 4.1 Where the agent lives
The agent runs as a **notebook task on the gate's failure branch**, inside the
existing Databricks Job — not as an external service. Keeping it in the
orchestrated pipeline is both simpler and a stronger portfolio story (one
coherent system, not a bolt-on).

```
            ┌─────────────┐
            │   DQ Gate   │
            └──────┬──────┘
          pass ┌───┴───┐ fail
               ▼       ▼
        (Silver…)   ┌──────────────────┐
                    │  Failure triage  │
                    └────┬────────┬────┘
             transient   │        │  deterministic data defect
                         ▼        ▼
                   ┌─────────┐  ┌──────────────────────┐
                   │ retry   │  │  GL Anomaly Agent    │
                   │ (x3)    │  │  investigate→diagnose│
                   └─────────┘  │  →draft→score        │
                                └──────────┬───────────┘
                                           ▼
                                  ┌──────────────────┐
                                  │ staged proposal  │
                                  │ + human approval │
                                  └────────┬─────────┘
                                           ▼
                                  apply → re-run DQ gate → pass?
```

### 4.2 Triage: transient vs deterministic
The fork everything hangs off. Cleanest heuristic for v1: **a failure that
reproduces identically across two attempts is deterministic.** Options to
implement, simplest first:
- **Signal-based (preferred):** the DQ gate already knows *which named check*
  tripped and against *which records*. If the failing check is one of the 7
  data-defect checks (balance, dedupe, referential integrity, dimension
  integrity, completeness, reconciliation), route to the agent immediately —
  these are definitionally deterministic. Transient errors surface as
  task/infra exceptions, not check failures.
- **Reproduction-based (fallback):** run the check twice; identical failure ⇒
  deterministic.

Prefer the signal-based route — the gate already emits the verdict; use it.

### 4.3 The agent loop
Four stages. The investigate stage is the agentic core (iterative, tool-driven).

1. **Detect** — read the gate verdict: which check failed, which
   tables/records. No LLM needed; this is structured input to the agent.
2. **Investigate** — the agent, given the failing check, decides what to query.
   Pulls failing records, pulls the clean baseline for the same seed/period,
   diffs them, drills into the specific voucher/account/dimension. Iterative:
   next query depends on last result.
3. **Diagnose** — produce a controller-ready narrative: failed check, root
   cause, dollar impact, offending vouchers/accounts, confidence.
4. **Draft** — propose the specific correcting action for this defect class,
   written to a **staging location** as a proposal object. Never applied
   automatically.

### 4.4 Guardrails (non-negotiable)
- **Read-mostly tool surface.** The agent queries freely; it writes only
  *proposals* to a staging table/path. No write access to Silver, Gold, or serving.
- **Human-in-the-loop on apply.** A person approves the staged proposal before
  it's applied and the gate re-runs.
- **Decision log.** Every agent action — query issued, result summary,
  conclusion, why — is logged to an append-only trace. This is both observability
  and interview gold (walk someone through the reasoning trace).
- **Scoped to one defect per run** in v1 (matches the scenario design).
- **Bounded iterations.** Cap investigation steps (e.g. ≤ 10 tool calls) to
  prevent runaway loops; if unresolved, escalate to human with what it found.

---

## 5. Tool interface (agent's capabilities)

Keep tools tight and mostly read-only. Suggested set:

| Tool | Purpose | Access |
|------|---------|--------|
| `get_gate_verdict()` | Which check failed, on which records/tables | read |
| `query_failing_table(table, filter)` | Inspect the bad data | read |
| `query_clean_baseline(table, filter)` | Same query against clean baseline | read |
| `get_chart_of_accounts()` | Account schema + classification | read |
| `get_dimensions()` | Departments / cost centers | read |
| `get_scenario_context()` | Period, entities, seed (NOT the answer key) | read |
| `stage_remediation_proposal(proposal)` | Write a proposal to staging | write-staging-only |
| `log_decision(step, query, finding, rationale)` | Append to decision trace | write-log-only |

**Important:** the agent must NOT have access to the answer key
(`run_manifest.json`) during investigation — that's the ground truth it's being
scored against. The answer key is used by the *scorer*, after the fact, not by
the agent.

---

## 6. Per-defect remediation reference

Each of the 7 scenarios has a known correcting action. The agent should derive
these from investigation, but here's the ground truth for building/validating:

| Scenario | Check tripped | Correct remediation |
|----------|---------------|---------------------|
| `unbalanced_voucher` | balance | Add balancing line so Σdebit = Σcredit |
| `duplicate_voucher` | dedupe | Remove the duplicate posting |
| `unmapped_account` | referential integrity | Map account to schema (or reclassify) |
| `missing_department` | dimension integrity | Populate the required dimension |
| `missing_entity_or_period` | completeness | Populate null entity/period |
| `period_cutoff` | reconciliation | Shift mis-cut date back into correct period |
| `intercompany_out_of_balance` | reconciliation | Restore the altered intercompany side |

Build order recommendation: start with `intercompany_out_of_balance` (most
impressive to diagnose, most "senior accountant" in feel), nail it end-to-end,
then generalize.

---

## 7. The scorecard (centerpiece deliverable)

An eval harness that, for each scenario:
1. Runs the pipeline with the defect injected → gate fails.
2. Invokes the agent → captures diagnosis + staged proposal.
3. Scores diagnosis vs the answer key (correct defect type? correct records?).
4. Applies the approved proposal.
5. Re-runs the DQ gate → records pass/fail.

Output a table: scenario × {detected, diagnosis correct, fix valid, gate passes
on re-run}. Publish it at the top of the README. This is the artifact that turns
"I built an agent" into "here's proof it correctly handles 7/7 seeded failures."

---

## 8. Build phases

- **Phase 1 — Wiring.** Triage at the gate; agent task fires on deterministic
  failure and receives context. No intelligence yet. De-risk the integration.
- **Phase 2 — Investigator.** Tools + investigation loop. One scenario
  (`intercompany_out_of_balance`) end-to-end, diagnosis narrative genuinely good.
  Then generalize to the other six. **This is the MVP.**
- **Phase 3 — Drafter.** Agent proposes staged remediations per defect class.
- **Phase 4 — Scorecard.** Eval harness across all 7; publish the table.
- **Phase 5 — Docs.** README as portfolio piece: story, architecture, guardrails, scorecard.

Phases 1–2 = minimum viable portfolio piece, impressive on its own. 3–4 = what
makes it exceptional.

---

## 9. Tech notes
- **LLM:** Anthropic API (Claude). Use tool-use / function-calling for the tool
  interface above. Keep the system prompt focused: the agent is a forensic
  accountant investigating a failed close.
- **Runtime:** Databricks serverless notebook task, consistent with the existing pipeline.
- **Secrets:** API key via Databricks secrets, never committed (mirror the
  existing OAuth-profile discipline in the repo).
- **Determinism:** the clean baseline regenerates from the same seed — lean on
  this for exact diffs.
- **Observability:** decision log is append-only; consider a simple markdown or
  Delta table trace per run.

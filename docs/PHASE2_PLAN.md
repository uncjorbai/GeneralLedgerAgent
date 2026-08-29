# Phase 2 — The Investigator (Plan of Record)

**Goal:** give the agent a brain. On a routed deterministic defect, the agent
**investigates** (iterative, tool-driven), then **diagnoses** in a
controller-ready narrative — proven end-to-end on the flagship
`intercompany_out_of_balance`, then generalized. This is the **MVP** (DESIGN §8).

Scope ends at a *genuinely good diagnosis*. The staged **drafter**
(`stage_remediation_proposal`) is Phase 3 — we hold the phase line (CLAUDE.md).

This document is the agreed plan; changes to it are deliberate. Companion to
`docs/PHASE2_PROGRESS.md` (living status).

---

## The discovery that shapes this phase

The Generator repo is checked out alongside this one
(`../GeneralLedgerGenerator`) **with real generated parquet and answer keys in
`out/`**. So Phase 2 is built and tested **entirely offline against real data**,
the same discipline as Phase 1 (pure/testable core; live-Databricks tail
deferred). No cluster, no API key required to build the investigator.

A faithful, committed fixture slice lives in `tests/fixtures/gl/` (derived by
filtering the real `out/` to every intercompany-touching voucher plus a
deterministic sample of unrelated ones; see the builder note in
`PHASE2_PROGRESS.md`). Same fidelity caveat as Phase 1's synthesized fixture:
re-verify against a fresh Generator run before live E2E.

---

## Locked decisions (this session)

| # | Decision | Outcome |
|---|----------|---------|
| P2-Q1 | **Tier-D trigger** — how the flagship (a reconciliation check that never fails the task) reaches the agent | **Structured-verdict source.** `dq_gate.py:210` already returns `dbutils.notebook.exit()` JSON carrying `checks[]` incl. the reconciliation variance. Extend `verdict.py` to build a `Verdict` from that structured payload. Intercompany becomes a first-class verdict; triage routes it unchanged (it already keys on `fails_task`). Doubles as the Tier-B "durable structured verdict" win. **No upstream change** — honors the `collaboration.md` boundary. |
| P2-Q2 | **LLM wiring this phase** | **Mock-first.** Build the loop around an *injected model client*; test end-to-end with a scripted mock (no key, no network). The live Anthropic call is a thin, config-gated, lazily-imported adapter — deferred exactly like `databricks-sdk` / `write_delta`. |
| P2-Q3 | **Data access** | All data flows through `agent/provider.py`. `LocalGLProvider` (offline, parquet/CSV) now; `spark_provider` stubbed for the cluster. Guardrail #4 enforced *at the provider*: the answer key path is unresolvable. |
| P2-Q4 | **Phase-2 scope boundary** | detect → investigate → diagnose. The drafter is Phase 3. |

---

## The flagship — ground truth (what the agent must rediscover)

`intercompany_out_of_balance`: two HQ (`USMI`) intercompany management-fee
vouchers had **both legs inflated by 1,500** (answer key: `intercompany_break`,
`alter_amount`, vouchers `USMI260600105` / `USMI260700105`). Each voucher still
balances internally, so the **DQ gate passes**; only the reconciliation
elimination check varies:

| Pair | HQ side | Sub side | Variance |
|------|--------:|--------:|--------:|
| Receivable `A14000` vs Payable `L21500` | 147,000 | 144,000 | **+3,000** |
| IC income `R42000` vs IC expense `X67000` | 147,000 | 144,000 | **+3,000** |

Clean baseline `total_debit` 57,033,128.34 vs dirty 57,036,128.34 = **+3,000**.
Correct remediation (Phase 3): restore the altered HQ legs 13,500 → 12,000.

A good diagnosis names: the failed check, the root cause (HQ side inflated), the
dollar impact (3,000 each pair), the offending vouchers/accounts, and confidence
— derived from investigation, **never** from the answer key.

---

## Tool interface (DESIGN §5) — Phase-2 subset

Read-only surface over the provider, shaped into JSON-serializable results for
the model. Adding a tool is deliberate (CLAUDE.md).

| Tool | Purpose | Phase |
|------|---------|:-----:|
| `get_gate_verdict()` | Which check varied, on which pair/records | 2 |
| `query_failing_table(filter)` | Inspect the bad data | 2 |
| `query_clean_baseline(filter)` | Same query vs the clean baseline (exact diff) | 2 |
| `get_chart_of_accounts()` | Account schema + classification | 2 |
| `get_dimensions()` | Departments / cost centers | 2 |
| `get_scenario_context()` | Period, entities, seed — **NOT the answer key** | 2 |
| `log_decision(step, query, finding, rationale)` | Append to the decision trace | 2 |
| `stage_remediation_proposal(proposal)` | Write a proposal to staging | **3** |

---

## Decision-log / record growth

Phase 1 reserved `finding` / `rationale` / `proposal` on `triage_log`. Phase 2
populates the **investigation trace** (append-only, per DESIGN §4.4 / guardrail
#5): each step's tool call, result summary, and rationale, plus the final
structured **diagnosis** (`failed_check`, `root_cause`, `dollar_impact`,
`offending`, `confidence`) and its narrative. The `proposal` column stays
reserved for Phase 3.

---

## Step-by-step build

1. **`agent/provider.py`** — read-only local GL/COA/dimension access; answer-key
   fenced; Spark impl stubbed. + tests. ✅ *done*
2. **`config` + fixture** — committed `tests/fixtures/gl/` slice. ✅ *done*
3. **`agent/verdict.py` structured source** — build a `Verdict` from the gate's
   `checks[]` exit JSON (P2-Q1); reconciliation variances become verdicts. +
   tests. Keep the brittle string-parse path intact as the fallback.
4. **`agent/tools.py`** — the §5 read tools + `log_decision` over the provider;
   Anthropic tool schemas; a dispatcher. + tests (dispatch, filters, fences).
5. **`agent/investigate.py`** — bounded (≤N) tool-use loop, injected model
   client, forensic-accountant system prompt, every step logged. + tests with a
   scripted mock client end-to-end on the intercompany fixture.
6. **`agent/diagnosis.py`** — assemble the structured diagnosis + narrative from
   the trace. + tests (assert it names the 3,000 variance and both vouchers).
7. **`agent/llm.py`** — live Anthropic adapter: config-gated, lazily imported,
   exercised only when a key is present. Stubbed/skipped in CI.
8. **Generalize** — run the loop on the other six scenarios' fixtures; confirm
   the diagnosis is correct for each (feeds the Phase-4 scorecard).

---

## Guardrails (unchanged — hold firmly)
- Read-mostly: the investigator queries freely, **writes nothing** to Silver /
  Gold / serving. Phase 2 writes only the decision trace.
- Answer key (`run_manifest.json`) is unreachable during investigation —
  enforced in `provider.py`, not merely by convention.
- Bounded loop: cap tool calls; on exhaustion, escalate with findings.
- Deterministic defect → agent; transient → retries. Never blur.

## Requirements / assumptions
- Offline build needs only `pandas` + `pyarrow` (installed). `anthropic` is
  runtime-only for the live adapter (Step 7), lazily imported.
- One defect per run (matches the scenario design).
- Live E2E (cluster) inherits Phase 1's deferred tail + the new structured-exit
  read; re-verify the fixture against a fresh Generator run first.

# CLAUDE.md — GL Anomaly Investigator

Guidance for Claude Code when working in this repo. This project adds an
**agentic remediation layer** to an existing Synthetic Lakehouse Pipeline.

## What this project is
When the pipeline's DQ gate fails on a *deterministic data defect* (not a
transient error), an LLM agent investigates, diagnoses the root cause, drafts a
proposed fix for human approval, and is scored against the pipeline's built-in
answer keys. See `DESIGN.md` for the full spec.

## Core principles — hold these firmly
1. **The agent handles deterministic data defects; retries handle transient
   failures.** Never blur this. Do not route infra errors to the agent, and do
   not retry deterministic bad data through the agent path.
2. **Read-mostly agent.** The agent queries freely but writes ONLY proposals to
   a staging location. No writes to Silver, Gold, or serving tables. Ever.
3. **Human-in-the-loop on every apply.** The agent stages a proposal; a human
   approves before it's applied and the gate re-runs.
4. **The agent never sees the answer key** (`run_manifest.json`) during
   investigation. The answer key belongs to the scorer, used after the fact.
5. **Every agent decision is logged** to an append-only trace (query, finding,
   rationale). Treat the decision log as a first-class deliverable.
6. **Bounded loops.** Cap investigation tool-calls; on exhaustion, escalate to a
   human with findings rather than looping.

## What already exists (do not rebuild)
- A config-driven synthetic data generator (`engine/`) — deterministic, seed-based.
- A medallion pipeline: Bronze → DQ gate → Silver → Gold (statements, KPIs,
  consolidation, reports, marts) → publish.
- 7 named defect scenarios, each with an answer-key manifest.
- A Databricks Job with the DQ gate as a real branch point and a `remediate`
  task that currently retries up to 4 times.

## What we are building (in order)
- **Phase 1:** triage (transient vs deterministic) + agent task wiring on the
  failure branch. No intelligence yet.
- **Phase 2:** tool interface + investigation loop; one scenario
  (`intercompany_out_of_balance`) end-to-end, then generalize. **MVP.**
- **Phase 3:** remediation drafter (staged proposals per defect class).
- **Phase 4:** scorecard eval harness across all 7 scenarios.
- **Phase 5:** portfolio README.

Work one phase at a time. Do not scaffold all 7 scenarios before one works end to end.

## Conventions
- Match the existing repo's style: config-driven, domain-in-config, generic plumbing.
- Secrets via Databricks secrets, never committed. Mirror the existing
  OAuth-profile discipline.
- Prefer small, testable functions. The existing pipeline self-verifies at each
  stage; keep that discipline in the agent layer (score everything against answer keys).
- Keep the agent's tool surface tight (see `DESIGN.md` §5). Adding a tool is a
  deliberate decision, not a convenience.

## When unsure
- Re-read `DESIGN.md`.
- Prefer the smallest change that advances the current phase.
- If a choice affects guardrails (writes, answer-key access, autonomy), stop and
  flag it rather than guessing.

# Getting Started — kicking this off with Claude Code

A sequence for starting the GL Anomaly Investigator with Claude Code. Work
phase by phase. Don't let it build ahead.

## Before you start
1. Drop `DESIGN.md` and `CLAUDE.md` into the repo root. Claude Code reads
   `CLAUDE.md` automatically as project context.
2. Make sure the baseline pipeline runs (`python reseed.py --scenarios clean`)
   and at least one defect scenario fails the gate as expected.
3. Have your Anthropic API key ready as a Databricks secret (not committed).

## Phase 1 — Wiring (start here)

**Kickoff prompt:**
> Read `DESIGN.md` and `CLAUDE.md`. We're building Phase 1 only: the triage +
> agent-task wiring. Do NOT build the investigation intelligence yet.
>
> First, walk me through how the current DQ gate emits its verdict — what does a
> failure actually produce (return value, table, exception), and how does the
> `remediate` task currently get invoked? Show me the relevant code before
> proposing changes.
>
> Then propose the smallest change that (a) distinguishes a deterministic
> data-defect failure from a transient error at the gate, and (b) routes
> deterministic failures to a new agent task that, for now, just logs the
> failure context it received. No LLM calls yet. Confirm the plan with me before
> writing code.

**Why this order:** you're de-risking the integration before building the brain.
Get "gate fails → agent task fires → it has the context" working with zero
intelligence first.

## Phase 2 — Investigator (the MVP)

**Kickoff prompt:**
> Phase 2. Read `DESIGN.md` §4.3 and §5. We're building the investigation loop
> for ONE scenario only: `intercompany_out_of_balance`. Do not touch the other
> six yet.
>
> Implement the read-only tools from §5 (start with `get_gate_verdict`,
> `query_failing_table`, `query_clean_baseline`, `get_scenario_context`,
> `log_decision`). Then wire an agent (Anthropic tool-use) that, given the failed
> reconciliation check, investigates: finds the mismatched intercompany pair,
> identifies which side was altered, quantifies the delta, and writes a
> controller-ready diagnosis narrative.
>
> The agent must NOT access the answer key. Log every decision. Show me the
> diagnosis output on a real failing run before we generalize.

**Checkpoint:** when the diagnosis for this one scenario reads like a competent
accountant wrote it, THEN generalize to the other six. That's your MVP.

## Phase 3 — Drafter

**Kickoff prompt:**
> Phase 3. Read `DESIGN.md` §4.4 and §6. Add the drafting stage: for each defect
> class, the agent proposes the specific correcting action as a staged proposal
> object written ONLY to the staging location. It must not write to Silver, Gold,
> or serving. Add `stage_remediation_proposal`. Keep human-in-the-loop: the
> proposal waits for approval. Start with `intercompany_out_of_balance`, then
> extend per the §6 table.

## Phase 4 — Scorecard (centerpiece)

**Kickoff prompt:**
> Phase 4. Read `DESIGN.md` §7. Build the eval harness: for each of the 7
> scenarios — inject defect, run to gate failure, invoke agent, capture diagnosis
> + proposal, score diagnosis against the answer key, apply the approved fix,
> re-run the DQ gate, record pass/fail. Output the scorecard table described in
> §7. This is the centerpiece — make the output clean and publishable.

## Phase 5 — Portfolio README

**Kickoff prompt:**
> Phase 5. Write a portfolio-grade README that tells the story: (1) the synthetic
> lakehouse with defect injection + answer keys, (2) the agentic investigator on
> the failure branch, (3) validation against answer keys with the published
> scorecard at the top. Include the architecture diagram, the transient-vs-
> deterministic triage rationale, and the guardrails. Audience: a hiring manager
> or senior engineer skimming for 60 seconds.

## Working rhythm with Claude Code
- **One phase at a time.** Resist letting it scaffold ahead.
- **Make it show you existing code before changing it.** Especially the gate and
  `remediate` task.
- **Guardrails are stop-the-line.** If a change touches writes, answer-key
  access, or autonomy, review it deliberately.
- **Keep the decision log from day one** — it's observability now and interview
  material later.
- **Commit at each working checkpoint** so you always have a shippable state.

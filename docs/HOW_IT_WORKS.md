# How it works — a plain-English walkthrough

A narrative companion to the specs. `DESIGN.md` is the vision, `PHASE1_PLAN.md`
is the plan, `PHASE1_PROGRESS.md` is the status. **This file is the story** — read
it to get back up to speed, then bring questions to the next session. No code
required to follow it.

---

## The whole thing in a paragraph

There's a financial-close data pipeline (a separate project,
[GeneralLedgerGenerator](https://github.com/uncjorbai/GeneralLedgerGenerator))
that loads general-ledger data, deliberately breaks it in known ways, and runs a
**data-quality gate** that catches the breaks and halts. This project is a
separate **agent** that wakes up when that gate halts, figures out *why* it
halted, decides whether it's the kind of problem worth investigating, and writes
its decision to a permanent log. Right now the agent is deliberately "brainless" —
it makes the *routing* decision but doesn't yet do the *investigating*. That
brain comes in Phase 2. Phase 1 was about building the nervous system: the
plumbing that reliably gets the right failures to the agent, with all the safety
rules in place, before we make it smart.

## The one idea everything hangs on

When software fails, there are two very different kinds of failure, and they need
opposite responses:

- **Transient failures** — a network blip, a machine that ran out of memory, a
  timeout. The data is fine; the world just hiccupped. **The right response is to
  retry.** Try again and it works.
- **Deterministic data defects** — the data itself is wrong. A journal entry
  where debits don't equal credits. An account that isn't in the chart of
  accounts. **Retrying is useless** — it will fail identically every single time,
  because nothing about the data changed. These need someone to *investigate and
  fix the data*.

The entire agent exists to enforce that split. Retries keep handling the hiccups.
The agent handles the bad data. The number one rule of the project is: **never
blur those two.** Don't send a hiccup to the agent (waste), and don't retry bad
data forever (pointless). Everything in Phase 1 is machinery to tell them apart
reliably and act accordingly.

## Follow one failure through the system

Here's the journey of a single bad batch of data, start to finish. This is the
best way to understand what each piece does.

1. **The data breaks.** Over in the pipeline, a batch loads with an unbalanced
   voucher (debits ≠ credits). The pipeline's quality gate runs its checks, the
   `debits_equal_credits` check fails, and the gate **halts the run with an
   error** so the bad data never flows downstream. Good — it did its job.

2. **A signal is left behind.** When the gate halts, the pipeline writes a little
   "something failed" row to a log table. That row's *arrival* is what will wake
   our agent up. (Think of it as the pipeline ringing a doorbell — it doesn't say
   much, just "come look.")

3. **The agent recovers the verdict** — `verdict.py`. The doorbell doesn't say
   *what* failed, so the agent goes and reads the failed run's error message,
   which looks like: `Failing checks: ['debits_equal_credits']`. It pulls the
   check name out of that message. This is the one genuinely fragile step — it
   depends on the exact wording of an error string we don't control — so we
   deliberately isolated *all* of that fragility into this single file. If the
   wording ever changes, there's exactly one place to fix.

4. **The agent makes the call** — `triage.py`. Now it has a fact ("the
   `debits_equal_credits` check failed") and it looks that up in its **catalog of
   known problems** (the registry). The check is known, it's a real data defect,
   and it's the kind that halts the pipeline — so the verdict is
   **deterministic → route to the agent for investigation.** If instead the error
   had been an out-of-memory crash with no check name, the call would be
   **transient → leave it to retries.** And if it were a check name the catalog
   has never heard of, the call would be **unknown → escalate to a human**, rather
   than guess.

5. **The decision is logged** — `audit.py`. Whatever it decided, the agent writes
   a permanent, append-only record: what failed, what it concluded, why, and the
   evidence. This log is a first-class deliverable, not an afterthought — it's how
   we'll audit the agent's judgment and, later, how it accumulates a memory of
   past problems.

6. **The conductor** — `entrypoint.py` — is just the thing that runs steps 3→4→5
   in order and prints a summary. It *is* the "agent" of Phase 1: wake, recover,
   decide, log. No intelligence yet — that's the point.

That whole journey runs today, on a laptop, in a fraction of a second, against a
saved example failure. You can watch it happen (see `PHASE1_PROGRESS.md` → "How to
run what exists").

## The pieces, named plainly

| File | In plain terms |
|------|----------------|
| `config/anomaly_registry.yaml` | The **catalog of known problems.** A plain list: for each quality check, what defect it catches and whether it halts the pipeline. New problems get added here as *config*, not code. |
| `agent/registry.py` | **Loads and sanity-checks that catalog.** Refuses to start if the catalog has a typo — better to fail loudly now than misjudge a failure later. |
| `agent/verdict.py` | **Reads the failure and extracts what broke.** The one fragile, quarantined piece. |
| `agent/triage.py` | **The decision.** Known defect → investigate; hiccup → retry; unrecognized → escalate. |
| `agent/audit.py` | **Writes the decision to a permanent log.** |
| `agent/entrypoint.py` | **The conductor** that runs the above in order. |

## The rules we're holding the line on (and why)

These are non-negotiable guardrails, decided *before* the agent gets smart —
because that's when they're easy to enforce and hardest to add later:

- **The agent only ever writes to its own corner.** It can *read* lots, but it
  writes only to its own log/proposals — never to the real financial tables. An
  agent that can't touch production can't corrupt it.
- **A human approves any actual fix.** The agent will eventually *propose*
  corrections; a person signs off before anything is applied. (Phase 3.)
- **The agent never sees the answer key.** The pipeline ships with a hidden file
  saying exactly what the planted defect was — that's how we *grade* the agent
  later. If it could peek, the grade would be meaningless. So that file is fenced
  off and off-limits.
- **Every decision is logged.** No silent judgments.
- **The agent works on a leash.** When it can't figure something out, it escalates
  to a human instead of spinning. (Matters more in Phase 2.)

## What "done" means right now

Phase 1's **thinking is complete and proven** — every decision path is built and
covered by tests, and the whole flow runs end-to-end on a laptop. What's *not*
done is the **live wiring into the real Databricks workspace**: actually reading a
real failed run, actually writing to the real log table, and setting it up to fire
automatically. That's not a gap in the design — it's work that simply needs the
live environment (and a real failure to point at), so it's cleanly deferred to a
"cluster session" and listed step-by-step in `PHASE1_PROGRESS.md`.

## A few judgment calls worth understanding

These are the "why did we do it *that* way" decisions — good things to have
opinions on going forward:

- **Config, not code, for the catalog of problems.** New defect types are new
  lines in a YAML file, so the agent's knowledge can grow without touching (or
  re-testing) the logic.
- **Quarantine the fragile part.** All the brittle "parse an error string"
  code lives in one file behind a clean result, so fragility can't spread.
- **When in doubt, escalate — don't guess.** If even one part of a failure is
  unrecognized, the whole thing goes to a human. We never route a
  half-understood failure.
- **Decide by structure, not by reading meaning.** "Is there a recognizable check
  name in the failure?" — not "does this error message *sound* like bad data?"
  Structural signals are reliable; interpreting prose is not (and that's exactly
  the sort of thing we do *not* want the brainless Phase-1 layer doing).
- **Keep the two projects separate.** This agent treats the pipeline as an outside
  system and only leans on a documented interface (`PIPELINE_CONTRACT.md`), never
  its internals. When it needs something new from the pipeline, that's a written,
  deliberate request (`collaboration.md`), not a reach across the fence.

## Questions you'll probably want to ask going into Phase 2

Phase 2 is where the agent gets a *brain* — actual investigation of one scenario
end to end. Good things to raise:

- **The flagship-scenario snag.** The scenario we most want to showcase —
  `intercompany_out_of_balance` — is caught by a *reconciliation* check, and those
  checks currently only *warn*; they don't halt the pipeline. Which means today
  they never ring the doorbell, so the agent never wakes for them. Before Phase 2
  can demo that scenario, we have to decide: change the pipeline so that check
  halts, or give the agent a different way to wake up for it? (Flagged as "Tier D"
  in the docs.)
- **What tools does the investigating agent get?** Phase 2 gives it the ability to
  query data. What exactly is it allowed to look at, and how do we keep that
  surface tight? (The guardrails say: read a lot, write nothing but proposals.)
- **How do we keep it from running in circles?** "Bounded loops" — how many
  investigation steps before it gives up and escalates?
- **How will we know it's any good?** Phase 4 grades it against those hidden
  answer keys across all seven scenarios. Worth keeping that scorecard in mind
  now, because it shapes what we log from the start.
- **The one thing to verify first on the cluster:** our example failure is
  *synthesized* — reconstructed from the pipeline's error format, not captured
  from a real run. The very first live task is to confirm a real failure looks the
  way we assumed, and fix the parser if not.

## A tiny glossary

- **Medallion pipeline / Bronze → Silver → Gold** — a common layout: raw data
  lands in *Bronze*, gets cleaned into *Silver*, and is shaped into
  business-ready *Gold*. The quality gate sits between Bronze and Silver so bad
  data never gets promoted.
- **DQ gate** — "data quality" gate. The checkpoint that runs the checks and
  halts if data is bad.
- **Databricks / Jobs API / Delta table** — Databricks is the cloud platform the
  pipeline runs on; the *Jobs API* is how we ask it about a run that failed; a
  *Delta table* is just the kind of table we write our log to.
- **Deterministic vs transient** — bad-data-fails-the-same-way-every-time vs
  random-hiccup. The whole split the agent is built around.
- **Triage** — the sorting decision: which bucket does this failure go in.
- **Answer key** — the hidden file naming the planted defect; used to grade the
  agent, never seen by it during investigation.

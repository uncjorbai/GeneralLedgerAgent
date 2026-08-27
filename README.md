# GL Anomaly Investigator

A companion project to my [Financial Close pipeline](../GeneralLedgerGenerator).
The pipeline generates realistic general-ledger data, deliberately breaks it in
known ways, and runs a data-quality gate that catches the breaks. This project is
what happens *after* the gate catches something: an agent that investigates the
failure the way a close accountant would, works out what actually went wrong, and
drafts a fix for a human to approve.

## The idea, in one breath

When a data pipeline fails, you retry it. That's the right move for a flaky
network or a timed-out cluster — the failure is transient, and trying again
clears it. It's the wrong move for *bad data*. An unbalanced journal entry fails
the same way every time; retrying it just fails slower.

So this project splits failures in two. Transient problems keep going to retries.
Data problems — the deterministic kind, where the same defect trips the same
check every run — go to the agent instead. That's the whole thesis: retries
handle the infrastructure, the agent handles the data, and each stays in its lane.

## Where to start reading

If you're picking this up (future me included), read in roughly this order:

- **[DESIGN.md](DESIGN.md)** — the full spec. Start here for the whole picture.
- **[docs/PHASE1_PLAN.md](docs/PHASE1_PLAN.md)** — what I'm building right now,
  and every decision I've locked in so far, with the reasoning behind each.
- **[docs/PIPELINE_CONTRACT.md](docs/PIPELINE_CONTRACT.md)** — the seam between
  this project and the pipeline: exactly what the agent reads from upstream, and
  the one file it is never allowed to open (the answer key).
- **[collaboration.md](collaboration.md)** — a note to the pipeline project
  asking for one small addition. Hand this to whoever works on that side.
- **[config/](config)** — the anomaly registry (the catalog of known defects)
  and the pointers to the live system.

## How it's coming together

Built in phases, one at a time, trying not to run ahead:

1. **Wiring** *(here now)* — get "gate fails → agent wakes → agent has the
   context" working with no intelligence at all. De-risk the plumbing first.
2. **Investigator** — give the agent tools and let it genuinely investigate one
   scenario end to end, then generalize to the rest.
3. **Drafter** — have it propose the specific correcting entry, staged for
   approval.
4. **Scorecard** — grade it against the answer keys across all seven defects.
   This is the centerpiece.
5. **Write-up.**

## Rules I'm holding the agent to

Not negotiable, and worth stating plainly up front:

- It **reads** widely but **writes** only to its own corner (`fin_close.agent`) —
  never the real Silver, Gold, or serving tables.
- A human approves every fix before it is applied.
- It never sees the answer key while investigating; that's what it's graded on.
- Every step it takes lands in an append-only trail. That log is a deliverable,
  not an afterthought.
- It works on a leash: bounded steps, and when it's stuck it escalates instead of
  looping.

## A note on the pipeline

The pipeline lives in its own project and I'm keeping it that way. This agent
treats it as an outside system and leans only on the documented contract, never
on its internals. When the agent needs something new from upstream, that's a
deliberate ask (see `collaboration.md`), not a reach across the fence.

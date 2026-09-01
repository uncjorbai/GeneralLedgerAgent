# GL Anomaly Investigator

*An LLM agent that wakes up when a financial-close data-quality gate fails, investigates **why**, and drafts a fix a human can approve — then gets graded against the pipeline's own answer keys.*

This is my first dive into producing an Agentic solution for something outside of school. I'm leveraging my financial background to focus on developing a solution I could benefit from in my every day life. This is only possible through the assistance of Claude, so I'm excited to get into it and develop.

This thread should be updated and maintained as I progress through, so feel free to fork it and follow the documented setup to join along.

---

## The result so far

The centerpiece is a **scorecard**: for all seven seeded defects, the agent runs the full loop — detect → diagnose → draft → apply → re-gate — and is scored against the pipeline's real answer keys. Everything below is offline (no cloud, no API key), and the fix is validated by the **same gate that caught the defect**.

| Scenario | Check | Detected | Diagnosis | Fix valid | Re-gate passes |
|---|---|:--:|:--:|:--:|:--:|
| `unbalanced_voucher` | `debits_equal_credits` | ✅ | ✅ | ✅ | ✅ |
| `duplicate_voucher` | `no_duplicate_vouchers` | ✅ | ✅ | ✅ | ✅ |
| `unmapped_account` | `account_in_coa` | ✅ | ✅ | ✅ | ✅ |
| `missing_department` | `required_dimensions_present` | ✅ | ✅ | ✅ | ✅ |
| `missing_entity_or_period` | `entity_and_period_present` | ✅ | ✅ | ✅ | ✅ |
| `period_cutoff` | `period_cutoff_correct` | ✅ | ✅ | ✅ | ✅ |
| `intercompany_out_of_balance` | `intercompany_eliminates` | ✅ | ✅ | ✅ | ✅ |

| Metric | Result | Target |
|---|:--:|:--:|
| Detection | **7/7** | 7/7 |
| Diagnosis accuracy | **7/7** | ≥ 6/7 |
| Remediation validity | **7/7** | ≥ 5/7 |
| Closed-loop (re-gate passes) | **7/7** | ≥ 3 |

Regenerate it yourself: `python -m agent.scorecard` → [docs/SCORECARD.md](docs/SCORECARD.md). **180 tests, all offline.**

**One thing I'm keeping honest:** offline, the investigation step is a deterministic, tools-only recovery, so the scorecard proves the tool surface is *sufficient* and the fix machinery *closes end-to-end*. It does **not** yet prove the live model diagnoses each defect unaided — that's the next step (a real API run), and the harness is built so the same scorer grades it with no changes.

## What I'm learning here

- Real world applications of LLMs
- Tools/Tasks/Skills Development
- End-to-end ownership
- Governance in action
- Human in the loop guardianship

## The problem I'm learning on

With the complex nature of GAAP accounting it is almost prophetic to expect that errors are bound to reach production data. Despite our best efforts, guard rails are strict, defined boundaries that work nonstop to mitigate as many errors as possible. The evolving nature of modern business requires that IT and Accounting maintain a balance of anticipating errors as a result of new business processes and reacting to issues with new business processes. When errors escape the guard rails: business is impacted, teams lose momentum implementing a fix, and trust is lost. This project aims to accelerate our solutions so records are accurate and timely, and reconciliation requires as little intervention as possible.

This is a real world application supported with a production-ready General Ledger completely configurable to the needs of the simulation.

## The one idea it all hangs on

When software fails, there are two very different kinds of failure that need opposite responses:

- **Transient failures** — a network blip, a timeout. The data is fine; the world hiccupped. **The right response is to retry.**
- **Deterministic data defects** — the data itself is wrong (debits ≠ credits, an account not in the chart of accounts). **Retrying is useless** — it fails identically every time. These need someone to *investigate and fix the data.*

The whole agent exists to enforce that split. Retries keep handling the hiccups; the agent handles the bad data. **Never blur the two** — that's rule number one.

## How it works

```
            ┌─────────────┐
            │   DQ Gate   │   the pipeline's data-quality checks
            └──────┬──────┘
          pass ┌───┴───┐ fail
               ▼       ▼
        (Silver…)   ┌──────────────────┐
                    │  Failure triage  │   transient vs deterministic
                    └────┬────────┬────┘
             transient   │        │  deterministic data defect
                         ▼        ▼
                   ┌─────────┐  ┌────────────────────────────┐
                   │ retry   │  │  GL Anomaly Agent          │
                   │ (x3)    │  │  investigate → diagnose →  │
                   └─────────┘  │  draft → (human approves)  │
                                └──────────────┬─────────────┘
                                               ▼
                                     apply → re-run the gate → pass?
```

The agent investigates with a **tight, read-only tool surface** (query the failing data, diff it against the clean baseline, inspect the chart of accounts / dimensions), logs every step to an append-only decision trace, and delivers a structured diagnosis. From that diagnosis it drafts a **staged** remediation proposal — never applied automatically. A human approves; the fix is applied; the same gate re-runs. The scorecard is the pipeline grading its own agent.

## The companion project

**→ [GeneralLedgerGenerator](https://github.com/uncjorbai/GeneralLedgerGenerator)** — Databricks-backed Medallion Architecture pipeline that loads configurable General Ledger data from a provided Chart of Accounts, Departments and Cost Centers. Built on a Free Serverless subscription, removing the complexity of spark management for easy maintenance and availability.

It generates production-shaped GL data, injects seven named defects each with an answer key, and enforces the DQ gate this agent responds to. Configurations live on the companion repo.

## How it's coming together

1. **Wiring** ✅ — triage at the gate; the agent task fires on a deterministic failure and receives context.
2. **Investigator** ✅ — the tool interface + bounded investigation loop; all 7 defects diagnosable.
3. **Drafter** ✅ — staged remediation proposals per defect class (restore-to-baseline; never auto-applied).
4. **Scorecard** ✅ — the eval harness above, graded against real answer keys and a fidelity-checked offline gate.
5. **Write-up** ✅ — this README + the phase docs below.

*Still ahead (deliberately deferred):* a live LLM run to measure **unaided** diagnosis, and the live-Databricks tail (Spark provider, live Delta writers).

## Rules I'm holding the agent to

- Writes only to its own corner; never real Silver/Gold/serving tables.
- A human approves every fix before it's applied.
- Never sees the answer key while investigating (the scorer sees it, after the fact).
- Every step lands in an append-only trail.
- Bounded steps; escalates instead of looping.

## How to run it

Offline — no cluster, no API key, no network:

```bash
pip install -r requirements-dev.txt
python -m pytest -q            # 180 tests
python -m agent.scorecard      # regenerate docs/SCORECARD.md + docs/scorecard.json
```

## How to read this repo

- **[DESIGN.md](DESIGN.md)** — the full spec: motivation, architecture, tool interface, guardrails, scorecard.
- **[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)** — the plain-English story, no code required.
- **[docs/SCORECARD.md](docs/SCORECARD.md)** — the generated scorecard.
- **Phase progress docs** — [1](docs/PHASE1_PROGRESS.md) · [2](docs/PHASE2_PROGRESS.md) · [3](docs/PHASE3_PROGRESS.md) · [4](docs/PHASE4_PROGRESS.md), each with a "▶ RESUME HERE".
- **[docs/PIPELINE_CONTRACT.md](docs/PIPELINE_CONTRACT.md)** — the contract between the agent and the pipeline (rules and responsibilities).
- **[config/](config)** — the anomaly registry and system config (domain-in-config; adding a defect class is a config change).

## If you're using this to learn

Welcome aboard, I hope you find this as exciting as I do, and I wish you the best of luck.

I will be treating this like a living notebook, so stay tuned for updates.

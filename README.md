# GL Anomaly Investigator

This is my first dive into producing an Agentic solution for something outside of school. I'm leveraging my financial background to focus on developing a solution I could benefit from in my every day life. This is only possible through the assistance of Claude, so I'm excited to get into it and develop.

This thread should be updated and maintained as I progress through, so feel free to fork it and follow the documented setup to join along. 

## What I'm learning here

- Real world applications of LLMs
- Tools/Tasks/Skills Development
- End-to-end ownership
- Governance in action
- Human in the loop guardianship

## The problem I'm learning on


With the complex nature of GAAP accounting it is almost prophetic to expect that errors are bound to reach production data. Despite our best efforts, guard rails are strict, defined boundaries that work nonstop to mitigate as many errors as possible. The evolving nature of modern business requires that IT and Accounting maintain a balance of anticipating errors as a result of new business processes and reacting to issues with new business processes. When errors escape the guard rails: business is impacted, teams lose momentum implementing a fix, and trust is lost. This project aims to accelerate our solutions so records are accurate and timely, and reconciliation requires as little intervention as possible. 

This is a real world application supported with a production-ready General Ledger completely configurable to the needs of the simulation. 

## The companion project


**→ [GeneralLedgerGenerator](https://github.com/uncjorbai/GeneralLedgerGenerator)** - Databricks backed Medallion Architecture pipeline that loads configurable General Ledger data from a provided Chart of Accounts, Departments and Cost Centers. Built on a Free Serverless subscription, removing the complexity of spark management and easy maintenance and availability. 

Configurations can be found on the companion repo. 

## How to read this repo

- **[DESIGN.md](DESIGN.md)** - Configuring now
- **[docs/PHASE1_PLAN.md](docs/PHASE1_PLAN.md)** - Phase one plan before any code/implementation
- **[docs/PIPELINE_CONTRACT.md](docs/PIPELINE_CONTRACT.md)** - contract with Agent regarding rules and responsibilities
- **[config/](config)** - config.

## How it's coming together


1. **Wiring** _(here now)_ —
2. **Investigator** —
3. **Drafter** —
4. **Scorecard** —
5. **Write-up** —

## Rules I'm holding the agent to

- Writes only to its own corner; never real Silver/Gold/serving tables.
- A human approves every fix before it's applied.
- Never sees the answer key while investigating.
- Every step lands in an append-only trail.
- Bounded steps; escalates instead of looping.

## If you're using this to learn

Welcome aboard, I hope you find this as exciting as I do, and I wish you the best of luck. 

I will be treating this like a living notebook, so stay tuned for updates. 
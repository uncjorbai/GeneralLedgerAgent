# Learning Resources — Agentic Systems & Claude Code

Curated for building the GL Anomaly Investigator. Ordered by usefulness to THIS
project, not by fame. Read the top tier before you write code.

---

## Tier 1 — Read these first (foundational, directly applicable)

### Anthropic: Building Effective Agents
`anthropic.com/engineering/building-effective-agents`
The canonical piece. The single most important idea for your project:
**start with the simplest thing, add agentic complexity only when simpler
solutions fall short.** Your project is a textbook fit for *why* an agent is
warranted — the failure is open-ended (which of many things went wrong, needing
iterative investigation) rather than a fixed path. It also covers the core
patterns (prompt chaining, routing, orchestrator-worker, evaluator-optimizer)
and stresses testing in sandboxed environments with guardrails — which is
exactly what your answer-key + DQ-gate setup provides. Read the whole thing.

### Anthropic: Building Effective AI Agents (eBook / Architecture Patterns)
`resources.anthropic.com/building-effective-ai-agents`
The longer-form companion with real deployments (Coinbase, Intercom, Thomson
Reuters). Best-practice to internalize: **single-purpose agents that do one
thing well, then grow.** Maps directly to your "nail one scenario, then
generalize" build order.

### Claude Code: Best Practices (official docs)
`code.claude.com/docs/en/best-practices`
Updated constantly. The most relevant patterns for you: a lean `CLAUDE.md`
(you have one), **plan mode before any edit**, and a **verification loop** —
have Claude show evidence (test output, the command it ran and what it returned)
rather than asserting success. For your project, the "second opinion" pattern
(a verification subagent that tries to refute a result, so the agent doing the
work isn't the one grading it) is directly analogous to your answer-key scorer.

---

## Tier 2 — Directly analogous prior work (study the architecture)

### Argos — agentic time-series anomaly detection (via Towards Data Science)
`towardsdatascience.com/boosting-your-anomaly-detection-with-llms/`
**Read this one closely — it's the closest published analog to your project.**
Argos is a three-agent pipeline for anomaly detection in cloud infrastructure:
a **Detection Agent** that generates detection rules, a **Repair Agent** that
checks/fixes them by executing on dummy data until they run, and a **Review
Agent** that evaluates accuracy on validation data and feeds back. The parallels
to your design: reproducible/explainable rules (your deterministic defects),
a repair loop (your drafter), and a review/validation stage (your scorecard).
Borrow the multi-agent separation-of-concerns idea; you can implement it as
stages of one agent to start.

### "An Agentic AI Pipeline for … Anomaly Detection and LLM-Driven Recommendations" (arXiv 2606.28467)
`arxiv.org/abs/2606.28467`
An end-to-end agentic pipeline that flags anomalies then produces **prioritized,
actionable recommendations** — the same investigate → diagnose → recommend arc
you're building. Especially worth studying: their **Context Agent → Diagnosis
Agent → Report Agent** split (evidence gathering → structured JSON diagnosis →
human-readable narrative), a **reflective memory layer** that incorporates
operator feedback, **hard rules and confidence caps that surround the LLM to
prevent overconfident/unsafe outputs** (your guardrails), and a capped reasoning
budget (they cap at eight steps — you should cap yours too). This is a clean
template for the diagnose+draft half of your system.

### Claude Code Best Practices repo (community, most-cited)
`github.com/shanraisshan/claude-code-best-practice`
Community field manual, refreshed ~weekly, with input from Boris Cherny (built
Claude Code at Anthropic). The recurring meta-pattern across every methodology:
**research → plan → execute → review → ship, human as oversight at each gate.**
That's exactly the rhythm in your GETTING_STARTED.md. Skim for the CLAUDE.md,
subagents, and hooks sections.

---

## Tier 3 — Deepen specific skills as you need them

### Anthropic: How Claude Code is used in practice (research)
`anthropic.com/research/claude-code-expertise`
Finding worth internalizing: **the more domain expertise you bring, the more
work Claude does per instruction** — the human makes the planning decisions
(what), Claude makes execution decisions (how). You have deep GL + pipeline
expertise; lean into it. Give Claude Code the *what* precisely (your DESIGN.md)
and let it handle the *how*.

### Anthropic Cookbook — tool use & agents (code)
`github.com/anthropics/anthropic-cookbook`
Runnable notebooks for tool-use / function-calling — the mechanics behind your
§5 tool interface. Go here when you're wiring the actual Anthropic API tool calls
in Phase 2.

### Claude Docs — tool use / function calling
`docs.claude.com` (Build with Claude → Tool use)
Reference for defining tools, handling tool-use responses, and multi-turn tool
loops. Your investigation loop is a multi-turn tool-use loop; this is the spec.

---

## How to use these for THIS project
1. Read Tier 1 in full before writing code. Internalize "simplest thing first"
   and "show evidence, don't assert."
2. Read the Argos writeup and the arXiv pipeline paper for architecture ideas —
   specifically the diagnose→draft→review separation and the confidence/rule
   guardrails. Adapt, don't copy.
3. Keep the Claude Code docs + cookbook open as references during Phase 2 wiring.
4. Everything else is depth-on-demand.

## A note on "vibe coding" vs what you're doing
The field has moved from "vibe coding" (throwaway demos) to **agentic
engineering** (orchestrating AI to ship reviewed, production-grade code with the
human in the oversight seat). Your project — with its answer keys, DQ gate,
guardrails, and scorecard — is squarely the latter. That framing itself is a
selling point: you're not showing a clever demo, you're showing engineering
discipline around an autonomous system. Say that in interviews.

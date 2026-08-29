"""The triage decision: is this failure the Agent's job, or not?

This is the heart of Phase 1 — and it stays deliberately dumb. It is a **pure
function** of `(verdict, registry, context)`: no clock, no network, no
environment, no LLM. Same inputs always give the same decision, so it is
trivially testable and auditable.

It answers exactly one question and produces exactly one record:

    deterministic data defect  -> route_to_agent
    transient / infra failure  -> leave_to_existing_path   (retries own it)
    anything we don't fully understand -> escalate         (a human looks)

The three "who/when" fields the audit row also needs — `agent_run_id`,
`detected_at`, `signal_source` — are NOT set here. Triage doesn't know the
Agent's run id or how the verdict was fetched; those get stamped by the
audit/entrypoint layer (steps 7–8). Keeping them out is what lets triage be a
clean, clock-free function.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.registry import Registry
from agent.verdict import Verdict

# String constants (not bare literals) so a typo is an ImportError, not a silent
# mislabel, and so tests assert against the same names the code emits.
FAILURE_DETERMINISTIC = "deterministic"
FAILURE_TRANSIENT = "transient"
FAILURE_UNKNOWN = "unknown"

DECISION_ROUTE = "route_to_agent"
DECISION_LEAVE = "leave_to_existing_path"
DECISION_ESCALATE = "escalate"


@dataclass(frozen=True)
class TriageResult:
    """The decision plus everything the audit row needs from triage.

    Frozen: once triage has decided, the record is immutable evidence.
    """

    failure_class: str                 # deterministic | transient | unknown
    triage_decision: str               # route_to_agent | leave_to_existing_path | escalate
    failed_checks: tuple[str, ...]     # all parsed failing checks, sorted
    unknown_checks: tuple[str, ...]    # failing checks NOT in the registry (the escalate reason)
    gate_types: tuple[str, ...]        # distinct gates among the KNOWN checks, sorted
    rationale: str                     # short human-readable why
    evidence: str                      # carried from the verdict (error snippet)
    # --- passthrough context (from the activation signal) ---
    scenario: str
    generator_run_id: str | int
    gl_table: str


def triage(
    verdict: Verdict,
    registry: Registry,
    *,
    scenario: str,
    generator_run_id: str | int,
    gl_table: str,
) -> TriageResult:
    """Classify a failed run and decide where it goes. Pure; deterministic."""
    failed = tuple(sorted(verdict.failed_checks))

    # Small closure so each return fills the shared context once, not four times.
    def result(failure_class, decision, *, rationale, unknown=(), gate_types=()):
        return TriageResult(
            failure_class=failure_class,
            triage_decision=decision,
            failed_checks=failed,
            unknown_checks=unknown,
            gate_types=gate_types,
            rationale=rationale,
            evidence=verdict.evidence,
            scenario=scenario,
            generator_run_id=generator_run_id,
            gl_table=gl_table,
        )

    # 1) TRANSIENT — no parseable DQ verdict in the output (OOM, timeout, driver
    #    loss). Structure, not message content, makes this call. Retries own it.
    if not verdict.parsed:
        return result(
            FAILURE_TRANSIENT,
            DECISION_LEAVE,
            rationale=(
                "No parseable DQ verdict in the run output; treating as a "
                "transient/infra failure and leaving it to the existing retry path."
            ),
        )

    # Resolve each failing check against the registry.
    resolved = {name: registry.get(name) for name in failed}
    unknown_checks = tuple(sorted(n for n, c in resolved.items() if c is None))
    known = [c for c in resolved.values() if c is not None]
    gate_types = tuple(sorted({c.gate for c in known}))

    # 2) ESCALATE (precedence: wins over routing) — any failing check the registry
    #    doesn't know. A registry gap means the pipeline grew a check we haven't
    #    catalogued; never route a partially-understood failure.
    if unknown_checks:
        return result(
            FAILURE_UNKNOWN,
            DECISION_ESCALATE,
            unknown=unknown_checks,
            gate_types=gate_types,
            rationale=(
                f"Unrecognized failing check(s) {list(unknown_checks)} not in the "
                "registry; escalating for human review instead of routing."
            ),
        )

    # 3) DETERMINISTIC -> route. A known, deterministic data defect. We key on
    #    `deterministic`, NOT `fails_task`: a check appears in `failed_checks` only
    #    because a verdict source already observed it fail (the exception string for
    #    a DQ-gate raise, OR the structured exit for a reconciliation variance —
    #    verdict.py). So "is this a real failure" is settled upstream; triage asks
    #    the orthogonal question "is it a deterministic defect the Agent handles?"
    #    Routing on `deterministic` is what wakes the Agent for the Tier-D
    #    reconciliation scenarios (intercompany, period_cutoff) without them ever
    #    failing the task. `fails_task` stays as metadata / the trigger-source hint.
    routable = [c for c in known if c.deterministic]
    if routable:
        return result(
            FAILURE_DETERMINISTIC,
            DECISION_ROUTE,
            gate_types=gate_types,
            rationale=(
                f"Failing check(s) {list(failed)} are known deterministic data "
                "defects; routing to the Agent."
            ),
        )

    # 4) EDGE — parsed, all checks known, but none is deterministic (e.g. a future
    #    flaky/non-deterministic check the registry marks `deterministic: false`).
    #    Not the Agent's job and not a clean transient either: escalate rather than
    #    silently drop.
    return result(
        FAILURE_UNKNOWN,
        DECISION_ESCALATE,
        gate_types=gate_types,
        rationale=(
            f"Failing check(s) {list(failed)} are known but none is a deterministic "
            "defect; unexpected, escalating for human review."
        ),
    )

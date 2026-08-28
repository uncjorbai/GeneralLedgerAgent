"""Recover the failing-check verdict from a failed pipeline run.

THIS IS THE ONE BRITTLE MODULE IN PHASE 1 — on purpose. The DQ gate destroys its
structured verdict when it fails (it `raise`s before it can emit JSON — see
GeneralLedgerGenerator/notebooks/dq_gate.py:205 vs :210), so the only durable
signal left is the exception STRING:

    DQ GATE FAILED — blocking Silver. Failing checks: ['debits_equal_credits']

Parsing that string is inherently fragile: it depends on a message format we do
not control. So we quarantine ALL of that fragility here, behind a clean output
(`Verdict`). When the gate's format changes, this module is the single place to
fix — nothing downstream parses strings. (PIPELINE_CONTRACT.md points here.)

Nothing in the pure parsing path imports the Databricks SDK; the live fetch does,
lazily, so the whole module is unit-testable on a laptop with no SDK and no
network.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

# Anchor on the literal marker the gate emits, then capture the trailing Python
# list literal `[...]`. `[^\]]*` deliberately stops at the first `]`, and the
# f-string SOURCE line in a traceback ("...Failing checks: {sorted(dq_failed)}")
# has a `{`, not a `[`, so this regex skips it and matches the RESOLVED list.
_MARKER = re.compile(r"Failing checks:\s*(\[[^\]]*\])")

_EVIDENCE_MAX = 300  # cap the evidence snippet we carry into the audit trail


@dataclass(frozen=True)
class Verdict:
    """What we could recover about why a run failed.

    `parsed` is the important flag, not `failed_checks`:
      * parsed=True  -> we found a structured failing-check list => a DETERMINISTIC
                        data defect the registry can classify.
      * parsed=False -> no parseable check list (an infra crash: OOM, timeout,
                        driver loss) => triage treats it as TRANSIENT.
    This is the deterministic-vs-transient split decided by STRUCTURE, never by
    interpreting the meaning of an error message.
    """

    failed_checks: frozenset[str]  # empty when parsed=False
    parsed: bool
    evidence: str                  # short snippet for the audit trail


def parse_failed_checks(text: str | None) -> frozenset[str] | None:
    """Extract the failing-check names from an error string.

    Returns a frozenset of names, or None when there is no parseable
    `Failing checks: [...]` marker (no marker, or a malformed list). None means
    "not a recognizable deterministic verdict" — the caller reads that as
    transient.
    """
    if not text:
        return None
    m = _MARKER.search(text)
    if not m:
        return None
    try:
        value = ast.literal_eval(m.group(1))  # safe: literals only, no code exec
    except (ValueError, SyntaxError):
        return None
    if not isinstance(value, (list, tuple, set)):
        return None
    return frozenset(str(x) for x in value)


def _evidence_snippet(text: str | None) -> str:
    """A short, human-readable snippet of the error for the audit trail."""
    text = (text or "").strip()
    if not text:
        return ""
    m = _MARKER.search(text)
    if m:
        return m.group(0)[:_EVIDENCE_MAX]  # the marker line itself, most useful
    return text[-_EVIDENCE_MAX:]           # else the tail (usually the exception)


def verdict_from_error(
    error: str | None = None, error_trace: str | None = None
) -> Verdict:
    """Build a Verdict from a run's `error` / `error_trace` fields.

    Both are considered (trace first, as it is fuller); a missing marker in both
    yields parsed=False.
    """
    text = "\n".join(t for t in (error_trace, error) if t)
    checks = parse_failed_checks(text)
    return Verdict(
        failed_checks=checks if checks is not None else frozenset(),
        parsed=checks is not None,
        evidence=_evidence_snippet(text),
    )


def fetch_verdict(run_id: int, output_getter=None) -> Verdict:
    """LIVE path: fetch a failed run's output and parse it into a Verdict.

    `output_getter` is injected (dependency injection) so tests supply a fake and
    this stays unit-testable with no SDK/network. In production it defaults to the
    Databricks Jobs API.
    """
    getter = output_getter or _default_output_getter
    out = getter(run_id)
    return verdict_from_error(
        error=getattr(out, "error", None),
        error_trace=getattr(out, "error_trace", None),
    )


def _default_output_getter(run_id: int):
    """Default getter: Databricks Jobs API. Imported lazily so the SDK is only
    required on the live path, not for local tests.

    TODO(cluster session): wire the `fin_close` auth profile from config/system.yaml
    instead of relying on ambient SDK auth.
    """
    from databricks.sdk import WorkspaceClient  # lazy: keeps local tests SDK-free

    return WorkspaceClient().jobs.get_run_output(run_id)

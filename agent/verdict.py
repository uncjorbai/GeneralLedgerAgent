"""Recover the failing-check verdict from a pipeline run.

This module is the single place that turns *whatever the pipeline emits* into a
clean `Verdict`. There are TWO recovery sources, because the gate leaves two very
different traces depending on which suite tripped (see
GeneralLedgerGenerator/notebooks/dq_gate.py):

  1. **Brittle string parse — `verdict_from_error` (the failed-task path).**
     A DQ-gate check `raise`s (dq_gate.py:205) *before* the gate can emit its
     structured JSON, so the only durable signal is the exception STRING:

         DQ GATE FAILED — blocking Silver. Failing checks: ['debits_equal_credits']

     Parsing that is inherently fragile — a message format we do not control — so
     ALL of that fragility is quarantined here behind `Verdict`.

  2. **Clean structured parse — `verdict_from_exit` (the Tier-D / reconciliation
     path).** A reconciliation check only *prints* a variance; the task does NOT
     raise, so it reaches `dbutils.notebook.exit(json.dumps({... "checks":[...]}))`
     (dq_gate.py:210) and emits a structured, per-check verdict. This is how the
     flagship `intercompany_out_of_balance` — which passes the DQ gate and only
     varies at reconciliation — is recovered. It is the clean counterpart to the
     string parse and doubles as the Tier-B "durable structured verdict".

Which source a caller uses is decided by the run's outcome: a task that FAILED →
`verdict_from_error` on its error/trace; a task that SUCCEEDED but reported a
reconciliation variance → `verdict_from_exit` on its exit value. Either way the
downstream (`triage`) sees only a `Verdict`.

Nothing in the pure parsing paths imports the Databricks SDK; the live fetch does,
lazily, so the whole module is unit-testable on a laptop with no SDK and no
network.
"""

from __future__ import annotations

import ast
import json
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


def _failing_from_checks(checks) -> tuple[frozenset[str], list[tuple[str, int]]]:
    """From a gate `checks[]` list, return (failing-names, [(name, failures)]).

    Each item is a `{check, gate, failures, passed}` dict. A check is failing when
    `passed` is falsy. Missing `passed` is treated as passed (never invent a
    failure from absent data). Items without a `check` name are ignored.
    """
    names: set[str] = set()
    detail: list[tuple[str, int]] = []
    for item in checks:
        if not isinstance(item, dict) or "check" not in item:
            continue
        if not item.get("passed", True):
            name = str(item["check"])
            names.add(name)
            detail.append((name, int(item.get("failures", 0) or 0)))
    return frozenset(names), detail


def verdict_from_exit(exit_value: dict | str | None) -> Verdict:
    """Build a Verdict from the gate's structured `notebook.exit()` payload.

    This is the CLEAN verdict source (contrast `verdict_from_error`, the brittle
    string path). On a run that did NOT raise, the gate emits:

        {"scenario","gl_table","dq_passed","recon_passed",
         "checks":[{"check","gate","failures","passed"}, ...]}

    A reconciliation variance (e.g. `intercompany_eliminates`) lives ONLY here and
    in no exception, so this is how the flagship reaches triage (Tier-D).
    `parsed=True` whenever a well-formed `checks[]` list is present; `failed_checks`
    are those with `passed=False`. Absent/malformed payload → `parsed=False`
    (nothing structured to act on), consistent with the transient reading.
    """
    if isinstance(exit_value, str):
        try:
            exit_value = json.loads(exit_value)
        except (ValueError, TypeError):
            return Verdict(failed_checks=frozenset(), parsed=False, evidence="")
    if not isinstance(exit_value, dict):
        return Verdict(failed_checks=frozenset(), parsed=False, evidence="")

    checks = exit_value.get("checks")
    if not isinstance(checks, list):
        return Verdict(failed_checks=frozenset(), parsed=False, evidence="")

    failed, detail = _failing_from_checks(checks)
    if detail:
        body = ", ".join(f"{name} ({n} failures)" for name, n in sorted(detail))
        evidence = f"gate verdict (structured exit): {body}"[:_EVIDENCE_MAX]
    else:
        evidence = "gate verdict (structured exit): all checks passed"
    return Verdict(failed_checks=failed, parsed=True, evidence=evidence)


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

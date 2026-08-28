"""Phase-1 Agent entrypoint: verdict -> triage -> audit. No LLM.

This IS the Phase-1 "agent": it wakes on a pipeline failure, recovers the verdict
from the failed run, classifies it, and logs the decision to the audit trail.
There is no intelligence yet — that's Phase 2. Two entry points, one body of
logic:

  * CLI (this file):     `python -m agent.entrypoint --run-output ... --dry-run`
                         runs the whole path on a laptop against a saved run
                         output, writing the audit row to a local JSONL.
  * Databricks notebook: a thin wrapper (deferred to the cluster session) that
                         fetches the run output live and calls write_delta().

`investigate()` holds the orchestration and does no I/O of its own, so it is
unit-testable; `main()` is the thin CLI shell around it.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent.audit import SIGNAL_JOBS_API, to_row, write_dry_run
from agent.registry import load_registry
from agent.triage import TriageResult, triage
from agent.verdict import verdict_from_error

DEFAULT_DRY_RUN_LOG = Path("out") / "triage_log.jsonl"


def investigate(
    *,
    run_output: dict,
    scenario: str,
    generator_run_id,
    gl_table: str,
    agent_run_id: str | None = None,
    now: datetime | None = None,
) -> tuple[TriageResult, dict]:
    """Run verdict -> triage -> row. Returns (TriageResult, audit-row dict).

    `run_output` is a dict carrying the failed run's 'error'/'error_trace'
    (from the Jobs API live, or a saved JSON locally). Pure orchestration: the
    only non-determinism (run id, clock) is injectable for tests.
    """
    verdict = verdict_from_error(
        error=run_output.get("error"), error_trace=run_output.get("error_trace")
    )
    result = triage(
        verdict,
        load_registry(),
        scenario=scenario,
        generator_run_id=generator_run_id,
        gl_table=gl_table,
    )
    agent_run_id = agent_run_id or f"local-{uuid.uuid4()}"
    now = now or datetime.now(timezone.utc)
    row = to_row(result, agent_run_id=agent_run_id, detected_at=now, signal_source=SIGNAL_JOBS_API)
    return result, row


def _summary(result: TriageResult) -> str:
    return (
        f"[triage] {result.failure_class.upper()} -> {result.triage_decision}\n"
        f"         checks={list(result.failed_checks)}  scenario={result.scenario}\n"
        f"         {result.rationale}"
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Phase-1 GL Anomaly Investigator — triage + audit, no LLM."
    )
    p.add_argument("--run-output", required=True,
                   help="JSON file with a failed run's 'error'/'error_trace' fields.")
    p.add_argument("--scenario", required=True)
    p.add_argument("--generator-run-id", required=True)
    p.add_argument("--gl-table", required=True)
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="(default) write the audit row to a local JSONL, not Databricks.")
    p.add_argument("--out", default=str(DEFAULT_DRY_RUN_LOG),
                   help=f"dry-run output file (default: {DEFAULT_DRY_RUN_LOG}).")
    args = p.parse_args(argv)

    run_output = json.loads(Path(args.run_output).read_text(encoding="utf-8"))
    result, row = investigate(
        run_output=run_output,
        scenario=args.scenario,
        generator_run_id=args.generator_run_id,
        gl_table=args.gl_table,
    )
    print(_summary(result))
    path = write_dry_run(row, args.out)
    print(f"[audit] dry-run row appended -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

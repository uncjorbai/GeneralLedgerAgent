"""Persist a triage decision to the append-only decision log.

`fin_close.agent.triage_log` is a first-class Phase-1 deliverable (guardrail #5:
every agent decision is logged) and the seed of the A5 knowledge base. As with
the rest of Phase 1 we split PURE from IMPURE:

  * to_row()        — pure mapping TriageResult -> a schema-shaped dict. Testable.
  * write_dry_run() — append the row to a local JSONL. No Databricks.
  * write_delta()   — the live Delta append. Cluster-only; DEFERRED (stub) so we
                      never ship unverified Spark as if it worked.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from agent.triage import TriageResult

SIGNAL_JOBS_API = "jobs_api"  # verdict was recovered via the Databricks Jobs API


def to_row(
    result: TriageResult,
    *,
    agent_run_id: str,
    detected_at: datetime | str,
    signal_source: str,
) -> dict:
    """Map a TriageResult + the stamped fields onto the triage_log schema.

    Array fields are plain lists here (array<string> in Delta). `detected_at` is
    an ISO-8601 string here (a real timestamp in Delta). The reserved
    finding/proposal columns are intentionally absent in Phase 1; `rationale` we
    DO have from triage, so we populate it.
    """
    return {
        "agent_run_id": agent_run_id,
        "generator_run_id": str(result.generator_run_id),
        "detected_at": detected_at.isoformat() if isinstance(detected_at, datetime) else str(detected_at),
        "scenario": result.scenario,
        "gl_table": result.gl_table,
        "failed_checks": list(result.failed_checks),
        "gate_types": list(result.gate_types),
        "failure_class": result.failure_class,
        "triage_decision": result.triage_decision,
        "signal_source": signal_source,
        "evidence": result.evidence,
        "rationale": result.rationale,
    }


def write_dry_run(row: dict, path: str | Path) -> Path:
    """Append `row` as one JSON line to a local file. Laptop-only, no Databricks.

    JSONL (one JSON object per line) so repeated runs accumulate an inspectable
    local mirror of what the Delta table would hold.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def write_delta(row: dict, *, catalog: str, schema: str, table: str, spark=None) -> None:
    """LIVE path — append `row` to fin_close.agent.triage_log. CLUSTER-ONLY.

    Deferred to the Databricks session (step 7-write / step 9); left as an explicit
    stub so it is never mistaken for verified. Intended implementation:

        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
        df = spark.createDataFrame([row], _TRIAGE_LOG_DDL)   # array<string> + timestamp
        (df.write.mode("append").saveAsTable(f"{catalog}.{schema}.{table}"))

    The DDL must match to_row(): agent_run_id string, generator_run_id string,
    detected_at timestamp, scenario string, gl_table string, failed_checks
    array<string>, gate_types array<string>, failure_class string,
    triage_decision string, signal_source string, evidence string, rationale string.
    """
    raise NotImplementedError(
        "write_delta is the cluster-session task (step 7-write / step 9). "
        "Use write_dry_run() for local runs."
    )

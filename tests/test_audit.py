"""Unit tests for agent.audit — pure mapping + local dry-run writer. No Databricks."""

import json
from datetime import datetime, timezone

import pytest

from agent.audit import SIGNAL_JOBS_API, to_row, write_delta, write_dry_run
from agent.registry import load_registry
from agent.triage import triage
from agent.verdict import Verdict

CTX = dict(
    scenario="unbalanced_voucher",
    generator_run_id=123456789,
    gl_table="gl_journal_lines__unbalanced_voucher",
)


@pytest.fixture(scope="module")
def route_result():
    """A real 'route_to_agent' TriageResult to map."""
    v = Verdict(
        failed_checks=frozenset({"debits_equal_credits"}),
        parsed=True,
        evidence="Failing checks: ['debits_equal_credits']",
    )
    return triage(v, load_registry(), **CTX)


def test_to_row_maps_every_schema_field(route_result):
    when = datetime(2026, 8, 28, 13, 0, 0, tzinfo=timezone.utc)
    row = to_row(route_result, agent_run_id="local-abc", detected_at=when, signal_source=SIGNAL_JOBS_API)

    assert row["agent_run_id"] == "local-abc"
    assert row["generator_run_id"] == "123456789"          # coerced to string
    assert row["detected_at"] == when.isoformat()          # datetime -> ISO string
    assert row["scenario"] == "unbalanced_voucher"
    assert row["gl_table"] == "gl_journal_lines__unbalanced_voucher"
    assert row["failed_checks"] == ["debits_equal_credits"]  # list, not frozenset
    assert row["gate_types"] == ["dq_gate"]
    assert row["failure_class"] == "deterministic"
    assert row["triage_decision"] == "route_to_agent"
    assert row["signal_source"] == "jobs_api"
    assert "debits_equal_credits" in row["evidence"]
    assert row["rationale"]                                  # populated from triage


def test_to_row_is_json_serializable(route_result):
    row = to_row(route_result, agent_run_id="x", detected_at="2026-08-28T00:00:00+00:00", signal_source="jobs_api")
    # must round-trip cleanly (frozensets/tuples would break this)
    assert json.loads(json.dumps(row)) == row


def test_write_dry_run_appends_a_readable_json_line(tmp_path, route_result):
    out = tmp_path / "sub" / "triage_log.jsonl"   # parent dir created on demand
    row = to_row(route_result, agent_run_id="x", detected_at="t", signal_source="jobs_api")

    write_dry_run(row, out)
    write_dry_run(row, out)                        # append, don't overwrite

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["triage_decision"] == "route_to_agent"


def test_write_delta_is_a_marked_stub(route_result):
    row = to_row(route_result, agent_run_id="x", detected_at="t", signal_source="jobs_api")
    with pytest.raises(NotImplementedError, match="cluster-session"):
        write_delta(row, catalog="fin_close", schema="agent", table="triage_log")

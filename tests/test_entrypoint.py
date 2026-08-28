"""Unit tests for agent.entrypoint — the whole Phase-1 path, end to end, offline."""

import json
from datetime import datetime, timezone
from pathlib import Path

from agent.entrypoint import investigate, main

_FIXTURE = Path(__file__).parent / "fixtures" / "failed_run_output.json"


def _run_output():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_investigate_routes_a_deterministic_defect_end_to_end():
    result, row = investigate(
        run_output=_run_output(),
        scenario="unbalanced_voucher",
        generator_run_id=123456789,
        gl_table="gl_journal_lines__unbalanced_voucher",
        agent_run_id="local-fixed",                       # injected -> deterministic
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert result.failure_class == "deterministic"
    assert result.triage_decision == "route_to_agent"
    assert row["agent_run_id"] == "local-fixed"
    assert row["failed_checks"] == ["debits_equal_credits"]
    assert row["detected_at"] == "2026-08-28T00:00:00+00:00"


def test_investigate_treats_infra_crash_as_transient():
    result, row = investigate(
        run_output={"error": "OutOfMemoryError: Java heap space", "error_trace": None},
        scenario="unbalanced_voucher",
        generator_run_id=1,
        gl_table="gl_journal_lines__unbalanced_voucher",
    )
    assert result.failure_class == "transient"
    assert row["triage_decision"] == "leave_to_existing_path"


def test_main_cli_writes_a_dry_run_row(tmp_path, capsys):
    out = tmp_path / "triage_log.jsonl"
    rc = main([
        "--run-output", str(_FIXTURE),
        "--scenario", "unbalanced_voucher",
        "--generator-run-id", "123456789",
        "--gl-table", "gl_journal_lines__unbalanced_voucher",
        "--out", str(out),
    ])
    assert rc == 0

    # a row was written...
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["triage_decision"] == "route_to_agent"
    assert row["scenario"] == "unbalanced_voucher"

    # ...and the human summary was printed
    printed = capsys.readouterr().out
    assert "DETERMINISTIC" in printed
    assert "route_to_agent" in printed

"""Unit tests for agent.verdict — pure Python, no Databricks, no network.

Covers the brittle parser (the point of the module), the parsed-vs-transient
semantics, and the injected live-fetch path via a fake getter.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from agent.verdict import (
    Verdict,
    fetch_verdict,
    parse_failed_checks,
    verdict_from_error,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "failed_run_output.json"


def _load_fixture():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# parse_failed_checks — the quarantined parser
# --------------------------------------------------------------------------- #

def test_parses_single_check_from_the_gate_format():
    text = "DQ GATE FAILED — blocking Silver. Failing checks: ['debits_equal_credits']"
    assert parse_failed_checks(text) == frozenset({"debits_equal_credits"})


def test_parses_multiple_checks():
    text = "Failing checks: ['account_in_coa', 'debits_equal_credits']"
    assert parse_failed_checks(text) == frozenset(
        {"account_in_coa", "debits_equal_credits"}
    )


def test_no_marker_returns_none():
    # A genuine infra crash carries no 'Failing checks:' marker.
    text = "RuntimeError: Driver became unresponsive; run cancelled after timeout."
    assert parse_failed_checks(text) is None


def test_malformed_list_returns_none():
    # Marker present but the list isn't a valid literal -> unparseable -> None.
    text = "Failing checks: [debits_equal_credits]"  # unquoted -> literal_eval fails
    assert parse_failed_checks(text) is None


def test_empty_or_none_text_returns_none():
    assert parse_failed_checks("") is None
    assert parse_failed_checks(None) is None


def test_skips_the_fstring_source_line_in_a_traceback():
    # The traceback's raise-line contains 'Failing checks: {sorted(dq_failed)}'
    # (a `{`, not a `[`) and must NOT match; only the resolved list should.
    trace = (
        '    raise Exception(f"... Failing checks: {sorted(dq_failed)}")\n'
        "Exception: DQ GATE FAILED — blocking Silver. Failing checks: ['no_duplicate_vouchers']\n"
    )
    assert parse_failed_checks(trace) == frozenset({"no_duplicate_vouchers"})


# --------------------------------------------------------------------------- #
# verdict_from_error — parsed vs transient
# --------------------------------------------------------------------------- #

def test_verdict_parsed_true_for_a_data_defect():
    v = verdict_from_error(error="Failing checks: ['debits_equal_credits']")
    assert v.parsed is True
    assert v.failed_checks == frozenset({"debits_equal_credits"})
    assert "debits_equal_credits" in v.evidence


def test_verdict_parsed_false_for_infra_crash():
    v = verdict_from_error(error="OutOfMemoryError: Java heap space")
    assert v.parsed is False
    assert v.failed_checks == frozenset()


def test_verdict_reads_trace_when_error_is_empty():
    trace = "Exception: DQ GATE FAILED — blocking Silver. Failing checks: ['account_in_coa']\n"
    v = verdict_from_error(error=None, error_trace=trace)
    assert v.parsed is True
    assert v.failed_checks == frozenset({"account_in_coa"})


# --------------------------------------------------------------------------- #
# fetch_verdict — injected getter (no SDK/network)
# --------------------------------------------------------------------------- #

def test_fetch_verdict_with_injected_fake_getter():
    fx = _load_fixture()

    def fake_getter(run_id):
        # mimics databricks.sdk RunOutput: attributes .error / .error_trace
        assert run_id == fx["run_id"]
        return SimpleNamespace(error=fx["error"], error_trace=fx["error_trace"])

    v = fetch_verdict(fx["run_id"], output_getter=fake_getter)
    assert v == Verdict(
        failed_checks=frozenset({"debits_equal_credits"}),
        parsed=True,
        evidence="Failing checks: ['debits_equal_credits']",
    )


def test_fetch_verdict_transient_when_getter_returns_no_marker():
    def fake_getter(run_id):
        return SimpleNamespace(error="ClusterTerminated: spot reclaimed", error_trace=None)

    v = fetch_verdict(999, output_getter=fake_getter)
    assert v.parsed is False
    assert v.failed_checks == frozenset()


# --------------------------------------------------------------------------- #
# fixture integrity
# --------------------------------------------------------------------------- #

def test_synthesized_fixture_matches_the_gate_format():
    fx = _load_fixture()
    v = verdict_from_error(error=fx["error"], error_trace=fx["error_trace"])
    assert v.parsed is True
    assert v.failed_checks == frozenset({"debits_equal_credits"})

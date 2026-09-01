"""Unit tests for agent.registry — pure Python, no Databricks.

Two halves:
  1. Happy path — the REAL registry shipped in config/ loads and answers
     questions correctly.
  2. Validation — deliberately-broken registries are REJECTED, one test per
     rule. Each writes a tiny YAML to pytest's `tmp_path` (a fresh temp dir per
     test) and asserts load_registry raises RegistryError. This proves each
     guard actually guards — a validator with no failing test is just a claim.
"""

import textwrap

import pytest

from agent.registry import Check, RegistryError, load_registry


# --------------------------------------------------------------------------- #
# Happy path: the real registry in config/anomaly_registry.yaml
# --------------------------------------------------------------------------- #

def test_real_registry_loads_all_seven_checks():
    reg = load_registry()          # no path -> the shipped registry
    assert len(reg) == 7


def test_known_check_resolves_to_expected_entry():
    reg = load_registry()
    # `frozen` dataclasses get free __eq__, so we can compare the whole record.
    assert reg.get("debits_equal_credits") == Check(
        name="debits_equal_credits",
        gate="dq_gate",
        defect_class="unbalanced_voucher",
        deterministic=True,
        fails_task=True,
        remediation="restore_voucher_balance",   # Phase-3 slug (optional field)
    )


def test_unknown_check_returns_none():
    reg = load_registry()
    assert reg.get("no_such_check") is None      # unknown -> None, not an error
    assert "no_such_check" not in reg


def test_task_failing_checks_are_the_five_dq_gate_checks():
    reg = load_registry()
    assert reg.task_failing_checks() == {
        "debits_equal_credits",
        "no_duplicate_vouchers",
        "account_in_coa",
        "required_dimensions_present",
        "entity_and_period_present",
    }


def test_reverse_lookup_by_defect_class():
    reg = load_registry()
    c = reg.by_defect_class("intercompany_out_of_balance")
    assert c is not None
    assert c.name == "intercompany_eliminates"
    assert c.fails_task is False   # reconciliation check: doesn't wake the Agent yet


# --------------------------------------------------------------------------- #
# Validation: one broken-registry test per rule
# --------------------------------------------------------------------------- #

def _write(tmp_path, body: str):
    """Write a dedented YAML body to a temp file and return its path."""
    p = tmp_path / "reg.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_missing_field_is_rejected(tmp_path):
    p = _write(tmp_path, """
        checks:
          bad_check:
            gate: dq_gate
            defect_class: something
            deterministic: true
            # fails_task intentionally missing
    """)
    with pytest.raises(RegistryError, match="missing field"):
        load_registry(p)


def test_unknown_field_is_rejected(tmp_path):
    p = _write(tmp_path, """
        checks:
          bad_check:
            gate: dq_gate
            defect_class: something
            deterministic: true
            fails_task: true
            fail_task: true          # typo'd near-duplicate of fails_task
    """)
    with pytest.raises(RegistryError, match="unknown field"):
        load_registry(p)


def test_bad_gate_enum_is_rejected(tmp_path):
    p = _write(tmp_path, """
        checks:
          bad_check:
            gate: not_a_gate
            defect_class: something
            deterministic: true
            fails_task: true
    """)
    with pytest.raises(RegistryError, match="gate must be one of"):
        load_registry(p)


def test_non_bool_flag_is_rejected(tmp_path):
    p = _write(tmp_path, """
        checks:
          bad_check:
            gate: dq_gate
            defect_class: something
            deterministic: "yes"     # a string, not a bool
            fails_task: true
    """)
    with pytest.raises(RegistryError, match="deterministic must be true/false"):
        load_registry(p)


def test_duplicate_defect_class_is_rejected(tmp_path):
    p = _write(tmp_path, """
        checks:
          check_a:
            gate: dq_gate
            defect_class: dupe
            deterministic: true
            fails_task: true
          check_b:
            gate: dq_gate
            defect_class: dupe       # same scenario as check_a -> not allowed
            deterministic: true
            fails_task: true
    """)
    with pytest.raises(RegistryError, match="must be unique"):
        load_registry(p)


def test_blank_remediation_is_rejected(tmp_path):
    # remediation is OPTIONAL, but if present it must be a non-empty action slug.
    p = _write(tmp_path, """
        checks:
          bad_check:
            gate: dq_gate
            defect_class: something
            deterministic: true
            fails_task: true
            remediation: "   "       # present but blank
    """)
    with pytest.raises(RegistryError, match="remediation must be a non-empty string"):
        load_registry(p)


def test_remediation_is_optional_and_defaults_to_empty(tmp_path):
    # An entry with no remediation loads fine; the field defaults to "".
    p = _write(tmp_path, """
        checks:
          plain_check:
            gate: dq_gate
            defect_class: something
            deterministic: true
            fails_task: true
    """)
    reg = load_registry(p)
    assert reg.get("plain_check").remediation == ""


def test_empty_checks_is_rejected(tmp_path):
    p = _write(tmp_path, "checks: {}\n")
    with pytest.raises(RegistryError, match="non-empty"):
        load_registry(p)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "does_not_exist.yaml")


def test_aggregates_multiple_errors_in_one_message(tmp_path):
    # Two separate problems -> ONE raise that mentions BOTH (fail-loud-and-complete).
    p = _write(tmp_path, """
        checks:
          broken_one:
            gate: not_a_gate
            defect_class: x
            deterministic: true
            fails_task: true
          broken_two:
            gate: dq_gate
            defect_class: y
            deterministic: true
            # fails_task missing
    """)
    with pytest.raises(RegistryError) as exc:
        load_registry(p)
    msg = str(exc.value)
    assert "gate must be one of" in msg      # problem from broken_one
    assert "missing field" in msg            # problem from broken_two

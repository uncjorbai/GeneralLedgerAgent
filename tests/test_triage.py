"""Unit tests for agent.triage — pure Python, no Databricks.

Every path of the Q10 predicate gets a test, including the escalate-precedence
call and the reconciliation-only edge case. Verdicts are constructed directly
(we're testing triage, not the parser), against the REAL registry.
"""

import pytest

from agent.registry import load_registry
from agent.triage import (
    DECISION_ESCALATE,
    DECISION_LEAVE,
    DECISION_ROUTE,
    FAILURE_DETERMINISTIC,
    FAILURE_TRANSIENT,
    FAILURE_UNKNOWN,
    triage,
)
from agent.verdict import Verdict

# Shared context every call needs (the passthrough from the activation signal).
CTX = dict(
    scenario="unbalanced_voucher",
    generator_run_id=123456789,
    gl_table="gl_journal_lines__unbalanced_voucher",
)


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def _verdict(checks=(), parsed=True, evidence="evidence"):
    return Verdict(failed_checks=frozenset(checks), parsed=parsed, evidence=evidence)


# --------------------------------------------------------------------------- #
# 1) transient
# --------------------------------------------------------------------------- #

def test_unparseable_verdict_is_transient_and_left_alone(registry):
    r = triage(_verdict(parsed=False), registry, **CTX)
    assert r.failure_class == FAILURE_TRANSIENT
    assert r.triage_decision == DECISION_LEAVE
    assert r.failed_checks == ()
    assert r.unknown_checks == ()


# --------------------------------------------------------------------------- #
# 2) deterministic -> route
# --------------------------------------------------------------------------- #

def test_known_dq_gate_check_routes_to_agent(registry):
    r = triage(_verdict({"debits_equal_credits"}), registry, **CTX)
    assert r.failure_class == FAILURE_DETERMINISTIC
    assert r.triage_decision == DECISION_ROUTE
    assert r.failed_checks == ("debits_equal_credits",)
    assert r.gate_types == ("dq_gate",)
    assert r.unknown_checks == ()


def test_multiple_known_dq_gate_checks_route(registry):
    r = triage(_verdict({"debits_equal_credits", "account_in_coa"}), registry, **CTX)
    assert r.triage_decision == DECISION_ROUTE
    assert r.failed_checks == ("account_in_coa", "debits_equal_credits")  # sorted


# --------------------------------------------------------------------------- #
# 3) unknown check -> escalate
# --------------------------------------------------------------------------- #

def test_unknown_check_escalates(registry):
    r = triage(_verdict({"totally_made_up_check"}), registry, **CTX)
    assert r.failure_class == FAILURE_UNKNOWN
    assert r.triage_decision == DECISION_ESCALATE
    assert r.unknown_checks == ("totally_made_up_check",)


# --------------------------------------------------------------------------- #
# 4) mixed known + unknown -> escalate WINS (the precedence decision)
# --------------------------------------------------------------------------- #

def test_mixed_known_and_unknown_escalates_precedence(registry):
    r = triage(_verdict({"debits_equal_credits", "mystery_check"}), registry, **CTX)
    assert r.triage_decision == DECISION_ESCALATE      # not route, despite the known one
    assert r.failure_class == FAILURE_UNKNOWN
    assert r.unknown_checks == ("mystery_check",)
    assert "debits_equal_credits" in r.failed_checks   # still recorded


# --------------------------------------------------------------------------- #
# 5) Tier-D resolved: a known reconciliation check now ROUTES.
#    Its variance reaches us via the gate's structured exit (verdict_from_exit),
#    not a task failure; triage routes on `deterministic`, not `fails_task`.
# --------------------------------------------------------------------------- #

def test_known_reconciliation_check_routes(registry):
    # intercompany_eliminates is a deterministic defect (fails_task=False, but that
    # no longer gates routing — the flagship must reach the Agent).
    r = triage(_verdict({"intercompany_eliminates"}), registry, **CTX)
    assert r.failure_class == FAILURE_DETERMINISTIC
    assert r.triage_decision == DECISION_ROUTE
    assert r.gate_types == ("reconciliation",)


# --------------------------------------------------------------------------- #
# 6) edge: a known but NON-deterministic check -> escalate (branch 4).
#    No such check exists in the real registry today, so build a tiny fake one.
# --------------------------------------------------------------------------- #

def test_known_nondeterministic_check_escalates():
    from agent.registry import Check, Registry

    fake = Registry(checks={
        "flaky_probe": Check(
            name="flaky_probe", gate="dq_gate", defect_class="flaky",
            deterministic=False, fails_task=True,
        )
    })
    r = triage(_verdict({"flaky_probe"}), fake, **CTX)
    assert r.failure_class == FAILURE_UNKNOWN
    assert r.triage_decision == DECISION_ESCALATE
    assert r.unknown_checks == ()          # it IS known, just not deterministic


# --------------------------------------------------------------------------- #
# passthrough + evidence
# --------------------------------------------------------------------------- #

def test_context_and_evidence_pass_through(registry):
    r = triage(_verdict({"debits_equal_credits"}, evidence="Failing checks: ['debits_equal_credits']"),
               registry, **CTX)
    assert r.scenario == CTX["scenario"]
    assert r.generator_run_id == CTX["generator_run_id"]
    assert r.gl_table == CTX["gl_table"]
    assert "debits_equal_credits" in r.evidence
    assert r.rationale  # non-empty human explanation on every path

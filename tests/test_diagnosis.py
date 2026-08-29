"""Unit tests for agent.diagnosis — the structured, gradeable verdict. No LLM.

Covers the deterministic grounding (verdict + registry), the coercion of the
model-supplied fields, and the loop terminating on submit_diagnosis with a real
Diagnosis attached.
"""

import json
from pathlib import Path

import pytest

from agent.diagnosis import SUBMIT_DIAGNOSIS_TOOL, Diagnosis, build_diagnosis
from agent.investigate import INVESTIGATION_TOOLS, OUTCOME_COMPLETED, investigate
from agent.provider import LocalGLProvider
from agent.registry import load_registry

# Reuse the scripted model + context from the loop tests.
from test_investigate import CTX, IC, ScriptedModel, _final, _tools

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"

SUBMITTED = {
    "root_cause": "Both legs of two HQ intercompany management-fee vouchers were inflated "
                  "by 1,500, so the HQ receivable/income no longer mirror the subsidiary side.",
    "dollar_impact": 3000,
    "offending_vouchers": ["USMI260600105", "USMI260700105"],
    "offending_accounts": ["A14000", "R42000"],
    "confidence": "high",
    "narrative": "intercompany_eliminates failed: HQ side runs 3,000 hot vs the clean baseline.",
}


@pytest.fixture()
def provider():
    return LocalGLProvider(FIXTURE_ROOT, "intercompany_out_of_balance")


# --- build_diagnosis ------------------------------------------------------- #
def test_grounds_failed_check_and_defect_class_from_the_registry():
    d = build_diagnosis(CTX, SUBMITTED, registry=load_registry())
    assert d.failed_check == "intercompany_eliminates"
    assert d.defect_class == "intercompany_out_of_balance"   # derived, not model-supplied
    assert d.scenario == "intercompany_out_of_balance"


def test_carries_the_model_supplied_investigation_fields():
    d = build_diagnosis(CTX, SUBMITTED)
    assert d.dollar_impact == 3000.0
    assert d.offending_vouchers == ("USMI260600105", "USMI260700105")
    assert d.offending_accounts == ("A14000", "R42000")
    assert d.confidence == "high"


def test_coerces_messy_model_values():
    messy = {**SUBMITTED, "dollar_impact": "$3,000.00", "confidence": "HIGH", "offending_vouchers": []}
    d = build_diagnosis(CTX, messy)
    assert d.dollar_impact == 3000.0
    assert d.confidence == "high"
    assert d.offending_vouchers == ()


def test_unknown_confidence_falls_back():
    d = build_diagnosis(CTX, {**SUBMITTED, "confidence": "pretty sure"})
    assert d.confidence == "unknown"


def test_to_row_is_json_serializable():
    row = build_diagnosis(CTX, SUBMITTED).to_row()
    assert json.loads(json.dumps(row)) == row
    assert row["offending_vouchers"] == ["USMI260600105", "USMI260700105"]


# --- the tool + the loop integration --------------------------------------- #
def test_submit_diagnosis_tool_is_offered():
    assert SUBMIT_DIAGNOSIS_TOOL in INVESTIGATION_TOOLS
    assert set(SUBMIT_DIAGNOSIS_TOOL) >= {"name", "description", "input_schema"}
    assert "dollar_impact" in SUBMIT_DIAGNOSIS_TOOL["input_schema"]["properties"]


def test_loop_returns_a_structured_diagnosis(provider):
    model = ScriptedModel([
        _tools(("query_failing_table", {"filters": {"main_account": IC}, "group_by": "main_account"})),
        _tools(("submit_diagnosis", SUBMITTED)),
        _final("should never be reached"),   # submit_diagnosis is terminal
    ])
    result = investigate(context=CTX, provider=provider, model=model)

    assert result.outcome == OUTCOME_COMPLETED and result.escalated is False
    assert isinstance(result.diagnosis, Diagnosis)
    assert result.diagnosis.defect_class == "intercompany_out_of_balance"
    assert result.diagnosis.dollar_impact == 3000.0
    assert set(result.diagnosis.offending_vouchers) == {"USMI260600105", "USMI260700105"}
    assert result.narrative == result.diagnosis.narrative
    assert len(model.turns) == 1                 # the _final turn was never consumed


def test_submit_diagnosis_is_recorded_on_the_trace(provider):
    model = ScriptedModel([_tools(("submit_diagnosis", SUBMITTED))])
    result = investigate(context=CTX, provider=provider, model=model)
    assert any(e["tool"] == "submit_diagnosis" for e in result.trace.tool_calls())

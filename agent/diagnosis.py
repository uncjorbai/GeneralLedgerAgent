"""The structured diagnosis — the Agent's gradeable verdict (DESIGN §4.3 step 3, §7).

The investigation loop produces prose; the *scorecard* needs machine-readable
fields (which defect, which records, how much money). Rather than parse the
model's free text — brittle, and it makes the model's self-report unauditable —
the model DELIVERS its conclusion through a terminal `submit_diagnosis` tool. That
tool call is its genuine, structured output; this module defines the tool, the
`Diagnosis` object, and the assembly that grounds a couple of fields
deterministically.

Two kinds of field, on purpose:
  * DETERMINISTIC grounding — `failed_check` and the diagnosed `defect_class` come
    from the routing verdict + the registry, not the model. These anchor the
    record so a confused model can't mislabel the defect class.
  * INVESTIGATED (model-supplied) — root cause, dollar impact, the offending
    vouchers/accounts, confidence, narrative. These are what the investigation
    actually discovered, and what the Phase-4 scorecard grades against the answer
    key (e.g. did it name the right vouchers and the right dollar variance?).

`submit_diagnosis` is a deliberate addition to the tool surface (CLAUDE.md: adding
a tool is a decision): it is the terminal *delivery* action, not another data
read, and it is what makes the agent's output scoreable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from agent.registry import Registry, load_registry

_CONFIDENCE = ("high", "medium", "low")


@dataclass(frozen=True)
class Diagnosis:
    """The controller-ready, gradeable result of one investigation."""

    scenario: str
    failed_check: str                       # deterministic (from the verdict)
    defect_class: str                       # deterministic (failed_check -> registry)
    root_cause: str                         # model
    dollar_impact: float                    # model
    offending_vouchers: tuple[str, ...]     # model — graded vs the answer key
    offending_accounts: tuple[str, ...]     # model
    confidence: str                         # model: high | medium | low | unknown
    narrative: str                          # model — the prose summary

    def to_row(self) -> dict:
        row = asdict(self)
        row["offending_vouchers"] = list(self.offending_vouchers)
        row["offending_accounts"] = list(self.offending_accounts)
        return row


SUBMIT_DIAGNOSIS_TOOL: dict = {
    "name": "submit_diagnosis",
    "description": "Deliver your final structured diagnosis and END the investigation. "
                   "Call this exactly once, when you have the root cause. Every figure must "
                   "come from a tool result — do not estimate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "root_cause": {"type": "string", "description": "The mechanism: what was altered or missing, and why the check failed."},
            "dollar_impact": {"type": "number", "description": "The net dollar variance the defect introduced, from the data."},
            "offending_vouchers": {"type": "array", "items": {"type": "string"}, "description": "The specific voucher id(s) involved."},
            "offending_accounts": {"type": "array", "items": {"type": "string"}, "description": "The account(s) involved."},
            "confidence": {"type": "string", "enum": list(_CONFIDENCE), "description": "How sure you are."},
            "narrative": {"type": "string", "description": "A short controller-ready summary of the finding."},
        },
        "required": ["root_cause", "dollar_impact", "offending_vouchers", "confidence", "narrative"],
        "additionalProperties": False,
    },
}


def _to_float(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _defect_class(failed_checks, registry: Registry) -> str:
    for name in failed_checks:
        check = registry.get(name)
        if check is not None:
            return check.defect_class
    return ""


def build_diagnosis(context: dict, submitted: dict, *, registry: Registry | None = None) -> Diagnosis:
    """Assemble a Diagnosis from the routing context + the model's submit_diagnosis input.

    `failed_check` / `defect_class` are grounded in the verdict + registry; the rest
    is taken from what the model submitted (coerced to safe types). `registry` is
    injectable for tests; it defaults to the real registry.
    """
    registry = registry or load_registry()
    failed = list(context.get("failed_checks", []))
    submitted = submitted or {}
    confidence = str(submitted.get("confidence", "unknown")).strip().lower()
    return Diagnosis(
        scenario=context.get("scenario", ""),
        failed_check=failed[0] if failed else "",
        defect_class=_defect_class(failed, registry),
        root_cause=str(submitted.get("root_cause", "")).strip(),
        dollar_impact=_to_float(submitted.get("dollar_impact")),
        offending_vouchers=tuple(str(v) for v in (submitted.get("offending_vouchers") or [])),
        offending_accounts=tuple(str(a) for a in (submitted.get("offending_accounts") or [])),
        confidence=confidence if confidence in _CONFIDENCE else "unknown",
        narrative=str(submitted.get("narrative", "")).strip(),
    )

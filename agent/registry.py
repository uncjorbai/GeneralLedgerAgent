"""Load and validate the anomaly registry.

`config/anomaly_registry.yaml` is the Agent's single source of truth for the
question: "is this failing check a known, deterministic data defect I should act
on?" Every downstream module (triage, audit) asks THIS module that question.
Nothing here touches Databricks, Spark, or an LLM — it is pure, laptop-testable
Python.

Design intent: **fail loud and early** on a malformed registry. A typo in that
file could otherwise cause the Agent to mis-route a real failure — a safety bug,
not a cosmetic one. So "loading" the registry doubles as strict schema
validation: a bad file raises here, at the edge, before any decision logic runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# The registry lives at <repo>/config/anomaly_registry.yaml. We resolve it
# relative to THIS file (__file__), not the current working directory, so the
# module loads correctly no matter where the process was launched from.
_DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "anomaly_registry.yaml"
)

# The only two check suites the pipeline has (mirrors dq_gate.py). Anything else
# is a typo or an un-negotiated change we want to hear about immediately.
_VALID_GATES = {"dq_gate", "reconciliation"}

# Exactly these fields on every entry — no more (unknown = typo), no less.
_REQUIRED_FIELDS = {"gate", "defect_class", "deterministic", "fails_task"}


class RegistryError(ValueError):
    """Raised when the registry is missing, unparseable, or malformed.

    Carries EVERY problem found in one message (not just the first), so a single
    run of the loader tells you everything to fix.
    """


@dataclass(frozen=True)
class Check:
    """One row of the registry: a named check and what it means.

    `frozen=True` makes instances immutable — once loaded, a Check cannot be
    mutated anywhere downstream. The registry is a fact, not a scratchpad.
    """

    name: str            # the check's name, verbatim from dq_gate.py
    gate: str            # "dq_gate" | "reconciliation"
    defect_class: str    # the named scenario this check catches
    deterministic: bool  # true for every data-defect check
    fails_task: bool     # does tripping this check fail the Databricks task today?


@dataclass(frozen=True)
class Registry:
    """The whole registry: check-name -> Check, plus the lookups triage needs."""

    checks: dict[str, Check]

    def get(self, check_name: str) -> Check | None:
        """The Check for this name, or None if the name is unknown.

        `None` is meaningful, not an error: triage reads an unknown failing-check
        as "not a registered deterministic defect" — i.e. treat as transient /
        escalate rather than route to the Agent.
        """
        return self.checks.get(check_name)

    def __contains__(self, check_name: str) -> bool:
        return check_name in self.checks

    def __len__(self) -> int:
        return len(self.checks)

    def task_failing_checks(self) -> set[str]:
        """Names of checks that FAIL the Databricks task today (fails_task=True).

        These are the ones that actually wake the Agent right now. Reconciliation
        checks are known but don't fail the task yet (see PIPELINE_CONTRACT Tier D).
        """
        return {name for name, c in self.checks.items() if c.fails_task}

    def by_defect_class(self, defect_class: str) -> Check | None:
        """Reverse lookup: scenario -> the check that catches it.

        Safe because `defect_class` is validated to be unique across the registry.
        """
        for c in self.checks.values():
            if c.defect_class == defect_class:
                return c
        return None


def load_registry(path: str | Path | None = None) -> Registry:
    """Read, validate, and return the registry. Raise RegistryError if invalid."""
    path = Path(path) if path is not None else _DEFAULT_REGISTRY_PATH

    if not path.exists():
        raise RegistryError(f"Registry file not found: {path}")

    # yaml.safe_load (NEVER plain yaml.load): safe_load will not construct
    # arbitrary Python objects from the file, so a malicious or typo'd YAML can't
    # execute code. Untrusted-input hygiene, cheap to always do.
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RegistryError(f"Registry is not valid YAML: {e}") from e

    # --- top-level shape (these are fatal on their own, so raise immediately) ---
    if not isinstance(raw, dict) or "checks" not in raw:
        raise RegistryError("Registry must be a mapping with a top-level 'checks:' key.")
    raw_checks = raw["checks"]
    if not isinstance(raw_checks, dict) or not raw_checks:
        raise RegistryError("'checks:' must be a non-empty mapping of check-name -> fields.")

    # --- per-entry validation: collect ALL problems, raise once at the end ---
    errors: list[str] = []
    seen_defect_classes: dict[str, str] = {}  # defect_class -> first check that used it

    for name, fields in raw_checks.items():
        if not isinstance(fields, dict):
            errors.append(f"{name}: entry must be a mapping of fields, got {type(fields).__name__}.")
            continue

        present = set(fields)
        missing = _REQUIRED_FIELDS - present
        unknown = present - _REQUIRED_FIELDS
        if missing:
            errors.append(f"{name}: missing field(s): {sorted(missing)}.")
        if unknown:
            errors.append(f"{name}: unknown field(s): {sorted(unknown)} (typo?).")
        if missing or unknown:
            continue  # shape is wrong; skip value checks for this entry

        if fields["gate"] not in _VALID_GATES:
            errors.append(f"{name}: gate must be one of {sorted(_VALID_GATES)}, got {fields['gate']!r}.")
        if not isinstance(fields["defect_class"], str) or not fields["defect_class"].strip():
            errors.append(f"{name}: defect_class must be a non-empty string.")
        # NOTE: in Python `bool` is a subclass of `int`, so isinstance(x, bool) is
        # the strict check — a stray 1/0 would slip past isinstance(x, int).
        if not isinstance(fields["deterministic"], bool):
            errors.append(f"{name}: deterministic must be true/false.")
        if not isinstance(fields["fails_task"], bool):
            errors.append(f"{name}: fails_task must be true/false.")

        # defect_class must be unique (1:1 scenario<->check; see PHASE1_PLAN assumptions)
        dc = fields["defect_class"]
        if isinstance(dc, str):
            if dc in seen_defect_classes:
                errors.append(
                    f"{name}: defect_class {dc!r} already used by "
                    f"{seen_defect_classes[dc]!r} (must be unique)."
                )
            else:
                seen_defect_classes[dc] = name

    if errors:
        raise RegistryError("Invalid anomaly registry:\n  - " + "\n  - ".join(errors))

    # Second pass: everything is validated, so building is now safe and boring.
    checks = {
        name: Check(
            name=name,
            gate=f["gate"],
            defect_class=f["defect_class"],
            deterministic=f["deterministic"],
            fails_task=f["fails_task"],
        )
        for name, f in raw_checks.items()
    }
    return Registry(checks=checks)

"""The remediation drafter — turn a Diagnosis into a STAGED, human-approvable fix.

DESIGN §4.3 step 4 / §6. Phase 3. This is where the Agent stops diagnosing and
proposes the specific correcting action for the defect it found. Two hard rules
shape every line of this module:

  * READ-MOSTLY (guardrail #2). The drafter is a PURE function of the Diagnosis +
    the read-only provider. It computes a *proposal* — a description of the fix —
    and NEVER mutates GL data. It does not touch Silver/Gold, and it does not even
    apply the correction; a human approves the staged proposal first (guardrail
    #3), and applying it is a later step (Phase 4 scorecard), not this one.
  * MODEL STAYS READ-ONLY. Deliberately, the model is NOT given a write tool (a
    conservative reading of DESIGN §5). The proposal is derived deterministically
    here, AFTER the investigation, from the offending records the Diagnosis already
    carries. Same pattern as `submit_diagnosis -> build_diagnosis`: the intelligent
    step produces structured facts, and plain Python turns them into the artifact.

How the correction is found WITHOUT the answer key: the clean baseline is a
regenerated-from-seed read tool the Agent already has (DESIGN tech notes,
`provider.clean_baseline()`) — NOT the answer key (`run_manifest.json`, which the
provider refuses to resolve, guardrail #4). For the "an existing value was
altered" defects, the honest, exact correction is simply "restore the changed
lines to their baseline values." That is what `restore_intercompany_side` does.

Scope this session: `intercompany_out_of_balance` end-to-end (DESIGN §6 build
order). The other six slugs are declared in the registry but raise
`UnsupportedRemediation` until the generalization pass — one working path first
(CLAUDE.md), not seven stubs.

PURE/IMPURE split mirrors Phase-1 `audit.py`:
  * draft_proposal()  — pure: (Diagnosis, provider) -> RemediationProposal.
  * proposal_to_row() — pure: proposal -> a staging-table-shaped dict.
  * write_dry_run()    — reused from audit.py: append the row to local JSONL.
  * write_delta()      — the live Delta append to fin_close.agent.*; cluster-only,
                         DEFERRED (stub), never shipped as if verified.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from agent.audit import write_dry_run  # reused: append one row as JSONL, laptop-only
from agent.diagnosis import Diagnosis
from agent.registry import Registry, load_registry

# The staging destination. NB: the `agent` schema only — never silver/gold/serving.
STAGING_CATALOG = "fin_close"
STAGING_SCHEMA = "agent"
STAGING_TABLE = "remediation_proposals"

STATUS_PROPOSED = "proposed"  # staged, awaiting human approval; never auto-applied

# The line-key that identifies one journal line across the failing/clean frames.
_LINE_KEY = ("company_id", "voucher", "line_number")
# The two amount columns a correction can restate.
_AMOUNT_COLS = ("amount_debit", "amount_credit")


class RemediationError(RuntimeError):
    """The drafter was handed something it cannot draft from (e.g. no offending
    vouchers, or a diagnosis whose lines are not found in the data)."""


class UnsupportedRemediation(RemediationError):
    """The defect's remediation slug is declared but not implemented this session.

    A clear, honest failure — the registry promises a correcting action the code
    does not yet provide — rather than a silent no-op or a wrong fix.
    """


# --------------------------------------------------------------------------- #
# the proposal objects (what a human reviews / the scorer later applies)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LineCorrection:
    """One line-level restatement: set `field` on this line to `corrected_value`.

    Reviewer-legible on purpose: it names the exact line, the field, what it is
    now, and what it should be. `delta = corrected - current` is the signed change.
    """

    company_id: str
    voucher: str
    line_number: int
    main_account: str
    field: str            # "amount_debit" | "amount_credit"
    current_value: float
    corrected_value: float

    @property
    def delta(self) -> float:
        return round(self.corrected_value - self.current_value, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["delta"] = self.delta
        return d


@dataclass(frozen=True)
class RemediationProposal:
    """A staged, human-approvable correcting action for one diagnosed defect.

    Frozen: a proposal is a record, not a scratchpad. `status` is always
    `proposed` at draft time — an approval/apply step (later) is what advances it.
    """

    scenario: str
    defect_class: str
    check: str                              # the check this fix should make pass
    action_type: str                        # the remediation slug (registry)
    target_vouchers: tuple[str, ...]        # vouchers the correction touches
    corrections: tuple[LineCorrection, ...]
    dollar_impact: float                    # total variance the fix restores
    narrative: str                          # controller-ready summary
    status: str = STATUS_PROPOSED

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "defect_class": self.defect_class,
            "check": self.check,
            "action_type": self.action_type,
            "target_vouchers": list(self.target_vouchers),
            "corrections": [c.to_dict() for c in self.corrections],
            "dollar_impact": self.dollar_impact,
            "narrative": self.narrative,
            "status": self.status,
        }


# --------------------------------------------------------------------------- #
# the drafter — dispatch on the registry's remediation slug
# --------------------------------------------------------------------------- #
def draft_proposal(diagnosis: Diagnosis, provider, *, registry: Registry | None = None) -> RemediationProposal:
    """Derive a staged RemediationProposal from a completed Diagnosis.

    Pure with respect to (diagnosis, provider): reads GL data through the provider,
    writes nothing. Dispatches on the defect's `remediation` slug from the
    registry; raises UnsupportedRemediation for slugs not implemented this session.
    """
    registry = registry or load_registry()
    check = registry.get(diagnosis.failed_check)
    if check is None:
        raise RemediationError(
            f"No registry entry for failed_check '{diagnosis.failed_check}'; cannot draft a fix."
        )
    slug = check.remediation
    drafter = _DRAFTERS.get(slug)
    if drafter is None:
        raise UnsupportedRemediation(
            f"Remediation '{slug or '(none)'}' for defect '{diagnosis.defect_class}' is not "
            "implemented yet (Phase 3 flagship = restore_intercompany_side only)."
        )
    return drafter(diagnosis, provider, check.name)


def _restore_from_baseline(diagnosis: Diagnosis, provider, check_name: str, action_type: str) -> RemediationProposal:
    """Correct an 'existing value was altered' defect by restoring the offending
    vouchers' lines to their clean-baseline amounts.

    The baseline is regenerated from the same seed, so the pre-defect value is
    exact — the correction is a faithful restoration, not an estimate. Every
    changed amount on an offending voucher becomes one LineCorrection.
    """
    vouchers = tuple(diagnosis.offending_vouchers)
    if not vouchers:
        raise RemediationError(
            f"Diagnosis for '{diagnosis.scenario}' names no offending vouchers; nothing to restore."
        )

    failing = provider.failing_table()
    clean = provider.clean_baseline()
    baseline = clean.set_index(list(_LINE_KEY))

    sub = failing[failing["voucher"].isin(vouchers)].sort_values(list(_LINE_KEY))
    if sub.empty:
        raise RemediationError(
            f"None of the offending vouchers {vouchers} were found in the failing table."
        )

    corrections: list[LineCorrection] = []
    for _, row in sub.iterrows():
        key = tuple(row[k] for k in _LINE_KEY)
        if key not in baseline.index:
            # A line with no baseline twin (e.g. a wholly injected line) is out of
            # scope for a pure restore; surface it rather than guess.
            raise RemediationError(
                f"Line {key} on an offending voucher has no clean-baseline counterpart; "
                "restore cannot be drafted safely."
            )
        base = baseline.loc[key]
        for col in _AMOUNT_COLS:
            cur = round(float(row[col]), 2)
            target = round(float(base[col]), 2)
            if cur != target:
                corrections.append(
                    LineCorrection(
                        company_id=str(row["company_id"]),
                        voucher=str(row["voucher"]),
                        line_number=int(row["line_number"]),
                        main_account=str(row["main_account"]),
                        field=col,
                        current_value=cur,
                        corrected_value=target,
                    )
                )

    # Per-voucher impact = how far the voucher's total debit moved off baseline.
    # Because each voucher stays internally balanced, the credit-side figure is
    # identical, so this neither privileges a side nor double-counts a move's two legs.
    def _voucher_impact(v: str) -> float:
        f_deb = float(failing.loc[failing["voucher"] == v, "amount_debit"].sum())
        b_deb = float(clean.loc[clean["voucher"] == v, "amount_debit"].sum())
        return round(abs(f_deb - b_deb), 2)

    dollar_impact = round(sum(_voucher_impact(v) for v in vouchers), 2)

    accounts = sorted({c.main_account for c in corrections})
    narrative = (
        f"Intercompany balance out by ${dollar_impact:,.2f} across {len(vouchers)} voucher(s) "
        f"({', '.join(vouchers)}): the {', '.join(accounts)} line(s) were altered off their "
        f"seed baseline. Proposed fix: restore the {len(corrections)} changed amount(s) to the "
        f"clean-baseline values so the intercompany side eliminates. Staged for approval; not applied."
    )
    return RemediationProposal(
        scenario=diagnosis.scenario,
        defect_class=diagnosis.defect_class,
        check=check_name,
        action_type=action_type,
        target_vouchers=vouchers,
        corrections=tuple(corrections),
        dollar_impact=dollar_impact,
        narrative=narrative,
    )


def _draft_restore_intercompany_side(diagnosis: Diagnosis, provider, check_name: str) -> RemediationProposal:
    return _restore_from_baseline(diagnosis, provider, check_name, action_type="restore_intercompany_side")


# slug -> drafter. Only the flagship is wired this session; the rest raise via the
# `draft_proposal` dispatch (UnsupportedRemediation) until the generalization pass.
_DRAFTERS = {
    "restore_intercompany_side": _draft_restore_intercompany_side,
}


# --------------------------------------------------------------------------- #
# staging persistence — PURE/IMPURE split, mirrors audit.py
# --------------------------------------------------------------------------- #
def proposal_to_row(
    proposal: RemediationProposal,
    *,
    agent_run_id: str,
    drafted_at: datetime | str,
) -> dict:
    """Map a RemediationProposal + stamped fields onto the staging-table schema.

    `corrections` is a list of structs; `drafted_at` is ISO-8601 here (a real
    timestamp in Delta). Shape mirrors audit.to_row so the two agent tables are
    consistent.
    """
    return {
        "agent_run_id": agent_run_id,
        "drafted_at": drafted_at.isoformat() if isinstance(drafted_at, datetime) else str(drafted_at),
        "scenario": proposal.scenario,
        "defect_class": proposal.defect_class,
        "check": proposal.check,
        "action_type": proposal.action_type,
        "target_vouchers": list(proposal.target_vouchers),
        "corrections": [c.to_dict() for c in proposal.corrections],
        "dollar_impact": proposal.dollar_impact,
        "narrative": proposal.narrative,
        "status": proposal.status,
    }


def write_delta(row: dict, *, catalog: str = STAGING_CATALOG, schema: str = STAGING_SCHEMA,
                table: str = STAGING_TABLE, spark=None) -> None:
    """LIVE path — append `row` to fin_close.agent.remediation_proposals. CLUSTER-ONLY.

    Deferred to the Databricks session, the same discipline as audit.write_delta;
    left as an explicit stub so it is never mistaken for verified. Intended:

        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
        df = spark.createDataFrame([row], _PROPOSAL_DDL)  # target_vouchers array<string>,
                                                          # corrections array<struct>, ...
        (df.write.mode("append").saveAsTable(f"{catalog}.{schema}.{table}"))

    STAGING ONLY — the `agent` schema. This function must never write silver, gold,
    or a serving table (guardrail #2). Use write_dry_run() for local runs.
    """
    raise NotImplementedError(
        "write_delta is the cluster-session task. Use write_dry_run() (reused from "
        "audit.py) for local runs; the agent stages to fin_close.agent only."
    )

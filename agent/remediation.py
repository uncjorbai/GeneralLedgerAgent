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
provider refuses to resolve, guardrail #4). The unifying insight: restoring an
offending voucher to its seed baseline is, by construction, a valid fix for EVERY
defect class — the gate passed on the baseline, so it passes on the restored data.

The seven defects need just two primitive operations, both a diff against baseline:
  * RESTATE — a field on a matched line differs from baseline; set it back. Covers
    six of seven (the altered amount, the wrong account, the blanked dimension, the
    nulled entity/period, the mis-cut date, the altered intercompany side). They
    differ only in WHICH column moved — which the diff discovers, not per-defect code.
  * REMOVE  — the failing voucher has more copies of a line than baseline; drop the
    extras. Covers `duplicate_voucher`.

The registry's `remediation` slug is the human-facing action label; the engine that
produces the corrections is shared. (`add`-a-line is a third primitive that no
seeded fixture needs, so it is deliberately not implemented — a validator with no
failing test is just a claim; we raise clearly if a baseline line is missing.)

PURE/IMPURE split mirrors Phase-1 `audit.py`:
  * draft_proposal()  — pure: (Diagnosis, provider) -> RemediationProposal.
  * proposal_to_row() — pure: proposal -> a staging-table-shaped dict.
  * write_dry_run()    — reused from audit.py: append the row to local JSONL.
  * write_delta()      — the live Delta append to fin_close.agent.*; cluster-only,
                         DEFERRED (stub), never shipped as if verified.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import pandas as pd

from agent.audit import write_dry_run  # reused: append one row as JSONL, laptop-only
from agent.diagnosis import Diagnosis
from agent.registry import Registry, load_registry

# The staging destination. NB: the `agent` schema only — never silver/gold/serving.
STAGING_CATALOG = "fin_close"
STAGING_SCHEMA = "agent"
STAGING_TABLE = "remediation_proposals"

STATUS_PROPOSED = "proposed"  # staged, awaiting human approval; never auto-applied

OP_RESTATE = "restate"
OP_REMOVE = "remove"

# Full line identity (kept for the human-facing correction record). company_id can
# be null (that IS the defect for missing_entity_or_period), so the DIFF matches on
# _MATCH_KEY below, which never includes a column a defect can blank out.
_LINE_KEY = ("company_id", "voucher", "line_number")
_MATCH_KEY = ("voucher", "line_number")   # globally unique; null-safe join key
_AMOUNT_COLS = ("amount_debit", "amount_credit")
# Columns a RESTATE may correct — every column a seeded defect can alter. The diff
# flags whichever actually moved off baseline, so no per-defect column knowledge is
# needed. (line_number/voucher are identity, not correctable.)
_CORRECTABLE = (
    "company_id", "main_account", "amount_debit", "amount_credit",
    "department", "cost_center", "period", "accounting_date",
)


class RemediationError(RuntimeError):
    """The drafter was handed something it cannot draft from (e.g. no offending
    vouchers, or a diagnosis whose lines are not found in the data)."""


class UnsupportedRemediation(RemediationError):
    """The defect's remediation slug is declared but not implemented.

    A clear, honest failure — the registry promises a correcting action the code
    does not yet provide — rather than a silent no-op or a wrong fix.
    """


# --------------------------------------------------------------------------- #
# value normalization — one dataframe cell -> a JSON-safe, comparable scalar
# --------------------------------------------------------------------------- #
def _norm(v):
    """NaN/blank -> None; Timestamp -> 'YYYY-MM-DD'; numpy -> python; float -> 2dp.

    The single definition of "equal to baseline" used by the diff, so a datetime and
    a string date, or a NaN and a blank, compare the way an accountant means them to.
    """
    if v is None or (not isinstance(v, (list, dict)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


# --------------------------------------------------------------------------- #
# the proposal objects (what a human reviews / the scorer later applies)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LineCorrection:
    """One correcting operation on one journal line.

    RESTATE names the field, its current value, and the baseline value to set.
    REMOVE drops one copy of a duplicated line (field/values are None). Reviewer-
    legible on purpose: it always names the exact voucher + line and what changes.
    """

    op: str                          # "restate" | "remove"
    voucher: str
    line_number: int
    main_account: str
    company_id: str | None = None
    field: str | None = None         # restate only
    current_value: object = None     # restate only
    corrected_value: object = None   # restate only

    @property
    def delta(self):
        """Signed numeric change for a restate of an amount column; else None."""
        if isinstance(self.current_value, (int, float)) and isinstance(self.corrected_value, (int, float)):
            return round(self.corrected_value - self.current_value, 2)
        return None

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
    dollar_impact: float                    # dollar variance the defect introduced
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
    writes nothing. Dispatches on the defect's `remediation` slug from the registry;
    raises UnsupportedRemediation for slugs with no drafter.
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
            f"Remediation '{slug or '(none)'}' for defect '{diagnosis.defect_class}' has no drafter."
        )
    return drafter(diagnosis, provider, slug, check.name)


def _offending(diagnosis: Diagnosis, provider):
    """The offending vouchers + the failing/clean frames restricted to them.

    Shared preamble for every drafter. Raises if the diagnosis named no vouchers,
    or if none of them are present in the failing table.
    """
    vouchers = tuple(diagnosis.offending_vouchers)
    if not vouchers:
        raise RemediationError(
            f"Diagnosis for '{diagnosis.scenario}' names no offending vouchers; nothing to correct."
        )
    failing = provider.failing_table()
    clean = provider.clean_baseline()
    fsub = failing[failing["voucher"].isin(vouchers)]
    if fsub.empty:
        raise RemediationError(
            f"None of the offending vouchers {vouchers} were found in the failing table."
        )
    return vouchers, failing, clean, fsub


def _proposal(diagnosis, action_type, check_name, vouchers, corrections, dollar_impact, narrative):
    return RemediationProposal(
        scenario=diagnosis.scenario,
        defect_class=diagnosis.defect_class,
        check=check_name,
        action_type=action_type,
        target_vouchers=tuple(vouchers),
        corrections=tuple(corrections),
        dollar_impact=round(float(dollar_impact), 2),
        narrative=narrative,
    )


def _draft_restore(diagnosis: Diagnosis, provider, action_type: str, check_name: str) -> RemediationProposal:
    """Restore offending vouchers to baseline by RESTATING every changed field.

    Matches each failing line to its baseline twin (voucher+line_number) and emits a
    RESTATE for any correctable column whose value moved. Works for all six non-
    duplicate defects because the diff discovers the altered column itself.
    """
    vouchers, failing, clean, fsub = _offending(diagnosis, provider)
    baseline = clean.set_index(list(_MATCH_KEY))

    corrections: list[LineCorrection] = []
    for _, row in fsub.sort_values(list(_MATCH_KEY)).iterrows():
        key = (row["voucher"], int(row["line_number"]))
        if key not in baseline.index:
            # No baseline twin => this would need an ADD, which no seeded fixture
            # exercises. Surface it rather than guess a line into existence.
            raise RemediationError(
                f"Line {key} on an offending voucher has no clean-baseline counterpart; "
                "a restore cannot be drafted safely (add-a-line is not supported)."
            )
        base = baseline.loc[key]
        for col in _CORRECTABLE:
            if col not in row or col not in base:
                continue
            cur, target = _norm(row[col]), _norm(base[col])
            if cur != target:
                corrections.append(LineCorrection(
                    op=OP_RESTATE,
                    voucher=str(row["voucher"]),
                    line_number=int(row["line_number"]),
                    main_account=str(row["main_account"]),
                    company_id=_norm(row["company_id"]),
                    field=col,
                    current_value=cur,
                    corrected_value=target,
                ))
    if not corrections:
        raise RemediationError(
            f"No line on the offending vouchers differs from baseline for '{diagnosis.scenario}'."
        )

    dollar_impact = _restate_dollar_impact(vouchers, failing, clean)
    narrative = _narrate(action_type, check_name, vouchers, corrections, dollar_impact)
    return _proposal(diagnosis, action_type, check_name, vouchers, corrections, dollar_impact, narrative)


def _draft_remove_duplicate(diagnosis: Diagnosis, provider, action_type: str, check_name: str) -> RemediationProposal:
    """Restore offending vouchers to baseline by REMOVING duplicated lines.

    For each (voucher, line_number) the failing table holds more copies of than the
    baseline, propose dropping the excess copies. The duplicated dollars are the
    reported impact.
    """
    vouchers, failing, clean, fsub = _offending(diagnosis, provider)
    base_counts = clean.groupby(list(_MATCH_KEY)).size()

    corrections: list[LineCorrection] = []
    for key, group in fsub.groupby(list(_MATCH_KEY), sort=True):
        excess = len(group) - int(base_counts.get(key, 0))
        if excess <= 0:
            continue
        row = group.iloc[0]
        for _ in range(excess):
            corrections.append(LineCorrection(
                op=OP_REMOVE,
                voucher=str(row["voucher"]),
                line_number=int(row["line_number"]),
                main_account=str(row["main_account"]),
                company_id=_norm(row["company_id"]),
            ))
    if not corrections:
        raise RemediationError(
            f"No duplicated line found on the offending vouchers for '{diagnosis.scenario}'."
        )

    dollar_impact = sum(
        abs(float(failing.loc[failing["voucher"] == v, "amount_debit"].sum())
            - float(clean.loc[clean["voucher"] == v, "amount_debit"].sum()))
        for v in vouchers
    )
    narrative = _narrate(action_type, check_name, vouchers, corrections, dollar_impact)
    return _proposal(diagnosis, action_type, check_name, vouchers, corrections, dollar_impact, narrative)


def _restate_dollar_impact(vouchers, failing, clean) -> float:
    """Dollar variance a value-alteration defect introduced.

    Per voucher, take the larger of the total debit move and the total credit move
    off baseline. Because a voucher stays internally balanced under these defects,
    the two are equal for a genuine amount change (so no double-count of a move's two
    legs); for non-amount defects (account/dimension/date) both are 0 — correctly 0
    dollars of variance. Summed across the offending vouchers.
    """
    total = 0.0
    for v in vouchers:
        f = failing[failing["voucher"] == v]
        c = clean[clean["voucher"] == v]
        d = abs(float(f["amount_debit"].sum()) - float(c["amount_debit"].sum()))
        cr = abs(float(f["amount_credit"].sum()) - float(c["amount_credit"].sum()))
        total += max(d, cr)
    return round(total, 2)


# --------------------------------------------------------------------------- #
# narrative — one controller-ready sentence, tailored by action label
# --------------------------------------------------------------------------- #
_LEAD = {
    "restore_voucher_balance": "The voucher's debits and credits are out of balance by ${imp:,.2f}",
    "remove_duplicate_line": "A voucher was posted more than once, inflating totals by ${imp:,.2f}",
    "map_account": "A line is posted to an account absent from the chart of accounts",
    "populate_dimension": "A line is missing its required department dimension",
    "populate_field": "A line is missing its required entity/period key",
    "shift_period": "An entry is dated outside the close period it is booked to",
    "restore_intercompany_side": "The intercompany side does not eliminate, out by ${imp:,.2f}",
}


def _narrate(action_type: str, check_name: str, vouchers, corrections, impact: float) -> str:
    lead = _LEAD.get(action_type, "A data-quality defect was found").format(imp=impact)
    vlist = ", ".join(vouchers)
    if corrections and corrections[0].op == OP_REMOVE:
        what = f"remove {len(corrections)} duplicate line(s)"
    else:
        cols = sorted({c.field for c in corrections if c.field})
        what = f"restore {len(corrections)} value(s) ({', '.join(cols)})"
    return (
        f"{lead}, across {len(vouchers)} voucher(s) ({vlist}). Proposed fix "
        f"({action_type}): {what}, matching the seed baseline so `{check_name}` passes. "
        f"Staged for approval; not applied."
    )


# slug -> drafter. Six defects restore off-baseline fields; duplicate removes extras.
_DRAFTERS = {
    "restore_intercompany_side": _draft_restore,
    "restore_voucher_balance": _draft_restore,
    "map_account": _draft_restore,
    "populate_dimension": _draft_restore,
    "populate_field": _draft_restore,
    "shift_period": _draft_restore,
    "remove_duplicate_line": _draft_remove_duplicate,
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

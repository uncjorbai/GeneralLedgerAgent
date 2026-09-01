"""An offline mirror of the pipeline's DQ + reconciliation gate (DESIGN §7).

The scorecard's closed loop (§3) rests on one claim: *the same gate that caught the
defect validates the agent's fix*. Live, that gate is the Generator's
`notebooks/dq_gate.py` (Spark, on a cluster). Offline, we need a faithful pandas
twin so the scorer can re-gate the corrected data on a laptop.

This is that twin — the seven checks, transcribed one-for-one from `dq_gate.py`:

    dq_gate (block Silver):  debits_equal_credits, no_duplicate_vouchers,
                             account_in_coa, required_dimensions_present,
                             entity_and_period_present
    reconciliation (preview): period_cutoff_correct, intercompany_eliminates

Fidelity is not asserted here — it is PROVEN in `tests/test_gate.py` against the
answer keys: clean passes all seven, and each scenario's failing data fails exactly
the check its answer key names. A mirror nobody validated would be a strawman; that
test is what earns this module the right to be called "the same gate."

Pure pandas, no Spark, no network. Returns per-check pass/fail plus a small sample
of the offending records, so a failure is inspectable (and so the scorer can show
the gate's own verdict next to the agent's).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

GATE_DQ = "dq_gate"
GATE_RECON = "reconciliation"

# Intercompany account roles (verbatim from dq_gate.py) and their normal balance.
_IC = {"receivable": "A14000", "payable": "L21500", "income": "R42000", "expense": "X67000"}

_DETAIL_CAP = 50  # cap the failing-row sample kept per check


@dataclass(frozen=True)
class CheckResult:
    check: str
    gate: str
    passed: bool
    failures: int
    detail: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class GateResult:
    """The verdict of one gate run: every check, in the pipeline's fixed order."""

    results: tuple[CheckResult, ...]

    def get(self, check: str) -> CheckResult | None:
        return next((r for r in self.results if r.check == check), None)

    @property
    def failed_checks(self) -> set[str]:
        return {r.check for r in self.results if not r.passed}

    @property
    def dq_failed_checks(self) -> set[str]:
        """dq_gate checks that failed — the ones that block promotion to Silver."""
        return {r.check for r in self.results if r.gate == GATE_DQ and not r.passed}

    @property
    def passed(self) -> bool:
        """True iff EVERY check passed (what 'clean' must satisfy)."""
        return not self.failed_checks


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cell(v):
    if v is None or (not isinstance(v, (list, dict)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        return round(v, 2)
    return v


def _detail(df: pd.DataFrame, cols) -> list[dict]:
    cols = [c for c in cols if c in df.columns]
    return [{c: _cell(r[c]) for c in cols} for _, r in df.head(_DETAIL_CAP).iterrows()]


def _blank(series: pd.Series) -> pd.Series:
    """Null, or (for text) blank — what dq_gate.py treats as a missing dimension."""
    m = series.isna()
    if not pd.api.types.is_datetime64_any_dtype(series) and not pd.api.types.is_numeric_dtype(series):
        m = m | (series.astype(str).str.strip() == "")
    return m


# --------------------------------------------------------------------------- #
# the seven checks (one function each, mirroring dq_gate.py)
# --------------------------------------------------------------------------- #
def _debits_equal_credits(gl: pd.DataFrame) -> CheckResult:
    g = (gl.groupby(["company_id", "voucher"], dropna=False)
           .agg(dr=("amount_debit", "sum"), cr=("amount_credit", "sum"))
           .reset_index())
    g["dr"] = g["dr"].round(2)
    g["cr"] = g["cr"].round(2)
    g["diff"] = (g["dr"] - g["cr"]).round(2)
    fail = g[g["diff"] != 0]
    return CheckResult("debits_equal_credits", GATE_DQ, fail.empty, len(fail),
                       _detail(fail, ["company_id", "voucher", "dr", "cr", "diff"]))


def _no_duplicate_vouchers(gl: pd.DataFrame) -> CheckResult:
    c = (gl.groupby(["company_id", "voucher", "line_number"], dropna=False)
           .size().reset_index(name="count"))
    fail = c[c["count"] > 1]
    return CheckResult("no_duplicate_vouchers", GATE_DQ, fail.empty, len(fail),
                       _detail(fail, ["company_id", "voucher", "line_number", "count"]))


def _account_in_coa(gl: pd.DataFrame, coa: pd.DataFrame) -> CheckResult:
    known = set(coa["account_key"].astype(str))
    fail = (gl[~gl["main_account"].astype(str).isin(known)]
            [["company_id", "voucher", "line_number", "main_account"]].drop_duplicates())
    return CheckResult("account_in_coa", GATE_DQ, fail.empty, len(fail),
                       _detail(fail, ["company_id", "voucher", "line_number", "main_account"]))


def _required_dimensions_present(gl: pd.DataFrame, coa: pd.DataFrame) -> CheckResult:
    required = set(coa.loc[coa["department_required"].astype(bool), "account_key"].astype(str))
    on_required = gl[gl["main_account"].astype(str).isin(required)]
    fail = on_required[_blank(on_required["department"])][
        ["company_id", "voucher", "line_number", "main_account", "department"]]
    return CheckResult("required_dimensions_present", GATE_DQ, fail.empty, len(fail),
                       _detail(fail, ["company_id", "voucher", "line_number", "main_account", "department"]))


def _entity_and_period_present(gl: pd.DataFrame) -> CheckResult:
    fail = gl[gl["company_id"].isna() | gl["period"].isna()][
        ["company_id", "voucher", "line_number", "period"]]
    return CheckResult("entity_and_period_present", GATE_DQ, fail.empty, len(fail),
                       _detail(fail, ["company_id", "voucher", "line_number", "period"]))


def _period_cutoff_correct(gl: pd.DataFrame) -> CheckResult:
    non_opening = gl[gl["journal_type"] != "OPENING"]
    acc = pd.to_datetime(non_opening["accounting_date"])
    per = pd.to_datetime(non_opening["period"])
    # Null-safe like Spark: year(null)!=year(x) is null -> not a failure here. A
    # null period is the completeness check's concern, not the cutoff check's.
    both = acc.notna() & per.notna()
    miscut = both & ((acc.dt.year != per.dt.year) | (acc.dt.month != per.dt.month))
    fail = non_opening[miscut][["company_id", "voucher", "journal_type", "accounting_date", "period"]]
    return CheckResult("period_cutoff_correct", GATE_RECON, fail.empty, len(fail),
                       _detail(fail, ["company_id", "voucher", "journal_type", "accounting_date", "period"]))


def _intercompany_eliminates(gl: pd.DataFrame) -> CheckResult:
    def net(acct: str) -> float:
        s = gl[gl["main_account"].astype(str) == acct]
        return round(float(s["amount_debit"].sum() - s["amount_credit"].sum()), 2)

    rec, pay = net(_IC["receivable"]), -net(_IC["payable"])
    inc, exp = -net(_IC["income"]), net(_IC["expense"])
    rows = []
    if round(rec - pay, 2) != 0:
        rows.append({"pair": "receivable_vs_payable", "hq_side": rec, "sub_side": pay, "diff": round(rec - pay, 2)})
    if round(inc - exp, 2) != 0:
        rows.append({"pair": "income_vs_expense", "hq_side": inc, "sub_side": exp, "diff": round(inc - exp, 2)})
    return CheckResult("intercompany_eliminates", GATE_RECON, not rows, len(rows), rows[:_DETAIL_CAP])


def run_gate(gl: pd.DataFrame, coa: pd.DataFrame) -> GateResult:
    """Run all seven checks in the pipeline's fixed order and return the verdict."""
    return GateResult((
        _debits_equal_credits(gl),
        _no_duplicate_vouchers(gl),
        _account_in_coa(gl, coa),
        _required_dimensions_present(gl, coa),
        _entity_and_period_present(gl),
        _period_cutoff_correct(gl),
        _intercompany_eliminates(gl),
    ))

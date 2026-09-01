"""Deterministic offline recovery of a defect's offending vouchers (Phase 4).

The scorecard needs a diagnosis to grade, but the live LLM investigation costs money
and a key. This module is the OFFLINE stand-in: it recovers each defect's offending
vouchers using ONLY the read-only tool surface (never the answer key), the same way
the Phase-2 tool-sufficiency tests do. It is the analyst's playbook expressed as
code — "for this failing check, here is the query that surfaces the culprits."

What it proves, and does not:
  * PROVES the tool surface is SUFFICIENT to recover each defect from the data, and
    (fed into the drafter + gate) that the fix machinery closes end-to-end.
  * Does NOT prove the live model finds the defect unaided — this recovery is
    hand-written per check. That gap is the deferred live run (your API key) and is
    called out on the scorecard itself.

Read-only: every branch goes through the provider-backed tool functions; there is no
write and no answer-key access here.
"""

from __future__ import annotations

from agent.provider import GLProvider
from agent.registry import load_registry
from agent.tools import get_chart_of_accounts, query_clean_baseline, query_failing_table

# Intercompany accounts whose group net must eliminate (mirrors dq_gate.py / tools).
_IC = ["A14000", "L21500", "R42000", "X67000"]


def check_for(scenario: str) -> str:
    """The DQ/reconciliation check a scenario trips, from the registry (no hardcoding)."""
    entry = load_registry().by_defect_class(scenario)
    if entry is None:
        raise ValueError(f"No registry entry for scenario '{scenario}'.")
    return entry.name


def _vouchers(rows_result) -> set[str]:
    return {r["voucher"] for r in rows_result["rows"]}


def recover_offending(scenario: str, provider: GLProvider) -> set[str]:
    """Recover the offending voucher ids for `scenario` using only the tool surface."""
    if scenario == "unbalanced_voucher":
        g = query_failing_table(provider, group_by="voucher", limit=200)
        return {r["voucher"] for r in g["groups"] if r["net"] != 0}

    if scenario == "duplicate_voucher":
        f = query_failing_table(provider, group_by="voucher", limit=200)
        b = query_clean_baseline(provider, group_by="voucher", limit=200)
        base = {r["voucher"]: r["line_count"] for r in b["groups"]}
        return {r["voucher"] for r in f["groups"] if r["line_count"] > base.get(r["voucher"], 0)}

    if scenario == "unmapped_account":
        coa = {a["account_key"] for a in get_chart_of_accounts(provider)["accounts"]}
        accts = [r["main_account"] for r in query_failing_table(provider, group_by="main_account", limit=200)["groups"]]
        bogus = [a for a in accts if a not in coa]
        return _vouchers(query_failing_table(provider, filters={"main_account": bogus}, limit=200))

    if scenario == "missing_department":
        req = [a["account_key"] for a in get_chart_of_accounts(provider)["accounts"] if a["department_required"]]
        return _vouchers(query_failing_table(provider, filters={"main_account": req, "department": None}, limit=200))

    if scenario == "missing_entity_or_period":
        by_entity = _vouchers(query_failing_table(provider, filters={"company_id": None}, limit=200))
        by_period = _vouchers(query_failing_table(provider, filters={"period": None}, limit=200))
        return by_entity | by_period

    if scenario == "period_cutoff":
        r = query_failing_table(provider, filters={"journal_type": "SALES"}, limit=200)
        return {row["voucher"] for row in r["rows"] if row["accounting_date"][:7] != row["period"][:7]}

    if scenario == "intercompany_out_of_balance":
        f = {r["main_account"]: r["net"]
             for r in query_failing_table(provider, filters={"main_account": _IC}, group_by="main_account")["groups"]}
        b = {r["main_account"]: r["net"]
             for r in query_clean_baseline(provider, filters={"main_account": _IC}, group_by="main_account")["groups"]}
        moved = [a for a in _IC if f.get(a) != b.get(a)]
        return _vouchers(query_failing_table(provider, filters={"main_account": moved}, limit=200))

    raise ValueError(f"No recovery strategy for scenario '{scenario}'.")

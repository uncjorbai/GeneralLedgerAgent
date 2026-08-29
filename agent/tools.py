"""The Agent's tool surface — the forensic accountant's instrument panel.

This is DESIGN §5, Phase-2 subset: a TIGHT, read-only set of tools the model can
call to investigate a GL defect, plus `log_decision` to record its reasoning.
Adding a tool is a deliberate decision (CLAUDE.md), not a convenience.

Three parts, cleanly separated:

  * TOOLS       — the tool SCHEMAS as plain data (Anthropic `input_schema` shape).
                  This is the only thing the model knows about each tool, so the
                  descriptions are written for the model to read. Order is fixed
                  (prompt-cache friendly).
  * the impls   — small functions over the Step-3 `provider`. Read-only; every
                  result is plain JSON (numbers rounded, dates stringified) so it
                  can go straight back to the model.
  * dispatch()  — the pure router: (name, input) -> (result_json, is_error). It
                  auto-logs every call to the DecisionTrace, so the audit trail is
                  complete even if the model never calls `log_decision` itself.

Guardrails live here by construction: there is no write tool (the only mutation
is appending to the trace), and every read goes through the provider, whose door
to the answer key is already shut (guardrail #4).
"""

from __future__ import annotations

import json

import pandas as pd

from agent.provider import GLProvider
from agent.trace import DecisionTrace

# Columns the model may filter or group on. A whitelist keeps the surface tight
# and forensic — no arbitrary column access, no free-form SQL.
FILTERABLE = (
    "company_id", "voucher", "main_account", "account_name",
    "journal_type", "department", "cost_center", "period", "accounting_date",
    "line_number", "counterparty",
)
# Columns returned for a row-level (non-aggregated) query — the useful subset.
# accounting_date is included so period-cutoff defects (a date outside its period)
# are visible; period is the close period the line is booked to.
ROW_COLUMNS = (
    "company_id", "voucher", "line_number", "main_account", "account_name",
    "amount_debit", "amount_credit", "department", "accounting_date", "period",
    "journal_type",
)
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class ToolError(Exception):
    """A tool was called with invalid input (bad column, bad args).

    The dispatcher turns this into a `tool_result` with `is_error=True` and the
    message as content, so the model sees the mistake and can correct itself —
    the documented way to surface tool errors to the model.
    """


# --------------------------------------------------------------------------- #
# JSON coercion + the shared query engine
# --------------------------------------------------------------------------- #
def _cell(v):
    """One dataframe cell -> a JSON-safe scalar."""
    if v is None or (not isinstance(v, (list, dict)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "item"):        # numpy scalar -> native python
        v = v.item()
    if isinstance(v, float):
        return round(v, 2)
    return v


def _records(df: pd.DataFrame, columns) -> list[dict]:
    cols = [c for c in columns if c in df.columns]
    return [{c: _cell(row[c]) for c in cols} for _, row in df.iterrows()]


def _match_str(df: pd.DataFrame, col: str) -> pd.Series:
    """A string view of a column for equality/`in` matching, dtype-agnostic.

    Datetime columns match on their date (YYYY-MM-DD), so a filter of
    '2026-06-30' works without the model knowing the underlying timestamp.
    """
    series = df[col]
    if pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.strftime("%Y-%m-%d")
    return series.astype(str)


def _is_missing(series: pd.Series) -> pd.Series:
    """Rows where a value is 'missing' — null, or (for text columns) blank.

    This is what an accountant means by a missing entity/period/dimension, and it
    covers both the null-key defect and the blank-department defect uniformly.
    """
    missing = series.isna()
    if not pd.api.types.is_datetime64_any_dtype(series) and not pd.api.types.is_numeric_dtype(series):
        missing = missing | (series.astype(str).str.strip() == "")
    return missing


def _apply_filters(df: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
    if not filters:
        return df
    if not isinstance(filters, dict):
        raise ToolError(f"'filters' must be an object of column -> value; got {type(filters).__name__}.")
    mask = pd.Series(True, index=df.index)
    for col, value in filters.items():
        if col not in FILTERABLE:
            raise ToolError(f"'{col}' is not filterable. Allowed columns: {list(FILTERABLE)}.")
        if value is None:                                   # match missing values
            mask &= _is_missing(df[col])
        elif isinstance(value, list):
            col_str = _match_str(df, col)
            m = col_str.isin([str(x) for x in value if x is not None])
            if any(x is None for x in value):               # null allowed inside a list too
                m = m | _is_missing(df[col])
            mask &= m
        else:
            mask &= _match_str(df, col) == str(value)
    return df[mask]


def _query(df: pd.DataFrame, filters: dict | None, group_by: str | None, limit) -> dict:
    """Shared engine for query_failing_table / query_clean_baseline.

    With `group_by`: return per-group debit/credit sums and the net
    (debit - credit) — the shape that reveals an intercompany imbalance at a
    glance. Without it: return the matching rows (capped, deterministic order).
    """
    limit = min(int(limit) if limit is not None else DEFAULT_LIMIT, MAX_LIMIT)
    sub = _apply_filters(df, filters)

    if group_by is not None:
        if group_by not in FILTERABLE:
            raise ToolError(f"'{group_by}' is not a valid group_by. Allowed: {list(FILTERABLE)}.")
        grouped = (
            sub.groupby(group_by, dropna=False)
               .agg(debit=("amount_debit", "sum"), credit=("amount_credit", "sum"),
                    line_count=("amount_debit", "size"))
               .reset_index()
               .sort_values(group_by)
        )
        rows = []
        for _, r in grouped.head(limit).iterrows():
            debit, credit = round(float(r["debit"]), 2), round(float(r["credit"]), 2)
            rows.append({
                group_by: _cell(r[group_by]),
                "debit": debit,
                "credit": credit,
                "net": round(debit - credit, 2),
                "line_count": int(r["line_count"]),
            })
        return {"group_by": group_by, "groups": rows, "group_count": int(len(grouped))}

    ordered = sub.sort_values([c for c in ("company_id", "voucher", "line_number") if c in sub.columns])
    rows = _records(ordered.head(limit), ROW_COLUMNS)
    return {"row_count": int(len(sub)), "returned": len(rows), "rows": rows}


# --------------------------------------------------------------------------- #
# the tool implementations (read-only, over the provider)
# --------------------------------------------------------------------------- #
def get_gate_verdict(context: dict) -> dict:
    return {
        "scenario": context.get("scenario"),
        "gl_table": context.get("gl_table"),
        "failed_checks": list(context.get("failed_checks", [])),
        "gate_types": list(context.get("gate_types", [])),
        "evidence": context.get("evidence", ""),
    }


def query_failing_table(provider: GLProvider, filters=None, group_by=None, limit=None) -> dict:
    return _query(provider.failing_table(), filters, group_by, limit)


def query_clean_baseline(provider: GLProvider, filters=None, group_by=None, limit=None) -> dict:
    return _query(provider.clean_baseline(), filters, group_by, limit)


def get_chart_of_accounts(provider: GLProvider) -> dict:
    coa = provider.chart_of_accounts()
    cols = ("account_key", "name", "class", "normal_balance", "statement", "department_required")
    return {"accounts": _records(coa, cols), "account_count": int(len(coa))}


def get_dimensions(provider: GLProvider) -> dict:
    dims = provider.departments()
    return {"departments": _records(dims, dims.columns), "count": int(len(dims))}


def get_scenario_context(provider: GLProvider, context: dict) -> dict:
    """Run context the Agent may see — entities/periods present in the data.

    Deliberately does NOT include the seed or the answer key. The seed lives in
    the Generator's config (not exposed here; Tier-C deferred), and the answer key
    is off-limits (guardrail #4).
    """
    gl = provider.failing_table()
    entities = sorted(str(e) for e in gl["company_id"].dropna().unique())
    periods = sorted({_cell(p) for p in gl["period"].dropna().unique()})
    return {
        "scenario": context.get("scenario"),
        "entities": entities,
        "periods": periods,
        "row_count": int(len(gl)),
        "note": "seed and answer key are intentionally not exposed (guardrail #4).",
    }


# --------------------------------------------------------------------------- #
# the tool SCHEMAS (what the model reads) — fixed order
# --------------------------------------------------------------------------- #
_FILTER_DESC = (
    "Optional object of column -> value (or column -> [values]) to filter rows. "
    f"Filterable columns: {', '.join(FILTERABLE)}. A list matches any of the values. "
    "Use null as the value to match MISSING entries (null or blank) — e.g. "
    "{\"company_id\": null} or {\"department\": null}."
)
_GROUP_DESC = (
    "Optional column to aggregate on. Returns per-group debit/credit sums, net "
    "(debit - credit), and line_count (rows in the group) instead of rows. Use "
    "main_account to expose an imbalance, or voucher + line_count to spot duplicates."
)

TOOLS: list[dict] = [
    {
        "name": "get_gate_verdict",
        "description": "The DQ/reconciliation verdict that woke the Agent: which check(s) "
                       "varied, on which scenario/table. Start here.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "query_failing_table",
        "description": "Inspect the FAILING GL table (the data that tripped the check). "
                       "Filter and/or aggregate. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "description": _FILTER_DESC},
                "group_by": {"type": "string", "enum": list(FILTERABLE), "description": _GROUP_DESC},
                "limit": {"type": "integer", "description": f"Max rows/groups (default {DEFAULT_LIMIT}, cap {MAX_LIMIT})."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "query_clean_baseline",
        "description": "The SAME query against the clean baseline (regenerated from the same "
                       "seed). Diff it against the failing table for an exact before/after.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "description": _FILTER_DESC},
                "group_by": {"type": "string", "enum": list(FILTERABLE), "description": _GROUP_DESC},
                "limit": {"type": "integer", "description": f"Max rows/groups (default {DEFAULT_LIMIT}, cap {MAX_LIMIT})."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_chart_of_accounts",
        "description": "The chart of accounts: account_key, name, class, normal_balance, "
                       "statement, department_required. Use to classify accounts.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_dimensions",
        "description": "The department / cost-center dimension.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_scenario_context",
        "description": "Run context: entities and periods present in the data. Excludes the "
                       "seed and the answer key by design.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "log_decision",
        "description": "Record a finding and the reasoning behind it to the append-only "
                       "decision trace. Call this as you conclude things, not only at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "finding": {"type": "string", "description": "What you concluded."},
                "rationale": {"type": "string", "description": "Why — the evidence that supports it."},
                "step": {"type": "string", "description": "Optional short label for this step."},
            },
            "required": ["finding", "rationale"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


# --------------------------------------------------------------------------- #
# the dispatcher — pure router, auto-logs every call
# --------------------------------------------------------------------------- #
def _summarize(name: str, result: dict) -> str:
    if name == "get_gate_verdict":
        return f"verdict: {result.get('failed_checks')}"
    if name in ("query_failing_table", "query_clean_baseline"):
        if "group_by" in result:
            return f"{result['group_by']} sums: {len(result['groups'])} of {result['group_count']} groups"
        return f"{result['returned']} of {result['row_count']} rows"
    if name == "get_chart_of_accounts":
        return f"{result['account_count']} accounts"
    if name == "get_dimensions":
        return f"{result['count']} departments"
    if name == "get_scenario_context":
        return f"{len(result['entities'])} entities, {len(result['periods'])} periods"
    return "ok"


def dispatch(name: str, tool_input: dict, *, provider: GLProvider, trace: DecisionTrace, context: dict) -> tuple[str, bool]:
    """Run one tool call. Returns (result_as_json_string, is_error).

    Pure with respect to its inputs (the only side effect is appending to `trace`,
    which is the point). Unknown tools and bad inputs come back as `is_error=True`
    so the model can recover rather than the loop crashing.
    """
    tool_input = tool_input or {}

    # log_decision is special: it writes the model's reasoning straight to the trace.
    if name == "log_decision":
        finding = tool_input.get("finding")
        rationale = tool_input.get("rationale")
        if not finding or not rationale:
            trace.record_tool(name, tool_input, "rejected: finding+rationale required", is_error=True)
            return json.dumps({"error": "log_decision requires both 'finding' and 'rationale'."}), True
        trace.record_decision(finding=finding, rationale=rationale, step=tool_input.get("step", ""))
        return json.dumps({"logged": True}), False

    try:
        if name == "get_gate_verdict":
            result = get_gate_verdict(context)
        elif name == "query_failing_table":
            result = query_failing_table(provider, tool_input.get("filters"), tool_input.get("group_by"), tool_input.get("limit"))
        elif name == "query_clean_baseline":
            result = query_clean_baseline(provider, tool_input.get("filters"), tool_input.get("group_by"), tool_input.get("limit"))
        elif name == "get_chart_of_accounts":
            result = get_chart_of_accounts(provider)
        elif name == "get_dimensions":
            result = get_dimensions(provider)
        elif name == "get_scenario_context":
            result = get_scenario_context(provider, context)
        else:
            trace.record_tool(name, tool_input, f"unknown tool '{name}'", is_error=True)
            return json.dumps({"error": f"Unknown tool '{name}'. Available: {sorted(TOOL_NAMES)}."}), True
    except ToolError as e:
        trace.record_tool(name, tool_input, f"error: {e}", is_error=True)
        return json.dumps({"error": str(e)}), True

    trace.record_tool(name, tool_input, _summarize(name, result))
    return json.dumps(result, ensure_ascii=False), False

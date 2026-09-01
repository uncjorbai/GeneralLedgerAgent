"""Apply an approved remediation proposal to a GL frame (Phase 4).

Human-in-the-loop (guardrail #3): a proposal is *staged* by the drafter and only
applied after a human approves it. This module is that apply step, kept PURE — it
takes a GL frame and a proposal's corrections and returns a NEW, corrected frame; it
mutates nothing in place and writes nothing. The scorer uses it to produce the data
the gate re-runs on; live, the same corrections would drive the Silver rebuild.

Two operations, matching the drafter's two primitives:
  * RESTATE — set a field on the matched line to the corrected value (coercing to the
    column's dtype, so a date restatement stays a real date, not a string).
  * REMOVE  — drop one copy of a duplicated line.

Lines are matched on (voucher, line_number), which is unique and — unlike a key that
includes company_id — survives the defect that nulls the entity.
"""

from __future__ import annotations

import pandas as pd

from agent.remediation import OP_REMOVE


def apply_corrections(gl: pd.DataFrame, corrections) -> pd.DataFrame:
    """Return a copy of `gl` with the proposal's corrections applied.

    RESTATE sets `field` to `corrected_value`; REMOVE drops one copy of the named
    line. Pure: `gl` is not modified.
    """
    df = gl.copy().reset_index(drop=True)
    dropped: set[int] = set()
    for c in corrections:
        match = df[(df["voucher"] == c.voucher) & (df["line_number"] == c.line_number)]
        if c.op == OP_REMOVE:
            avail = [i for i in match.index if i not in dropped]
            if not avail:
                continue
            dropped.add(avail[0])
        else:
            value = c.corrected_value
            if pd.api.types.is_datetime64_any_dtype(df[c.field]):
                value = pd.to_datetime(value)
            df.loc[match.index, c.field] = value
    if dropped:
        df = df.drop(index=list(dropped)).reset_index(drop=True)
    return df

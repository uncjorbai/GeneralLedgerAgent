"""Rebuild the committed GL test fixture from the real Generator output.

The fixtures under tests/fixtures/gl/ are a small, faithful slice of
GeneralLedgerGenerator's `out/`. They are committed (tests do not depend on the
Generator being checked out), but this script is kept so the slice is
REPRODUCIBLE and can be re-verified before any live run — the same discipline as
Phase 1's synthesized fixture (docs/PHASE2_PROGRESS.md, "Fixture provenance").

One shared voucher set V spans all seven scenarios so every scenario's clean
baseline contains its own defect vouchers:
    V = all intercompany-touching vouchers (for the group-level IC netting)
        ∪ every scenario's defect vouchers (from the answer keys)
        ∪ a deterministic sample of ordinary vouchers.
Only each scenario's own vouchers are mutated, so the slice stays faithful.

Usage:
    # Generator repo defaults to ../GeneralLedgerGenerator; override with GLG_REPO.
    python tests/fixtures/build_fixture.py
"""
import glob
import json
import os
from pathlib import Path

import pandas as pd

AGENT_ROOT = Path(__file__).resolve().parents[2]
GEN = Path(os.environ.get("GLG_REPO", AGENT_ROOT.parent / "GeneralLedgerGenerator"))
OUT = AGENT_ROOT / "tests" / "fixtures" / "gl"
IC = {"A14000", "L21500", "R42000", "X67000"}
SCENARIOS = [
    "unbalanced_voucher", "duplicate_voucher", "unmapped_account", "missing_department",
    "missing_entity_or_period", "period_cutoff", "intercompany_out_of_balance",
]
SAMPLE = 15


def load(sc: str) -> pd.DataFrame:
    sub = "clean/gl_journal_lines" if sc == "clean" else f"{sc}/gl_journal_lines"
    files = sorted(glob.glob(str(GEN / "out" / sub / "**" / "*.parquet"), recursive=True))
    if not files:
        raise SystemExit(f"No Generator parquet under {GEN / 'out' / sub}. Set GLG_REPO.")
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)


def manifest(sc: str) -> dict:
    return json.loads((GEN / "out" / sc / "_qa" / "run_manifest.json").read_text())


def main() -> None:
    clean = load("clean")
    defect_vouchers: set[str] = set()
    for sc in SCENARIOS:
        for dp in manifest(sc)["defects_applied"]:
            defect_vouchers |= set(dp.get("vouchers", []))

    ic = set(clean.loc[clean.main_account.isin(IC), "voucher"])
    sample = sorted(set(clean.voucher) - ic - defect_vouchers)[:SAMPLE]
    V = (ic | defect_vouchers | set(sample)) & set(clean.voucher)

    for sc in ["clean", *SCENARIOS]:
        df = clean if sc == "clean" else load(sc)
        sl = df[df.voucher.isin(V)].sort_values(["voucher", "line_number"]).reset_index(drop=True)
        dest = OUT / sc / "gl_journal_lines"
        dest.mkdir(parents=True, exist_ok=True)
        for old in dest.glob("*.parquet"):
            old.unlink()
        sl.to_parquet(dest / "part-0.parquet", index=False)
        print(f"[{sc:28}] vouchers={sl.voucher.nunique():3d} rows={len(sl):3d}")

    for name in ("chart_of_accounts.csv", "departments.csv"):
        (OUT / name).write_bytes((GEN / "config" / name).read_bytes())
    print(f"V = {len(V)} vouchers (IC={len(ic)}, defect={len(defect_vouchers)}, sample={len(sample)})")


if __name__ == "__main__":
    main()

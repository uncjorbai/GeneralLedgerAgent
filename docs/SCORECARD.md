# GL Anomaly Investigator — Scorecard

_Generated 2026-09-01 02:18 UTC · 7 seeded scenarios · offline (no LLM, no network)._

| Scenario | Check | Detected | Diagnosis | Fix valid | Re-gate passes | Action | $ impact |
|---|---|:--:|:--:|:--:|:--:|---|--:|
| `unbalanced_voucher` | `debits_equal_credits` | ✅ | ✅ | ✅ | ✅ | `restore_voucher_balance` | 1,500 |
| `duplicate_voucher` | `no_duplicate_vouchers` | ✅ | ✅ | ✅ | ✅ | `remove_duplicate_line` | 46,849 |
| `unmapped_account` | `account_in_coa` | ✅ | ✅ | ✅ | ✅ | `map_account` | 0 |
| `missing_department` | `required_dimensions_present` | ✅ | ✅ | ✅ | ✅ | `populate_dimension` | 0 |
| `missing_entity_or_period` | `entity_and_period_present` | ✅ | ✅ | ✅ | ✅ | `populate_field` | 0 |
| `period_cutoff` | `period_cutoff_correct` | ✅ | ✅ | ✅ | ✅ | `shift_period` | 0 |
| `intercompany_out_of_balance` | `intercompany_eliminates` | ✅ | ✅ | ✅ | ✅ | `restore_intercompany_side` | 3,000 |

## Totals vs targets (DESIGN §3)

| Metric | Result | Target |
|---|:--:|:--:|
| Detection | 7/7 | 7/7 |
| Diagnosis accuracy | 7/7 | ≥ 6/7 |
| Remediation validity | 7/7 | ≥ 5/7 |
| Closed-loop (re-gate passes) | 7/7 | ≥ 3 |

## What this proves — and what it doesn't

- Graded against the real answer keys (`run_manifest.json`) and a fidelity-
  checked offline mirror of the pipeline gate (`agent/gate.py`): clean passes
  all seven checks, each defect fails exactly its expected check, and every
  fix is validated by the SAME checks that caught it (the closed loop).
- Offline, the investigation is a deterministic, tools-only recovery
  (`agent/recover.py`) — this proves the tool surface is **sufficient** and the
  fix machinery closes end-to-end. It does **not** prove the live model
  diagnoses each defect unaided; that is the deferred live run (API key), which
  the same scorer grades with no change.

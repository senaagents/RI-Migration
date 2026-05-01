# LLM COA Setup Application

Date: 2026-04-25

Target: `llm.localhost`

Status: applied.

## Changes Applied

- Added `Account.custom_sap_acct_code` as a read-only unique SAP mapping field.
- Populated SAP account mapping for numbered ERPNext accounts.
- Mapped SAP root bucket codes to ERPNext root accounts:
  - `100000000000000` -> `Application of Funds (Assets) - L`
  - `200000000000000` -> `Source of Funds (Liabilities) - L`
  - `300000000000000` -> `Equity - L`
  - `400000000000000` -> `Income - L`
  - `500000000000000` -> `Expenses - L`
- Skipped inactive/unreferenced SAP root buckets `600000000000000`, `700000000000000`, `800000000000000`, `900000000000000`, and `A00000000000000`.
- Created 4 missing SAP postable investment accounts under `1103200 - KOTAK INVESTMENT-MUTUAL FUNDS - L`.
- Fixed SAP account `1204202` from `SALARY wa` to `SALARY ADVANCE`, including Account document rename.

## Verification

- SAP `OACT`: 600
- ERPNext `Account`: 666
- ERPNext numbered accounts: 590
- Accounts with `custom_sap_acct_code`: 595
- Missing SAP codes excluding mapped roots and inactive buckets: 0
- SAP/ERPNext account-name mismatches: 0
- SAP/ERPNext group-vs-leaf mismatches: 0

## Script

Applied by:

```bash
env PYTHONPATH=/Users/aakashchid/workshop/sap-db-pipeline/scripts \
  /Users/aakashchid/workshop/sena/senaBench/env/bin/python - <<'PY'
import apply_llm_coa_setup
apply_llm_coa_setup.main(dry_run=False)
PY
```

Source script:

- `scripts/apply_llm_coa_setup.py`

## Gate Result

COA account-code coverage is now ready for transaction dry-run mapping.

Remaining blockers before transaction import are not COA coverage. They are:

- SAP dimension mapping for Dim 2 and Dim 3
- Missing referenced items
- Missing referenced customers/suppliers
- Tax template setup
- Bank/payment setup

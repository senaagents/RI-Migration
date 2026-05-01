# LLM Business Partner Setup Application

Date: 2026-04-25

Target: `llm.localhost`

Status: applied.

## Changes Applied

- Imported/mapped all SAP `OCRD` customers and suppliers missing from ERPNext.
- Created 5 missing customers.
- Created missing suppliers where no ERPNext Supplier existed.
- Mapped exact existing Suppliers where the name existed and no SAP code was set.
- Created duplicate-name Supplier records with SAP code suffix where SAP has multiple CardCodes with the same BP name.

## Verification

- SAP customer BPs: 849
- ERPNext SAP-mapped customers: 849
- Missing customers: 0
- SAP supplier BPs: 1,425
- ERPNext SAP-mapped suppliers: 1,425
- Missing suppliers: 0

## Duplicate-Name Handling

Some SAP suppliers have duplicate names across multiple CardCodes. ERPNext has a unique `custom_sap_card_code`, so one ERPNext Supplier cannot safely represent multiple SAP CardCodes for transaction idempotence.

For these, the script creates distinct Suppliers with names like:

`R R MARKETING (V01167)`

This preserves SAP CardCode identity for transaction import.

## Script

Source script:

- `scripts/apply_llm_missing_bps.py`

Applied by:

```bash
env PYTHONPATH=/Users/aakashchid/workshop/sap-db-pipeline/scripts \
  /Users/aakashchid/workshop/sena/senaBench/env/bin/python - <<'PY'
import apply_llm_missing_bps
apply_llm_missing_bps.main(dry_run=False)
PY
```

## Gate Result

Customer/Supplier SAP CardCode coverage is ready for transaction dry-run mapping.

Remaining blockers before transaction import:

- SAP dimension mapping for Dim 2 and Dim 3
- Missing referenced items
- Tax template setup
- Bank/payment setup

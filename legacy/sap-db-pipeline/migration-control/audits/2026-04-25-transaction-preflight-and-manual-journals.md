# LLM Transaction Preflight And Manual Journal Import

Date: 2026-04-25 12:42 IST

Scope: run the first full transaction gate and import SAP manual journal entries only.

## Scripts

- `scripts/dry_run_llm_transaction_preflight.py`
- `scripts/apply_llm_resource_items.py`
- `scripts/import_llm_manual_journals.py`

## Resource Items

SAP production/order lines reference SAP resource codes from `ORSC` as `RES...` item codes.

Applied:

- Created 380 non-stock ERPNext `Item` records for SAP `ORSC` resources.
- Item group: `Operation Item` where available.
- Missing SAP resource items after import: 0.

## Transaction Preflight

The read-only preflight checked staged headers, lines, and payment account references for:

- SAP customer/supplier code mappings.
- Item/resource codes.
- Account mappings.
- Warehouse mappings.
- Branch mappings.
- Cost Center / SAP Dim 2 / SAP Dim 3 mappings.
- Sales and purchase tax templates.
- Payment header account mappings.

Final result:

- Header missing references: 0.
- Line missing references: 0.
- Payment missing references: 0.

Staged transaction counts checked:

- Headers: 236,404.
- Lines: 727,503.
- Payments from raw headers: 15,661 (`ORCT` + `OVPM`).

## Manual Journal Import

Imported only SAP manual journals:

- Source: staged `OJDT` where `TransType = 30`.
- Target: submitted ERPNext `Journal Entry`.
- Idempotence key: `Journal Entry.custom_sap_trans_id`.
- Zero debit/credit SAP lines are skipped because ERPNext rejects zero-value JE rows.

Verification:

- SAP manual journal headers in staged window: 1,888.
- Submitted ERPNext manual journals imported: 1,888.
- Date range: 2025-04-01 through 2026-04-24.
- SAP non-zero line totals: debit `337,571,844.90`, credit `337,571,844.90`.
- ERPNext imported JE totals: debit `337,571,844.90`, credit `337,571,844.90`.

## Important Boundary

Other SAP `OJDT` transaction types were not imported as Journal Entries in this step. They belong to invoices, payments, inventory, production, and logistics documents. Importing those generated GL rows separately would double-post accounting if the source documents are imported and submitted in ERPNext.

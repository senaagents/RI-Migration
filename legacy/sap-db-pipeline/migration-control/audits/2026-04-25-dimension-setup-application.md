# LLM Dimension Setup Application

Date: 2026-04-25 12:28 IST

Scope: preserve SAP accounting dimensions required by staged transactions before GL and document import.

## Inputs

- SAP source: staged raw `OPRC` rows in `data/llm_sap_pipeline.sqlite3`.
- ERPNext target: `Cost Center`, custom dimension DocTypes, `Accounting Dimension`, and generated accounting custom fields on `llm.localhost`.
- Script: `scripts/apply_llm_dimensions.py`.

## Applied

- Kept SAP Dim 1 / `JDT1.ProfitCode` mapped to ERPNext `Cost Center`; these records already existed.
- Created `SAP Department` for SAP Dim 2 / `JDT1.OcrCode2`.
- Created `SAP Product Segment` for SAP Dim 3 / `JDT1.OcrCode3`.
- Created 15 `SAP Department` records from SAP `OPRC`.
- Created 15 `SAP Product Segment` records from SAP `OPRC`.
- Created ERPNext `Accounting Dimension` records:
  - `SAP Department` -> field `sap_department`
  - `SAP Product Segment` -> field `sap_product_segment`
- Enabled `Accounts Settings.enable_accounting_dimensions`.
- Ensured generated accounting-dimension fields exist on ERPNext accounting doctypes, including `GL Entry`.

## Verification

- Idempotency dry-run after application:
  - `doctypes = 0`
  - `values = 0`
  - `accounting_dimensions = 0`
  - `dimension_fields = 0`
  - `settings = 0`
- `Accounts Settings.enable_accounting_dimensions = 1`.
- `SAP Department` records: 15.
- `SAP Product Segment` records: 15.
- Missing SAP Dim 2 values after import: 0.
- Missing SAP Dim 3 values after import: 0.
- `GL Entry` has `sap_department` and `sap_product_segment` link fields.

## Notes

- SAP Dim 4 / `JDT1.OcrCode4` and Dim 5 / `JDT1.OcrCode5` were not created because staged `JDT1` has zero populated values for both fields.
- SAP `OcrCode3 = NONE` was imported as an explicit product segment because it is heavily present in staged GL lines and needs a deterministic migration target.
- A partial MariaDB DDL state survived an initial failed attempt while creating accounting dimension fields. The final script is idempotent and completed the partial state cleanly.

# LLM Item Setup Application

Date: 2026-04-25 12:23 IST

Scope: close the SAP `OITM` item master gap in ERPNext site `llm.localhost` before transaction migration.

## Inputs

- SAP source: staged raw `OITM` and `OITB` rows in `data/llm_sap_pipeline.sqlite3`.
- ERPNext target: `Item`, `Item Group`, and `UOM` records on `llm.localhost`.
- Script: `scripts/apply_llm_missing_items.py`.

## Applied

- Created 906 missing ERPNext `Item` records using SAP `OITM.ItemCode` as the ERPNext item code.
- Preserved SAP item group where the matching ERPNext `Item Group` exists.
- Preserved stock/sales/purchase enablement from `InvntItem`, `SellItem`, and `PrchseItem`.
- Preserved disabled state from SAP `frozenFor` and `validFor`.
- Mapped SAP units to existing ERPNext UOMs, with blank/manual unknowns falling back to `Nos`.

## Verification

- SAP `OITM` item count: 4,969.
- ERPNext `Item` count after import: 5,079.
- Missing SAP item codes after import: 0.
- Dry verification after import: `would_create = 0`, `missing_before = 0`.

## Notes

- The 110 ERP-only items were left untouched. They appear to be existing vendor/import artifacts such as `V...` item codes and `NEW CODE`.
- SAP fixed-asset-coded items were imported as ordinary ERPNext item references, not as ERPNext fixed asset masters. Asset register migration should be handled separately if required.

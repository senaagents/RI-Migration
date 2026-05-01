# LLM SAP Migration Plan

## Objective

Move LLM/RFGB SAP Business One HANA data for `2025-04-01` through `2026-04-25` into SenaERP / ERPNext site `llm.localhost`.

## Source And Target

- Source: SAP Business One on HANA, schema `LLM_LIVENEW`
- Target: ERPNext/SenaERP site `llm.localhost`
- Staging: local SQLite database managed by this repo

## Position

Use SAP HANA read-only SQL for bulk extraction and staging. Do not write directly from SAP into ERPNext.

Direct import is rejected for this migration because the period contains large ledger, inventory, payment, and document volumes. Staging gives us restartability, idempotence, mapping review, raw payload preservation, and reconciliation evidence.

## Phases

1. Source profiling
   - Confirm SAP connectivity.
   - Count master and transaction tables.
   - Capture date-window counts for every planned document family.

2. Master staging
   - Stage company, branches, accounts, business partners, items, warehouses, item groups, UOMs, addresses, contacts, and tax identifiers.
   - Compare staged counts to existing `llm.localhost` master counts.
   - Produce exception lists for unmapped or missing records.

3. Transaction staging
   - Stage document headers and lines for the approved window.
   - Preserve full raw row JSON and source primary keys.
   - Validate all staged document dates stay inside the requested window.

4. Mapping and import design
   - Define ERPNext doctype mapping per source family.
   - Define idempotence keys using SAP table plus `DocEntry` or natural master keys.
   - Define cancellation, returns, taxes, branches, warehouses, dimensions, and cost center handling.

5. Guarded ERPNext import
   - Run dry-run transforms first.
   - Import in small batches with checkpoint records.
   - Stop on validation errors; write exception reports.

6. Reconciliation
   - Reconcile voucher counts, sales/purchase registers, journal totals, AR/AP outstanding, cash/bank, stock quantity/value, and opening balances.

## Initial Import Families

- Accounts: `OACT`
- Business partners: `OCRD`, `OCRG`, `CRD1`, `CRD7`, `OCPR`
- Items and inventory masters: `OITM`, `OITB`, `OUOM`, `OWHS`, `ITM1`, `OITW`
- Sales invoices and credit memos: `OINV`/`INV1`, `ORIN`/`RIN1`
- Purchase invoices and credit memos: `OPCH`/`PCH1`, `ORPC`/`RPC1`
- Payments: `ORCT`, `OVPM`
- Journals: `OJDT`/`JDT1`
- Inventory movements: `OIGN`/`IGN1`, `OIGE`/`IGE1`, `OWTR`/`WTR1`
- Orders and logistics: `ORDR`/`RDR1`, `OPOR`/`POR1`, `ODLN`/`DLN1`, `OPDN`/`PDN1`
- Production: `OWOR`/`WOR1`

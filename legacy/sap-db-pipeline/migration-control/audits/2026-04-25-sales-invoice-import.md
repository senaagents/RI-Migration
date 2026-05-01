# Sales Invoice Import Audit - 2026-04-25

## Scope

- Source: staged SAP Business One `OINV` headers, `INV1` lines, `INV4` tax rows.
- Header-level components also applied from `INV3` freight/expense rows, `INV5` TCS/withholding rows, and `OINV.RoundDif`.
- Target: ERPNext `Sales Invoice` on `llm.localhost`, company `LLM`.
- Imported only non-canceled SAP invoices for this pass.

## Result

| Metric | SAP | ERPNext |
| --- | ---: | ---: |
| Non-canceled invoices | 6,787 | 6,787 |
| Grand total | 715,104,413.63 | 715,104,413.63 |
| Net total | 640,724,186.72 | 640,724,186.72 |
| Tax/charge total | 74,380,226.91 | 74,380,226.91 |
| Per-invoice grand total differences | 0 | 0 |

## ERPNext Setup Applied

- Added idempotence fields:
  - `Sales Invoice.custom_sap_docentry`
  - `Sales Invoice.custom_sap_docnum`
  - `Sales Invoice.custom_sap_trans_id`
  - `Sales Invoice Item.custom_sap_line_num`
- Marked SAP debtor control account `1202101` as `Receivable`.
- Marked SAP output/TCS tax accounts used by AR invoices as `Tax`.
- Created support items:
  - `SAP-TAX-ONLY`
  - `SAP-SERVICE-LINE`
- Enabled 5 customers referenced by historic AR invoices.
- Enabled 2 items referenced by historic AR invoices.

## Import Rules

- SAP canceled invoices remain excluded; credit/cancel flows will be handled separately.
- AR invoices are financial imports with `update_stock=0`; stock movement will come from SAP logistics/stock documents.
- `TaxOnly=Y` SAP lines do not become revenue lines; they are represented with a zero-net support item and actual tax rows.
- SAP service/amount-only lines without item codes use `SAP-SERVICE-LINE` at quantity 1 with the SAP line amount.
- Fractional SAP quantities that collide with ERPNext item stock UOM integer validation use `SAP-SERVICE-LINE` for this financial import.
- SAP `INV3` freight/expense rows are added as service lines using `OEXD.RevAcct`.
- SAP `INV5` TCS/withholding rows are added as actual tax/charge rows.
- SAP `RoundDif` is folded into the final tax/charge residual so the ERPNext invoice grand total matches `OINV.DocTotal`.

## Verification

Completed at `2026-04-25 13:53:54 IST`.

Final checks:

- ERPNext submitted Sales Invoices with `custom_sap_docentry`: `6,787`.
- SAP non-canceled `OINV` headers staged: `6,787`.
- ERPNext grand total: `715,104,413.63`.
- SAP `OINV.DocTotal` grand total: `715,104,413.63`.
- Per-invoice grand-total diff count: `0`.

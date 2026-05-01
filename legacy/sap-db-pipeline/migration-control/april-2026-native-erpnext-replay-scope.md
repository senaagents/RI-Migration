# April 2026 Native ERPNext Replay Scope

Date: 2026-04-28

Source: SAP Business One on HANA, schema `LLM_LIVENEW`

Target: `llmnew.localhost`, company `LLM`

Cutoff rule:

- `2018-02-22` through `2026-03-31`: historical archive layer, already imported as SAP accounting movement into ERPNext Journal Entries.
- `2026-04-01` onward: current financial year replay. This must be migrated as native ERPNext operational documents, not as a ledger-only shortcut.

This file is the working checklist for the April/FY2026-27 SAP-to-ERPNext replay.

## What "native ERPNext" Means

"Native" means we let ERPNext own the operational document and generate its own stock, accounting, tax, outstanding, and status effects.

Examples:

- A SAP sales invoice becomes an ERPNext `Sales Invoice`.
- A SAP delivery becomes an ERPNext `Delivery Note`.
- A SAP incoming payment becomes an ERPNext `Payment Entry`.
- A SAP goods issue/receipt/transfer becomes an ERPNext `Stock Entry`.
- A SAP production order becomes an ERPNext `Work Order` plus its related stock/manufacturing transactions.

It does not mean:

- Importing every April SAP `OJDT/JDT1` row as Journal Entries.
- Copying SAP raw tables into ERPNext custom doctypes when ERPNext already has the right operational doctype.
- Posting "adjustment" JEs to make reports look right while leaving invoices, stock, payments, and orders missing.

Important double-posting rule:

SAP creates accounting rows in `OJDT/JDT1` for invoices, payments, stock movements, and production. ERPNext will also create GL rows when native documents are submitted. Therefore:

- Native business documents must be imported first.
- SAP generated journals must not be imported separately.
- Only true standalone/manual journals, normally `OJDT.TransType = 30`, should become ERPNext `Journal Entry` documents.

## April 2026 Live SAP Snapshot

Queried live SAP HANA on 2026-04-28 for posting dates `2026-04-01` through `2026-04-28`. SAP currently has postings through `2026-04-27` in this window.

ERPNext `llmnew.localhost` currently has `0` April native documents for the standard doctypes checked, so this is a fresh native replay phase.

| SAP family | SAP source | ERPNext target | Headers | Lines | Open | Closed | Canceled | SAP total |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Sales invoices | `OINV` / `INV1` | `Sales Invoice` | 273 | 1,679 | 232 | 41 | 6 | 32,518,852.06 |
| Sales credit memos | `ORIN` / `RIN1` | `Sales Invoice` return / credit note | 85 | 353 | 5 | 80 | 0 | 3,503,696.12 |
| Purchase invoices | `OPCH` / `PCH1` | `Purchase Invoice` | 373 | 1,079 | 303 | 70 | 3 | 29,935,733.69 |
| Purchase credit memos | `ORPC` / `RPC1` | `Purchase Invoice` return / debit-credit flow | 22 | 61 | 5 | 17 | 3 | 351,774.54 |
| Incoming payments | `ORCT` plus payment child tables | `Payment Entry` Receive | 160 | TBD | n/a | n/a | n/a | 233,113,593.94 |
| Outgoing payments | `OVPM` plus payment child tables | `Payment Entry` Pay | 446 | TBD | n/a | n/a | n/a | 526,106,631.55 |
| Sales orders | `ORDR` / `RDR1` | `Sales Order` | 130 | 1,440 | 43 | 87 | 0 | 31,398,029.76 |
| Purchase orders | `OPOR` / `POR1` | `Purchase Order` | 264 | 894 | 117 | 147 | 30 | 54,754,599.53 |
| Deliveries | `ODLN` / `DLN1` | `Delivery Note` | 318 | 1,690 | 77 | 241 | 3 | 75,732,520.67 |
| Purchase receipts | `OPDN` / `PDN1` | `Purchase Receipt` | 277 | 827 | 98 | 179 | 4 | 31,947,431.99 |
| Goods receipts | `OIGN` / `IGN1` | `Stock Entry` - Material Receipt | 665 | 1,188 | n/a | n/a | n/a | 79,685,137.43 |
| Goods issues | `OIGE` / `IGE1` | `Stock Entry` - Material Issue | 690 | 2,629 | n/a | n/a | n/a | 70,165,613.57 |
| Inventory transfers | `OWTR` / `WTR1` | `Stock Entry` - Material Transfer | 412 | 1,810 | n/a | n/a | n/a | 24,505,422.12 |
| Production orders | `OWOR` / `WOR1` | `Work Order` and manufacturing stock flow | 769 | 3,228 | 323 | 446 | n/a | n/a |
| Journals | `OJDT` / `JDT1` | `Journal Entry` only for standalone journals | 3,945 | 11,416 | n/a | n/a | n/a | 940,393,601.17 |

## Current FY Opening State Required

Because April is replayed as live ERPNext operations, ERPNext must have the operational opening state as of `2026-04-01`.

The historical Journal Entries through `2026-03-31` give accounting balances, but they do not create ERPNext stock ledger, open invoice references, order statuses, or work order state. For native replay we also need:

| Opening object | SAP source | ERPNext target | Why it is required |
|---|---|---|---|
| Opening stock by item/warehouse | `OITW` plus valuation source (`AvgPrice`, stock valuation tables if needed) | `Stock Reconciliation` at `2026-04-01 00:00:00` | April deliveries, issues, receipts, transfers, and production need real ERPNext stock quantities and valuation. |
| Open customer receivables from before April | Open `OINV` and possibly unmatched receivable balances | Opening `Sales Invoice` or opening allocation documents | April incoming payments may settle pre-April invoices. Payment Entry needs references or a deliberate unallocated-payment policy. |
| Open supplier payables from before April | Open `OPCH` and unmatched payable balances | Opening `Purchase Invoice` or opening allocation documents | April outgoing payments may settle pre-April bills. |
| Open sales orders | `ORDR` / `RDR1` open at `2026-04-01` | `Sales Order` | April deliveries/invoices may draw against pre-April orders. |
| Open purchase orders | `OPOR` / `POR1` open at `2026-04-01` | `Purchase Order` | April purchase receipts/invoices may draw against pre-April POs. |
| Open work orders | `OWOR` / `WOR1` not closed at `2026-04-01` | `Work Order` | April production/issue/receipt flow may depend on existing WOs. |
| Batch/serial opening state, if any | SAP batch/serial tables, plus stock tables | `Batch`, `Serial No`, opening stock rows | Required if items are batch/serial controlled in ERPNext. |

Do not start April transactional import until this opening-state decision is locked, because stock and payment allocation can become wrong even if GL totals look right.

## Standard SAP Data To Replay

### 1. Setup And Master Deltas

These must be complete before any April transaction is submitted.

| SAP source | ERPNext target | Current live SAP count | Notes |
|---|---|---:|---|
| `OACT` | `Account` | 603 | Accounts must include SAP account code custom field and correct account types. |
| `OBPL` | Branch / company dimensions | 6 | Branch mapping required on transactions where SAP branch is populated. |
| `OPRC`, `OOCR` | `Cost Center`, accounting dimensions | 48 + 43 | Required for GL, P&L, and manufacturing dimensions. |
| `OCRG` | Customer/Supplier Group | 13 | Create before business partners. |
| `OCRD` | `Customer`, `Supplier`, `Lead` where needed | 2,277 | Include CardCode identity and disabled/frozen state. |
| `CRD1`, `CRD7`, `OCPR` | `Address`, tax IDs, `Contact` | already staged previously; refresh deltas | Needed for GST/tax, shipping, billing, and communications. |
| `OITB` | `Item Group` | 13 | Create before items. |
| `OUOM` | `UOM` | 7 | Normalize SAP UOM names to ERPNext UOMs. |
| `OITM` | `Item` | 4,991 | Include stock/sales/purchase flags, default warehouses, tax defaults, batch/serial flags. |
| `ITM1` / `OPLN` | `Price List`, `Item Price` | 49k+ item-price rows | Required if April orders/invoices use price lists. |
| `OWHS` | `Warehouse` | 29 | Required before stock, delivery, receipt, and production docs. |
| `OBNK` | `Bank`, bank accounts | 605 | Required for Payment Entry mapping. |
| `OCTG` | `Payment Terms Template` | 17 | Required for sales/purchase invoice due dates and terms. |
| `OSTC`, `OSTA`, tax UDTs | Tax templates / tax categories | 49 + 71 | Required for proper tax rows. |
| `OSLP` | `Sales Person` / Sales Team | 62 | Required if we preserve salesperson attribution. |
| `OHEM`, `@DEPT` | `Employee`, `Department` | 20 + 14 | Required for HR/manufacturing ownership if used. |
| `OITT`, `ITT1` | `BOM`, BOM items | 1,764 + 5,492 | Required before real Work Orders can be submitted. |

Teaching note: masters are not "nice to have." Native ERPNext documents validate links. If an April invoice line references an item, warehouse, tax code, cost center, or party that does not exist, the import either fails or forces a bad placeholder. Placeholders are how migrations become unmaintainable.

### 2. Sales Cycle

| SAP source | ERPNext document | Must preserve |
|---|---|---|
| `ORDR` / `RDR1` | `Sales Order` | SAP `DocEntry`, `DocNum`, customer, dates, items, quantities, rates, tax, delivery status, branch, salesperson, dimensions. |
| `ODLN` / `DLN1` | `Delivery Note` | Link to Sales Order when SAP base links exist, customer, source warehouse, item quantity, batch/serial if present. |
| `OINV` / `INV1` | `Sales Invoice` | Link to Sales Order/Delivery Note where possible, tax rows, customer PO ref, receivable account, income account, cost center. |
| `ORIN` / `RIN1` | `Sales Invoice` return / credit note | Return against original invoice when link exists, correct stock update behavior, tax reversal. |
| `ORCT` plus allocation tables | `Payment Entry` Receive | Customer, bank/cash account, payment method, reference no, invoice allocations, unallocated amount if any. |

Native replay target:

- Orders show order status.
- Delivery Notes update stock when submitted.
- Sales Invoices create receivable and tax/revenue GL.
- Payment Entries close invoices and update bank/cash.

Do not import the `OJDT` generated by these SAP docs separately.

### 3. Purchase Cycle

| SAP source | ERPNext document | Must preserve |
|---|---|---|
| `OPOR` / `POR1` | `Purchase Order` | Supplier, item, quantity, rate, expected date, tax, warehouse, branch/dimensions. |
| `OPDN` / `PDN1` | `Purchase Receipt` | Link to PO where possible, supplier, target warehouse, accepted quantity, valuation rate. |
| `OPCH` / `PCH1` | `Purchase Invoice` | Link to PO/receipt where possible, payable account, expense/stock account, tax, due date. |
| `ORPC` / `RPC1` | `Purchase Invoice` return / debit-credit flow | Return against original bill/receipt when link exists, tax/stock reversal. |
| `OVPM` plus allocation tables | `Payment Entry` Pay | Supplier, bank/cash account, payment method, reference no, invoice allocations. |

Native replay target:

- Purchase Orders and Receipts show procurement status.
- Purchase Receipts update stock.
- Purchase Invoices create payable and expense/tax/stock GL.
- Payment Entries close supplier invoices.

### 4. Inventory And Warehouse Movement

| SAP source | ERPNext document | Stock effect |
|---|---|---|
| `OITW` at opening | `Stock Reconciliation` | Creates April opening quantity/value by item and warehouse. |
| `OIGN` / `IGN1` | `Stock Entry` - Material Receipt | Adds stock into target warehouse. |
| `OIGE` / `IGE1` | `Stock Entry` - Material Issue | Removes stock from source warehouse. |
| `OWTR` / `WTR1` | `Stock Entry` - Material Transfer | Moves stock between source and target warehouses. |
| SAP inventory revaluation docs / `OJDT.TransType=162` | ERPNext valuation adjustment path | Must be mapped separately; do not bury as ordinary JE if stock value must reconcile. |
| SAP inventory logs `OILM`, `OITL`, `OIVL` | Reconciliation/evidence, not direct ERPNext docs | Use these to validate stock movement and valuation after native docs are submitted. |

Teaching note: inventory is where shortcut imports hurt most. ERPNext stock ledger is generated from Stock Reconciliations, Delivery Notes, Purchase Receipts, Stock Entries, and manufacturing transactions. If we only post GL, item balances and valuation will be wrong.

### 5. Manufacturing

| SAP source | ERPNext document | Must preserve |
|---|---|---|
| `OITT` / `ITT1` | `BOM` | Finished item, raw materials, quantities, UOMs, operations if available. |
| `OWOR` / `WOR1` | `Work Order` | Production item, planned quantity, status, planned dates, warehouse, BOM link. |
| SAP issue/receipt docs linked to production (`OIGE`, `OIGN`, `OWDD`/`WDD1` if used) | `Stock Entry` - Material Transfer for Manufacture / Manufacture | Component issue, WIP, finished goods receipt, scrap if any. |
| LLM addon work-order tables `@INUR_WOR*` | Custom manufacturing/job-card doctypes or ERPNext extensions | Decode before final import; likely contains operational fields SAP B1 core tables do not carry. |

Native replay target:

- Work Orders exist as operational records.
- Material consumption and finished goods receipts are represented through ERPNext stock transactions.
- WIP/finished stock and manufacturing GL reconcile to SAP.

### 6. Standalone Accounting

| SAP source | ERPNext document | Rule |
|---|---|---|
| `OJDT` / `JDT1` with `TransType=30` | `Journal Entry` | Import as native JE only when it is a standalone/manual journal. |
| `OJDT` generated by invoices, payments, stock, production | none directly | Native ERPNext source docs generate equivalent GL. |
| `OACM` and posting-period close docs if needed | `Period Closing Voucher` / accounting close process | Map separately after normal docs reconcile. |

April live SAP postable `OJDT` by `TransType` shows generated journals from many native document families. These are useful for reconciliation but must not all be replayed as Journal Entries.

Notable April postable `OJDT` buckets:

| SAP `TransType` | Meaning for migration | Postable count |
|---:|---|---:|
| 13 | Sales Invoice generated GL | 273 |
| 14 | Sales Credit Memo generated GL | 85 |
| 18 | Purchase Invoice generated GL | 373 |
| 19 | Purchase Credit Memo generated GL | 22 |
| 20 | Purchase Receipt generated GL | 89 |
| 24 | Incoming Payment generated GL | 161 |
| 46 | Outgoing Payment generated GL | 460 |
| 59 | Goods Receipt generated GL | 633 |
| 60 | Goods Issue generated GL | 659 |
| 67 | Inventory Transfer generated GL | 405 |
| 202 | Production Order related GL | 386 |
| 30 | Manual/standalone journal candidate | 109 |
| 162, 321, 1470000049, -4 | Special accounting/stock/system buckets | Must be classified before import. |

## Payment Child Tables To Stage

The current staging pipeline treats `ORCT` and `OVPM` as header-only families. That is not enough for native ERPNext Payment Entries.

Payment replay needs allocation and method detail tables as well. These must be staged and mapped before importing payments:

| SAP area | Likely SAP child tables | ERPNext target detail |
|---|---|---|
| Incoming payment invoice allocation | `RCT2` and related `RCT*` tables | `Payment Entry Reference` rows against Sales Invoices / credit notes. |
| Outgoing payment invoice allocation | `VPM2` and related `VPM*` tables | `Payment Entry Reference` rows against Purchase Invoices / debit notes. |
| Cheque details | `RCT1`, `VPM1`, `OCHO`, `CHO1` | `reference_no`, `reference_date`, bank/cheque tracking. |
| Account/GL payment rows | `RCT4`, `VPM4` | Payment Entries or Journal Entries for payments not against parties. |
| Withholding/tax on payment | `RCT5`, `VPM5` where present | Tax deduction rows or separate accounting treatment. |

Action: add these payment children to staging before building the Payment Entry importer.

## SAP Addon And Custom Data To Copy

SAP B1 at LLM has meaningful business data in user-defined `@...` tables. These should not be ignored. They also should not be dumped as anonymous raw rows into ERPNext if there is a real operational concept behind them.

Current live active-addon inventory:

| SAP cluster | Live tables / rows | Likely business meaning | ERPNext target |
|---|---:|---|---|
| `@OBRTF`, `@BRTF1..5` | 6 tables / 5,334 rows | Batch Receipt Form active data | Custom DocTypes or mapped batch/quality/manufacturing records after decoding. |
| `@OBRTR`, `@BRTR1..5` | 6 tables / 5,116 rows | Batch Return/Rework active data | Custom DocTypes plus stock/manufacturing links after decoding. |
| `@BRTCON*` | 2 tables / 61 rows | Batch container data | Custom child table or linked warehouse/batch metadata. |
| `@MIGTIN*` | 3 tables / 10,882 rows | Migration stock-in active data | Decode; may map to stock entries or custom migration trace docs. |
| `@MIGTOT*` | 3 tables / 13,248 rows | Migration stock-out active data | Decode; may map to stock entries or custom migration trace docs. |
| `@INUR_WOR*` | 11 tables / 18,680 rows | Work order addon data | ERPNext Work Order extensions / custom job-card doctypes. |
| `@INUR_PDN*` | 4 tables / 729 rows | Purchase receipt / GRPO extension | Purchase Receipt custom fields/child tables. |
| `@INURC_JDC*` | 3 tables / 8,575 rows | Job card data | Custom Job Card / manufacturing operation doctypes. |
| `@INURC_JOR*` | 6 tables / 8,176 rows | Job order data | Custom manufacturing operation doctypes. |
| `@INUR_CRM*`, `@INUR_OCRM*` | 8 tables / 2,444 rows | Customer/CRM addon data | Customer custom fields / CRM custom doctypes. |
| `@INUR_RAT*` | 5 tables / 522 rows | Rating/scoring addon | Custom customer/vendor/item rating doctypes after decoding. |
| `@ITEMPROPERTY`, `@ITEMSUBGROUP`, `@SUBCATEGORY` | 3 tables / 181 rows | Item classification | Item attributes / item group hierarchy / custom fields. |
| `@BPDIVISION`, `@BPSUBGRP` | 2 tables / 27 rows | BP segmentation | Customer/Supplier Group or custom fields. |
| `@REQBY`, `@DEPT`, `@LOCS`, `@PRJS` | 4 tables / 87 rows | Requested-by, department, location, project lookups | Fixtures / custom fields / `Department` / `Project`. |
| `@TAXEICON*`, `@ATAXEICON*` | 4 tables / 162 rows | Tax/e-invoice code data | Tax/GST/e-invoice mapping fixtures. |

Action: create a decoding sheet for every active addon cluster before claiming "everything from April is in ERPNext." If the business uses the table operationally, we model it as a proper ERPNext/custom document. If it is only SAP internal archive/system data, we document why it is excluded.

## Tables Not To Replay As Native ERPNext Docs

These are still evidence, but not operational ERPNext documents:

| SAP data | Treatment |
|---|---|
| SAP generated `OJDT/JDT1` for native docs | Use for reconciliation only; do not import separately. |
| `A*`, `ADO*`, archive/history mirror tables | Keep in archive/evidence layer, not native ERPNext. |
| `CGEV`, `CPRF`, `USR5`, `ES_*`, `PAL_*`, HANA metadata/logs | Skip from operational migration. |
| `OILM`, `OITL`, `OIVL` inventory logs | Use for stock/valuation reconciliation, not direct DocType import. |
| SAP drafts unless approved | Either skip or import as ERPNext drafts only if users need them. |

## Required Custom Trace Fields

Every imported native ERPNext document should carry enough SAP identity to be idempotent and auditable.

Minimum fields to create or reuse on every imported doctype:

- `custom_sap_source_table`
- `custom_sap_doc_entry`
- `custom_sap_doc_num`
- `custom_sap_trans_id` where available
- `custom_sap_base_ref` where available
- `custom_sap_import_window`
- `custom_sap_payload_hash` or link to staged SQLite source row

Line/child rows should preserve:

- SAP line number (`LineNum` or `Line_ID`)
- SAP item/account/warehouse codes
- SAP base document links (`BaseType`, `BaseEntry`, `BaseLine`) where present

Teaching note: this is how we make imports rerunnable. A proper migration must be able to say "SAP DocEntry X became ERPNext document Y" without searching remarks text.

## Import Order

This is the order that keeps ERPNext behavior closest to a real go-live.

1. Lock source window and stage SAP April data into SQLite.
2. Refresh master deltas: accounts, parties, items, warehouses, UOMs, dimensions, taxes, banks, payment terms, price lists.
3. Create missing trace custom fields on all target doctypes.
4. Create opening operational state at `2026-04-01`:
   - stock reconciliation,
   - open AR/AP,
   - open SO/PO,
   - open Work Orders,
   - batch/serial state if needed.
5. Import Sales Orders and Purchase Orders.
6. Import Work Orders and BOM-dependent manufacturing setup.
7. Import Purchase Receipts and Delivery Notes.
8. Import Stock Entries for goods receipts, goods issues, transfers, and manufacturing issue/receipt.
9. Import Sales Invoices and Purchase Invoices, linked to orders/receipts/deliveries when SAP base links exist.
10. Import Credit Notes / Debit Notes / returns.
11. Import Payment Entries with invoice allocations.
12. Import standalone/manual Journal Entries only.
13. Import/decode addon custom operational data.
14. Reconcile counts, ledgers, stock, party outstanding, tax reports, and document status.

## Reconciliation Required Before Sign-Off

We do not call the April replay complete until these match:

| Area | Reconciliation |
|---|---|
| Counts | SAP header counts by family vs ERPNext document counts by source table/docentry. |
| Sales | SAP sales register vs ERPNext Sales Invoice totals, taxes, returns, customer split. |
| Purchase | SAP purchase register vs ERPNext Purchase Invoice totals, taxes, returns, supplier split. |
| Payments | SAP incoming/outgoing payments vs ERPNext Payment Entry totals, bank/cash accounts, allocations. |
| AR/AP | Customer and supplier outstanding as of each checkpoint date. |
| Stock quantity | SAP item-warehouse stock vs ERPNext stock balance. |
| Stock value | SAP valuation (`OIVL`/inventory valuation reports) vs ERPNext stock value. |
| GL | SAP `OJDT/JDT1` generated GL vs ERPNext GL from native documents, excluding double-posted generated journals. |
| Tax/GST | Taxable value, tax amount, credit notes, debit notes, and e-invoice/e-way references where applicable. |
| Manufacturing | Work Order counts/status, produced qty, consumed qty, WIP/finished goods movement. |
| Addon data | Every active addon table is either imported to a named doctype or explicitly excluded with reason. |

## Immediate Build Tasks

1. Extend SAP staging for April native replay:
   - include payment child tables (`RCT*`, `VPM*`),
   - include base document link fields,
   - include active addon clusters,
   - include batch/serial tables if items require them.
2. Add ERPNext trace fields to all target doctypes.
3. Build preflight reports:
   - missing item/party/warehouse/account/tax/cost-center references,
   - canceled/closed/open status split,
   - base-link chain availability,
   - payment allocation coverage,
   - April SAP generated GL by `TransType`.
4. Decide opening-state mechanics for April 1:
   - stock reconciliation,
   - open AR/AP invoices,
   - open orders,
   - open work orders.
5. Implement importers in dependency order, starting with the smallest closed loop:
   - Sales Order -> Delivery Note -> Sales Invoice -> Incoming Payment for a bounded sample,
   - then Purchase Order -> Purchase Receipt -> Purchase Invoice -> Outgoing Payment,
   - then stock/manufacturing.


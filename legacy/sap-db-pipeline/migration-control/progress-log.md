# Progress Log

## 2026-04-27

- Created clean target site `llmnew.localhost` for the LLM SAP migration.
- Applied clean-site bootstrap for company `LLM`: fiscal years, UOMs, groups, branches, warehouses, cost centers, and SAP COA.
- Applied SAP master data to `llmnew.localhost`: 849 customers, 1,425 suppliers, 4,969 SAP items, and 380 SAP resource/operation items.
- Applied SAP tax/payment setup: 17 AR tax templates, 24 AP tax templates, 44 banks, and 2 company bank accounts.
- Ran transaction preflight across the full staged SAP window through `2026-03-31`; missing references were 0.
- Imported Option 3 historical accounting movement from SAP `OJDT/JDT1` as submitted ERPNext Journal Entries for `2018-02-22` through `2026-03-31`.
- Historical journal import result after the 2026-04-28 late-March SAP delta: 139,215 submitted SAP Journal Entries, 139,215 distinct SAP `TransId`s, no missing/extra `TransId`s, no April 2026 or later postings.
- Reconciled imported JE child totals to SQLite source: debit and credit both `33,956,629,719.60`; account-level net movement matched for all 399 SAP accounts with movement.

## 2026-04-28

- Generated the April 1 hybrid cutover report from live SAP reconciliation tables (`OITR`/`ITR1`) and SAP GL lines (`JDT1`), not from current open invoice headers.
- Imported pre-April opening operational AR/AP into `llmnew.localhost`: 1,284 opening Sales Invoices, 2,149 opening Purchase Invoices, 762 opening party-balance Journal Entries, and 42 walk-back clearing Journal Entries.
- Verified the opening AR/AP import has zero duplicate opening keys and zero net GL effect after walk-back clearing.
- Verified the four SAP control account balances are unchanged from `2026-03-31` to `2026-04-01` after opening import.
- Imported April 1 stock opening operational state into `llmnew.localhost`: 66 opening Stock Reconciliations for 6,577 positive item/warehouse groups, 1 opening negative Stock Entry for 19 negative item/warehouse groups, and 67 stock walk-back clearing Journal Entries.
- Verified stock opening has 6,596 active Stock Ledger Entries, zero net GL effect after stock walk-back clearing, and no non-opening April Stock Entries.
- Reconciled stock opening quantity to SAP source within ERPNext precision: SAP source `7,145,461.60434`, ERPNext active opening `7,145,461.604000`.
- Reconciled stock opening value with a precision variance: SAP source `191,311,493.60`, ERPNext active opening `191,312,829.46`, variance `+1,335.86` from ERPNext valuation-rate rounding.
- Stopped before April 2026 business transactions. No normal April Sales Invoices, Purchase Invoices, Payment Entries, Delivery Notes, Purchase Receipts, or Stock Entries have been imported.
- Refreshed April 2026 live SAP document staging through `2026-04-28`: 273 AR invoices, 85 AR credit memos, 373 AP invoices, 24 AP credit memos, 160 incoming payments, 446 outgoing payments, 130 sales orders, 264 purchase orders, 318 deliveries, 281 purchase receipts, 665 goods receipts, 690 goods issues, 412 transfers, 773 production orders, and 3,949 journals.
- Imported April native order layer into `llmnew.localhost`: 130 April Sales Orders plus 91 pre-April referenced Sales Orders; 234 April Purchase Orders plus 89 pre-April referenced Purchase Orders.
- Enabled migration settings needed to preserve SAP behavior: multiple Sales Orders per customer PO, rate mismatch as warning instead of stop, fractional `Nos`, over-delivery/receipt allowance, negative stock/valuation seeds for April delivery items.
- Imported April Delivery Notes and Purchase Receipts as native ERPNext stock documents. First taxless pass was canceled and retagged as `CANCELED-NOTAX-*`; clean active pass has 312 submitted Delivery Notes and 273 submitted Purchase Receipts with zero duplicate active SAP DocEntry keys.
- Active clean stock ledger rows from April Delivery/Purchase Receipt layer: 1,423 Delivery Note SLE rows and 327 Purchase Receipt SLE rows.
- April transaction replay is not complete yet. Pending families: goods receipts, goods issues, inventory transfers, production/work orders, sales/purchase invoices, credit/debit notes, payments, standalone manual journals, and final reconciliation.

## 2026-04-25

- Created `sap-db-pipeline` as the SAP equivalent of the Tally staging pipeline.
- Confirmed the preferred extraction path is SAP HANA read-only SQL through `hdbcli`, not ODBC-only and not direct ERPNext writes.
- Added migration control documents for plan, progress, source inventory, and reconciliation.
- Live `doctor` connected to SAP HANA as the read-only LLM user and confirmed 3,319 tables in source schema `LLM_LIVENEW`.
- Live `profile-documents` succeeded for `2025-04-01` through `2026-04-25`.
- Live `sync-masters` staged SAP master-related rows: branches 6, accounts 600, BP groups 13, business partners 2,274, BP addresses 6,686, BP tax IDs 5,372, contacts 21, item groups 13, UOMs 7, warehouses 29, items 4,969, item prices 49,690, item-warehouse balances 144,101.
- Fixed the `CRD7` staging key after reconciliation showed row collapse. Re-ran full master staging and confirmed SQLite holds 213,781 master rows, matching the extracted table counts exactly.
- Ran a limited sales-invoice document staging smoke test. Fixed line extraction so header limits do not truncate child lines; 10 staged headers produced 13 staged lines.
- Ran `sync-documents --family all --limit 2` and fixed SAP line-number handling for `JDT1` journal lines. The smoke test now passes for all planned document families.
- Ran full first-leg transaction staging for `2025-04-01` through `2026-04-25`. SQLite now holds 236,404 SAP document headers and 727,503 SAP document lines.
- Re-profiled SAP after full staging and found the live source moved during the run. Final migration needs a SAP posting freeze or a tighter cutoff before final reconciliation.
- Added generic raw SAP sweep staging and ran it against `LLM_LIVENEW`.
- Full raw clean sweep reconciled exactly: 820 non-empty SAP tables, 13,741,507 SAP source rows, and 13,741,507 SQLite raw rows.
- Fixed binary/memoryview serialization and restaged 9 previously failing non-empty tables. Zero non-empty SAP table errors remain.
- Marked 30 zero-row HANA table-type artifacts as skipped metadata, not source-data failures.
- Audited setup and masters before transaction migration. COA is mostly mapped, but 4 postable SAP accounts, referenced items, referenced BPs, cost-center/dimension mappings, tax templates, and bank/payment setup must be fixed before transactions.
- Applied COA setup fixes to `llm.localhost`: added `custom_sap_acct_code`, mapped SAP account codes, created 4 missing postable investment accounts, mapped SAP root buckets, and fixed/renamed `1204202` to `SALARY ADVANCE`.
- Applied missing Business Partner setup to `llm.localhost`: all 849 SAP customer CardCodes and 1,425 SAP supplier CardCodes now have ERPNext Customer/Supplier mappings.
# 2026-04-25 - Sales Invoice Import

- Imported SAP non-canceled `OINV` AR invoices into ERPNext `Sales Invoice`.
- Count: `6,787`.
- Reconciled grand total: SAP `715,104,413.63`, ERPNext `715,104,413.63`.
- Per-invoice grand-total differences: `0`.
- Added importer: `scripts/import_llm_sales_invoices.py`.

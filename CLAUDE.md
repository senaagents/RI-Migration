# RI-Migration
AI-powered data migration agent-app for the Sena platform.
Migrates data from external systems (Tally, SAP, etc.) into Sena agent-apps (SenaERP, etc.).

## Critical: Tally API Gotchas

- **`TallyClient.get_day_book()` is BROKEN for full-year exports.** It uses the Day Book report which is locked to Tally's UI period (F2 date range). SVFROMDATE/SVTODATE in the XML do NOT override it. Use the TDL Collection approach with `ISINITIALIZE="Yes"` and `SVCURRENTCOMPANY` instead. See MIGRATION_GUIDE.md "Full-Year Voucher Export" section.
- **SVCURRENTCOMPANY is required for voucher queries.** Without it, TDL Collection returns empty for Voucher objects (master data works without it).
- **Tally crashes on concurrent requests.** Always send ONE XML request at a time. The HTTP server is single-threaded.
- **CMPINFO VOUCHER:0 is misleading.** It's a counter/summary field, not the actual voucher count. Always query the data directly.

## Transformer Gaps

`transform_vouchers()` silently drops unhandled voucher types. For Avinash Industries, 63% of vouchers (18,750 of 29,562) are stock/manufacturing types that get dropped:
- Stock Journal (all variants): needs Stock Entry (Repack/Manufacture)
- Material In/Out: needs Stock Entry (Receipt/Issue)
- Receipt Note: needs Purchase Receipt
- Delivery Challan: needs Delivery Note
- Purchase/Sales/Job Orders: needs PO/SO/Work Order support

## Idempotency Warning

Master data import is idempotent (duplicates are skipped). Opening entries (JE, SR) are NOT -- they create new documents every run. Cancel duplicates manually.

## Docs

- `MIGRATION_GUIDE.md` -- Full step-by-step guide with API reference, voucher type mapping, and troubleshooting
- `senacode/docs/scars/migration.md` -- Gotchas from real migration failures

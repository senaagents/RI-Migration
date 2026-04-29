# Wave 2 Productization - 2026-04-28

## What Landed

Wave 2 was reviewed as a productization batch. Only rules that kept the final
export boundary safe were ported.

Code changes:

```text
tally1800/decoded_export.py
scripts/cross_company_validation_report.py
scripts/probe_w2e_production_confidence.py
```

Docs updated:

```text
README.md
DECODED-SQLITE-MANIFEST.md
MISSION-CONTRACT.md
HYBRID-1800-XML-AI-LOOP.md
```

## Promoted To Product Surface

Permanent production selector views are now emitted by `decode-sqlite`:

```text
production_voucher_headers_1800
production_ledger_entries_1800
production_inventory_entries_1800
production_no_row_hard_misses_1800
production_review_queue_1800
production_xml_repair_queue_1800
```

These views encode the Lane F/C09 contract:

```text
final vouchers:
  keyed voucher_header_candidates where base_type_code != 31

final ledger:
  voucher_ledger_entries_1800 where confidence='high', joined to final vouchers

final inventory:
  voucher_inventory_entries_1800 where confidence='high', joined to final vouchers

review:
  needs_xml_review=1 and needs_xml_repair=0

repair:
  xml_repair_queue joined to final vouchers
```

## Allocation Candidate Tables

W2C allocation work is now productized as candidate/audit surfaces, not final
business allocation rows:

```text
voucher_bill_allocation_candidates_1800
voucher_bank_allocation_candidates_1800
voucher_order_allocation_candidates_1800
```

Promoted source facts:

```text
LinkMgr 0x232a/type 0x0d raw voucher pointer, trusted only if it resolves
LinkMgr 0x232d/type 0x03 raw ledger pointer, trusted only if it resolves
typed UTF-16 LinkMgr strings for bank/payment fields
bill/bank amount-shaped LinkMgr fields as candidate evidence
```

Important gate:

```text
bill candidates require amount evidence.
0x0002 string-only rows are not emitted as bill candidates because they produced
a large name-only false surface in the 070525 probe.
```

## Rejected Or Kept Research-Only

No W2B final ledger recall rule was promoted. The tested candidates added true
rows but also added false rows.

No W2D synthetic two-line Sales/Purchase parser rule was promoted. The fixtures
are useful, but the current decoder still misses those repeated-line synthetic
inventory rows and should repair them through XML until a byte rule is proven.

No final bill, bank, or order allocation table was promoted. Current LinkMgr
tables are repair hints and parser-learning evidence only.

## Verification

Commands run:

```bash
python3 -m py_compile \
  tally1800/decoded_export.py \
  scripts/cross_company_validation_report.py \
  scripts/probe_w2e_production_confidence.py

PYTHONPATH=. python3 -m tally1800 decode-sqlite \
  out/synthetic-live/SALES_XML001/after \
  out/wave2_productized_sales_xml001.sqlite3

PYTHONPATH=. python3 -m tally1800 decode-sqlite \
  live-tally-data/100000 \
  out/wave2_productized_test_synthetic_current.sqlite3

PYTHONPATH=. python3 scripts/probe_w2e_production_confidence.py \
  --db out/wave2_productized_sales_xml001.sqlite3 \
  --db out/wave2_productized_test_synthetic_current.sqlite3 \
  --out-dir out/wave2-productized-confidence-smoke
```

Smoke result:

```text
quick_check: ok
Test Synthetic current:
  production headers: 13
  production inventory rows: 2
  production ledger rows: 22
  production XML repairs: 2
```

Large LinkMgr-only 070525 probe:

```text
linkmgr pages:       44,688
voucher_resolves:     3,589
ledger_resolves:      3,453
bill candidates:     12,856
bank candidates:      3,777
```

This probe reused the current 070525 decoded header/master tables and parsed
only `live-tally-data/070525/LinkMgr.1800`; it did not re-run the huge full
TranMgr decode.

## Next Product Step

Build the actual hybrid runner around these surfaces:

```text
1. run decode-sqlite
2. read production_xml_repair_queue_1800 and production_review_queue_1800
3. fetch only targeted TDL/XML repairs
4. write repaired sibling tables with provenance
5. write parser-learning handoffs from every repair
```

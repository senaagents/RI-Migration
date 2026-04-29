# 1800 Integration Board

This is the main-thread coordination board. The cracker agents work in lanes;
this thread owns productization, integration, and truthfulness.

## Main Thread Role

Codex main thread is the productization owner, not another duplicate cracker
lane.

Responsibilities:

- Continue narrow decoder work that is already in flight.
- Review incoming handoffs from `out/cracker-handoffs/`.
- Port only byte-evidenced, validated rules into `tally1800/`.
- Reject or quarantine noisy rules as candidate/audit-only.
- Run syntax checks and focused decode/regression checks after each port.
- Update `STATUS-2026-04-27.md`, `OPEN-PROBLEMS.md`,
  `BREAKTHROUGHS-2026-04-27.md`, and `SYNTHETIC-MAPPING-PLAYBOOK.md`.
- Keep the strict target honest: no `100%` claim until direct non-vaulted
  `.1800 -> SQLite` works cross-company without XML/Rosetta in production.

## Active Main-Thread Work

1. Finish and validate `purchase_stock_rate_f6`.
   - Synthetic target: `PURCHASE_XML001`, voucher `11`.
   - Expected trusted row: item `211`, ledger `214`, unit `210`, qty `4.00`,
     rate `55.55`, amount `-222.20`.
   - Current state: parser emits the synthetic row in
     `voucher_inventory_entries_1800` and Avinash replay validates cleanly.
   - Avinash replay: `75 / 75` exact full-tuple matches under
     `31-Puchase(Monthly)`, `0` extras, hard XML repair `84 -> 65`.
   - Next checks: keep this rule stable while Lane A attacks remaining Purchase
     Monthly misses.

2. Keep synthetic Sales/Purchase inventory learnings documented.
   - `sales_stock_rate_f6` is already validated against synthetic and five
     Avinash live XML checks.
   - `purchase_stock_rate_f6` is documented with synthetic and Avinash Rosetta
     replay evidence.

3. Prepare to ingest cracker handoffs.

## Incoming Handoff Intake

Cracker agents must write outputs here:

```text
out/cracker-handoffs/
```

For each handoff, integration review should record:

```text
handoff file:
lane:
claim:
decision: port / reject / audit-only / needs rerun
changed files:
verification run:
docs updated:
remaining risk:
```

## Porting Checklist

Before porting:

- Read the full handoff and any referenced scripts/artifacts.
- Check whether the claim supersedes, retracts, or conflicts with existing
  docs/code.
- Confirm the byte pattern has a false-positive guard.
- Confirm validation includes numerator, denominator, extras, and misses.
- Prefer source-labelled candidates over silent final-table promotion.

After porting:

- Run `python3 -m py_compile` on touched Python files.
- Regenerate at least one synthetic decoded SQLite if the rule affects that
  family.
- Regenerate or compare one Avinash decoded SQLite if the rule affects
  production voucher families.
- Query trusted and candidate tables for source counts and obvious extras.
- Update docs immediately.

## Current Cracker Lanes

```text
Lane A: high-volume inventory rows
Lane B: accounting ledger rows + LinkMgr allocations
Lane C: masters + statutory + invoice/e-invoice
Lane D: synthetic live writer/diff microscope
Lane E: cross-company regression harness
Lane F: production SQLite contract + XML repair planner
```

Only Lane D may write to live Tally.

## Integration Decisions Log

Append decisions here as handoffs arrive.

### 2026-04-28 - Board Created

Main thread will continue productization/integration while cracker lanes run in
parallel. First local integration target is `purchase_stock_rate_f6`.

### 2026-04-28 - `purchase_stock_rate_f6` Promoted

Decision: port/promote as high-confidence for Purchase Monthly style rows.

Evidence:

```text
Synthetic PURCHASE_XML001:
  voucher 11 / Purchase / item 211 / ledger 214 / unit 210
  qty 4.0 / rate 55.55 / amount -222.2

Avinash 070525 replay:
  source rows: 75
  exact Rosetta full-tuple matches: 75 / 75
  extras from this source: 0
  trusted 31-Puchase(Monthly): 7583 / 7682, extras 136 unchanged
  XML repair queue: 84 -> 65
```

Changed files:

```text
tally1800/inventory_decode.py
STATUS-2026-04-27.md
BREAKTHROUGHS-2026-04-27.md
SYNTHETIC-MAPPING-PLAYBOOK.md
OPEN-PROBLEMS.md
1800-INTEGRATION-BOARD.md
```

Verification:

```text
PYTHONPATH=. python3 -m tally1800 decode-sqlite live-tally-data/070525 out/070525_decoded_purchase_stock_rate_f6.sqlite3
python3 -m py_compile tally1800/inventory_decode.py tally1800/decoded_export.py scripts/synthetic_tally_lab.py
```

### 2026-04-28 - Lane E Validation Harness Accepted

Handoffs:

```text
out/cracker-handoffs/E01-validation-harness-2026-04-28.md
out/cracker-handoffs/E02-cross-company-metrics-2026-04-28.md
out/cracker-handoffs/lane-e-summary-2026-04-28.md
```

Decision: port/accept as a read-only regression gate, not as parser proof.

Accepted artifact:

```text
scripts/cross_company_validation_report.py
```

Local verification:

```text
python3 -m py_compile scripts/cross_company_validation_report.py
PYTHONPATH=. python3 scripts/cross_company_validation_report.py --include-synthetic --out-dir out/cross-company-validation-review
```

Current useful baseline from the harness:

```text
070525 latest: headers=31113, inventory final/candidates=100502/243876, repair=64
100001:        headers=2275,  inventory final/candidates=797/1774,    repair=40
300925:        headers=1859,  inventory final/candidates=311/722,     repair=22
```

Remaining risk: this is shape/regression validation only. It cannot prove row
precision on companies without same-snapshot XML/Rosetta truth.

### 2026-04-28 - Lane F Production SQL Intake

Handoffs:

```text
out/cracker-handoffs/C09-production-contract-queries-2026-04-28.sql
out/cracker-handoffs/C09-query-results-070525-2026-04-28.txt
out/cracker-handoffs/C09-query-results-SALES_XML001-2026-04-28.txt
out/cracker-handoffs/C09-query-results-PURCHASE_XML001-2026-04-28.txt
```

Decision: keep the SQL as a candidate production contract/query appendix. Do
not treat the checked-in `070525` result file as current after
`purchase_stock_rate_f6`; it was generated before the latest replay.

Fresh run against `out/070525_decoded_purchase_stock_rate_f6.sqlite3`:

```text
q00_exportable_voucher_headers:        30545
q00_final_ledger_rows:                 38132
q00_final_inventory_rows:              100497
q00_inventory_no_row_hard_misses:      65
q00_medium_candidate_review_vouchers:  15255
q00_minimal_xml_repair_queue:          65
```

Next action: convert the useful parts into documented production views or a
CLI/report command after Lane F provides its lane summary.

Lane F summary later arrived:

```text
out/cracker-handoffs/C09-production-contract-2026-04-28.md
out/cracker-handoffs/C09-query-appendix-2026-04-28.md
out/cracker-handoffs/F-summary-2026-04-28.md
```

Accepted decisions:

```text
final vouchers = keyed voucher_header_candidates with base_type_code != 31
final inventory = voucher_inventory_entries_1800 where confidence='high'
final ledger = voucher_ledger_entries_1800 where confidence='high'
review = needs_xml_review=1 and needs_xml_repair=0
repair = xml_repair_queue
```

Patch accepted: `Sales` and `Purchase` are included in
`INVENTORY_VOUCHER_TYPES` so synthetic built-in inventory voucher rows do not
become false hard-repair queue entries.

### 2026-04-28 - Wave 2 Productization Intake

Productization summary:

```text
WAVE2-PRODUCTIZATION-2026-04-28.md
```

Ported into code:

```text
production_voucher_headers_1800
production_ledger_entries_1800
production_inventory_entries_1800
production_no_row_hard_misses_1800
production_review_queue_1800
production_xml_repair_queue_1800

voucher_bill_allocation_candidates_1800
voucher_bank_allocation_candidates_1800
voucher_order_allocation_candidates_1800
```

Kept out of final exports:

```text
W2B ledger recall candidates
W2D two-line synthetic inventory fixtures
LinkMgr bill/bank/order allocation semantics
```

Verification:

```text
py_compile passed for changed Python files
synthetic decode smoke passed
production confidence smoke passed
070525 LinkMgr-only candidate probe:
  bill candidates 12,856
  bank candidates 3,777
```

### 2026-04-28 - 100025 Current Decode Regenerated

Lane E flagged `100025` as stale. Main thread regenerated it with current code:

```text
PYTHONPATH=. python3 -m tally1800 decode-sqlite \
  /Users/aakashchid/workshop/sena/office/_workspace-admin/data-dumps/avinash-tally-live-2026-04-21/100025 \
  out/100025_decoded_current.sqlite3
```

During the first attempt, a derived inventory candidate rate exceeded SQLite's
signed 64-bit integer range and crashed insertion. Patch:

```text
tally1800/inventory_decode.py
  drop inventory candidates whose quantity/rate/amount scaled values do not fit
  SQLite int64
```

This is a robustness filter, not a new parser rule.

Current `100025` metrics:

```text
headers:               28,562
masters:                7,958
ledger final/cand:     42,766 / 516,465
inventory final/cand:  70,572 / 173,300
review vouchers:       13,454
repair queue:           1,541
LinkMgr/Stat/EInv:     37,386 / 8,198 / 931
```

Fresh harness output:

```text
out/cross-company-validation-current/cross-company-validation.json
out/cross-company-validation-current/cross-company-validation.md
out/cross-company-validation-current/cross-company-validation-summary.csv
```

### 2026-04-28 - Lane B Ledger/LinkMgr Intake

Handoffs:

```text
out/cracker-handoffs/C05-ledger-source-corroboration-2026-04-28.md
out/cracker-handoffs/C06-linkmgr-allocation-backptr-fields-2026-04-28.md
out/cracker-handoffs/C09-ledger-allocation-sql-policy-2026-04-28.md
out/cracker-handoffs/lane-b-summary-2026-04-28.md
```

Accepted:

```text
voucher_ledger_entries_1800 remains the final direct ledger surface
voucher_ledger_entry_candidates remains review/audit
standalone f6_01_44 remains rejected for final ledger rows
extended 0x0002/0009 remains amount-only audit evidence
LinkMgr (0x232a,0x0d) is voucher_master_id
LinkMgr (0x232d,0x03) is ledger_master_id
fid-only LinkMgr 0x232a matching is rejected
```

Validation to preserve:

```text
Synthetic CTR002 cumulative ledger rows: 18 / 18, extras 0
070525 final ledger tuples: 38,132 / 40,777, extras 0
070525 LinkMgr allocation pages: 3,610
070525 resolving voucher+ledger back-pointer pages: 3,432
```

Deferred:

```text
LinkMgr bill/bank detail tables stay candidate-only
bill type from date-slot presence stays heuristic
Advance bill type unresolved
```

### 2026-04-28 - Lane A Inventory Intake

Handoffs:

```text
out/cracker-handoffs/C02-sales-logical-compact-2026-04-28.md
out/cracker-handoffs/C03-journal-columnar-variant-rejection-2026-04-28.md
out/cracker-handoffs/C04-purchase-stock-rate-f6-and-medium-gates-2026-04-28.md
out/cracker-handoffs/lane-a-summary-2026-04-28.md
```

Accepted:

```text
sales_logical_compact stays high-confidence, but only under the strict
page-crossing compact Sales row guard already in tally1800/inventory_decode.py
purchase_stock_rate_f6 remains high-confidence
post_rate_f6_rowrate remains high-confidence for journal inventory rows
columnar_f6_44_46 remains medium/candidate-only
```

Rejected:

```text
sales_logical_post_rate_f6 broad promotion
journal f6 flag 0x0c high promotion
journal f6 flag 0x08 and f7/f8/f9/fa/06 02 tested promotions
purchase_voucher_unique_ledger promotion to high-confidence
```

Local reproducible tuple check against
`out/070525_decoded_laneA_sales_logical.sqlite3`:

```text
61 Sales final full tuples:            2,051 / 2,068, extras 83
31-Puchase(Monthly) final full tuples: 7,583 / 7,682, extras 136
sales_logical_compact source rows:     1,148 direct, 1,128 exact, 20 extras
purchase_stock_rate_f6 source rows:       75 direct,    75 exact,  0 extras
xml_repair_queue rows:                    64
```

Decision note: Lane A reported `2,058 / 2,068` Sales exact and `7,611 / 7,682`
Purchase exact. The main-thread product docs use the reproducible local tuple
comparison above until those optimistic counts are independently reproduced.

Remaining risk: `sales_logical_compact` improves hard repair by one voucher and
adds no new final-table extras overall, but source-local `sales_logical_compact`
still has 20 rows absent from Rosetta when queried by source label. Keep its
guards narrow and do not use it as a template for broader F6 promotion.

### 2026-04-28 - Lane C Masters/Statutory/E-Invoice Intake

Handoffs:

```text
out/cracker-handoffs/C08-typed-master-tables-2026-04-28.md
out/cracker-handoffs/C07-statstatus-policy-2026-04-28.md
out/cracker-handoffs/C07-invoice-einvoice-archive-2026-04-28.md
out/cracker-handoffs/lane-c-summary-2026-04-28.md
```

Accepted:

```text
typed master export tables: companies, groups, ledgers, stock_items, units,
  godowns, cost_centres, voucher_types
voucher_types.export_policy separates exportable_observed, status_observed,
  and internal_candidate
voucher_statutory_status stays a direct statutory index table, not a voucher-id
  resolver
voucher_invoice_metadata stays view=1 record-level gated direct metadata
voucher_einvoice_archive stays view>=3 IRN/ACK/JWT-gated archive/audit payload
```

Rejected or audit-only:

```text
voucher type kind=10 as exportable by itself
ungated 0x0019 IRN extraction
broad view>=3 archive scans
e-way-only 12-digit scans
StatStatus as a unique voucher MASTERID resolver
render-time/default master fields as direct source truth
```

Cross-company Lane C validation to preserve:

```text
Typed master counts:
  070525 groups=283 ledgers=2968 stock_items=2754 units=23 godowns=111 cost_centres=310 voucher_types=125
  100001 groups=118 ledgers=417  stock_items=374  units=6  godowns=34  cost_centres=6   voucher_types=70
  100025 groups=278 ledgers=2456 stock_items=1878 units=23 godowns=112 cost_centres=310 voucher_types=119
  300925 groups=374 ledgers=1673 stock_items=625  units=22 godowns=65  cost_centres=197 voucher_types=108

StatStatus:
  070525 8350 rows, date/type multiset 8345/8350, warnings 0
  100001 1324 rows, date/type multiset 1324/1324, warnings 0
  100025 8198 rows, date/type multiset 8195/8198, warnings 0
  300925  388 rows, date/type multiset 388/388,   warnings 0

Invoice/e-invoice:
  070525 view=1 metadata rows=741, unique_irns=724, conflicts=0
  100025 view=1 metadata rows=931, unique_irns=897, conflicts=0
  070525 view>=3 archive rows=16, unique archive IRNs=13, all overlap view=1
```

Patch accepted in the regression harness:
`scripts/cross_company_validation_report.py` now expects typed master tables and
`voucher_einvoice_archive`, and reports typed/archive counts in CSV/Markdown.

Remaining risk: typed tables are direct surfaces, but hierarchy, aliases,
rendered defaults, full archive semantics, and StatStatus-to-voucher uniqueness
remain open problems.

### 2026-04-28 - Lane D Status

No Lane D live-writer handoff is present in `out/cracker-handoffs/` as of this
review. Synthetic live writing has earlier proven ledger/accounting/Sales/
Purchase cases, but no new productized Lane D result is available from the
handoff queue.

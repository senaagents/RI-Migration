# Hybrid 1800/XML AI Loop

This is the product loop that combines `tallydatacrack-codex` and
`tally-db-pipeline`.

Canonical contract: `MISSION-CONTRACT.md`. If anything here conflicts with
that file, follow `MISSION-CONTRACT.md`.

The goal is not "XML forever". The goal is:

```text
direct .1800 decode first
small targeted XML only for gaps
use each XML gap to improve the direct .1800 decoder
```

## Repos

```text
direct decoder:
  /Users/aakashchid/workshop/sena/tallydatacrack-codex

Tally XML pipeline:
  /Users/aakashchid/workshop/tally-db-pipeline
```

## Roles

`tallydatacrack-codex` owns:

```text
.1800 file parsing
direct SQLite output
confidence labels
repair/review queue generation
byte-level parser learning
TDL helper experiments
```

`tally-db-pipeline` owns:

```text
live Tally health checks
one-request-at-a-time XML HTTP
narrow voucher MASTERID/GUID object exports
XML parsing into production-style tables
raw XML payload preservation
Tally operational scars and retries
```

## Production Flow

1. Decode local `.1800` folder:

```bash
cd /Users/aakashchid/workshop/sena/tallydatacrack-codex
PYTHONPATH=. python3 -m tally1800 decode-sqlite <company-1800-folder> out/<company>_decoded.sqlite3
```

2. Use direct SQLite surfaces:

```text
final/high confidence:
  companies
  groups
  ledgers
  stock_items
  units
  godowns
  cost_centres
  voucher_types
  voucher_header_candidates
  voucher_ledger_entries_1800
  voucher_inventory_entries_1800
  voucher_statutory_status
  voucher_invoice_metadata
  voucher_einvoice_archive

candidate/review:
  voucher_ledger_entry_candidates
  voucher_inventory_entry_candidates
  linkmgr_allocation_pages

control plane:
  voucher_decode_audit
  xml_repair_queue
```

3. Generate a targeted XML repair plan:

```bash
PYTHONPATH=. python3 scripts/hybrid_1800_pipeline_loop.py plan \
  --decoded-db out/070525_decoded_laneA_sales_logical.sqlite3 \
  --company "Avinash Industries - Chennai Unit - 2025-26" \
  --host 10.211.55.3 \
  --port 9001 \
  --out-dir out/hybrid-repair-070525
```

Outputs:

```text
out/hybrid-repair-070525/repair-plan.json
out/hybrid-repair-070525/repair-plan.md
out/hybrid-repair-070525/repair-commands.sh
```

4. Fetch only repair targets through `tally-db-pipeline`.

The generated commands call:

```bash
tally-db-pipeline sync-voucher-detail-master-id \
  --company "<exact open company name>" \
  --master-id <voucher MASTERID>
```

This uses the safer narrow object export path already implemented in
`tally-db-pipeline`, not broad Day Book/report exports.

5. Merge XML-backed repairs into the working SQLite.

The merge policy is:

```text
direct high-confidence row:
  keep direct row

xml repair row for voucher in xml_repair_queue:
  insert/update production-facing row with provenance = xml_repair

medium/review direct candidate:
  keep candidate unless XML confirms exact tuple

raw/audit-only row:
  never expose as final without a parser promotion
```

The first implementation should write repaired rows into sibling tables rather
than overwriting direct decoder tables:

```text
voucher_ledger_entries_repaired
voucher_inventory_entries_repaired
voucher_xml_repair_provenance
```

Then a production view can union:

```text
direct high-confidence rows
+ XML repair rows for exact gap vouchers
```

6. Learn from every XML repair.

For every repaired voucher, the AI/debug loop must store:

```text
voucher MASTERID
voucher type/date
XML truth rows
direct final rows
direct candidate rows
raw TranMgr page group bytes
raw LinkMgr pages if allocation-related
missing tuple(s)
candidate tuple(s) that almost matched
byte neighborhood for sentinel amount/item/ledger ids
```

If a repeatable byte rule is found, write a strict handoff under:

```text
out/cracker-handoffs/
```

Only promote it if it reduces misses without adding false positives.

## TDL Helper Policy

`tdl/CODX1800Bridge.tdl` is now the starting point for TDL-assisted microscopy.

Current status:

```text
read-only helper skeleton exists
not yet production-loaded
safe only for Test Synthetic until validated
```

The immediate production loop does not require loading TDL. We can already use
`tally-db-pipeline` narrow XML object exports by `MASTERID`.

TDL becomes valuable for:

```text
lab-only synthetic writes with better preflight
custom tiny truth reports for one voucher/master
explicit echo of MASTERID/GUID/object identity after writes
avoiding brittle giant XML reports
```

Do not load custom TDL into customer production without explicit approval.

## Why This Shrinks The Long Tail

Every production gap falls into one of four buckets:

```text
1. direct rule already exists but was too conservative
2. raw bytes contain the truth but parser does not know the shape
3. Tally renders/defaults a value not stored directly
4. real Tally/client-only behavior that should remain XML-backed
```

Buckets 1 and 2 become new `.1800` parser rules. Bucket 3 becomes documented
default/render policy. Bucket 4 stays targeted XML repair.

That means XML use should trend downward over time. It does not need to block
production while we continue reverse engineering.

## Current Safe Defaults

```text
9000 = Test Synthetic / 100000
9001 = Avinash 2025-26 / 70525

Run one Tally HTTP command at a time.
Verify the open company before fetching repairs.
Prefer MASTERID object export.
Avoid broad XML reports.
Never write to Avinash from synthetic tooling.
```

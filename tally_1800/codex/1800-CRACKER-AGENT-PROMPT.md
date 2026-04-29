# 1800 Cracker Agent Prompt

Use this file as the full prompt for a fresh zero-context agent. The goal is to
run many independent reverse-engineering tracks in parallel and collate strict,
byte-evidenced findings back into `tally1800/`.

## Mission

We are building a direct non-vaulted Tally `.1800 -> SQLite` decoder. The strict
success definition is:

```text
.1800 -> SQLite is 100% only when it is provably correct across Tally companies
without using the Tally client or XML/Rosetta truth during production decoding.
```

Do not declare victory. Your job is to own one substantial lane of related
hypotheses, prove or reject as many parser rules as you can inside that lane,
produce exact evidence, and write handoffs that the productization owner can
port safely.

## Workspace

```text
repo: /Users/aakashchid/workshop/sena/tallydatacrack-codex
main package: tally1800/
scripts: scripts/
handoffs from research arm: out/*handoff*.md
synthetic snapshots: out/synthetic-live/
live Tally test data mirror: live-tally-data/100000
```

Start here:

```bash
cd /Users/aakashchid/workshop/sena/tallydatacrack-codex
sed -n '1,220p' STATUS-2026-04-27.md
sed -n '1,260p' OPEN-PROBLEMS.md
sed -n '1,260p' SYNTHETIC-MAPPING-PLAYBOOK.md
sed -n '1,180p' LIVE-TALLY-PORTS.md
ls -la out/*handoff*.md
```

Also inspect the relevant parser code for your card:

```text
tally1800/decoded_export.py
tally1800/inventory_decode.py
tally1800/ledger_decode.py
tally1800/probe.py
tally1800/live_xml_diff.py
scripts/synthetic_tally_lab.py
```

## Safety Rules

- Never touch vault/encrypted companies.
- Never send synthetic writes to Avinash.
- Never send Avinash company XML to the synthetic port.
- Never parallelize Tally HTTP calls. Live Tally is a sequential microscope.
- Prefer offline work from snapshots and SQLite artifacts.
- Do not broad-fetch XML reports unless your card explicitly says to validate a
  small target. Use narrow Object exports or already recorded XML/Rosetta data.
- Do not claim `high` confidence just because a byte pattern looks plausible.
  High confidence needs internal corroboration or strict validation against an
  existing truth snapshot.
- If you edit code, keep the patch narrow and list every changed path.

Current live Tally map:

```text
10.211.55.3:9000 = Test Synthetic, company number 100000
10.211.55.3:9001 = Avinash Industries - Chennai Unit - 2025-26, company number 70525
```

Safe company check:

```bash
PYTHONPATH=. python3 - <<'PY'
from scripts.synthetic_tally_lab import list_companies
for port in (9000, 9001):
    print(port, list_companies("10.211.55.3", port, 8))
PY
```

## Current Proven Surface

Core identity:

- `Manager.1800` page header `+4` is master `MASTERID` for GUID-bearing masters.
- Master GUID can be reconstructed as `<company_guid>-<master_id:08x>`.
- `TranMgr.1800` compact `0x0bbb/0006` is voucher `MASTERID`.
- `TranMgr.1800` compact `0x0067/000d` is voucher date serial, base
  `1899-12-31`.
- Exportable voucher headers on `070525` match by master id/GUID/date after
  same-page key filter and base-type-31 exclusion.

Current direct decoded tables:

```text
voucher_header_candidates
manager_master_records
manager_master_kind_guesses
voucher_ledger_entries_1800
voucher_inventory_entries_1800
voucher_inventory_entry_candidates
voucher_decode_audit
xml_repair_queue
linkmgr_allocation_pages
voucher_statutory_status
voucher_invoice_metadata
```

Important current rule: use `voucher_inventory_entries_1800` as trusted direct
surface. Treat `voucher_inventory_entry_candidates` as research/repair input
unless the source+confidence rule is promoted.

## Known Synthetic Proofs

Synthetic company `Test Synthetic` has controlled snapshots in
`out/synthetic-live/`.

Already useful runs:

```text
PAY001/PAY002/PAY003: Payment ledger row evidence.
RCT001/RCT002: Receipt ledger rows.
JRN001/JRN002: Journal ledger rows.
CTR001/CTR002: Contra ledger rows.
SALES_XML001: Sales inventory row, MASTERID 10.
PURCHASE_XML001: Purchase inventory row, MASTERID 11.
```

Current synthetic inventory truth:

```text
SALES_XML001:
  voucher 10, Sales
  item 211 CODX_SYNT_ITEM_MANUAL1
  unit 210 PCS
  sales ledger 212 CODX_SYNTH_LEDGER_SALXML1A
  party ledger 213 CODX_SYNTH_LEDGER_PARTYXML1A
  qty 3.00, rate 44.44, amount +133.32

PURCHASE_XML001:
  voucher 11, Purchase
  item 211 CODX_SYNT_ITEM_MANUAL1
  unit 210 PCS
  purchase ledger 214 CODX_SYNTH_LEDGER_PURXML1A
  supplier ledger 215 CODX_SYNTH_LEDGER_SUPXML1A
  qty 4.00, rate 55.55, amount -222.20
```

Useful decode commands:

```bash
PYTHONPATH=. python3 -m tally1800 decode-sqlite \
  out/synthetic-live/SALES_XML001/after \
  out/synthetic-live/SALES_XML001/after_decoded.sqlite3

PYTHONPATH=. python3 -m tally1800 decode-sqlite \
  out/synthetic-live/PURCHASE_XML001/after \
  out/synthetic-live/PURCHASE_XML001/after_decoded.sqlite3
```

## Handoff Format

Write one handoff file per card or per proven/rejected hypothesis:

```text
out/cracker-handoffs/<card-id>-<short-name>-YYYY-MM-DD.md
```

Use this exact structure:

```markdown
# <Card ID> - <Short Name>

## Claim
Promote / reject / needs more data.

## Scope
Company folders, files, voucher types, and synthetic runs inspected.

## Files Inspected
List exact paths.

## Commands
Commands that reproduce the evidence.

## Byte Evidence
Offsets, page numbers, hex patterns, decoded values, and why the pattern is
not a false positive.

## Validation
Counts against Rosetta/XML/synthetic truth where available. Include numerator,
denominator, extras, and remaining misses.

## Parser Rule
Precise rule in plain English and pseudo-code. Include source/confidence label.

## False-Positive Risk
Known collision shapes and required guards.

## Remaining Misses
Concrete examples by voucher id/master id/item/ledger/amount when possible.

## What Productization Should Port
Exact files/functions to change, or say "no code port; documentation only".
```

If you produce a code patch, also include:

```text
Changed files:
- path
- path

Verification:
- command
- command
```

## Recommended Parallelism

Run 6 crackers first, not 10. Only one should touch live synthetic writes.
The other five can run offline against snapshots and SQLite artifacts. After
handoffs land cleanly, launch the next batch.

Do not stop after the first small finding. If assigned a lane below, work the
whole lane in order until you have either:

- completed every task in the lane,
- produced at least three strict handoffs plus one aggregate lane summary, or
- hit a hard blocker that prevents further progress without human action.

Each lane should end with:

```text
out/cracker-handoffs/<lane-id>-summary-YYYY-MM-DD.md
```

The lane summary must list every promoted rule, rejected rule, code patch, open
problem, and recommended next lane.

## Six Cracker Lane Assignments

Launch one fresh agent per lane. Paste this full prompt plus the lane id.

### Lane A - High-Volume Inventory Rows

Own cards: `C02`, `C03`, `C04`, and read `C01` for context.

Goal: improve trusted direct inventory coverage on the largest row families
without importing noisy candidates.

Work queue:

1. Establish the current baseline from `out/070525_decoded_sales_stock_rate_f6.sqlite3`
   or the latest decoded DB available. Record counts for:
   `61 Sales`, `Conversion Stock Journal`, `Stock Journal`,
   `31-Puchase(Monthly)`, trusted table, candidate table, extras.
2. Attack the four known `61 Sales` misses. Specifically inspect page-boundary
   marker splits, split `00 50 f6 01` headers, and ledger allocation distance.
3. Attack residual Stock Journal / Conversion Stock Journal rows. Test one
   guarded variant at a time: `0x0c`/`0x08` flags, `f7/f8/f9/fa/06 02`
   families, counterpart association, and repeated-stock ordinal pairing.
4. Attack Purchase Monthly candidate promotion. Find a gate that moves rows
   from medium candidates to trusted rows while keeping extras flat or lower.
5. Produce a source matrix:
   source name, voucher type, exact matches, extras, false-positive guard,
   promote/reject decision.
6. If a rule is proven, make a narrow patch in `tally1800/inventory_decode.py`
   and/or `tally1800/decoded_export.py`; otherwise leave a strict rejection
   handoff with byte evidence.

Expected outputs:

- At least three handoffs: one Sales, one Journal, one Purchase Monthly.
- Optional patch if a rule is proven.
- Lane summary with before/after counts.

### Lane B - Accounting Ledger Rows and Allocations

Own cards: `C05`, `C06`, and the ledger/allocation part of `C09`.

Goal: make accounting ledger entries and LinkMgr allocations production-grade
with source-labelled confidence, not Rosetta-only confidence.

Work queue:

1. Re-read:
   `out/synthetic-payment-corroboration-handoff-2026-04-27.md`,
   `out/0002-ledger-subrecord-validation-handoff-2026-04-27.md`,
   `out/linkmgr-backptr-bill-rules-handoff-2026-04-27.md`.
2. Validate synthetic Payment/Receipt/Journal/Contra rows from:
   `PAY003`, `RCT002`, `JRN002`, `CTR002`.
3. Build a source evidence table for `52d3_anchor`, `subrecord_0002`,
   `subrecord_0002_wide`, `subrecord_2713`, extended-form amounts, and
   raw-only `f6_01_44`.
4. Prove which source combinations are trusted. Explicitly document why
   standalone `f6_01_44` stays audit-only.
5. Validate `LinkMgr.1800` dual back-pointers:
   `(0x232a,0x000d)=voucher_master_id`,
   `(0x232d,0x0003)=ledger_master_id`.
   Confirm that fid-only matching is unsafe.
6. Map bill type, bill amount, bank fields, UTR, account, branch, print status
   into proposed typed tables.
7. Write SQL policies for which ledger/allocation rows are final, review, or
   XML-repair candidates.

Expected outputs:

- Ledger source-corrob handoff.
- LinkMgr allocation handoff.
- Proposed SQLite schema or patch for allocation tables.
- Lane summary with precision/coverage numbers.

### Lane C - Masters, Statutory, Invoice, E-Invoice

Own cards: `C07`, `C08`.

Goal: complete typed master surfaces and statutory/e-invoice tables without
confusing system registry pages with normal voucher rows.

Work queue:

1. Re-read:
   `out/manager-01f7-kind-classifier-handoff-2026-04-27.md`,
   `out/statstatus-fixed-slot-handoff-2026-04-27.md`,
   `out/einvoice-view1-block-parser-handoff-2026-04-27.md`.
2. Validate the `0x01f7` master kind classifier across every decoded company DB
   in `out/` that has `manager_master_records`.
3. Design and, if safe, patch typed exports for:
   companies, groups, ledgers, stock_items, units, godowns, cost_centres,
   voucher_types.
4. Document fields that are render-time constructed and cannot be direct-source
   100%, especially state/country/defaults and display names.
5. Validate `StatStatus.1800` fixed slot mapping and produce final
   `voucher_statutory_status` policy.
6. Validate `view=1` invoice metadata fields and `view>=3` e-invoice/e-way-bill
   archive parsing. Dedupe IRNs and identify opaque payload fields.
7. Produce table-level schema decisions: direct final table vs archive table vs
   raw/audit table.

Expected outputs:

- Master typed table handoff or patch.
- StatStatus handoff.
- Invoice/e-invoice handoff.
- Lane summary with cross-company coverage.

### Lane D - Synthetic Live Writer and Diff Microscope

Own card: `C10`. This is the only lane allowed to write to live Tally.

Goal: create controlled one-mutation snapshots that unlock missing parser
families. Work sequentially. Do not batch unknowns.

Use only:

```text
10.211.55.3:9000 Test Synthetic
live-tally-data/100000
scripts/synthetic_tally_lab.py
```

Work queue:

1. Verify port/company with the safe company-list command.
2. Read `LIVE-TALLY-PORTS.md` and `SYNTHETIC-MAPPING-PLAYBOOK.md`.
3. First run a verify-only/no-write health check if the harness supports it.
4. Create one stock journal with one consumed item and one produced item.
5. Create one Sales voucher with tax ledger split, using unique amounts.
6. Create one Purchase voucher with tax ledger split, using unique amounts.
7. Create one bill allocation case.
8. Create one bank allocation case with UTR/bank/account/branch fields.
9. Create one cost-centre allocation case.
10. Create one batch/godown inventory case only if the XML builder and object
    preflight are understood. Otherwise write the blocked handoff.

For every mutation:

- snapshot before and after,
- save XML request/response,
- decode after snapshot,
- run targeted synthetic diff for changed files,
- write one handoff with byte evidence,
- stop immediately on duplicate/error and document the exact response.

Expected outputs:

- Multiple synthetic run folders under `out/synthetic-live/`.
- At least three mapping handoffs unless blocked by Tally import errors.
- Lane summary listing which mutations are safe to repeat.

### Lane E - Cross-Company Regression and Validation Harness

Own validation across all cards.

Goal: stop us from overfitting to one company. Build a repeatable offline
validation report across decoded company artifacts and available raw folders.

Work queue:

1. Inventory all decoded DBs in `out/*.sqlite3` and identify which have:
   master tables, voucher headers, ledger entries, inventory entries,
   LinkMgr/stat/e-invoice tables.
2. Inventory raw company folders reachable from `source_files` tables or local
   paths. Do not assume all paths still exist; document missing inputs.
3. Write or propose one command that regenerates decoded SQLite for a company
   folder without mutating source data.
4. Build a metrics script/report that outputs:
   master counts by kind, voucher counts by type, ledger row totals,
   inventory row counts by source/confidence, audit/repair queue counts,
   candidate/trusted ratios, suspicious extras.
5. Run the report on every usable decoded DB. Compare shapes across companies.
6. Identify rules that are `070525`-specific versus cross-company stable.
7. Produce a regression checklist productization must run before merging future
   parser rules.

Expected outputs:

- Validation harness script or detailed command set.
- Cross-company metrics handoff.
- Lane summary naming the riskiest overfit rules.

### Lane F - Production SQLite Contract and Hybrid Repair Planner

Own card: `C09` plus final product contract.

Goal: define how production uses direct `.1800` rows today without lying about
100%, and how XML fills only the gaps.

Work queue:

1. Read `STATUS-2026-04-27.md`, `OPEN-PROBLEMS.md`,
   `tally1800/decoded_export.py`, and `tally1800/live_xml_diff.py`.
2. Document every output table currently emitted by `decode-sqlite`, including
   final/trusted, candidate, audit, and raw/archive role.
3. Define confidence semantics:
   source-local high, corpus-calibrated high, medium review, raw/audit only.
4. Write deterministic SQL queries for:
   trusted final export,
   no-row hard misses,
   medium-candidate review,
   suspected extra/duplicate rows,
   minimal XML repair queue by voucher id/type/date.
5. Test those queries on latest `070525` decoded DB and synthetic decoded DBs.
6. Propose a production export layout:
   final tables, `_candidates`, `_audit`, `_raw`, `_xml_repair`.
7. Identify exactly what the app can know without Rosetta on a brand-new
   company, and what it cannot know until XML confirms.

Expected outputs:

- Production contract handoff.
- SQL query file or markdown query appendix.
- Lane summary with current hard limits.

## Optional Second Batch Lanes

Use these after the first six return useful handoffs.

### Lane G - Voucher Type Pairing and Cancellation

Own VchStatus alignment, voucher type pairing, `is_cancelled`, `is_optional`,
date-cluster reordering, and duplicate date/type ambiguity.

### Lane H - RAM Scraping Microscope

Only a bounded experiment. Compare Windows memory neighborhoods around
synthetic sentinels to on-disk `.1800` neighborhoods. Stop if it does not expose
structured decoded records quickly.

## Card C01 - Purchase Synthetic Row Validation

Goal: validate the new built-in `Purchase` inventory rule from
`PURCHASE_XML001`, then replay it on Avinash if comparable voucher types exist.

Start points:

```text
out/synthetic-live/PURCHASE_XML001/after/TranMgr.1800
out/synthetic-live/PURCHASE_XML001/after_decoded.sqlite3
tally1800/inventory_decode.py
```

Known expected row:

```text
voucher 11, Purchase, item 211, ledger 214, unit 210,
qty 4.00, rate 55.55, amount -222.20
```

Hypothesis:

```text
Built-in Purchase stores stock+rate first, then a following f6 subrecord stores
negative amount and quantity. Ledger is the closest preceding signed allocation
for the same amount.
```

Pattern to prove:

```text
02 00 00 03 <stock_id>
02 00 00 0c <rate_i64_x100000>
... <unit_id near rate scale marker>
00 50 f6 01 00 10 <len>
  02 00 00 09 <negative_amount_i64_x100000>
  02 00 00 0b <qty_i64_x100000>
preceding signed allocation:
  02 00 00 03 <purchase_ledger_id> ... 02 00 00 09 <same negative amount>
```

Output needed: prove/reject `purchase_stock_rate_f6` as high confidence. Check
whether it creates extras on `070525` if the rule is applied to built-in
`Purchase` or related purchase voucher types.

## Card C02 - Sales Remaining Four Misses

Goal: reduce the known `61 Sales` misses after compact+matrix union.

Known misses:

```text
30652 Glass Stove 2 Burner Sample
34667 L3994B00000 BUTTERFLY MAGNUM 3B LPG STOVE
55395 PRPSCOMN01077-Batti Stand Coated Luxe ( 3 Wing)
60124 BTRDTCOMN000876- Butterfly Magnum Drip Tray Jumbo
```

Hypotheses to test:

- Compact field marker itself split across a page boundary.
- `00 50 f6 01 00 10` header split or polluted by next-page header bytes.
- Ledger allocation is present but not closest by naive distance.

Output needed: one or more strict page-split scanner fixes, with before/after
exact counts and extra counts.

## Card C03 - Stock Journal / Conversion Journal Residuals

Goal: recover high-confidence rows from the largest inventory families without
promoting noisy columnar candidates.

Start points:

```text
scripts/probe_journal_inventory_rows.py
tally1800/inventory_decode.py
out/070525_decoded_inventory.sqlite3
```

Known families:

```text
post-rate f6:
  stock + rate -> 00 50 f6 01 00 10 <len> -> amount + qty

strict columnar:
  <flags> f6 01 44 00 <stock_id> = amount side
  <flags> f6 01 46 00 <stock_id> = quantity side
```

Hypotheses:

- Remaining misses are mostly positive/negative counterpart association.
- Some valid values use `0x0c`/`0x08` flags or `f7/f8/f9/fa/06 02` variants,
  but need ordinal/body-hash guards.
- Repeated stock items inside one voucher need local row-boundary pairing.

Output needed: promote one guarded family or explicitly reject a noisy family.

## Card C04 - Purchase Monthly False-Positive Reduction

Goal: move coverage from broad `voucher_inventory_entry_candidates` into trusted
`voucher_inventory_entries_1800` without thousands of extras.

Known numbers on `070525`:

```text
trusted 31-Puchase(Monthly): 7536 / 7682 full tuples, 136 extra unique
candidate table:             7642 / 7682 full tuples, 5193 extra unique
```

Hypotheses:

- Some medium `purchase_voucher_unique_ledger` rows are promotable only when
  voucher has one stock item or one ledger and one exact amount group.
- Repeated item vouchers need row-local ledger evidence, not voucher-level
  fallback.
- Sample/unit rows require local unit id or stock-repeat evidence.

Output needed: a confidence gate that improves trusted coverage while keeping
extras flat or lower.

## Card C05 - Accounting Ledger Rows Across Voucher Types

Goal: harden `voucher_ledger_entries_1800` for Payment/Receipt/Journal/Contra
using source corroboration, not Rosetta.

Start points:

```text
out/synthetic-live/PAY003/
out/synthetic-live/RCT002/
out/synthetic-live/JRN002/
out/synthetic-live/CTR002/
tally1800/ledger_decode.py
out/0002-ledger-subrecord-validation-handoff-2026-04-27.md
out/synthetic-payment-corroboration-handoff-2026-04-27.md
```

Known guard:

```text
Standalone f6_01_44 is unsafe. It produced a false PAY003 cumulative row:
voucher=2 ledger=31 amount=-1111.10.
```

Output needed: exact source rules for `52d3_anchor`, `subrecord_0002`,
`subrecord_0002_wide`, `subrecord_2713`, and extended-form amounts. Include
precision/recall against synthetic and `070525` where available.

## Card C06 - LinkMgr Allocations

Goal: promote typed bill/bank/reference allocation tables from `LinkMgr.1800`.

Start points:

```text
out/linkmgr-backptr-bill-rules-handoff-2026-04-27.md
tally1800/decoded_export.py
LinkMgr.1800 snapshots in company folders
```

Known claims to validate:

```text
0x232a/0x0d = voucher_master_id
0x232d/0x03 = ledger_master_id
filter by (fid,type), not fid alone
fid 0x0005 type 00 0d inside LinkMgr slot block => Agst Ref, absent => New Ref
```

Output needed: typed table design and validation numbers for bills, bank
fields, voucher/ledger back-pointers, and known ceiling.

## Card C07 - StatStatus and E-Invoice Archive

Goal: validate and productize statutory/e-invoice tables without confusing
system registry pages with voucher data.

Start points:

```text
out/statstatus-fixed-slot-handoff-2026-04-27.md
out/einvoice-view1-block-parser-handoff-2026-04-27.md
tally1800/decoded_export.py
```

Known claims:

```text
StatStatus: 3 slots per page.
word[10]=date
word[25]=GST registration master id
word[26]=voucher type master id
word[30]=return-line seq
word[31]=validity

TranMgr view=1 carries invoice metadata.
view>=3 carries e-invoice/e-way-bill payloads, not normal voucher rows.
```

Output needed: validation and final schema for `voucher_statutory_status`,
`voucher_invoice_metadata`, and `voucher_einvoice_archive`.

## Card C08 - Master Kind and Typed Master Tables

Goal: finish cross-company master classification and typed master exports.

Start points:

```text
out/manager-01f7-kind-classifier-handoff-2026-04-27.md
tally1800/decoded_export.py
tally1800/probe.py
```

Known rule:

```text
0x01f7 sub-record body has TLV fid=0x0002 type=0x0006 followed by u32 kind:
2=groups, 3=ledgers, 5=cost_centres, 6=godowns,
9=stock_items, 10=voucher_types, 12=units
```

Output needed: typed table schema and coverage for groups, ledgers, stock
items, units, godowns, cost centres, voucher types. Document render-time-only
fields that cannot be sourced directly.

## Card C09 - Confidence and XML Repair Without Rosetta

Goal: make production hybrid mode honest. Given only `.1800`, identify what is
trusted, what is missing, and what needs XML repair.

Start points:

```text
voucher_decode_audit
xml_repair_queue
tally1800/decoded_export.py
tally1800/live_xml_diff.py
```

Questions:

- Can hard misses be identified without truth data?
- Which confidence labels are source-local versus corpus-calibrated?
- Can we generate a minimal XML fetch plan by voucher id/type/date?
- Which medium candidates should be included only after XML confirms them?

Output needed: deterministic production policy and SQL queries, not just prose.

## Card C10 - Synthetic Live Writer Lane

Goal: create new controlled mutations that unlock one unknown family at a time.
Only one cracker should run this card at a time.

Use only:

```text
10.211.55.3:9000 Test Synthetic
live-tally-data/100000
scripts/synthetic_tally_lab.py
```

High-value mutations:

- one stock journal with one consumed and one produced item
- one purchase with tax ledger split
- one sales with CGST/SGST/IGST ledgers
- one bill allocation
- one bank allocation with UTR/bank fields
- one cost-centre allocation
- one batch/godown inventory line
- one e-way/e-invoice-like metadata case if Tally accepts XML safely

Rules:

- Snapshot before and after.
- Use unique sentinel names and unique amounts.
- Save XML request/response artifacts.
- Decode after snapshot.
- Diff only the targeted files first.
- Stop immediately on duplicate/error responses and document the response.

Output needed: one proven mapping per run. Do not bundle multiple unknowns into
one mutation.

## Final Response To Productization Owner

When done, answer with:

```text
Card:
Decision:
Handoff:
Changed files:
Validation:
Next card recommendation:
```

Keep the response short. The handoff file carries the details.

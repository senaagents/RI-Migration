# Open Problems

The project is not done until these are solved and reconciled.

## P0 - Typed Master Tables

- Direct typed tables now exist for `companies`, `groups`, `ledgers`,
  `stock_items`, `units`, `godowns`, `cost_centres`, and `voucher_types`.
- Classification uses the Manager `0x01f7` kind-code subrecord, not only
  name-shape guesses.
- `voucher_types.export_policy` is required because `kind=10` includes
  internal/status candidates as well as exportable voucher types.
- Still open: parent hierarchy, aliases, alternate display names, rendered
  default fields, unit conversion semantics, stock group/category links, and
  cross-company truth reconciliation beyond structural count validation.

## P0 - Typed Voucher Headers

- Decode voucher page identity from `TranMgr.1800`. Current best bridge is
  compact `0x0bbb` type `0006`, which matches all 30,545 Rosetta voucher
  `MASTERID`s in `070525`.
- Use same-page `0x0003` key and filter internal base type `31` to produce
  30,545 exportable voucher headers for `070525`; validate this rule across
  other non-vaulted company folders before generalizing.
- Find where voucher `MASTERID`, GUID suffix, date, voucher type, voucher number,
  party, GSTIN, and narration live.
- Expand the partial `0x0bbe` composite-key bridge into full voucher coverage or
  identify the alternate identity field for vouchers missing `0x0bbe`.
- Classify no-key `0x0bbb/0006` compact IDs and prove base type `31`
  (`Stat Only Outwards`) should be excluded from the voucher table.
- Build page graph collection from header links before attempting record end
  boundaries; contiguous TLV walking is insufficient.
- Reconcile voucher counts by type and date against live profile.
- Reconcile voucher GUID/master-id sets against XML SQLite.

## P0 - Voucher Lines

- Use the new line-reference breakthrough: accounting ledgers and stock items
  are often stored as `Manager.1800` `MASTERID` integers inside the voucher page
  group, not as repeated names.
- Turn integer references into typed accounting ledger lines.
- Turn integer references into typed inventory lines, including stock journal
  in/out shapes.
- Improve ledger-row coverage beyond current anchor families. Rich teammate
  matcher reached `35,091 / 40,781`; conservative script currently reaches
  `29,764 / 40,781`.
- Convert inventory identity/value anchors into exact row multiplicity. Current
  conservative script sees item identity for `115,505 / 115,505` rows and some
  quantity/rate/amount value for `114,411 / 115,505`, but it does not produce
  typed rows yet.
- Pair signed fixed-point amounts (`i64 / 100000`) with the correct line refs.
- Promote quantity (`0x0001/0008` candidates) and rate (`0x0002/000c`
  candidates) into validated cross-voucher rules.
- Decode signs and exact row boundaries around those integer references.
- Use sub-record headers (`00 50 <subrec_id> 00 10 <length>`) to pair refs,
  amounts, GST, batch, and inventory child rows.
- Promote partial `LinkMgr.1800` allocation bridges into typed bill/bank/order
  allocation tables. Current matches: bill `10,265 / 12,304`, bank
  `2,667 / 3,601` unique Rosetta allocation entries.
- Preserve unknown/raw record sections for audit fallback.
- Reconcile ledger/inventory line counts and totals against XML SQLite.

## P0 - Sales Inventory Row Family

- `scripts/probe_sales_inventory_runs.py` proves a strict `61 Sales` compact-run
  family can decode full row tuples:
  `stock item + sales ledger + quantity + rate + amount`.
- The productized `decode-sqlite` final table currently reaches
  `2,051 / 2,068` Rosetta `61 Sales` full tuples with `83` extras in
  `out/070525_decoded_laneA_sales_logical.sqlite3`.
- Lane A reported a narrower probe at `2,058 / 2,068`, but the main-thread
  reproducible SQLite tuple check is `2,051 / 2,068`. Do not quote the higher
  number as productized status until reproduced.
- The row starts with:
  `0x0002/0003 stock_item`, `0x0069/0003 ledger`,
  `0x006d/0003 stock_item repeat`, `0x0072/0003 unit`,
  `0x006e/0009 amount`.
- Some rows omit inline `0x0069/0003`; for those, ledger can be resolved only
  from a unique field-coded ledger/amount pair in the same voucher tree:
  `0x0002/0003 <ledger> ... 0x0002/0009 <amount>`.
- Another row family stores stock + rate first, then a following
  `00 50 f6 01 00 10 <len>` block stores amount and quantity.
- Quantity candidate: `0x0066/000b` signed scaled quantity, usually mirrored by
  `0x0067/000b`, with unit id after the scaled i64.
- Rate candidate: next `0x0002/000c` after the quantity block.
- Some rows have extra bytes between quantity and rate; fixed offsets are not
  enough. Parser must scan within the compact run and stop at the correct row
  boundary.
- Some rows cross 512-byte pages. `sales_logical_compact` now reads the strict
  compact row shape through page headers and drops the hard repair count
  from `65` to `64`.
- Remaining productized gap: `17` Sales full tuples are still missing in the
  current final table, and `sales_logical_compact` has `20` source-local rows
  absent from Rosetta. The broader `sales_logical_post_rate_f6` rule is
  rejected because it adds known false rows.

## P0 - Inventory Non-Sales Row Families

- Conversion Stock Journal and Stock Journal dominate inventory volume and do
  not use only the sales compact-run shape.
- `scripts/probe_journal_inventory_rows.py` now produces typed row candidates
  for these families and validates exact full tuples against Rosetta:
  - `Conversion Stock Journal`: `71,257 / 71,518` exact.
  - `Stock Journal`: `19,577 / 19,831` exact.
- Proven row families:
  - `stock + rate` header, then
    `00 50 f6 01 00 10 <len>` with `0x0002/0009` amount and
    `0x0002/000b` quantity.
  - strict columnar pair:
    `<flags> f6 01 44 00 <stock_id>` and
    `<flags> f6 01 46 00 <stock_id>`.
  - For `0x04` columnar rows, pair amount `+24` with quantity `+32`, and
    amount `+32` with quantity `+24`; derive rate from amount/quantity.
- `repair_split_i64()` now handles the marker-edge split where the 4-byte
  compact field marker ends at page byte `511` and the actual i64 resumes at
  offset `0x1c` on the next page. This fixed the largest Stock Journal miss
  `42385 WIP Blank Stage Hilux 3b +210000.00`.
- Current open variants for these voucher types:
  - remaining positive-side rows where the matching negative side is decoded
    but the counterpart association is not.
  - `0x0c` / `0x08` flag rows that carry valid values but are unsafe to pair
    with the strict `0x04` ordinal rule.
  - broader columnar forms such as `f7 01`, `f8 01`, `f9 01`, `fa 01`,
    `06 02`, and kind `64`; these are visible in large misses but have higher
    false-match risk than the page-boundary fixes.
  - zero-quantity/zero-rate/zero-amount inventory rows.
  - repeated stock items inside one voucher where ordinal pairing is still
    ambiguous.
- Conversion journals show paired negative and positive rows for the same item;
  the parser must preserve both, not collapse by `(voucher, stock_id)`.

## P0 - Purchase Monthly Hybrid Parser

`31-Puchase(Monthly)` is the next biggest inventory family after the journal
families. Current probes:

```text
purchase hybrid parser exact with ledger:      7645 / 7682
journal parser union exact item/qty/rate/amt:  7667 / 7682
```

The current script is `scripts/probe_purchase_inventory_rows.py`. It combines
Sales compact rows, purchase stock/repeat rows, inline ledger rows, and a
voucher-unique ledger fallback. Remaining misses are mostly repeated sample/unit
rows and a few high-rate/low-qty other-item purchases.

`decode-sqlite` now integrates the high-confidence parts into
`voucher_inventory_entries_1800` and keeps the broader fallback in
`voucher_inventory_entry_candidates`.

Current `070525` validation:

```text
high-confidence table:      7583 / 7682 full tuples, 136 extra unique
broader candidate table:    7642 / 7682 full tuples, 5193 extra unique
```

Latest promoted subfamily:

```text
purchase_stock_rate_f6:
  synthetic PURCHASE_XML001 exact row: voucher 11, item 211, ledger 214,
    unit 210, qty 4.0, rate 55.55, amount -222.2
  070525 replay: 75 / 75 exact full tuples under 31-Puchase(Monthly), 0 extras
  hard XML repair: 84 -> 65 total, 36 -> 17 for 31-Puchase(Monthly)
```

Open problem: recover the extra candidate coverage without importing thousands
of false rows into the trusted table.

Hybrid production note: `voucher_decode_audit` can identify hard gaps without
Rosetta. On the current `070525` artifact, hard XML repair is `64` vouchers,
but ambiguous review is `15,365` vouchers because medium/extra candidates
remain common.

## P0 - Controlled Synthetic Corpus

The synthetic-diff harness now has actual controlled before/after Tally
snapshots. It is proven useful for discovering specific byte mappings, but it
is not a general decoder by itself.

Live lab status:

- Disposable company exists: `Test Synthetic`.
- Tally company number: `100000`.
- XML health is good through `10.211.55.3:9000`.
- Live `.1800` folder is readable from macOS:
  `live-tally-data/100000`.
- `scripts/synthetic_tally_lab.py` can verify the company, snapshot before,
  import one safe mutation, snapshot after, and run `synthetic-diff`.
- The runner intentionally refuses to import without `--company-dir` because the
  whole point is a controlled `.1800` before/after diff.
- First unsafe broad import hit a duplicate Unit error (`CSUH001`). Avoid custom
  unit creation until isolated; continue with ledger-only and accounting Payment
  voucher modes.
- See `SYNTHETIC-MAPPING-PLAYBOOK.md` for exact commands and proof artifacts.

Already proven:

- Ledger creation maps `Manager.1800` page header `+4` to `MASTERID` and field
  `0x0002/000f` to primary ledger name.
- Payment voucher creation maps `TranMgr.1800` `0x0bbb/0006` to voucher
  `MASTERID`, `0x0067/000d` to date serial, `0x00cd/000f` to narration, and
  `0x07d5/000f` to voucher number for the tested Payment vouchers.

Continue with tiny mutations, one thing at a time:

- one ledger with sentinel name and GSTIN
- one more Payment voucher with a third amount and different debit ledger
- one Receipt/Journal/Contra accounting voucher with unique ledgers/amounts
- one stock item with sentinel item name and an existing/built-in unit only
- one sales voucher with unique quantity/rate/amount/date after unit handling is
  safe
- one purchase voucher with unique ledger/item/amounts after sales succeeds
- one stock journal with one negative and one positive row after stock item/unit
  creation is understood
- one bill allocation and one bank allocation

Then run:

```bash
PYTHONPATH=. python3 -m tally1800 synthetic-diff before after \
  --file TranMgr.1800 \
  --sentinel SYNTH_ITEM_001 \
  --number 13.37 \
  --number 19.23 \
  --date 2026-04-01 \
  --out out/synth-case-003.json
```

## P1 - RAM Scraping Microscope

Worth a bounded experiment, not a product path.

- Need a Windows-side `TallyPrime.exe` dump or a Parallels VM memory dump after
  loading a tiny synthetic company.
- Scan memory for the same synthetic sentinels used by `synthetic-diff`.
- Compare memory neighborhoods to on-disk `.1800` neighborhoods.
- Stop if dumps do not expose structured decoded records quickly; do not let
  this replace the direct file decoder.

## P1 - Cross-Company Regression Gate

`scripts/cross_company_validation_report.py` is now the read-only regression
gate for parser changes. It inventories decoded SQLite artifacts, source-file
reachability, row-surface counts, candidate/trusted ratios, typed master
surfaces, invoice/archive surfaces, and audit/repair queues.

Current current-schema real-company baselines:

```text
070525: repair 64, inventory final/candidates 100502 / 243876
100025: repair 1541, inventory final/candidates 70572 / 173300
100001: repair 40, inventory final/candidates 797 / 1774
300925: repair 22, inventory final/candidates 311 / 722
```

Open regression work:

- Regenerate `100001` and `300925` with the latest decoder, not just older
  `kind_audit` artifacts.
- Attach same-snapshot XML/Rosetta truth for `100001`, `100025`, and `300925`
  before interpreting row counts as precision/recall.
- Treat any rise in hard repair count or candidate/trusted blowup as a blocker
  unless a byte-evidenced rule explains it.

- Detect stock anchor `02 00 00 03 <stock_id>`.
- Variant A:
  `68 00 00 03 <same stock_id>` then `6d 00 00 03 <same stock_id>`.
  Resolve ledger from nearest preceding field-coded ledger/amount block with
  the same amount:
  `02 00 00 03 <ledger_id> ... 02 00 00 09 <amount>`.
- Variant B:
  `69 00 00 03 <ledger_id>` then `6e 00 00 03 <ledger_id>`.
  Ledger is inline.
- Rate comes from nearby `02 00 00 0c <rate_i64_x100000>`.
- Amount/quantity come from following F6:
  `00 50 f6 01 00 10 <len> ... 02 00 00 09 <amount> ... 02 00 00 0b <qty>`.
- F6 parsing cannot trust apparent `subrec_len` when the F6 header starts near
  the end of a page. Example from MID `28738`: F6 starts 8 bytes before page
  end; the next page header/noise appears before the amount marker.

Example direct ledger variant from MID `28739`:

```text
02 00 00 03 78 1a 00 00        stock id
69 00 00 03 fe 0e 00 00        ledger id
6e 00 00 03 fe 0e 00 00        ledger repeat
02 00 00 0c e0 4d e0 00 ...    rate 147.00
02 00 00 09 80 33 ef 5e ...    amount -69972.00
02 00 00 0b 80 51 d6 02 ...    qty 476.00
```

Example stock-repeat variant from MID `28738`:

```text
02 00 00 03 7e 1b 00 00        stock id
68 00 00 03 7e 1b 00 00        stock repeat
6d 00 00 03 7e 1b 00 00        stock repeat
02 00 00 0c 30 57 05 00 ...    rate 3.50
00 50 f6 01 00 10 98 00 ...    F6 line body
02 00 00 09 80 45 6a c1 ...    amount -10500.00
02 00 00 0b 00 a3 e1 11 ...    qty 3000.00
```

## P0 - Accounting Ledger Row Families

- Direct subrecord `0x0006`, summary `0x52d3/0009`, and `0x0002` ledger-row
  subrecords now emit into `voucher_ledger_entries_1800`.
- `070525` truth diff: gated high-confidence table is `93.51%` recall /
  `100.00%` precision.
- Need close the remaining `2,645` missing truth rows without admitting the
  rejected noisy raw families.
- Need port the full `0x0006` body-hash sum-fallback before claiming the Claude
  `12,920/12,921` mid-volume coverage level in Codex.
- Need identify additional families for:
  - GST ledger rows where duplicate SGST/CGST values make role pairing
    ambiguous.
  - party ledger rows that appear in bill/bank allocation contexts.
  - inventory-ledger totals that appear only in summary/calc pages.
  - round-off and small-value rows that collide with incidental byte patterns.
- Need a canonical rule for de-duplicating repeated copies of the same
  `(voucher, ledger, amount)` without deleting legitimate repeated rows.

## P0 - Synthetic Inventory Writes

- Core accounting writes are unlocked, and one Sales plus one Purchase
  inventory voucher write are now proven.
- `FULL001` custom unit/item/sales bundle timed out during master import and
  produced no after snapshot. Avoid bundled custom-unit experiments until Tally
  is reset/cleared and a smaller unit-only import succeeds.
- `INVXML01_UNIT` then proved a smaller unit-only XML also times out and wedges
  Tally. Local diff showed no unit sentinel landed; only `TSTATE.TSF` page 0
  changed. Treat custom Unit XML as unsafe, not merely the bundled import.
- `SALES_XML001` created one Sales voucher by XML using existing `PCS` and
  `CODX_SYNT_ITEM_MANUAL1`, plus two XML-created ledgers. Direct decode now
  emits the row as high-confidence `sales_stock_rate_f6`.
- `PURCHASE_XML001` created one Purchase voucher by XML using the same item/unit
  and two XML-created ledgers. Direct decode now emits the row as
  high-confidence `purchase_stock_rate_f6`.
- Next safer path: continue avoiding Unit XML. Use preflighted existing/proven
  units and stock items, create only missing safe ledgers by XML, then write
  one voucher per request.
- Open next experiments: one Stock Journal with source and destination rows,
  one tax-split Sales/Purchase voucher, one bill allocation, one bank
  allocation, and one Sales voucher with repeated stock item lines.

## P0 - Voucher Status / Type

- Exported `vch_status_slots` now preserves date/type/status words from
  `VchStatus.1800`.
- Use `VchStatus` `word[25]` as voucher type `MASTERID` only after solving or
  proving slot-to-voucher ordering.
- Find an independent order/link field. The current near-solution based on
  `MASTERID` order plus two legacy low ids is not proof.
- Decode cancellation/optional flags. `word[1]` correlates with cancelled and
  optional rows but is not clean.

## P0 - Allocation Reconstruction

- `linkmgr_allocation_pages` now exposes typed voucher/ledger back-pointers and
  a 95%-class `Agst Ref` / `New Ref` heuristic, but this is not yet full bill,
  bank, order, or advance allocation reconstruction.
- Need attach LinkMgr allocation pages back to `voucher_ledger_entries_1800`
  rows without relying on ambiguous bill names.
- Need separate `Advance` bill allocations; current LinkMgr features do not
  distinguish them cleanly.

## P0 - Statutory / E-Invoice Coverage

- `voucher_statutory_status`, `voucher_invoice_metadata`, and
  `voucher_einvoice_archive` are now decoded sibling tables.
- Need map statutory slots to unique voucher ids when multiple vouchers share
  the same `(date, voucher_type)` pair.
- View `>=3` e-invoice archive rows are promoted only under the strict
  IRN/ACK/JWT gate. Need decode e-way-only records and transporter/date
  semantics without broad string scans.

## P1 - Page/Record Structure

- Identify checksum/marker at page offset `+0`.
- Formalize 512-byte page header fields.
- Identify record boundaries and cross-page chaining.
- Distinguish live, deleted, index, and payload pages.

## P1 - Snapshot Drift Control

- Use same-date `.1800` snapshot and XML export when proving 100%.
- Current `.1800` snapshot is `2026-04-21`; XML SQLite/live were later.
- Any mismatch against later XML/live may be data drift.
- Live Tally HTTP routing is in `LIVE-TALLY-PORTS.md`: currently synthetic is
  `10.211.55.3:9000`, Avinash is `10.211.55.3:9001`; localhost `9000` is not
  listening. Use live XML only as targeted microscope/repair, not as proof that
  `.1800` decoding is complete.

## P1 - Vault Layer

- Detect vaulted/encrypted `.1800`.
- Implement vault decryption only with supplied password.
- Do not claim support for vaulted files until decrypted output reconciles.
- Do not claim support for all companies without a vault password.

## P2 - Performance

- Avoid scanning every 2-byte offset for huge files.
- Prefer tag-signature scanning and page-aware parsing.
- Add indexes to decoded SQLite for reconciliation queries.

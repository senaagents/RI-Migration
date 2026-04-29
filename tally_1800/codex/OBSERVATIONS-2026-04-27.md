# Observations - 2026-04-27

Sample used read-only:

`office/_workspace-admin/data-dumps/avinash-tally-live-2026-04-21/100025`

## Vault Status

This sample does not look fully TallyVault-encrypted:

- `Company.1800`, `Manager.1800`, `TranMgr.1800`, `LinkMgr.1800`, and others
  contain many readable UTF-16LE strings.
- Entropy is low/mid rather than uniformly high:
  - `Company.1800`: about `2.925`
  - `Manager.1800`: about `4.014`
  - `TranMgr.1800`: about `3.623`
  - `LinkMgr.1800`: about `3.808`
- A vaulted/compressed sample would likely show entropy closer to `7.8+` and
  near-zero readable strings.

## Page Layout

Strong evidence for 512-byte pages:

- Many `.1800` files are exact multiples of 512 bytes.
- Page 0 is sparse and looks like file-level/root metadata.
- Page 1+ contains structured little-endian words followed by payload.

Examples:

`Company.1800`, page 1 at offset `512`:

```text
c510546c 01000000 01000000 00000000
0b000000 ffffffff 00000000 32000000
```

`Manager.1800`, page 1 at offset `512`:

```text
41fea1ce 01000000 01000000 00000000
0b000000 ffffffff 00000000 14000000
```

`Manager.1800`, pages 2..6 show offset `+4` matching page number:

```text
page 2: bdaf2344 02000000 ...
page 3: 9824adbc 03000000 ...
page 4: f4395492 04000000 ...
```

`TranMgr.1800` has mixed page families: sparse index-ish pages and denser data
pages.

## String Encoding Breakthrough

Business strings are frequently encoded as:

```text
u16 length_bytes
utf16le payload including null terminator
```

Example from `Manager.1800`:

```text
24 00 = 36 bytes
4c 00 2d 00 43 00 ... 74 00 00 00 = "L-Capital Account\0"
```

The naive string extractor showed `$L-Capital Account`; `$` was really the
length word `0x0024`.

Confirmed extracted strings:

- From `Company.1800`:
  - `Avinash Industries - Chennai Unit - 2023-24 - (Audited)`
  - `33AAPFA0733K1ZJ`
  - address/email/state/pincode fields
- From `Manager.1800`:
  - `L-Capital Account`
  - `L-Loans (Liability)`
  - `A-Fixed Assets`
  - `A-Current Assets`
  - many GUID-like IDs
- From `TranMgr.1800`:
  - party GSTINs
  - party names
  - addresses
  - vehicle numbers
  - narrations

## Tooling Added

```bash
python3 -m tally1800 inspect <company-dir>
python3 -m tally1800 pages <file> --limit 8
python3 -m tally1800 strings <company-dir> --utf16
python3 -m tally1800 context <file> --needle "Capital Account"
python3 -m tally1800 lpstrings <company-dir> --file Manager
python3 -m tally1800 sqlite <company-dir> out/probe.sqlite3
python3 -m tally1800 decode-sqlite <company-dir> out/decoded.sqlite3
```

## First Decoded SQLite Output

Generated:

`out/100025_decoded.sqlite3`

Tag-based extraction results:

- `field_strings`: `1,011,719` decoded tagged strings.
- `Manager.1800` strings: `58,008`.
- `TranMgr.1800` strings: `909,578`.
- `manager_records` with names: `16,109`.
- `voucher_text_pages` with party/GSTIN/text signal: `167,643`.

Confirmed direct extraction from `.1800` without Tally:

```text
company_name = Avinash Industries - Chennai Unit - 2023-24 - (Audited)
gstin        = 33AAPFA0733K1ZJ
pincode      = 603202
state        = Tamil Nadu
country      = India
```

Example `manager_records` names:

```text
L-Capital Account
L-Loans (Liability)
L-Current Liabilities
A-Fixed Assets
A-Current Assets
L4000-Branch / Divisions
L-Sundry Creditors
Stock-in-Hand
```

Example `voucher_text_pages` extraction:

```text
party/name = 50L2 Gold Star Coating Technologies
gstin      = 33AAVFG8457A1Z7
address    = 243 & 244 Sri Vinayaka Nagar Part-3 Sriperumbudur
```

## Full Avinash Dump Decode

Generated decoded SQLite outputs for all five company folders in
`avinash-tally-live-2026-04-21`.

See `DECODED-SQLITE-MANIFEST.md`.

## Identity Breakthrough: Manager Page `MASTERID`

After reconciliation against XML SQLite, `Manager.1800` page offset `+4` was
identified as the Tally master object id for many master records.

Examples:

```text
00A0 CUB -CCOD-20054930
  Manager page: 4655
  u32 at +4: 2431
  hex: 0000097f
  XML GUID: 8739d473-fcf3-43ab-8c58-9bf307c70b03-0000097f

S.S.Normal Scraps
  Manager page: 21577
  u32 at +4: 7868
  hex: 00001ebc
  XML GUID: 8739d473-fcf3-43ab-8c58-9bf307c70b03-00001ebc

E10.1 Accounts / Admin
  Manager page: 3791
  u32 at +4: 2189
  hex: 0000088d
  XML GUID: 8739d473-fcf3-43ab-8c58-9bf307c70b03-0000088d
```

This means `.1800` master extraction can move from string search to identity
reconstruction.

## Reconciliation Lessons

Live XML and the existing XML-derived SQLite matched 100% for the FY window
`2025-04-01..2026-03-31`, but this is **not** proof that `.1800` decoding is
complete.

The direct `.1800` snapshot was older:

```text
070525/Company.1800: 2026-04-21 18:00:58
XML SQLite:          2026-04-26 19:41:18
Live Tally query:    2026-04-27
```

So some direct `.1800` vs XML/live deltas may be snapshot drift.

Also observed:

- Tally names can carry trailing `\r\n`; comparisons need Tally-compatible
  whitespace normalization.
- Name-only joins are unsafe because identical names can exist across master
  classes. Use `MASTERID`/GUID and record classification.
- Some XML names are present in raw bytes but were missed by strict tagged
  extraction because punctuation/control encoding differs, for example `+` was
  absent from some decoded transaction item strings.
- Voucher GUIDs/master IDs are not yet decoded from `TranMgr.1800` as plain
  tagged UTF-16 strings. This is a major blocker for 100% voucher SQLite.

## Current Status Statement

Do not claim victory. Current status:

## Tally DB Pipeline / Scar Cross-Learning

Read local docs before the live synthetic lab:

- `/Users/aakashchid/workshop/tally-db-pipeline/README.md`
- `/Users/aakashchid/workshop/tally-db-pipeline/LIVE_VALIDATION_NOTES.md`
- `/Users/aakashchid/workshop/tally-db-pipeline/PRODUCTION_GAP_LIST.md`
- `/Users/aakashchid/workshop/tally-db-pipeline/RELIABILITY_PLAN.md`
- `/Users/aakashchid/workshop/tally-db-pipeline/TECHNICAL_RESOURCES.md`
- `/Users/aakashchid/workshop/sena/senacode/scars/migration.md`
- `/Users/aakashchid/workshop/sena/senacode/scars/tally-full-fetch-voucher-extraction-stalls.md`
- `/Users/aakashchid/workshop/sena/senacode/scars/tally-stock-opening-date-extraction.md`
- `/Users/aakashchid/workshop/sena/senacode/scars/tally-company-folders-encrypted.md`
- `/Users/aakashchid/workshop/sena/senacode/scars/tally-malformed-xml-crashes-server.md`
- `/Users/aakashchid/workshop/sena/senacode/playbooks/tally-sqlite-first-leg-audit.md`

Operational scars that matter for `.1800` synthetic work:

- Tally HTTP must be treated as single-threaded. Use one request at a time, with
  a lock and pacing.
- Always include `SVCURRENTCOMPANY` on company-specific collection/voucher
  requests.
- Avoid broad/full-detail voucher pulls and computed stock valuation probes.
  They can wedge Tally while port `9000` still accepts TCP.
- Company folders are ID-named, not name-named. Use Tally HTTP company metadata
  to map name to folder id.
- Malformed or unsupported XML can crash older Tally builds. Keep synthetic
  writes narrow, deterministic, and lab-only.

## Live Synthetic Lab

The user created a disposable Tally company:

```text
name:          Test Synthetic
GUID:          f776087f-35d2-4bee-bb80-6307328dbfb9
company id:    100000
period:        2026-04-01..2026-04-01
```

Validation:

```text
tally-db-pipeline doctor --company "Test Synthetic"
health: healthy
companies discovered: 2
voucher types: 25
groups: 29
ledgers: 3
```

Added guarded runner:

```text
scripts/synthetic_tally_lab.py
```

What it does now:

- verifies `Test Synthetic` is visible and still maps to company number
  `100000`;
- refuses non-synthetic company names;
- uses a local lock so parallel Tally requests cannot happen accidentally;
- if a readable company folder is supplied, snapshots `.1800` files before and
  after the import;
- supports safe ledger-only mode and accounting Payment voucher mode;
- runs `synthetic-diff` with sentinel names, amounts, and dates.

Live file access is now working:

```text
live-tally-data/100000
```

First broad synthetic import was intentionally abandoned after Tally reported an
internal duplicate-unit error for `CSUH001`. Do not use custom Unit XML until
that shape is isolated. The safe path is ledger-only and accounting-voucher
experiments.

Proven live synthetic results:

```text
LEDGER001:
  created CODX_SYNTH_LEDGER_L1841A
  Manager.1800 page 370 header +4 = 206
  Manager.1800 field 0x0002 = CODX_SYNTH_LEDGER_L1841A
  live XML MASTERID = 206

LEDGER_EXP001:
  created CODX_SYNTH_LEDGER_EXP1846A under Indirect Expenses
  Manager.1800 page 371 header +4 = 207
  Manager.1800 field 0x0002 = CODX_SYNTH_LEDGER_EXP1846A
  live XML MASTERID = 207

PAY001:
  created Payment voucher MASTERID=1 amount=123.45
  TranMgr.1800 0x0bbb/0006 = 1
  TranMgr.1800 0x0067/000d = 46112 -> 2026-04-01
  TranMgr.1800 0x00cd = CODX_SYNTH_PAYMENT_PAY1847A
  live XML confirmed MASTERID=1 and ledger rows

PAY002:
  created Payment voucher MASTERID=2 amount=987.65
  TranMgr.1800 0x0bbb/0006 = 2
  TranMgr.1800 0x0067/000d = 46112 -> 2026-04-01
  TranMgr.1800 0x00cd = CODX_SYNTH_PAYMENT_PAY1850B
  live XML confirmed MASTERID=2 and ledger rows
```

The exact continuation playbook is:

```text
SYNTHETIC-MAPPING-PLAYBOOK.md
```

```text
non-vaulted .1800 -> tagged-field SQLite: working
non-vaulted .1800 -> typed master SQLite: in progress
non-vaulted .1800 -> typed voucher SQLite: not solved
vaulted .1800 -> SQLite: not solved, requires vault layer
all Tally companies without vault password: not supported
```

The target remains full typed SQLite from `.1800`, with a provable reconciliation
against live/XML exports.

## Cross-Learning From Claude Workspace

Read `/Users/aakashchid/workshop/sena/tallydatacrack-claude` and folded in
these useful findings:

- Treat `02 10 <field_id:u16le> <type:u16> <len:u16le> <value>` as a general
  TLV frame, not only a string tag.
- Confirmed string type code `00 0f` for UTF-16LE null-terminated values.
- Candidate non-string type codes from Claude: `00 0d` / `00 06` / `00 08`
  u32-like, `00 0a` i32-like, `00 09` u8/bool-like, `00 0b` f64-like. Current
  high-confidence Avinash scans still mostly see `00 0f`, so non-string parsing
  needs more proof.
- Master record names are not always anchored by `0x0002`; candidate additional
  anchors are `0x01f7` and rare `0x0af3`.
- Claude's `070525` master extractor reached roughly 97% name-level coverage
  against its Rosetta SQLite, but voucher rows were still 0. This agrees with
  our blocker: master names are tractable, voucher records are not bounded yet.
- `TranMgr.1800` fields worth prioritizing: `0x00cd` narration-like, `0x00ce`
  and `0x00cf` party/address-like, `0x07d5` voucher-number-like, `0x0bbc`
  company-GUID-like, and `0x0bbe` composite voucher-key-like.
- Strong voucher-key bridge: field `0x0bbe` values ending in
  `<high_hex>:<low_hex>` convert to Rosetta/XML `voucher_key` as
  `(int(high_hex, 16) << 32) + int(low_hex, 16)`. In `070525/TranMgr.1800`,
  a full scan found 2,371 `0x0bbe` occurrences, 2,357 distinct parseable keys,
  and 1,935 distinct matches against 30,545 Rosetta voucher keys. This is not
  complete coverage, but it is a real identity bridge for part of TranMgr.
- Stronger voucher `MASTERID` bridge: compact numeric field
  `bb 0b 00 06 <u32le> 00 00` (`0x0bbb` type `0006`) appears in
  `TranMgr.1800`. In `070525`, a full scan found 31,113 distinct values and
  matched all 30,545 Rosetta voucher `MASTERID`s with zero missing and zero
  duplicate matched IDs. There are 568 extra compact IDs, so this locates
  voucher headers but does not yet classify live/deleted/non-voucher records.
- Requiring same-page TLV key field `0x0003` reduces the compact candidates from
  31,113 to 30,564. Filtering base type code `31` (`Stat Only Outwards`) leaves
  exactly 30,545 exportable voucher headers: `30,545/30,545` Rosetta
  `MASTERID`s, `30,545/30,545` GUIDs, and `30,545/30,545` voucher dates match.
- Nearby compact field `0x0067` type `000d` appears to carry the voucher date
  serial. For `TranMgr`, serial `45747` maps to `2025-04-01` with base
  `1899-12-31`.
- `LinkMgr.1800` is TLV-readable. Quick scan of `070525/LinkMgr.1800` found
  29,521 high-confidence string TLVs, including `0x232e`, `0x2346`, `0x232f`,
  `0x232b`, and related bank/payment/reference-looking fields. It likely
  matters for full voucher/bill allocation reconstruction.
- Side-file pass across `070525`, `100025`, `100001`, and `300925`:
  `LinkMgr.1800`, `SecTran.1800`, and `Aggr.1800` are TLV-readable;
  `VchStatus.1800` and `StatStatus.1800` are not TLV streams.
- `LinkMgr.1800` field `0x0002` matched all 5,364 distinct gold bill allocation
  names in the side-file pass. `0x232e` matched 413/452 distinct bank allocation
  parties. Useful bank/payment fields include `0x2346` (`NEFT`, `RTGS`),
  `0x232f` (`A/c Payee`), `0x232b` UTR/instrument strings, `0x2331` bank name,
  `0x2332` branch, `0x2333` account numbers, and `0x2348` print status.
- `VchStatus.1800` appears to be 512-byte paged fixed slots: three 128-byte
  slots per data page at offsets `0x80`, `0x100`, `0x180`. In `070525`, the
  probe found 30,564 non-empty slots, close to 30,545 gold vouchers. Slot word
  at `+0x28` decodes as a Tally date serial using base `1899-12-31`. Word `+0x64`
  (`words[25]`) maps to exact voucher type `MASTERID`; type counts match
  Rosetta exactly except for 19 `Stat Only Outwards` slots. Word `+0x04` has rare
  values `2`, `16`, `32`, `64` that are flag candidates, but cancelled/optional
  mapping is not proven.
- `StatStatus.1800` uses a similar fixed-slot idea for statutory/status subsets;
  `070525` has about 8,350 non-empty slots.

Local code updates after this cross-read:

- Added reusable `iter_tlvs()` in `tally1800.probe`.
- Added CLI `python3 -m tally1800 tlv-hist <file>`.
- Added a `raw_tlvs` table to `decode-sqlite` so typed TLV evidence is
  preserved alongside decoded string pages.
- Added `scripts/probe_voucher_keys.py` to reproduce the `0x0bbe` to
  `voucher_key` bridge.
- Added `scripts/probe_status_slots.py` to dump `VchStatus`/`StatStatus`
  fixed-slot words and date serials.
- Added compact numeric scanning:
  - `python3 -m tally1800 compact-scan <file>`
  - `scripts/probe_voucher_masterids.py`
  - decoded SQLite tables `compact_numeric_fields` and
    `voucher_header_candidates`

## Second Claude Cross-Read

Re-read `/Users/aakashchid/workshop/sena/tallydatacrack-claude` after Claude
read our notes.

Useful confirmations:

- Claude independently verified Manager page `+4` as master identity and reports
  `070525` GUID matches for ledgers, groups, and stock items at `100%` after
  grouping pages by `MASTERID`.
- Claude's voucher extractor reports the same `0x0bbb/0006` voucher
  `MASTERID` bridge: 31,113 extracted IDs, 30,545 Rosetta GUID matches, 568
  extras.
- Their structural run on other company folders found the same anchor shape:
  `100001` 2,275 anchor pages, `100025` 28,562, `300925` 1,859. These are not
  ground-truth validated because Rosetta only covers `070525`.
- Their same-page voucher field coverage is close to ours:
  voucher key near-complete, voucher number about 60%, narration about 64%, party
  very low.

Important correction from our re-test:

- Claude hypothesized compact slots are aligned every 10 bytes from page offset
  `+28` until the first TLV marker. Implementing that as a page-bounded scanner
  only found 15,791 `0x0bbb/0006` values and missed 14,754 Rosetta voucher IDs.
  Therefore the comprehensive signature scan remains the production path for
  voucher identity. Page-bounded compact-slot parsing is useful as a diagnostic
  for some page shapes, but not complete.

Cross-help item for Claude:

- Do not rely on strict `+28 + n*10` slot alignment for `TranMgr.1800` voucher
  anchors. Page 28 has `0x0bbb/0006` at offset `+0xc2`, which is not aligned to
  `+28 + n*10`, yet it is Rosetta voucher `MASTERID=28723`.

## TranMgr Page Group Clue

Promoted a new voucher clustering clue:

- `TranMgr.1800` page header word at offset `+4` is not voucher `MASTERID`, but
  it often acts like a page-family/group id.
- Each exportable voucher header in `070525` has a unique header-page group id.
- Collecting decoded text from all pages sharing that group id improves header
  text recovery:

```text
same-page voucher_number: 18,527 / 28,248
group-page voucher_number: 26,395 / 28,248

same-page party: 527 / 13,857
group-page party: 8,842 / 13,857

same-page narration: 19,505 / 29,584
group-page narration: 26,319 / 29,584
```

Inventory item name recovery by group is currently weak overall:

```text
group inventory item exact: 8,740 / 115,505
group inventory ledger exact: 0 / 19,574
```

Breakdown shows this is voucher-type dependent. Sales invoice item names are
mostly present as group text (`61 Sales` about 95.6%), while stock journals and
conversion journals mostly store item references in compact/numeric structures.

Caveat: page-group id is not a proven exact page chain. Some group ids include
many pages, so grouped text is useful evidence but not yet a final record
boundary model.

## Cross-Company Voucher Header Structure

Measured current compact/TLV parsing on four non-empty company folders:

| Company | `0x0bbb/0006` hits | Distinct | key_raw rows | Date serial range | Base type 31 |
| --- | ---: | ---: | ---: | --- | ---: |
| `070525` | 31,113 | 31,113 | 30,564 | 2025-04-01..2026-04-21 | 19 |
| `100001` | 2,275 | 2,275 | 2,229 | 2024-04-01..2026-04-20 | 6 |
| `100025` | 28,576 | 28,562 | 28,416 | 2023-04-01..2024-03-31 | 29 |
| `300925` | 1,859 | 1,859 | 1,772 | 2024-04-01..2026-04-18 | 4 |

Notes:

- `0x0067/000d` date hits matched `0x0bbb/0006` hit counts in every measured
  company.
- `100025` has 14 duplicate compact master-id hits, deduping to 28,562
  candidates.
- Claude's saved `*_vouchers_v1.sqlite3` master-id sets match the current
  distinct candidates exactly for all four companies.
- Only `070525` has Rosetta ground truth, so this is structural consistency,
  **not** proof of all-company support.

This improves evidence capture only. It does **not** solve voucher identity or
make `.1800 -> SQLite` complete.

## Voucher Line Reference Breakthrough

Raw string search undercounts voucher lines because many child-line references
are stored as `Manager.1800` `MASTERID` integers, not names.

Probe command:

```bash
PYTHONPATH=. python3 scripts/probe_inventory_item_refs.py \
  out/070525_decoded_headers.sqlite3 \
  --manager-decoded out/070525_decoded_compact.sqlite3 \
  --tranmgr /Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/TranMgr.1800 \
  --rosetta /Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3 \
  --limit-per-type 500
```

Inventory line result: sampled stock item `MASTERID`s appear in the voucher page
group at or near 100% for tested voucher types:

```text
Conversion Stock Journal:           490 / 490
Stock Journal:                      500 / 500
31-Puchase(Monthly):                473 / 473
Store Movement Stk Jrl:             429 / 429
Purchase Order:                     477 / 477
61 Sales:                           482 / 482
Purchase Order -Others:             497 / 497
Delivery Challan - Stock Alignment: 497 / 497
```

Accounting line result: ledger names are likewise represented as Manager ledger
`MASTERID` integers in `TranMgr` page groups:

```text
61 Sales ledger refs:          486 / 486
12 Payt Bank ledger refs:      583 / 583
31-Puchase(Monthly) refs:      579 / 579
```

This shifts voucher-line decoding from string search to integer-reference
parsing. Amounts, quantities, rates, signs, and exact child-row boundaries are
still unresolved.

## Sales Probe Status After Claude Cross-Learning

Claude's code confirmed the same high-level model: collect `TranMgr.1800` pages
by voucher tree id, use `00 50 <subrec_id> 00 10 <length>` sub-record framing,
and treat ledger rows as aggregatable. The useful part for Codex was not
Claude's old broad parser; it was the framing clue plus the warning that
record-local rows can move between compact cells and `0x01f6` sub-records.

Current safe `61 Sales` result:

```text
compact runs 52149 exact 2042/2068 item_amount_only 12 missing 14
matrix  runs  1529 exact 1501/2068 item_amount_only 28 missing 539
union   runs 53678 exact 2064/2068 item_amount_only 0  missing 4
```

New rules now encoded in probes:

- Rows may use `0x0068/0003` or `0x006d/0003` stock repeats instead of inline
  `0x0069/0003` ledger.
- Missing inline ledger is resolved only by a unique field-coded ledger/amount
  pair in the same voucher tree.
- `00 50 f6 01 00 10` blocks can carry amount and quantity after the compact
  stock/rate prefix.
- Page-split `i64` values can resume after a 28-byte next-page header.

Remaining four `61 Sales` misses:

```text
30652 Glass Stove 2 Burner Sample
34667 L3994B00000 BUTTERFLY MAGNUM 3B LPG STOVE
55395 PRPSCOMN01077-Batti Stand Coated Luxe ( 3 Wing)
60124 BTRDTCOMN000876- Butterfly Magnum Drip Tray Jumbo
```

This is very close for `61 Sales` in `070525`, but it is not the user's 100%
definition. The full project still needs all voucher types and cross-company
proof.

Live Tally is reachable at `10.211.55.3:9000`; `localhost:9000` is not
listening. Use live XML for targeted inspection or production repair, not as
evidence that direct `.1800` decoding is complete.

## Claude Cross-Learning: Amounts and Sub-Records

Re-read Claude's latest `FINDINGS.md` and `extract_voucher_lineitems.py`.

Useful new claims:

- `TranMgr.1800` has a per-voucher page tree keyed by the page header word at
  offset `+4`; anchor pages map voucher `MASTERID` to this tree id through
  `0x0bbb/0006`.
- Nested voucher child blocks use sub-record headers:

```text
00 50 <subrec_id:u16le> 00 10 <length:u32le>
```

Observed sub-record ids:

```text
0x01f6  primary line-item content candidate
0x01fe  tax/GST/batch allocation candidate
0x1327  inventory entry block candidate
```

Locally verified part of the amount claim against Rosetta voucher amounts:
money values are stored as signed little-endian integers scaled by `100000`,
not as IEEE-754 floats.

Examples from `070525/TranMgr.1800` voucher trees:

```text
voucher 28776 amount -2004486.00 -> -200448600000 found 7 times in tree, f64 0
voucher 28776 amount  1698717.34 ->  169871734000 found 11 times in tree, f64 0
voucher 28776 amount   783308.00 ->   78330800000 found 16 times in tree, f64 0
voucher 28952 amount   -41615.00 ->   -4161500000 found 5 times in tree, f64 0
voucher 28952 amount    35266.80 ->    3526680000 found 11 times in tree, f64 0
```

Important correction before sharing back: Claude wrote that amount type bytes
are generally `00 0c`. The local byte-context check did not verify that as the
general line amount type. Verified contexts include compact fields such as
`0x006e/0009`, `0x0004/0008`, and `0x0007/0008` carrying the signed scaled
i64 value. `00 0c` may be a sub-record-local marker or another amount class;
do not promote it to a global type-code rule yet.

Boole independently confirmed this correction:

- `00 0c` is not the generic voucher line amount type.
- Own-tree `00 0c` hits only covered `127 / 40,192` ledger amounts and
  `5,914 / 114,435` inventory amounts in the broad scan.
- In sales rows, `00 0c` is a strong rate candidate.
- Quantity candidates use `0x0001/0008 = quantity * 100000`.
- Rate candidates use `0x0002/000c = rate * 100000`, with duplicates at
  `0x0003/0008`.
- Inventory line amount is commonly `0x006e/0009 = amount * 100000`.

Examples:

```text
28776 Sparkle Power Duo: qty 200.0 -> 0x0001/0008 = 20000000
28776 Sparkle Power Duo: rate 3916.54 -> 0x0002/000c = 391654000
28776 Sparkle Power Duo: amount 783308.00 -> 0x006e/0009 = 78330800000
28952 CRCA Coil: qty 110.2 -> 0x0001/0008 = 11020000
28952 CRCA Coil: rate 68.0 -> 0x0002/000c = 6800000
```

## Voucher Child Anchor Breakthrough

Zeno produced:

`out/teammate_e_child_anchors_20260427.md`

Key row families:

```text
ledger direct block:
  00 50 06 00 00 10 <len:u32le> ... 02 00 00 03 <ledger_id> ... 02 00 00 09 <amount_i64_x100000>

ledger summary row:
  00 04 d3 52 00 09 <ledger_id:u32le> <amount_i64_x100000>

inventory identity row:
  <flags:u16> 2e 01 42 00 <stock_item_id:u32le>

inventory value rows:
  <flags:u16> f6 01 44 00 <stock_item_id:u32le> ...
  <flags:u16> f6 01 46 00 <stock_item_id:u32le> ...
```

Zeno's richer matcher:

```text
direct ledger exact hits:             26,311 / 40,781
direct + summary ledger exact hits:   35,091 / 40,781
inventory item presence hits:        115,436 / 115,505
inventory any amount/qty/rate hit:    88,489 / 115,505
```

Added `scripts/probe_voucher_child_anchors.py` as a conservative reproducible
probe. Current run:

```text
voucher_header_groups=31113
ledger_truth_rows=40781
direct_ledger_exact=24787/40781
summary_ledger_exact=9132/40781
ledger_union_exact=29764/40781
inventory_truth_rows=115505
inventory_presence_exact=115505/115505
inventory_any_value_exact=114411/115505
```

The script proves the anchors are accessible from our code path. It is not yet
a typed `voucher_ledger_entries` / `voucher_inventory_entries` extractor.

Added `scripts/probe_sales_inventory_runs.py` to test sales compact row
families as full typed rows:

```text
0x0002/0003 stock_item
0x0069/0003 ledger
0x006d/0003 stock_item repeat
0x0072/0003 unit
0x006e/0009 amount_i64_x100000
0x0066/000b quantity_i64_x100000 + unit
0x0067/000b quantity_i64_x100000 + unit
next 0x0002/000c rate_i64_x100000
```

Earlier strict-offset result was only `712/2068`. Current safe result after
flexible marker scanning, no-inline-ledger resolution, post-rate `0x01f6`
blocks, and page-split repair:

```text
truth_rows=2068
type_sales_pattern_runs=12075
exact_qty_rate_amount_ledger_matches=2042/2068
item_amount_matches_without_full_tuple=12/2068
```

With the separate matrix probe union, `61 Sales` reaches `2064/2068` exact and
zero item+amount-only rows. This is still not a full `.1800` decoder because it
is one voucher type in one company.

## Journal Inventory Typed Probe

Added `scripts/probe_journal_inventory_rows.py` for the largest inventory
voucher families: Conversion Stock Journal and Stock Journal.

Current run:

```text
PYTHONPATH=. python3 scripts/probe_journal_inventory_rows.py \
  out/070525_decoded_headers.sqlite3 \
  --tranmgr ../tallydatacrack-claude/corpus/070525/TranMgr.1800 \
  --rosetta ../tallydatacrack-claude/corpus/rosetta.sqlite3 \
  --voucher-type 'Conversion Stock Journal' \
  --voucher-type 'Stock Journal'
```

Verified output after the page-boundary fix:

```text
[post_rate_f6]
Conversion Stock Journal exact=71066/71518
Stock Journal            exact=19237/19831

[columnar_f6_44_46]
Conversion Stock Journal exact=34293/71518
Stock Journal            exact=9501/19831

[union]
Conversion Stock Journal exact=71257/71518 (99.64%)
Stock Journal            exact=19577/19831 (98.72%)
```

Typed family 1:

```text
02 00 00 03 <stock_item_master>
02 00 00 0c <rate_i64_x100000> ...
00 50 f6 01 00 10 <len>
  ... 02 00 00 09 <amount_i64_x100000>
  ... 02 00 00 0b <quantity_i64_x100000> <unit_master>
```

Typed family 2:

```text
<flags:u16> f6 01 44 00 <stock_item_master> ...
<flags:u16> f6 01 46 00 <stock_item_master> ...
```

For strict `0x04` rows:

```text
amount@+24 -> quantity@+32
amount@+32 -> quantity@+24
rate = abs(amount) / abs(quantity)
```

Page-boundary breakthrough: `repair_split_i64()` now handles the case where the
4-byte compact field marker itself ends at byte `511`, with the actual i64
resuming at offset `0x1c` on the next page. This is separate from the earlier
case where the numeric i64 itself was split.

Example fixed by this rule:

```text
42385 Stock Journal WIP Blank Stage Hilux 3b
qty=150 rate=1400 amount=210000
```

Heisenberg's read-only page-boundary prototype has been implemented in the
journal probe as a logical F6 block reader. It reconstructs the 10-byte F6
header and body through page continuation bytes.
- Broader columnar encodings are visible in remaining large misses:
  `f7 01`, `f8 01`, `f9 01`, `fa 01`, `06 02`, and kind `64` beside kind `44`.
  These need stricter validation than the page-boundary fixes.

Largest remaining journal misses after this update include:

```text
58414 Conversion Stock Journal PRBBOSMTB00781 Brass Burner Jumbo with OD +196020.00
31156 Conversion Stock Journal SS Sheets - 2mm                         +179055.00
54473 Conversion Stock Journal WIP Channel Sparkle 3B Unpainted - TP    +177487.36
53607 Stock Journal            WIP Blank Stage Hilux 3b                 +126000.00
```

## Purchase Monthly Probe

Averroes tested `31-Puchase(Monthly)` using the existing Sales and Journal
probes.

Results:

```text
Journal-style probe:
truth_rows=7682
[post_rate_f6]       exact=7625/7682 (99.26%)
[columnar_f6_44_46] exact=5358/7682 (69.75%)
[union]             exact=7667/7682 (99.80%)

Purchase hybrid probe:
exact_qty_rate_amount_ledger_matches=7645/7682
```

Rosetta says all inventory rows within one `31-Puchase(Monthly)` voucher share
one inventory ledger, but exact raw decoding should still prefer row amount ->
ledger/amount block.

Normal stock-repeat variant:

```text
02 00 00 03 <stock_id>
68 00 00 03 <same stock_id>
6d 00 00 03 <same stock_id>
02 00 00 0c <rate_i64_x100000>
00 50 f6 01 00 10 <len>
  ... 02 00 00 09 <amount_i64_x100000>
  ... 02 00 00 0b <quantity_i64_x100000>
```

Inline ledger variant:

```text
02 00 00 03 <stock_id>
69 00 00 03 <ledger_id>
6e 00 00 03 <ledger_id>
02 00 00 0c <rate_i64_x100000>
...
```

For the `0x68` stock-repeat variant, resolve the ledger through the nearest
preceding block:

```text
02 00 00 03 <ledger_id> ... 02 00 00 09 <same amount_i64_x100000>
```

Open implementation requirement: make F6 parsing tolerant when the F6 header
itself is split across page-end/next-page continuation. MID `28738` has F6
starting 8 bytes before page end, with amount/qty markers only after next-page
header/noise.

## Claude Wins Port

Ported three Claude implementation wins into Codex:

- Page-aware TLV walking in `tally1800/probe.py`.
- `VchStatus` voucher-type pairing in `tally1800/decoded_export.py`, exposed as
  `paired_voucher_type_master_id`.
- Field-chain fallback and shape validators in `tally1800/typed_fields.py`,
  exposed through `manager_master_records`.

Validation on regenerated `out/070525_decoded_headers.sqlite3`:

```text
voucher_type exact via Manager voucher-type IDs: 30464/30545 = 99.73%
manager_master_records: 9477
ledger state with defaults:   99.87%
ledger country with defaults: 100.00%
ledger pincode:               99.50%
ledger GSTIN:                 98.30%
ledger PAN:                   98.45%
```

## LinkMgr Allocation Breakthrough

Erdos produced a focused `LinkMgr.1800` handoff:

`out/linkmgr_allocations_F_2026-04-27.md`

Key points:

- Compact `0x0002/0007` stores voucher key as `<low:u32><high:u32>`, where
  `(high << 32) + low` matches Rosetta voucher keys.
- Found `30,768` LinkMgr key hits matching `10,144` distinct Rosetta vouchers.
- Bill allocation name is TLV `0x0002/000f`.
- Bill amount fields:
  - `0x0642/0009`: signed scaled amount, scale `100000`, likely original
    reference amount.
  - `0x0644/0009`: `<date_serial:u32><amount:i64 scaled 100000>`, likely
    allocation/application amount paired to a following `0x0002/0007`.
- Exact bill allocation matches by `(voucher_key, bill_name, amount)`:
  `10,265 / 12,304`.
- Bank allocation amount is `0x232e/0009`, also signed scaled by `100000`.
- Bank pages map directly by `0x0002/0007` or indirectly through
  `0x0003/0006` application id -> `0x0002/0006` before `0x0002/0007`.
- Exact bank matches by `(voucher_key, party, amount)`:
  `2,667 / 3,601` unique Rosetta bank allocation entries.

Limits: bill pages can aggregate applications, zero-amount cases exist, and
bank transaction type is not fully decoded.

## VchStatus Alignment Breakthrough

Nash produced:

`out/vchstatus_alignment_teammate_d_2026-04-27.md`

Code now exports `vch_status_slots` from `decode-sqlite`. Regenerated
`out/070525_decoded_headers.sqlite3` verifies:

```text
vch_status_slots:              30,564
keyed voucher candidates:      30,564
base_type_code=31 candidates:      19
```

`VchStatus` `(date_serial, voucher_type_master_id)` is an exact multiset match
for Rosetta's 30,545 exportable vouchers plus the 19 internal base-type-31
`Stat Only Outwards` candidates. This is stronger evidence that
`VchStatus.1800` covers keyed voucher headers and gives the voucher type
`MASTERID`.

Ordering is not solved. Naive `MASTERID` order matches `29,236 / 30,564`;
after inserting the legacy low ids `2347` and `2366` into the Apr-30 block,
the sequence gets to `30,558 / 30,564`, but duplicates make the last local
assignments ambiguous. Do not emit a hard voucher-status link until an
independent order field is found or cross-company validation proves the rule.

## Shareable Breakthrough Note

Created `BREAKTHROUGHS-2026-04-27.md` as the compact cross-team handoff. It
summarizes:

- TLV frame and string encoding.
- Manager `MASTERID` identity and field map.
- TranMgr voucher header `MASTERID` bridge.
- Exportable voucher header filter using `0x0003` and base type `31`.
- Voucher date serial base.
- VchStatus fixed-slot fields.
- TranMgr page-group text recovery.
- Voucher line integer-reference breakthrough.
- Fixed-point amount encoding evidence.
- LinkMgr allocation surface.
- Cross-company header consistency and remaining blockers.

## Next Questions

1. What is the page checksum at word 0?
2. What do page header words 1..10 mean by file role?
3. What are the object/field tags around strings?
4. How are records chained across pages?
5. How does `TranMgr.1800` reference `Manager.1800` master IDs?
6. How are voucher amounts, quantities, rates, and signs encoded near the
   integer master references?
7. Does vaulted `.1800` retain the old 512-byte DES-like structure or use a new
   cipher/KDF?

## Direct Inventory SQLite Integration

Implemented in `tally1800/inventory_decode.py` and
`tally1800/decoded_export.py`.

New decoded tables:

- `manager_master_kind_guesses`
- `voucher_inventory_entry_candidates`
- `voucher_inventory_entries_1800`
- `voucher_decode_audit`
- `xml_repair_queue`

`manager_master_kind_guesses` uses direct Manager anchors:

```text
stock_item: field 0x0fd4, high confidence
ledger:     field 0x0067, high confidence
```

On `070525`, this gives `2747` high-confidence stock item ids and `2965`
high-confidence ledger ids without Rosetta. All inventory stock/ledger ids used
by the biggest tested voucher families are covered by these anchors.

Regenerated `out/070525_decoded_inventory.sqlite3` validates:

```text
voucher_inventory_entries_1800:
  Conversion Stock Journal 71016/71518, extra unique 415
  Stock Journal            19214/19831, extra unique 196
  31-Puchase(Monthly)       7536/7682,  extra unique 136
  61 Sales                  2048/2068,  extra unique 83

voucher_inventory_entry_candidates:
  Conversion Stock Journal 71208/71518, extra unique 48455
  Stock Journal            19554/19831, extra unique 15301
  31-Puchase(Monthly)       7642/7682,  extra unique 5193
  61 Sales                  2048/2068,  extra unique 83
```

Noise finding: for journal F6 rows, top-1 preceding stock row plus its own rate
is the best production rule. Top-2/top-3 preceding stocks and derived rates add
little exact coverage but explode false candidates. Keep those broader probes in
research scripts, not in the trusted table.

Confidence finding: in production, confidence is an internal evidence level,
not a truth label. A `high` row is produced by a calibrated row-local rule;
only XML/live or later balance checks can prove it for a new company.

Current audit output on `070525`:

```text
voucher_decode_audit rows: 30564
needs_xml_review:         15322
needs_xml_repair:            86
xml_repair_queue rows:       86
```

`xml_repair_queue` is intentionally hard-gap-only. Review rows remain queryable
through `voucher_decode_audit`.

## Synthetic Diff Tool

Implemented:

- `tally1800/synthetic_diff.py`
- `scripts/synthetic_diff.py`
- CLI command: `python3 -m tally1800 synthetic-diff`

Purpose: given two company snapshots, find what changed when a controlled
synthetic mutation is made. Inputs can include sentinel text, numbers, and
dates. The tool reports:

- changed files and changed page samples
- raw UTF-8/UTF-16 sentinel hits
- tagged field-string hits for sentinels
- TLV value deltas
- raw numeric hits for integer/scaled/date encodings
- compact numeric hits with field id/type
- local byte windows around first changed pages

Smoke tests completed:

```text
out/synthetic-diff-smoke-manager.json
out/synthetic-diff-smoke-tranmgr.json
```

These smoke tests compared unrelated real snapshots, so the changed-page counts
are noisy. They still prove the tool locates fields such as `0x0067`, `0x0069`,
`0x07d3`, `0x07d4`, and compact numeric hits. Real value comes from controlled
before/after snapshots with unique values like `SYNTH_LEDGER_001`,
`SYNTH_ITEM_001`, quantity `13.37`, rate `19.23`, and date `2026-04-01`.

## RAM Scraping Assessment

RAM scraping is potentially useful as a research microscope, not as the main
product route.

Potential value:

- Tally may hold decoded/decrypted masters/vouchers in memory after loading a
  company.
- Synthetic sentinel names/numbers could reveal in-memory object layouts faster
  than disk archaeology alone.
- Comparing memory hits with `.1800` offsets can identify which on-disk records
  feed which in-memory structures.

Limits:

- Tally is running inside Windows/Parallels, so macOS cannot directly inspect
  `TallyPrime.exe` memory. We would need a Windows-side dump tool or a Parallels
  VM memory dump.
- It is version/build/process-state dependent and not suitable as the production
  `.1800 -> SQLite` decoder.
- Memory dumps may contain sensitive business data and credentials, so they
  need stricter handling than `.1800` snapshots.

Best use: combine RAM dumps with synthetic-diff snapshots. Create a tiny company
with unique sentinels, load it in Tally, dump process/VM memory, then scan both
memory and `.1800` for the same sentinel names, master ids, quantities, rates,
amounts, and dates.

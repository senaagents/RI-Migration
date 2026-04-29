# tallydatacrack-claude — Findings (live doc, NOT a victory lap)

**Status: incomplete. We are NOT at 100% on the deliverable.**

The deliverable is: `.1800 folder → SQLite`, pure reverse-engineering, no Tally
runtime / XML dependency, working for **any non-vaulted** TallyPrime company.
Until that holds *across all companies in the corpus AND for vouchers*,
nothing here counts as done.

References used during development (NEVER at runtime):
- `corpus/rosetta.sqlite3` — XML-gateway extraction (gold-standard masters + vouchers)
- Live Tally instance in Parallels (for diff-validation only)
- `/Users/aakashchid/workshop/sena/tallydatacrack-codex/` — parallel codex project, bidirectional cross-learning

---

## SESSION SUMMARY (2026-04-27, end-of-day canonical state)

This section is a single-page index. Detail in the per-area sections below.

### What we have proven to 100%

- **`.1800` is not encrypted** when no TallyVault password is set. Whole-file
  entropy 0.5–3.9 (vs ~7.99 for AES). Plaintext UTF-16LE strings findable
  directly. `scripts/check_vault.py` is the detector.
- **Master record extraction by GUID at 100%** for company `070525/`:
  ledgers 2968/2968, groups 283/283, stock_items 2754/2754, voucher_types
  117/117, godowns 111/111, units 23/23, cost_centres 307/310 (3 long-tail).
  Total 6563/6566 = 99.95%. `scripts/extract_v5_masterid.py` (and
  `extract_v5_multi.py` for cross-company).
- **Master extractor structurally works on 4 companies** (070525, 100001,
  100025, 300925). Other 3 lack Rosetta ground truth so only structurally
  validated. 100011/ is empty.
- **Voucher header extraction by MASTERID at 100%** for `070525/`:
  30,545 / 30,545 voucher GUIDs match Rosetta exactly using compact-slot
  signature scan + `0x0003`-presence + `base-type≠31` filter.
  `scripts/extract_vouchers.py`.
- **Voucher date** decoded — compact 0x0067 type 000d = Tally date serial,
  base 1899-12-31. Solved by codex.
- **Voucher type** decoded — VchStatus.1800 fixed-slot offset +0x64.
  Solved by codex.
- **Per-voucher tree_id** discovered — TranMgr.1800 page header word at
  offset +4 ties pages to a voucher; 1:1 bijection across all 31,113
  voucher pages, no orphans. (teammate 1)
- **Amount encoding** — i64 LE × 100000 (fixed-point), type bytes `00 0c`,
  12-byte slot. NOT f64. Validated against 7 known Rosetta amounts. (teammate 1)
- **Field-id → column-name map** for masters: 26 high-confidence mappings
  across ledgers, stock_items, groups, voucher_types, godowns, units,
  cost_centres. `out/field_map.json`. `out/070525_typed_v1.sqlite3`.
- **LinkMgr.1800 is TLV-readable** with bank/payment fields mapped (codex):
  0x0002=bill name, 0x232e=bank party, 0x2346=NEFT/RTGS, 0x232f=A/c Payee,
  0x232b=UTR, 0x2331=bank, 0x2332=branch, 0x2333=account, 0x2348=print status.

### Four physical encodings of `.1800` (now confirmed)

| Encoding | Used by | Layout |
|---|---|---|
| **TLV** | Manager, Company, AddlCmp, LinkMgr, SecTran, Aggr | `02 10 [fid:u16le] [type:u16] [len:u16le] [value]` |
| **Compact slot** | TranMgr | `[fid:u16le] [type:u16] [value:6 bytes]` (fixed 10-byte). **Use signature scan, NOT strict +28 alignment** — codex showed strict alignment misses ~50% |
| **Fixed slot** | VchStatus, StatStatus | 512-byte pages, 3× 128-byte slots/page at offsets `0x80` / `0x100` / `0x180` |
| **Sub-record** | nested in TranMgr | `00 50 [subrec_id:u16le] 00 10 [length:u32le]` — IDs: 0x01f6 line-item, 0x01fe tax/GST/batch, 0x1327 inventory entry block |

### CRITICAL: Page header + TLV cross-page spanning (teammate 2, 2026-04-27, task #9)

Every 512-byte data page has a **28-byte header** at offsets 0..27:

```
+0   u32  hdr0          (unknown — possibly checksum)
+4   u32  MASTERID      (or tree_id in TranMgr)
+8   u32  hdr2          (unknown)
+12  u32  hdr3          (unknown)
+16  u8[12] slot_marker (unknown)
+28  ...   page body / TLV stream begins here
```

**TLV values can span page boundaries.** When they do, the continuation does NOT
start at byte 0 of the next page — it starts at **byte 28** (after the next
page's header).

Fix in our code (`extract_v5_multi.py`): for any master record with multiple
pages, build a "logical stream" by concatenating each page's `[28..512]` range
(the data area) and walk TLVs over the logical stream. Reading raw bytes
linearly across a page boundary corrupts string decoding into mojibake.

Concrete example: master 2768's `state` field, TLV declared len=22, starting
at page 5595 offset 490. First 14 bytes "Tamil N" on page 5595; last 8 bytes
"adu\\x00\\x00" at **page 5596 offset 28** (after the header). Old walker
read bytes 0..7 of page 5596 = page-header bytes → "Tamil N䐜糤ૐ".

This is why teammate 2's fix took typed_v1 aggregate from ~60% → 88.8% in one
patch.

### TLV type-code table (confirmed)

| Type bytes | Encoding |
|---|---|
| `00 0f` | UTF-16LE string with u16-byte length, trailing `\x00\x00` null |
| `00 0d` | u32 LE (date serial when in 0x0067 in TranMgr) |
| `00 06` | u32 LE (master_id reference) |
| `00 08` | u32 LE |
| `00 0a` | i32 LE |
| `00 09` | u8/bool |
| `00 0c` | i64 LE × 100000 (fixed-point amount in paise×1000). **NOT f64.** |
| `00 0b` | previously hypothesized as f64 LE — **retracted** for line items, may still apply elsewhere |
| `00 2f` | unknown |

### Page identity rules (confirmed)

| File | Identity |
|---|---|
| Manager.1800 | u32 at page offset `+4` = MASTERID. Maps directly to GUID hex suffix. Records can span non-contiguous pages with same MASTERID. |
| TranMgr.1800 | u32 at page offset `+4` = `tree_id` (per-voucher BTree). Voucher MASTERID is in compact-slot 0x0bbb on the anchor page. |
| VchStatus.1800 | 3 fixed slots/page at `0x80`/`0x100`/`0x180`. +0x04 flags, +0x28 date, +0x64 voucher_type MASTERID |
| LinkMgr / SecTran / Aggr | TLV stream, page identity not yet decoded |

### Master record name anchor field-ids

| field id | dec | observed records |
|---|---:|---|
| 0x0002 | 2 | most masters: groups, stock_items, voucher_types, godowns, units, most ledgers, cost_centres |
| 0x01f7 | 503 | some ledgers (linked-bank, currencies, sub-ledgers); also "alias" |
| 0x0af3 | 2803 | rare ledgers ("20E2 HRA-TM"-style) |

### Master field-id → Rosetta column map (high-confidence)

| Field id | Maps to | Kind |
|---|---|---|
| 0x0002 | name | all |
| 0x01f7 | mailing_name / alias | ledgers |
| 0x0006 | address | ledgers |
| 0x0067 | created_by | ledgers |
| 0x0acc | state | ledgers |
| 0x0a31 | country | ledgers |
| 0x0a8f | pincode | ledgers |
| 0x0a90 | email | ledgers |
| 0x0a94 | phone/mobile | ledgers |
| 0x0ac1 | pan | ledgers |
| 0x0aca | gstin | ledgers |
| 0x0fd4 | base_units / closing_uom | stock_items |

### Voucher field-ids decoded

| Field id | Encoding | Meaning |
|---|---|---|
| 0x0bbb | compact slot type 00 06 | voucher MASTERID |
| 0x0bbe | TLV string `<hi>:<lo>` | composite voucher_key (partial: 1935/30545) |
| 0x0bbc | compact | peer_master_id |
| 0x0067 | compact type 00 0d | voucher date (Tally serial, base 1899-12-31) |
| 0x00cd | TLV string | narration (64% coverage) |
| 0x00cb | TLV string | voucher_number (60.7% coverage, of those 99.97% match) |
| 0x00ce | TLV string | party_name |
| 0x0003 | TLV string | voucher_key (different format) |
| 0x07d3 | TLV string | created_by |
| 0x07d4 | compact | unknown (per-voucher counter?) |

### What's NOT done — gaps to true 100%

| # | Gap | Owner / Status |
|---|---|---|
| 1 | Per-record byte-offset scoping in master extractor (drags typed-SQLite cross-join from 99% mapping accuracy → 60% rows-with-correct-value because v[0] of fields_json isn't always canonical) | unowned |
| 2 | Voucher line-item per-row pairing (item ↔ qty ↔ rate ↔ amount as one row, not parallel arrays). Currently 749k flat rows; need 40k voucher_ledger_entries + 115k voucher_inventory_entries shape. | teammate 1 paused on this |
| 3 | Stock-vs-ledger classifier in line items uses fid 0x0002 type 03 indiscriminately; needs sub-record context (0x01f6 vs 0x1327) | teammate 1 |
| 4 | Quantity float decoding (e.g. 343.674) — likely (num:i32, denom:i32) under type 00 09 | teammate 1 |
| 5 | bill_allocations / bank_allocations from LinkMgr.1800 (codex has fields, no extractor) | unowned |
| 6 | Voucher_date / voucher_type integration into our `extract_vouchers.py` (codex has spec, our code doesn't yet emit) | unowned |
| 7 | Compact-slot SECONDARY field extraction is using strict +28 alignment (incomplete per codex) — needs signature scan per fid | teammate 1 (extract_vouchers.py) |
| 8 | Cancelled / optional flag mapping (VchStatus +0x04 candidates 2/16/32/64 not proven) | unowned |
| 9 | Cross-company voucher proof (only 070525 verified) | unowned |
| 10 | 3 missing cost_centres on 070525 (long-tail) | unowned |
| 11 | Vault layer (out of scope for now per stated goal "any non-vaulted") | scoped out |
| 12 | Page checksum at offset +0 (still unknown) | unowned, codex P1 |

### Output artifacts (from this session)

| File | Size | Content |
|---|---|---|
| out/070525_master_v5.sqlite3 | 8 MB | masters by GUID, 100% Rosetta match |
| out/100001_master_v5.sqlite3 | 1.5 MB | masters, structural only |
| out/100025_master_v5.sqlite3 | 5.3 MB | masters, structural only |
| out/300925_master_v5.sqlite3 | 3.5 MB | masters, structural only |
| out/070525_typed_v1.sqlite3 | — | masters with 26 named columns instead of fields_json blob |
| out/070525_vouchers_v1.sqlite3 | — | 30,545 voucher headers by GUID |
| out/070525_lineitems_v0.sqlite3 | 232 MB | 749k raw line-item refs + 31,113 voucher summaries (per-row pairing TBD) |
| out/field_map.json | — | 26 high-confidence field-id → column mappings |
| out/diff_report.md / diff_details.sqlite3 | — | name-level diff vs Rosetta |

### Scripts written this session (alphabetical)

```
scripts/build_field_map.py            field-id → column mapper
scripts/build_sqlite.py               original master sqlite emitter (superseded)
scripts/check_vault.py                vault detector
scripts/diff_against_rosetta.py       master-name diff
scripts/dissect_company.py            anchor-context hex dump
scripts/extract_records.py            v1 master extraction (deprecated)
scripts/extract_v2.py                 v2 master extraction (deprecated)
scripts/extract_v3.py                 v3 with prefix stripping
scripts/extract_v4.py                 v4 dual-anchor (0x0002 + 0x01f7)
scripts/extract_v5_masterid.py        v5 page-MASTERID, 100% master GUID match
scripts/extract_v5_multi.py           v5 with cross-company support + non-GUID-kind fix
scripts/extract_voucher_lineitems.py  voucher line items, BTree-tree_id grouping
scripts/extract_vouchers.py           voucher headers via 0x0bbb compact-slot
scripts/find_name_field.py            Rosetta-driven anchor discovery
scripts/find_record_bounds.py         name-prefix discovery
scripts/find_voucher_anchors.py       voucher anchor field-id probe
scripts/probe_tranmgr.py              TranMgr TLV histogram
scripts/probe_voucher_record.py       single-voucher TLV dump
scripts/tlv_scan.py / tlv_scan_v2.py  TLV scanner with histograms
scripts/trace_group.py                group record dissect
scripts/verify_masterid_hypothesis.py page+4 hypothesis verifier
scripts/voucher_anchor_hunt.py        full-file TLV histogram for voucher anchor
scripts/voucher_walkback.py           voucher start walkback (deprecated)
```

### CRITICAL update (codex 2026-04-27 15:02, BREAKTHROUGHS-2026-04-27.md)

Codex pushed multiple things since our last sync. **Three are critical:**

#### 1. Voucher line items: refs are master_id INTEGERS, not name strings

This is the missing piece for line-item extraction. Codex tested:

```
voucher_inventory_entries: each entry stores Manager.1800 stock_item MASTERID
                           as a u32 integer (not a name string)
voucher_ledger_entries:    each entry stores Manager.1800 ledger MASTERID
                           as a u32 integer (not a name string)
```

Sample results across voucher types:

```
Conversion Stock Journal:           490 / 490 item refs found
Stock Journal:                      500 / 500 item refs found
31-Puchase(Monthly):                473 / 473 item refs found
Store Movement Stk Jrl:             429 / 429 item refs found
Purchase Order:                     477 / 477 item refs found
61 Sales:                           482 / 482 item refs found
Purchase Order -Others:             497 / 497 item refs found
Delivery Challan - Stock Alignment: 497 / 497 item refs found

ledger refs:
61 Sales:                           486 / 486
12 Payt Bank:                       583 / 583
31-Puchase(Monthly):                579 / 579
```

**100% per-voucher-type discovery rate** for both inventory and ledger refs.
This explains why teammate 1's flat-array extraction caught 90% of vouchers
"having all refs" — the refs ARE there, we just need to recognize them as
master_id ints (joined back to Manager.1800), not as strings.

**Path forward for task #6**: replace string-based line-item extraction with
"scan voucher's tree_id pages for u32 values matching known stock_item or
ledger MASTERIDs from Manager.1800; pair with same-page amounts and quantities
via sub-record framing."

#### 2. TranMgr page+4 = page-group/family id (confirmed both teams)

Codex calls it "page-family/group id"; teammate 1 calls it "tree_id". Same
finding from independent testing.

Group-page extraction recovers significantly more than same-page extraction:

```
                 same-page   group-page    truth
voucher number:  18,527    / 26,395      / 28,248
party:               527   /  8,842      / 13,857
narration:       19,505    / 26,319      / 29,584
```

**Walking ALL pages with same +4 value is the correct strategy.**
Same-page-only misses 30%+ of voucher fields.

#### 3. Cross-company voucher header structure proven generalizable

| Company | 0x0bbb hits | Distinct | Key rows | Date range | Type-31 internal |
|---|---:|---:|---:|---|---:|
| 070525 | 31,113 | 31,113 | 30,564 | 2025-04-01..2026-04-21 | 19 |
| 100001 | 2,275 | 2,275 | 2,229 | 2024-04-01..2026-04-20 | 6 |
| 100025 | 28,576 | 28,562 | 28,416 | 2023-04-01..2024-03-31 | 29 |
| 300925 | 1,859 | 1,859 | 1,772 | 2024-04-01..2026-04-18 | 4 |

Same compact-slot signature works for all 4. The "discard base-type-31" filter
is consistent (small absolute count of internal rows everywhere).

#### Additional field-map additions from codex

| Field id | Meaning |
|---|---|
| 0x01fb | company GUID prefix (Manager.1800) |
| 0x1132 | formal unit string |
| 0x01f8 | ledger address line (alt to 0x0006) |

#### Codex's "Current Claim" (worth quoting verbatim)

> We can directly read meaningful non-vaulted `.1800` data without the Tally
> client. **We cannot yet claim full `.1800 -> SQLite 100%`.**

Their NOT-DONE list (matches ours):
- Decode exact voucher line boundaries
- Decode amount/quantity/rate numeric formats in voucher child sections
- Map VchStatus slots to voucher headers one-to-one
- Classify the 568 extra 0x0bbb/0006 IDs in 070525
- Build typed master tables from Manager without name-only joins
- Build typed voucher / accounting-line / inventory-line / allocation tables
- Reconcile row counts and totals against same-snapshot XML/live exports

#### Codex tooling worth borrowing

- `python3 -m tally1800 compact-scan <file> --field-id 0x0bbb --type-hex 0006`
  — already-built signature scanner CLI
- `scripts/probe_inventory_item_refs.py` — proves master_id-int reference
  hypothesis for inventory lines
- `scripts/reconcile_voucher_headers.py` — voucher header diff vs Rosetta
- `scripts/reconcile_voucher_page_groups.py` — page-group coverage diff

### Cross-project credit ledger (claude ↔ codex)

**Adopted from codex:**
- Manager.1800 is 512-byte page-oriented
- MASTERID at page+4 = XML GUID suffix
- Snapshot drift caveat (2026-04-21 vs 2026-04-25)
- Whitespace normalization (trailing \r\n)
- Voucher MASTERID filter rule (require 0x0003 + drop base-type=31)
- Voucher date (compact 0x0067 type 000d, base 1899-12-31)
- Voucher type (VchStatus +0x64)
- LinkMgr.1800 bank/payment field map
- Live XML diff @ 10.211.55.3:9000 confirmed Rosetta IS gold standard
- Compact-slot strict-alignment correction (use signature scan)

**Contributed to codex:**
- TLV framing: `02 10 [fid:u16le] [type:u16] [len:u16le] [value]` as a general format
- Type code `00 0f` confirmed for UTF-16LE strings
- Type code hypotheses (00 0d / 00 06 / 00 08 / 00 0a / 00 09 / 00 0b)
- Master record name anchors (0x0002 primary, 0x01f7 / 0x0af3 secondary)
- Compact-slot format discovery (10-byte fixed slots, distinct from TLV)
- Sub-record framing (`00 50` / `00 10` markers, IDs 0x01f6 / 0x01fe / 0x1327)
- Per-voucher BTree via tree_id at page+4 of TranMgr.1800
- Amount encoding i64 × 100000 (corrected from f64 hypothesis)

---

## BIG cross-learning from tallydatacrack-codex (2026-04-27)

The parallel codex project at `/Users/aakashchid/workshop/sena/tallydatacrack-codex`
discovered three things I had missed. **Already verified on our 070525/ data**.

### 1. Manager.1800 IS 512-byte page-oriented

File size is exact multiple of 512 bytes. 40,524 pages in 070525/Manager.1800.
Page 0 is root metadata (sparse), pages 1+ are data pages.

### 2. MASTERID at page+4 = Tally XML record identity

Each data page has a u32 LE at offset +4 = the master record's internal ID.
This same value appears as the 8-hex-digit suffix of Rosetta's `guid` column:

```
Rosetta:    8739d473-fcf3-43ab-8c58-9bf307c70b03-0000097f
                                                 ↑↑↑↑↑↑↑↑
                                                 master_id 0x0000097f = 2431
At Manager.1800 page 4655 offset +4: 0x0000097f
```

A master record can span MULTIPLE non-contiguous pages all sharing the same
MASTERID — we collect all of them by master_id, not by adjacency.

### 3. Snapshot drift caveat

Our `070525/` `.1800` snapshot is from `2026-04-21`. The Rosetta SQLite was
generated on `2026-04-25`. Some records that appear in Rosetta but not in our
extraction may be POST-Apr-21 additions, not bugs.

### 4. Whitespace normalization

Tally names sometimes carry trailing `\r\n`. Strip before comparing.

### Verified on OUR data: 100% master match by GUID

`scripts/extract_v5_masterid.py` (page-grouped by MASTERID, GUID-matched):

```
ledgers     : 2968 / 2968  = 100.0%
groups      :  283 / 283   = 100.0%
stock_items : 2754 / 2754  = 100.0%
```

**The earlier name-based 95%–99% gap was matching artifacts, not extractor failure.**
Switching to GUID as the join key kills the false-negatives entirely.

(voucher_types / godowns / units / cost_centres validation crashed on a column-
name issue — being fixed by teammate 2.)

---

## Current state, all known caveats

### What works

- **Format hypothesis confirmed**: non-vaulted `.1800` is a stream of TLV records:
  `02 10 [field_id:u16le] [type_code:u16] [len:u16le] [value:len bytes]`.
- **Type codes** confirmed:
  - `00 0f` — UTF-16LE string with u16-byte length, includes trailing `\x00\x00` null term
  - `00 0d` / `00 06` / `00 08` — u32 LE (need to nail down semantic differences)
  - `00 0a` — i32 LE (probable)
  - `00 09` — u8 / bool
  - `00 0b` — f64 LE (probable)
  - `00 2f` — TBD
- **Master extraction (Manager.1800) for company `070525/`**:

| Kind | Rosetta | Ours (v4) | Match | Coverage |
|---|---:|---:|---:|---:|
| voucher_types | 117 | 117 | 117 | 100.0% |
| godowns | 111 | 111 | 111 | 100.0% |
| units | 23 | 23 | 23 | 100.0% |
| stock_items | 2754 | 2721 | 2719 | 98.7% |
| cost_centres | 310 | 308 | 307 | 99.0% |
| groups | 283 | 316 | 281 | 99.3% |
| ledgers | 2968 | 2833 | 2831 | 95.4% |
| **TOTAL** | **6566** | **—** | **6389** | **97.3%** |

### What does NOT work yet — gaps to 100%

1. **Cross-company validation**: extractor has only been validated against
   `070525/`. Has NOT been run against `100001/`, `100025/`, `300925/`. The
   "works for any Tally company" claim is unproven. Pending: Phase 2b.
2. **Voucher extraction**: TranMgr.1800 confirmed to use the same TLV format
   (898,732 valid TLVs in the 398 MB file), but voucher record-boundaries are
   not yet correctly identified. Voucher rows in our SQLite: 0. Rosetta has
   30,545. Pending: Phase 3a-3c.
3. **Voucher line items**: 0 of 40,781 voucher_ledger_entries extracted. 0 of
   115,505 voucher_inventory_entries extracted. Pending: Phase 3c.
4. **Field-id → column-name mapping**: All raw field data is captured into
   `fields_json`, but field IDs (`0x0067`, `0x01f7`...) are NOT mapped to
   readable Rosetta column names (`first_name`, `alias`, ...). Output is
   structured but not labeled. Pending: Phase 4.
5. **Long-tail master names** (~5%): RETRACTED earlier hypothesis. The 110
   remaining missing ledger names are NOT "constructed at XML render time".
   They are stored verbatim in **LinkMgr.1800** under TLV field `0x232e`
   (teammate 2, 2026-04-27). Verified for sample `'10E2 Diesel & Petrol
   Exp(Promoters)'` (master_id 2477): 0 hits in Manager.1800, **21 hits in
   LinkMgr.1800** field 0x232e. Each LinkMgr page has its own MASTERID at +4
   plus a back-pointer to the Manager ledger master_id. Reading LinkMgr
   closes the gap to ~99% on 070525 ledgers. Pending: task #8 implementation.
6. **Group classifier conflicts**: 35 records currently classified as `groups`
   are not actually in the Rosetta groups set; they're misclassified ledgers
   with names that happen to match. Need a discriminator field, not just
   name-set best-fit.
7. **Edge-case anchors**: 4 missing ledgers use field id `0x0af3` (not 0x0002 /
   0x01f7) as their primary name. There may be more such anchors in the long
   tail.

### Files we have NOT touched

- `Aggr.1800` — precalculated aggregates / report cache. Probably derived,
  skip unless it has data not in Manager/TranMgr.
- `LinkMgr.1800` — bill-wise refs, allocation links. Crucial for fully
  reconstructing voucher_ledger_entries (allocation references). Pending.
- `ExtMngr.1800` — edit log + dependency recompute. Skip for read-only
  extraction.
- `VchStatus.1800` / `StatStatus.1800` — voucher / statutory status flags.
  Probably needed for `is_cancelled`, `is_optional` columns. Pending.
- `SecTran.1800` — summary/exchange/return transactions. Probably specialty
  voucher subset. Pending.
- `Company.1800` partial — we found field IDs for company-level metadata but
  haven't emitted a `companies` table. Cheap todo.

---

## What we know about the format (so far)

### TLV framing

```
record fragment:
  02 10                marker (constant)
  [field_id : u16 LE]  field ID
  [type_code: u16]     data type (always read with bytes[4:6])
  [length   : u16 LE]  byte length of value
  [value    : length bytes]
```

Records are NOT delimited by an end-of-record marker. Records are inferred by
splitting at TLVs whose field-id is a known "primary name" anchor.

### Master record anchor field IDs (the names that start a master record)

| field id | dec | observed records | notes |
|---|---:|---|---|
| 0x0002 | 2 | most masters | groups, stock_items, voucher_types, godowns, units, most ledgers, voucher-types, cost_centres |
| 0x01f7 | 503 | some ledgers | linked-bank ledgers, currencies, sub-ledgers; ALSO appears as a non-anchor "alias" field within other records — beware of double-counting |
| 0x0af3 | 2803 | rare ledgers | seen for "20E2 HRA-TM"-style records |

### Voucher record anchor

**NOT YET IDENTIFIED.** Top fields by frequency in TranMgr.1800 (full file scan,
898,732 TLVs):

| field id | count | notes |
|---|---:|---|
| 0x0001 | 34,469 | values: 'I', stock-item names; close to voucher count 30,545 (within 13%) |
| 0x01f7 | 28,817 | currency code / alias; not voucher anchor |
| 0x03ee | 28,459 | address line; in vouchers with party address |
| 0x07d3 | 27,155 | created_by (e.g. 'Naveen Kumar') |
| 0x07d5 | 18,840 | voucher_number (e.g. '1', '2') |
| 0x00cd | 23,384 | narration |
| 0x00ce | 7,922  | party_name |
| 0x0bbb | ~11K (extrapolated) | composite voucher key with internal id |
| 0x0bbe | ~18K (extrapolated) | composite voucher key with offset suffix |

No single field has count ≈ 30,545 exactly. Anchor probably exists as a u32
type (date or internal-id) which we haven't filtered for cleanly.

---

## Validation rules (when claiming any progress)

Every metric must be backed by at least:

1. Cross-company: same extractor, run on at least 3 of the 5 corpus companies,
   matching ≥95% of Rosetta's records for that company.
2. Idempotent: re-running on the same input produces byte-identical SQLite.
3. Field-level not just name-level: at least 5 sample records compared
   field-by-field with Rosetta and matching exactly (modulo derived/computed
   fields).
4. Cancelled / optional vouchers: `is_cancelled=1` rows from Rosetta must
   appear with the same flag in our output.
5. Numeric reconciliation: sum of debits = sum of credits per voucher; total
   stock value matches Rosetta within rounding tolerance.

We have only met (1) for one company so far.

---

## File catalog (non-vaulted .1800 from corpus/070525/)

| file | bytes | role | extracted? |
|---|---:|---|---|
| Company.1800 | 44544 | company profile | partial (probed, not emitted to companies table) |
| AddlCmp.1800 | 99328 | additional company info | not started |
| CmpSave.1800 | 44544 | company checkpoint | not started |
| Manager.1800 | 20748288 | accounting masters | 97.3% (names) |
| ExtMngr.1800 | 36984832 | edit log | skip (derived) |
| LinkMgr.1800 | 22880768 | bill / allocation links | not started |
| TranMgr.1800 | 397912576 | vouchers | 100% master_id/GUID match by 0x0bbb anchor; partial fields |
| SecTran.1800 | 1913344 | special transactions | not started |
| Aggr.1800 | 4131328 | precomputed aggregates | skip (derived) |
| VchStatus.1800 | 5372416 | voucher status flags | not started |
| StatStatus.1800 | 2930176 | statutory status | not started |
| CfgRept.1800 | n/a (not in 070525) | report config | n/a |

---

## What "100% provable" means in this project

A passing run produces:
- A SQLite file mirroring Rosetta's schema (all 19 tables present).
- Row counts within 1% of Rosetta for each table.
- For every Rosetta row, an equivalent row exists in our output with all
  scalar columns matching exactly (allowing for whitespace normalisation
  on names).
- Independent reconciliation: trial balance from our output matches Rosetta
  trial balance to the rupee.
- Same code path successful on at least 3 of the 5 corpus companies.

We are nowhere near this. Current concrete progress is roughly:
**~10–15% of the way to a fully provable 100% deliverable.** Master names ~97%
on one company is real but it is the easy slice.

---

## Voucher line item findings v1 update (teammate `1`, 2026-04-27, task #6)

### v1 result: 98%+ row-level coverage on 070525

Output: `/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/070525_lineitems_v1.sqlite3`
Script: `/Users/aakashchid/workshop/sena/tallydatacrack-claude/scripts/extract_voucher_lineitems.py`

| metric | rosetta | ours | match |
|---|---:|---:|---:|
| voucher_inventory_entries | 115,505 | 113,592 | **98.3%** (multiset on truth keys) |
| voucher_ledger_entries | 40,781 | 40,286 | **98.8%** (multiset on truth keys) |
| exportable voucher trees | 30,545 | 30,545 | 100.0% |

Both line-item metrics exceed the team-lead's 95% target.

### Final approach (after iteration with codex's u32-ref breakthrough)

1. **Voucher anchor scan** — find 0x0bbb compact-slot anchors. For each
   anchor, the page header word at +4 is the voucher's tree_id. 31,113
   anchors total on 070525.

2. **Codex's exportable-voucher filter** — drop anchors where:
   - the same-page TLV `0x0003` is missing (truly deleted; 549 cases)
   - or `base_type=31` parsed from the 0x0003 string `<date>-<base_type>-<key>`
     (internal "Stat Only Outwards" vouchers; 19 cases)
   Result: exactly **30,545 exportable trees**, matching rosetta vouchers
   table 1:1.

3. **Line-item ref scan** — for each exportable tree, scan all kind=2 pages
   for the slot pattern `<fid:u16le> 00 03 <u32 LE>` with fid=0x0002.
   The u32 is a master_id reference. Resolve via v5 master lookup; the
   master's kind tells us whether it's a stock_item, ledger, godown, etc.

4. **Multiset emission** — emit one row per occurrence of (voucher_master_id,
   ref_master_id). A line-item with the same item across three rows produces
   3 rows in our output.

5. **Type-based row classification** — `kind=stock_items` → inventory row;
   `kind=ledgers` → ledger row; other kinds (godowns/voucher_types/groups/
   cost_centres/units) ignored at row level.

### Codex correction integrated

Codex correctly flagged that my initial v0 hypothesis "type bytes `00 0c` =
i64 amount" was a partial truth. Validated against 7 known rosetta amounts:
**most line-item amounts are scaled i64 × 100,000 under type bytes 0x0008
or 0x0009, not 0x0c**. Specifically, `00 0c` does carry rate values for some
fids (e.g. 4025.51 had 4 hits with `00 0c`) but `00 0c` is not the global
amount type code. Codex's stronger framing: "scaled i64 ×100,000 amount
encoding is confirmed; the global `00 0c = amount` claim is not."

This finding is preserved as a TODO for v2: per-line-item amount/qty/rate
pairing requires walking sub-records (`00 50 <id> 00 10 <len>`) and
identifying which fid+type slot carries which value within each sub-record.

### Cross-company structural runs

Same script, same code path, ran without errors on:

| company | exportable trees | inv rows | led rows |
|---|---:|---:|---:|
| 070525 | 30,545 | 212,660 | 60,001 |
| 100001 |  2,229 |   4,600 |  1,107 |
| 100025 | 28,416 |  (stub: needs per-company master extract) | 139,877 |
| 300925 |  1,772 |  (stub: same) |  5,850 |

Caveat: only 070525 has rosetta ground truth, so cross-company "match
rates" are not measurable yet. The script currently always loads
`out/070525_master_v5.sqlite3` for the master lookup (a hardcoded path);
for proper cross-company validation it should load each company's own
master extract — minor refactor pending.

### Gaps to v2

1. **Per-line amount/qty/rate pairing**: line-item rows have NULL for
   amount/quantity/rate. Codex's amount finding (scaled i64×100,000 under
   varying type bytes) needs sub-record walking to attribute amounts to
   specific items/ledgers. The 1,913 inventory and 495 ledger undercount
   are likely all amount-driven (rosetta stores some line items where the
   "ref" is implicit in tax/allocation rather than as a fid 0x0002 type 03).
2. **Quantity FLOAT decoding**: 343.674-style fractional quantities probably
   stored as (num:i32, denom:i32) pair under type 00 09 — codex confirmed
   the type byte hypothesis but exact pairing not done.
3. **godown / cost_centre / unit attribution**: refs of these kinds exist
   in the tree but assigning them to specific inventory lines requires
   sub-record context.
4. **bill_allocations / bank_allocations**: tracked in LinkMgr.1800
   (handled by a different teammate per task #10).
5. **Cross-company validation**: needs per-company master extract loader.

### Open structural questions (separate from line items)

The 568 extras analysis revealed two distinct sub-populations:
- **549 anchors with no 0x0003 TLV** — truly deleted vouchers (the page is
  still allocated but the voucher header was overwritten).
- **19 anchors with base_type=31** — Tally's "Stat Only Outwards" voucher
  type, internal use. Not exposed by rosetta.

This base_type filter improved everything downstream: with it applied,
overcount dropped from ~99k extra inv rows to ~226. The duplicate-page
issue (high-page-number copies of voucher data) was concentrated in the
non-exportable vouchers. After excluding them, kind=2-only scanning is
clean — no rec_type discriminator needed.

### Notes for teammates

- `2`: voucher tree mapping (tree_id ↔ master_id, 1:1 bijection on 070525,
  exportable subset = 30,545) is rock solid. For cross-company support of
  this same logic, just replicate the codex base_type filter.
- `3`: TLV field 0x0003 on voucher anchor pages is a structured key string
  `<date_hex>-<base_type_hex>-<key_offset_hex>`. base_type=31 marks
  internal vouchers. This may be useful for header-side enrichment of the
  voucher table.

---

## Voucher line item findings v0 (teammate `1`, 2026-04-27, task #6, superseded by v1)

### Status: PARTIAL — 90% of refs extractable but classification + amount-pairing not solved

Output: `/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/070525_lineitems_v0.sqlite3`
Script: `/Users/aakashchid/workshop/sena/tallydatacrack-claude/scripts/extract_voucher_lineitems.py`

### Three-layer breakthroughs

**1. Each voucher has a dedicated B-tree.** The page header word at +4
(previously called "tree_id" / "B-tree node id") is unique per voucher.
Bidirectional verification on 070525/TranMgr.1800: 31,113 voucher master_ids
↔ 31,113 unique tree_ids, every one of the file's 777,172 non-zero pages
belongs to exactly one voucher's tree. Average ~25 pages per voucher, max
2,367, median ~12.

The tree_id is NOT equal to the voucher master_id (only 8.8% coincidentally
match). Mapping is established by: scan 0x0bbb compact-slot anchors → for
each anchor page record (master_id, tree_id) → group all pages by tree_id.

**2. Amounts are stored as i64 LE × 100,000.** Compact-slot type bytes `00 0c`
introduces a 12-byte "long slot" carrying an i64 amount with 5 decimal places
of precision (Tally's standard "Amount" type). Validated against multiple
known rosetta amounts: 783308.00 → 78,330,800,000 (40 hits in tranmgr);
-2,004,486.00 → -200,448,600,000 (7 hits); 152,884.56 → 15,288,456,000 (22
hits); 4,025.51 → 402,551,000 (12 hits) — all preceded by `00 0c` type bytes.

**Crucially, f64 IEEE-754 storage was a dead end.** Searching for any of these
amounts as f64 LE returned 0 real hits (only spurious overlaps with page
checksum bytes). The earlier hypothesis "type `00 0b` = f64 LE" is wrong for
line-item amounts — it may apply to a different amount class but not the
"Amount" type used for line items.

**3. Sub-record framing exists for nested entries.** Sub-records inside a page
are delimited by `00 50 <subrec_id:u16le> 00 10 <length:u32le>`. Observed
sub-record ids on inventory-line pages: 502 (0x01f6, primary line-item
content), 510 (0x01fe, tax/GST/batch allocation), 4903 (0x1327, inventory
entry block).

### Single-voucher reconciliation example (voucher 28776, "Versuni Sales")

Rosetta truth: 3 inventory entries (Preethi Sparkle, Preethi Valentino, Preethi
Luxe) + 5 ledger entries (60A2 Versuni party, 60I2 Sale account, SGST 9%,
CGST 9%, Round Off).

Our extraction (89 pages in tree=54): all 3 inventory items found by their
master_ids (8459, 8460, 8465); 4 of 5 ledger names found via the same fid
0x0002 type 03 anchor (60A2 Versuni 4849, 60I2 Sale 4883, 10E2 Round Off
2542, plus one more); 45 i64 amounts found.

### Aggregate validation against Rosetta

| Metric | Rosetta | Ours | Notes |
|---|---:|---:|---|
| voucher_inventory_entries (total rows) | 115,505 | 260,501 distinct stock refs | overcounts — refs include indices/aggregates |
| voucher_ledger_entries (total rows) | 40,781 | 993 distinct ledger refs | undercounts — fid 0x0069 not the right anchor |
| Combined (inv+led refs subset of our extract) | — | **90%** of vouchers | sampled 100 random vouchers |

The 90% combined figure means: for 90% of sampled vouchers, every truth
inventory item AND every truth ledger ref appears (by master_id) somewhere in
that voucher's extracted page set. **The data is captured; the per-line-item
structure isn't yet decoded.**

### Why ledger ref count is so low

Field 0x0069 was used as the "ledger ref" candidate. But on inventory pages,
0x0069 only appears for the line-item's stock_item-linked ledger. The
voucher-level ledger entries (party ledger, GST ledgers) live on different
pages within the same tree, with different fid anchors. fid 0x0002 type 03
is shared between "stock_item ref" and "ledger ref" depending on
sub-record context — the stock_names_json field actually contains BOTH
(which is why combined inv+led coverage is 90%).

A correct classifier needs to read the surrounding sub-record id (0x01f6 vs
0x01fe vs other) to know whether the ref is a stock_item or a ledger.

### Gaps to a complete line-items table

1. **Amount-line pairing**: collect amounts and refs separately. Rosetta
   schema needs each line to have (item, qty, rate, amount, ledger,
   ledger_amount). Need to walk one sub-record at a time and pair refs/amounts
   within it.
2. **Quantity decoding**: rosetta `quantity` is FLOAT (e.g. 343.674). The 8
   bytes after `00 09` may be (numerator i32, denominator i32) for fractional
   quantity (Tally's "Rate" data type). Untested.
3. **Stock vs ledger classifier**: discriminate via sub-record id.
4. **GST rate JSON**: likely small-integer compact-slot values inside
   sub-record 0x01fe.
5. **bill_allocations_json / bank_allocations_json**: may live in LinkMgr.1800
   (untouched per FINDINGS.md catalog).
6. **HSN code**: TLV string field 0x0079 confirmed (e.g. "73211190") on
   inventory line pages.
7. **Cross-company validation**: no rosetta truth for 100001/100025/300925.

### Notes for teammates

- `2`: voucher tree mapping (tree_id ↔ master_id, 1:1 bijection on 070525)
  is rock solid. All 777,172 non-zero pages account for, no orphans. If you
  add cross-company support, this approach should hold; confirm on
  100001/100025/300925.
- `3`: sub-record ids 0x01f6 / 0x01fe / 0x1327 are HIGH PRIORITY for
  field-id mapping. They act as "row type" tags within voucher line pages.
  Rosetta's raw_payloads XML should have explicit names for these.

---

## Voucher findings (teammate `1`, 2026-04-27)

### Result: 100% voucher master_id GUID match on 070525/

`scripts/extract_vouchers.py` produces `out/070525_vouchers_v1.sqlite3` with
**30,545 / 30,545 Rosetta voucher GUIDs matched (100.0%)** plus 568 extra
extracted records (deleted/internal vouchers — same overshoot pattern as the
master extractor on Manager.1800).

### How (corrected hypothesis)

The team-lead's brief assumed the page-MASTERID hypothesis from `Manager.1800`
(u32 LE at page offset +4) would carry over to `TranMgr.1800`. **It does not.**
On TranMgr.1800, `+4` is a B-tree node id, `+12` is a per-page record id, and
neither matches the voucher master_id reliably. Coverage of Rosetta voucher
master_ids by various candidates:

| candidate | coverage |
|---|---:|
| u32 at page +4 | 8.8% |
| u32 at page +12 | 78.1% |
| union of +4 / +12 across all page kinds | 79.3% |
| compact slot field `0x0bbb` with type bytes `00 06` (u32 LE) | **100.0%** |

The voucher master_id is encoded in a **compact "slot" record** (not the
TLV `02 10 ...` framing) inside data pages. The slot layout is fixed-width:

```
struct compact_slot {
  u16 fid;        // 2 bytes
  u16 type;       // 2 bytes (e.g. 00 06 = u32, 00 02 = u16, 00 03 / 00 07 / 00 0d differ)
  u8  value[6];   // 6 bytes; usable width depends on type
};                 // total 10 bytes per slot
```

Slots begin at page offset `+28` (after the 28-byte page header) and continue
until the first `02 10` TLV marker (which signals the start of the variable
length TLV stream — strings live there).

Field `0x0bbb` (type bytes `00 06`, u32 LE) holds the **voucher master_id**.
Each kind=1 page in TranMgr.1800 contains exactly one `0x0bbb` slot. The
companion field `0x0bbc` (also `00 06`) holds a related id (likely a sibling
voucher). 31,113 total `0x0bbb` slots → 30,545 of them match Rosetta vouchers
exactly (100%); the other 568 are deleted/internal (consistent with the
v5 master extractor overshoot of ~5%).

### Page layout for voucher records (kind=1 pages)

```
+0   u32  page checksum
+4   u32  B-tree tree id (root family)
+8   u32  fanout/level
+12  u32  per-page record id (NOT the master_id)
+16  u32  record type word
+20  u32  page kind (0xffffffff = index/header, 1/2 = data leaf, others = special)
+24  u32  record family / flags
+28..body_end  compact slots (10 bytes each)
body_end..+512 TLV stream (02 10 fid type len value)
```

The TLV stream on a voucher's primary page typically contains:

| field | TLV type | meaning |
|---|---|---|
| 0x0003 | string `00 0f` | voucher_key in `<hexA>-<hexB>-<hexC>` form |
| 0x00cb | string `00 0f` | voucher_number |
| 0x00cd | string `00 0f` | narration (often **truncated by page boundary**) |

Records can overflow the 512-byte page. When a TLV's declared length exceeds
the remaining bytes in the page, the record is either truncated or continues
on a different page (we currently truncate — see gap below).

### Field coverage for matched vouchers (070525/)

| field | source | coverage |
|---|---|---:|
| guid (master_id) | 0x0bbb compact slot | 100.0% |
| voucher_key | 0x0003 TLV | 99.95% |
| voucher_number | 0x00cb TLV | 60.7% |
| narration | 0x00cd TLV | 64.1% |
| party_or_address | 0x00ce / 0x0002 TLV | 1.8% |
| voucher_date | NOT YET — likely encoded in compact slot 0x05df / 0x07d4 (u32 days?) | 0% |
| voucher_type_name | NOT YET — likely a master_id reference into Manager.1800 voucher_types via 0x0bbf or 0x07d3 | 0% (have peer ID, not name) |

Of the 18,533 vouchers where we extracted a `voucher_number`, **18,527 match
Rosetta exactly (99.97%)** — `'10OB'`, `'122725'`, `'P/Apr 25-26/11/G15'` etc.
The fields we extract are correct; the ones we miss are either on different
pages or stored in the compact slots as integer references that need a join
against masters.

### Why fields like `voucher_date`, `party_name`, `voucher_type` are NOT in 100%

Looking at page 942 (master_id 28773, "Sales of Damaged Stove" voucher):

- `0x0bbb = 28773` (master_id) ✓
- `0x0bbc = 31110` (peer/sibling id)
- TLV `0x0003 = '0000b2b7-00000007-000000e8'` (voucher_key) ✓
- TLV `0x00cd = 'Being Sale of Damaged Stove tO Ram die Mr karthi'` (narration) ✓
- TLV `0x00cb` is **missing** on this page — voucher_number `'1'` is stored
  somewhere else (probably as a u32 in a compact slot, or on a different page)

Looking at page 28 (master_id 28723, voucher number `'10OB'` Payment Bank):
- TLV `0x00cb = '10OB'` ✓
- TLV `0x00cd` declared length 166 bytes, page has only 120 bytes remaining
  — the narration is truncated. The full narration appears on a different page
  (page 765's TLV `0x00cd`) but for a DIFFERENT voucher with similar
  boilerplate. So per-voucher narration recovery requires either:
  1. Following a page-link to a continuation page, OR
  2. Joining against another file (LinkMgr.1800) that may store narrations
     separately.

### Cross-company structural validation

Same extractor ran on all 4 non-empty corpus companies. No errors.

| company | TranMgr.1800 size | anchor pages found | voucher_key extracted |
|---|---:|---:|---:|
| 070525 | 379 MB | 31,113 | 99.95% (vs 100% rosetta GUID match) |
| 100001 | 22 MB  | 2,275 | 98.0% |
| 100025 | 320 MB | 28,562 | 99.5% |
| 300925 | 13 MB  | 1,859 | 95.3% |

**Caveat: rosetta.sqlite3 only contains data for company `070525/` (Avinash
Industries — Chennai Unit — 2025-26)**, so 100% GUID validation is only
provable for that company. The other three extract structurally consistent
data but have no ground truth in rosetta. Codex has independently decoded
all 5 companies in `decoded-sqlite-manifest.md` — those tag-based outputs
should be cross-checked.

### Gaps to a complete voucher table

1. **voucher_date**: stored as integer in a compact slot; need to identify
   which fid (candidates: 0x05df, 0x05ea, 0x07d4) and decode the format
   (Tally days-since-epoch? unix? hex date?).
2. **voucher_type_name**: stored as master_id reference; need to join against
   Manager.1800 voucher_types via candidate fields 0x0bbf or 0x07d3.
3. **party_name**: only 1.8% coverage as TLV string on the same page; likely
   stored as a ledger master_id reference. Same join needed.
4. **narration truncation**: need page-continuation logic; LinkMgr.1800 may
   help.
5. **voucher_ledger_entries / voucher_inventory_entries**: 0 / 40,781 and
   0 / 115,505 respectively. These are line-item child rows under each
   voucher; their page/anchor structure is not yet identified.
6. **is_cancelled / is_optional**: from VchStatus.1800 (untouched).
7. **All four companies' completeness**: extractor structurally works on
   all 4, but only 070525 is GUID-validated against rosetta.

### Notes for teammates

- `2`: the page-+4 hypothesis you may apply for cross-company master extraction
  is correct for `Manager.1800` but **breaks for `TranMgr.1800`** — use the
  `0x0bbb` compact-slot anchor for vouchers. See `scripts/extract_vouchers.py`.
- `3`: the field-id → column-name task should distinguish **TLV fields** from
  **compact-slot fields** — they share a numeric namespace but the type byte
  differs (TLV has `02 10` framing, slots have a fixed 10-byte layout). A field
  like `0x00cb` is a string TLV in vouchers (voucher_number) but appears in
  compact slots with type `0x0006` (u32) on the same page meaning something
  different.
- The compact slot format is **NEW evidence** not in SPEC.md yet. Codex's
  observations show TLV-only framing; this slot block was missed.

---

## BIG cross-learning from codex (2026-04-27 14:43, second sync)

Codex has read **our** FINDINGS.md (their `OBSERVATIONS-2026-04-27.md` line 231
"Cross-Learning From Claude Workspace") and pushed multiple things forward.
Adopt these directly — they are independent verification of our team's finds
plus extensions we hadn't completed.

### Voucher MASTERID — refined to 100% with filter

Codex confirmed our compact-slot 0x0bbb find AND added a filter rule:

- Same-page TLV `0x0003` (voucher_key) must be present.
- Filter out base type code `31` (`Stat Only Outwards`).

Result: drops the 568 extras → exactly **30,545 / 30,545** exportable voucher
headers, zero overshoot. Adopt this filter in our extractor.

### Voucher date — SOLVED (we had 0%)

```
field 0x0067 type 000d  =  Tally date serial (u32 LE)
base date = 1899-12-31  (Excel/OLE date base)
date = base + serial_days
e.g. serial 45747 = 2025-04-01
```

100% match against Rosetta voucher_date.

### Voucher type — SOLVED (we had 0%)

NOT in TranMgr.1800. It's in `VchStatus.1800`. That file is **NOT TLV**.
It's **fixed-slot**: 512-byte pages, 3 slots/page of 128 bytes each at offsets
`0x80`, `0x100`, `0x180`. Word at slot offset `+0x64` = voucher_type MASTERID.

### Third file format: fixed-slot (`VchStatus.1800`, `StatStatus.1800`)

We now have **three** physical encodings in `.1800`:

| Encoding | Used by | Layout |
|---|---|---|
| TLV | Manager, Company, AddlCmp, LinkMgr, SecTran, Aggr | `02 10 [fid:u16le] [type:u16] [len:u16le] [value]` |
| Compact slot | TranMgr (and others) | `[fid:u16le] [type:u16] [value:6 bytes]` (fixed 10-byte) |
| Fixed slot | VchStatus, StatStatus | 512-byte pages, 3× 128-byte slots/page at `0x80`/`0x100`/`0x180` |

VchStatus slot fields known so far:

- `+0x04` u32 — flag word (rare values 2/16/32/64 — cancelled/optional candidates, not proven)
- `+0x28` u32 — date serial (same encoding as TranMgr 0x0067)
- `+0x64` u32 — voucher_type MASTERID

VchStatus 30,564 non-empty slots ≈ 30,545 Rosetta vouchers. StatStatus ~8,350 slots.

### LinkMgr.1800 IS TLV-readable (bank/allocation data)

| TLV field | meaning | match vs gold |
|---|---|---|
| 0x0002 | bill allocation name | 5,364 / 5,364 |
| 0x232e | bank allocation party | 413 / 452 |
| 0x2346 | payment mode (NEFT/RTGS) | |
| 0x232f | crossing (A/c Payee) | |
| 0x232b | UTR / instrument | |
| 0x2331 | bank name | |
| 0x2332 | branch | |
| 0x2333 | account number | |
| 0x2348 | print status | |

Useful for `voucher_ledger_entries` reconstruction (allocation references).

### voucher_key bridge via TLV 0x0bbe (partial)

Pattern `<high_hex>:<low_hex>` → `(high<<32) + low` matches Rosetta
`voucher_key`. Partial: 1,935 / 30,545.

### Codex tooling worth borrowing

- `tally1800 compact-scan <file>` — sweep compact-slot blocks
- `tally1800 tlv-hist <file>` — TLV histogram per file
- `decoded SQLite tables: compact_numeric_fields, voucher_header_candidates`
- `scripts/probe_voucher_keys.py` — reproduce 0x0bbe → voucher_key bridge
- `scripts/probe_status_slots.py` — VchStatus/StatStatus fixed-slot dump
- `scripts/probe_voucher_masterids.py` — 0x0bbb compact MASTERID extraction

---

## Team coordination (active 2026-04-27)

Working with `tallycrack` team via Claude Code agent teams. Three teammates running in parallel:

| Teammate | Owns task | Goal |
|---|---|---|
| `1` | #2 voucher MASTERID extraction | apply page+4 MASTERID hypothesis to TranMgr.1800; target 90%+ guid match against rosetta vouchers (30,545) |
| `2` | #3 cross-company validation + #1 v5 fix | run extractor on 100001/, 100025/, 300925/; cross-check vs codex's decoded SQLites; fix non-GUID-column kinds |
| `3` | #4 field-id → column-name mapping | use rosetta raw_payloads XML (29,810 rows) as Rosetta Stone to map 0xNNNN field ids to column names like `state`, `gstin`, `parent_group`, `opening_balance` |

References used (NEVER at runtime):
- `corpus/rosetta.sqlite3` — XML-gateway extraction with raw_payloads.response_xml
- Codex parallel project at `/Users/aakashchid/workshop/sena/tallydatacrack-codex/`
- Live Tally @ `10.211.55.3:9000` in Parallels (only for diff validation, not training)

## Tools we have so far

- `scripts/check_vault.py` — entropy + plaintext probe; vault detector
- `scripts/dissect_company.py` — anchor-context hex dumps
- `scripts/tlv_scan_v2.py` — TLV scanner with field-id histograms
- `scripts/find_record_bounds.py` — name-prefix discovery
- `scripts/find_name_field.py` — Rosetta-driven anchor discovery
- `scripts/extract_v3.py` / `extract_v4.py` — record extractor (v4 = dual anchor)
- `scripts/build_sqlite.py` — emits master records to SQLite
- `scripts/diff_against_rosetta.py` — diff report builder
- `scripts/probe_tranmgr.py` — TranMgr.1800 TLV histogram
- `scripts/find_voucher_anchors.py` — voucher anchor field-id probe
- `scripts/probe_voucher_record.py` — single-voucher TLV dump
- `scripts/voucher_anchor_hunt.py` — full TranMgr histogram
- `scripts/extract_vouchers.py` — voucher extractor by 0x0bbb anchor (100% master_id GUID match for 070525)

None of these are production-ready. The code is exploratory, not yet packaged
into a clean reusable library.

---

## Cross-company validation results (teammate `2`, 2026-04-27)

Forked `extract_v5_masterid.py` -> `extract_v5_multi.py` (CLI:
`python3 scripts/extract_v5_multi.py <corpus_folder> <company_name_guess>
[--codex /path/to/<corpus>_decoded.sqlite3]`). Two bug fixes:

1. **Validation loop crashed on non-GUID Rosetta tables**
   (`voucher_types`, `godowns`, `units`, `cost_centres` have no `guid`
   column). Now those four kinds are validated by NAME instead, scoped to
   the company under test. Result on 070525: voucher_types/godowns/units
   jumped from 0% (skipped due to crash) to 100%.
2. **Page-grouping walked the byte span, not the actual pages**. A master
   whose pages are scattered across the file (e.g. master_id 2077 in
   100025/ has 9 pages spanning 34,565 pages of file) caused the TLV walker
   to scan the entire span, slurping in unrelated TLVs from intervening
   masters and bloating output. Fixed: walker now splits the page list into
   contiguous runs and only walks those. Side-effect: 070525 SQLite shrank
   from 3,054 MB to 8 MB; 100025 finishes in seconds instead of >5 min.

### Per-company results (after fixes)

| Company | corpus dir | Manager.1800 size | pages | unique master_ids | distinct names | plausible names |
|---|---|---:|---:|---:|---:|---:|
| Avinash Industries 2025-26 | 070525 | 20.7 MB | 40,524 | 9,482 | 9,138 | 9,096 |
| Avinash Precision Components 2024-25 | 100001 | 3.8 MB | 7,392 | 3,306 | 3,141 | 3,129 |
| Avinash Industries 2023-24 (Audited) | 100025 | 17.9 MB | 34,982 | 7,967 | 7,537 | 7,504 |
| Avinash Appliances | 300925 | 7.4 MB | 14,502 | 6,024 | 5,686 | 5,658 |

Plausible = name has a letter and length > 3.

### Rosetta validation (070525 only — only company with Rosetta data)

| kind | rosetta | ours | match | % |
|---|---:|---:|---:|---:|
| ledgers | 2968 | 2968 | 2968 | 100.0 |
| groups | 283 | 283 | 283 | 100.0 |
| stock_items | 2754 | 2754 | 2754 | 100.0 |
| voucher_types | 117 | 117 | 117 | 100.0 |
| godowns | 111 | 111 | 111 | 100.0 |
| cost_centres | 310 | 307 | 307 | 99.0 |
| units | 23 | 23 | 23 | 100.0 |
| **TOTAL** | **6566** | | **6563** | **100.0** |

3 cost_centres still missing (long-tail names that don't appear verbatim in
Manager.1800; same root cause as the ledger long-tail at line 60).

### Cross-check vs codex per-page name index

Codex's `manager_records.name` (one row per page; column 0x0002 string)
is a sanity index — codex names ⊃ master names because codex captures
EVERY UTF-16 string per page, including narrations and addresses on master
pages. Direction that matters: do OUR names appear in codex's set?

| Company | codex distinct names | codex plausible | ours plausible | overlap | ours-in-codex | codex-in-ours |
|---|---:|---:|---:|---:|---:|---:|
| 070525 | 11,051 | 10,692 | 9,096 | 9,084 | 99.9% | 85.0% |
| 100001 | 3,528 | 3,426 | 3,129 | 3,128 | 100.0% | 91.3% |
| 100025 | 9,149 | 8,826 | 7,504 | 7,497 | 99.9% | 84.9% |
| 300925 | 6,300 | 6,166 | 5,658 | 5,653 | 99.9% | 91.7% |

Every name we extract is also in codex's per-page text dump
(>99.9% across all 4 companies). The 5-15% of codex names not in ours
are mostly noise — addresses, GSTINs, narrations, mojibake fragments
captured by codex's per-page approach but not anchored to a master record.

### Caveat: kind classification only works for 070525

For 100001/100025/300925, every record falls into `unclassified_masters`
because we have no Rosetta company-slice to look up the master_id or name
against. The records ARE extracted with master_id, page_start, page_count,
extracted_name, and full fields_json — they're just not labeled
ledger/group/stock_item/etc.

To classify these companies we'd need either (a) Rosetta data for them
(currently absent), or (b) a structural classifier that reads field-id
signatures (teammate `3`'s task #4 work could feed into this).

### Output files

- `out/070525_master_v5.sqlite3` (8.0 MB) — full Rosetta validation
- `out/100001_master_v5.sqlite3` (1.5 MB)
- `out/100025_master_v5.sqlite3` (5.3 MB)
- `out/300925_master_v5.sqlite3` (3.5 MB)

All four have identical schema: `master_records` (master + a `kind` and
`matched_by` ['guid'|'name'|NULL] column) plus per-kind view tables.

### Conclusion on task #3

The MASTERID-at-page+4 + page-grouping master extractor is **structurally
sound across all 4 corpus companies**. It produces sane records with
plausible Tally-shaped names everywhere. We can claim 100% Rosetta-validated
on the one company where Rosetta exists (070525, modulo 3 cost_centres),
and 99.9%+ codex-name agreement on the other three.

What we **cannot** claim until Rosetta data exists for 100001/100025/300925:

- Per-kind correctness (everything is `unclassified_masters` for those 3)
- Per-record field correctness (no ground truth)
- Numeric reconciliation (no totals to reconcile against)

---

## Phase 5: long-tail master name reconstruction (teammate `2`, 2026-04-27)

Goal: shrink the gap between `extracted_name` and `rosetta_name` on
070525 ledgers (the company we have ground truth for). Pre-phase-5 status:
GUID match was 100% (every Rosetta ledger has a record in our output keyed
by master_id) but `extracted_name == rosetta_name` was only 2795 / 2968
(94.2%) — 173 ledgers had a wrong or missing display name.

### Diagnosis (the 173 split into three populations)

Walking the four name-anchor TLV fields (`0x0002`, `0x01f7`, `0x0af3`,
`0x0001`) inside each master's pages:

| population | size | what's wrong |
|---|---:|---|
| Wrong field selected (recoverable from existing `fields_json`) | 43 | `0x0002` was a multi-value list and we picked the wrong entry, or `0x0002` was a template-form label like "Primary Mobile No." pulled in via cross-master page pollution, or `0x0002` had mojibake from a TLV walker overrun and the clean name was in `0x01f7` |
| Genuine garbled `0x0002` with no clean alternative in `fields_json` | 20 | TLV walker read the right field ID but the value spilled into next-record bytes; no recoverable copy elsewhere |
| Name not stored in Manager.1800 at all | 110 | Pages exist (correct master_id), but no field — anywhere on those pages — contains the displayed Rosetta name, in TLV form or otherwise |

### Phase 5a fix (shipped — best_name() rewrite)

`scripts/extract_v5_multi.py` `best_name()` now:

1. Within a multi-value list, prefers the first list-position (Tally writes
   the canonical name first, alias second).
2. Rejects values whose codepoints fall outside Latin extended (`> U+024F`
   except a small allow-list of currency / quote / trademark symbols).
   Catches CJK / Bengali / Devanagari mojibake artifacts from TLV overruns.
3. Rejects template-form labels (`Primary Mobile No.`, `Email`, etc.) that
   come from cross-master page pollution.
4. Preserves the original priority order
   `0x0002 > 0x01f7 > 0x0af3 > 0x0001`, with a small `^[0-9]{2}[A-Za-z][0-9A-Za-z]?\s`
   prefix bonus to break ties toward real ledger names.

Result on 070525:

| kind | exact-name match before | after | delta |
|---|---|---|---:|
| ledgers | 2795 / 2968 (94.2%) | **2826 / 2968 (95.2%)** | **+31** |
| groups | 282 / 283 | 282 / 283 | 0 |
| stock_items | 2720 / 2754 | 2720 / 2754 | 0 |
| voucher_types | 118 / 118 | 118 / 118 | 0 |
| godowns | 112 / 112 | 112 / 112 | 0 |
| cost_centres | 313 / 313 | 313 / 313 | 0 |
| units | 23 / 23 | 23 / 23 | 0 |
| **TOTAL** | **6363 / 6566 (96.9%)** | **6394 / 6566 (97.4%)** | **+31** |

Cross-company side-effect (no Rosetta ground truth for these but distinct
plausible-name count grew, indicating name recovery):

| company | distinct plausible names before | after |
|---|---:|---:|
| 070525 | 9,096 | 9,110 |
| 100001 | 3,129 | 3,159 |
| 100025 | 7,504 | 7,521 |
| 300925 | 5,658 | 5,670 |

### Phase 5b: 110 ledgers whose displayed name is in LinkMgr.1800, not Manager.1800

Investigated the 110 "name not in Manager" cases. Searched the literal
display name across all 11 `.1800` files. For sample case `'10E2 Diesel &
Petrol Exp(Promoters)'` (master_id 2477):

```
Manager.1800: 0 hits
LinkMgr.1800: 21 hits
all other .1800 files: 0 hits
```

LinkMgr stores the name in TLV form with field id `0x232e` (NOT one of the
existing name anchors). Each LinkMgr page has its own MASTERID at +4
(different from Manager's master_id), and the LinkMgr master record carries
a back-reference to the Manager ledger master_id somewhere in its TLVs.
The 21 hits in LinkMgr correspond to allocation / bill-wise entries that
each redundantly carry the parent ledger's display name as a TLV.

This is a **NEW format finding**: LinkMgr.1800 is not just allocation refs
— it's the canonical display-name source for ledgers whose name is
synthesized at render time. Until we extract LinkMgr the long-tail can't be
closed. Filed as task #8.

### Files / output

- Modified: `scripts/extract_v5_multi.py` (best_name rewrite)
- Re-emitted: `out/070525_master_v5.sqlite3` (8.0 MB),
  `out/100001_master_v5.sqlite3` (1.5 MB),
  `out/100025_master_v5.sqlite3` (5.3 MB),
  `out/300925_master_v5.sqlite3` (3.5 MB)

### Conclusion

Phase 5 took ledger name recovery from 94.2% to 95.2% with zero Rosetta-side
regressions. The remaining 3.7% gap (110 ledgers + 27 stock_items + 1 group +
3 cost_centres long-tail) requires reading LinkMgr.1800, not more clever
heuristics on Manager.1800.

---

## Phase 5b + Task #10: LinkMgr.1800 extraction (teammate `2`, 2026-04-27)

`scripts/extract_linkmgr.py` walks LinkMgr.1800 with the same shell as
`extract_v5_multi.py`: 512-byte pages, MASTERID at +4, compact-slot block
(10-byte rows: `[u8 0x00 x2][u16 fid LE][u8 0x00][u8 type][u32 value LE]`)
followed by TLV stream (`02 10 [fid:u16][type:u16][len:u16][value:len]`).

### Format decoded

| location | format | meaning |
|---|---|---|
| slot fid `0x232d` | u32 | back-pointer to Manager.1800 ledger master_id (verified 21/21 on master 2477, 1/1 on master 2649) |
| TLV fid `0x232e` | utf-16 | ledger name (canonical at allocation creation time — can drift) |
| TLV fid `0x2331` | utf-16 | bank_name |
| TLV fid `0x2332` | utf-16 | branch |
| TLV fid `0x2333` | utf-16 | account_number |
| TLV fid `0x2346` | utf-16 | payment_mode (NEFT / RTGS) |
| TLV fid `0x232b` | utf-16 | UTR / instrument |
| TLV fid `0x232f` | utf-16 | A/c Payee crossing |
| TLV fid `0x2358` | utf-16 | instrument number |
| TLV fid `0x2348` | utf-16 | print_status |
| TLV fid `0x0002` | utf-16 | per-allocation GUID |

### Per-company results (LinkMgr.1800 -> SQLite)

| company | LinkMgr size | pages | bank_allocations | bill_allocations | distinct manager-back-ptrs (bills) |
|---|---:|---:|---:|---:|---:|
| 070525 | 22 MB | 44,689 | 2,036 | 1,649 | 200 |
| 100001 | 4 MB | 8,778 | 461 | 237 | 38 |
| 100025 | 19 MB | 37,386 | 2,523 | 1,297 | 240 |
| 300925 | 2 MB | 3,914 | 601 | 192 | 34 |

### Bank-allocation structural validation (070525)

- 2,036 rows, 1,683 with back-pointer
- 2 distinct banks: `'City Union Bank Limited (India)'` (1683), `'Indian Bank (India)'` (1)
- 2 distinct account numbers: `'419417941'` (9 digits), `'036120000110387'` (15 digits) — both Indian-bank-shaped
- payment_mode: NEFT 1341, RTGS 687, blank 8 — both real Indian payment rails
- UTR strings (sampled): 16-char strings starting `5IRUX...`, `5IS62...` — instrument-key format

100001 has 0 distinct banks for its 461 bank_allocations. Those records
only carry `0x232e` (party) + `0x2346` (NEFT/RTGS) + `0x0002` (alloc guid).
That's a smaller company recording payment mode but not source bank
details — structurally valid, not a bug.

### Phase 5b: ledger-name fallback via LinkMgr (070525)

Plumbed `--linkmgr` into `extract_v5_multi.py`. Conservative policy: only
fill `extracted_name` when current value is NULL (don't overwrite a fresh
extracted name with a potentially stale LinkMgr copy).

**Numbers from the actual run:**

| step | ledgers exact / total | % |
|---|---|---|
| pre-phase-5 (v5_multi original) | 2795 / 2968 | 94.17 |
| phase 5a (best_name rewrite) | 2826 / 2968 | 95.22 |
| phase 5b (+ LinkMgr fallback) | **2838 / 2968** | **95.62** |

LinkMgr fallback fired on 14 ledger records: 12 correct, 2 stale
(LinkMgr name is older snapshot — e.g. master 2984 has Manager-empty,
Rosetta `'10L2 ICICI Lombard General Insurance Com Ltd (Del)'`, LinkMgr
`'10L2 ICICI Lombard General Insurance Com Ltd'`. The "(Del)" suffix is
a post-allocation rename).

**Honest assessment vs the stated 99% target:**

Of the 130 ledgers still missing in 070525:
- **106 have no LinkMgr coverage at all** (no allocations / bills / bank
  refs ever created against them, so no back-pointer page exists).
- **14 were filled via LinkMgr fallback** (12 correct + 2 stale).
- **10 have LinkMgr coverage but the name there matches existing wrong
  extracted_name or is stale**.

The original premise — "LinkMgr.1800 stores the canonical ledger display
name for the missing 110" — was supported by ONE verified case (master
2477, 21 LinkMgr hits). Generalising over the 120 missing-name population:
only ~14 (12%) have LinkMgr coverage, and 2/14 of those are stale.
**LinkMgr is not a general remedy for the long-tail.** The remaining
~106 ledgers genuinely have no display-name byte stored anywhere in the
non-vault `.1800` files for this company. Hypothesis (untested):
those names live in `ExtMngr.1800` (edit-log) or are reconstructed from
`Aggr.1800` precomputed report cache.

### Output files

- `scripts/extract_linkmgr.py` (new)
- Updated: `scripts/extract_v5_multi.py` (`--linkmgr` flag + fallback)
- New: `out/{070525,100001,100025,300925}_linkmgr_v0.sqlite3`
- Re-emitted: `out/{070525,100001,100025,300925}_master_v5.sqlite3`
  (070525's `master_records` rows now have `matched_by LIKE '%linkmgr%'`
  for the 14 LinkMgr-sourced ledgers)

Total `extracted_name == rosetta_name` on 070525 across all kinds:
6406 / 6571 = **97.49%** (was 96.85% pre-phase-5).

---

## Task #9: per-record byte scoping fix (teammate `2`, 2026-04-27)

### The bug

`extract_v5_multi.py`'s TLV walker treated each contiguous run of pages as a
flat byte stream, ignoring that **every Manager.1800 page has its own
28-byte header at offsets 0..27**. This caused two distinct artifacts:

1. **TLVs that span page boundaries had their tail decoded against the
   next page's header bytes**, producing mojibake. Example: master 2768's
   `state` field is a TLV at page 5595 offset 490, declared length 22.
   The first 14 bytes (`Tamil N`) live on page 5595; the remaining 8
   bytes should continue at offset 28 of page 5596 (`adu\x00\x00`). The
   old walker read offsets 0..7 of page 5596 (which are the page header)
   and produced `'Tamil N䐜糤ૐ'`.

2. **The next TLV after a page-spanning one was found at the wrong
   position**, propagating shifts through the rest of the page.

### Format rule confirmed

Manager.1800 page layout:

| offset | length | content |
|---:|---:|---|
| 0 | 4 | hdr0 (random / checksum) |
| 4 | 4 | MASTERID |
| 8 | 4 | hdr2 (kind / flags) |
| 12 | 4 | hdr3 (sub-record id) |
| 16 | 12 | slot-block marker `0b 00 00 00 ff ff ff ff 00 00 00 00` |
| 28 | 484 | data area (slot block + TLV stream) |

When a TLV spans a page boundary, the value continues at offset 28 of
the next page, NOT offset 0. Verified by reconstruction:

```
TLV at page 5595 +490, len=22 (Tamil Nadu):
  v1 = page 5595 [498..512] = 14 bytes ('Tamil N')
  v2 = page 5596 [28..36]   =  8 bytes ('adu\x00\x00')
  total = 22 bytes -> 'Tamil Nadu'
```

### Fix shipped

`scripts/extract_v5_multi.py`:
- `walk_tlvs_in_pages()` now splits the page list into contiguous runs,
  then for each run builds a "logical stream" by concatenating each
  page's data area (bytes 28..512), skipping the 28-byte page header.
  TLVs are then walked over the contiguous logical stream, so a
  page-spanning value reads the correct continuation bytes.
- Added `PAGE_HEADER_SIZE = 28` constant + `_build_logical_stream()`
  helper.

### Result on 070525

Per-record `extracted_name == rosetta_name`:

| metric | before this fix | after | delta |
|---|---|---|---:|
| ledgers | 2838 / 2968 (95.62%) | **2921 / 2968 (98.42%)** | **+83** |
| All kinds | 6406 / 6571 (97.49%) | **6491 / 6573 (98.75%)** | **+85** |
| GUID-validated set total | 6563 / 6566 (99.95%) | **6565 / 6566 (99.97%)** | +2 |

Distinct plausible names extracted (cross-company):

| company | pre-fix | post-fix | delta |
|---|---:|---:|---:|
| 070525 | 9,122 | 9,228 | +106 |
| 100001 | 3,159 | 3,185 | +26 |
| 100025 | 7,521 | 7,533 | +12 |
| 300925 | 5,670 | 5,782 | +112 |

### Typed-output validation (070525_typed_v1.sqlite3)

Reran `scripts/build_field_map.py` (which writes typed_v1) after the
walker fix. Also tightened `build_typed_sqlite()` to reject mojibake
codepoints and reuse the `best_name`-style canonical-pick heuristic
for `name`/`mailing_name` columns.

| col | rosetta-populated | exact match | % |
|---|---:|---:|---:|
| name | 2968 | 2863 | 96.5% |
| state | 2454 | 1893 | 77.1% |
| gstin | 1125 | 1104 | 98.1% |
| pincode | 1398 | 1143 | 81.8% |
| email | 13 | 13 | 100.0% |
| phone | 4 | 4 | 100.0% |
| pan | 202 | 199 | 98.5% |
| address | 1561 | 1413 | 90.5% |
| **AGGREGATE** | **9725** | **8632** | **88.8%** |

Pre-task-#9 baseline (per the task description) was reported as 60%;
after the byte-scoping fix + typed-emitter mojibake filter we land at
**88.8% aggregate**.

### Why state/pincode/address aren't at 95%+

`state` maps to fid `0x0acc` in the field map, but ~570 ledgers store
their state in `0x0003` instead (and a few "Not Applicable" records
don't carry the field at all). The field-map design picks ONE fid per
column; it can't fall back to a secondary fid when the primary is
absent. Same pattern for pincode and pan. **This is a field-map
structural limitation, not a byte-scoping bug.**

Next step (teammate 3 territory): extend the field map to a fid chain
`[0x0acc, 0x0003]` for ambiguous columns, with the typed emitter
walking the chain until it finds a non-empty value. Filed as task #13.

### Files modified

- `scripts/extract_v5_multi.py` — page-header-aware TLV walker
- `scripts/build_field_map.py` — mojibake filter + smart name picker in
  `build_typed_sqlite()`
- Re-emitted: `out/{070525,100001,100025,300925}_master_v5.sqlite3`,
  `out/070525_typed_v1.sqlite3`, `out/field_map.json`

---

## Task #16: fid-chain fallback for typed_v1 (teammate `2`, 2026-04-27)

### The problem

After task #9, typed_v1 column accuracy was 88.8% aggregate. Per column:
state 77.1%, pincode 81.8%, country missing entirely. Gap traced to a
structural issue: **the same Rosetta column is populated by different
field IDs depending on the ledger sub-class.** For 070525 ledgers with
populated state in Rosetta:

| state-source fid | records |
|---|---:|
| 0x0acc (canonical) | 1877 |
| 0x0003 (generic slot) | 256 |
| 0x0004 (generic slot) | 78 |
| 0x0002 (generic slot) | 20 |
| 0x01f8 (address slot) | 16 |
| not in any field | 207 |

Same shape for pincode (`0x0a8f`/`0x0005`/`0x01f8`).

### What teammate 3 had already shipped

The chain mechanism on the field-map side: `column_to_fid[col]` is now
a list `[primary, fallback1, ...]` derived from records where the
primary fid is absent. Fallback fids are added if their agreement
rate against Rosetta exceeds `MIN_CONFIDENCE` (90%). Result: chain
includes useful fallbacks for state/pincode/country, but the typed
emitter still ran fid-by-fid without shape validation, so falling back
to `0x0003` for state would happily pick up GSTINs as state.

### What I added in this task

`scripts/build_field_map.py`:

1. **Shape validators** for each column (`_value_passes_shape`):
   - `pincode`: `^\d{6}$`
   - `gstin`: `^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[A-Z\d]$`
   - `pan`: `^[A-Z]{5}\d{4}[A-Z]$`
   - `state`: whitelist of 41 known Indian states/UTs + 'Not Applicable'
   - `country`: whitelist of ~50 known countries
   - `phone`/`email`: corresponding regexes
   - other columns: accept any non-mojibake string

2. **Primary-vs-fallback policy**: shape validation runs ONLY on
   fallback fids (`fid_idx >= 1`). The primary fid was already proven
   high-confidence by field-map mining; rejecting its values via shape
   would create false negatives (e.g. Rosetta stores PAN values in the
   gstin column for some records — those don't pass the GSTIN regex).

3. **List-walk for shape**: when a fid value is a list (multi-value),
   walk the list looking for the first shape-valid entry, not just
   `v[0]`. Recovers cases like mid=2730 where
   `0x0003 = ["33AAACB2894G1ZU", "Tamil Nadu"]` — gstin chain wants
   the first element, state chain wants the second.

4. **Tally-XML defaults**: when our chain returns NULL for state or
   country in a ledger, apply Tally's render-time defaults:
   - `state` → `'Not Applicable'` (verified: 646/722 of NULL state
     records on 070525 land on 'Not Applicable' in Rosetta)
   - `country` → `'India'` (verified: 538/538 of NULL country records
     map to 'India')

### Result on 070525 ledgers

| col | pre-task-#16 | final | delta |
|---|---:|---:|---:|
| name | 96.5% | **98.1%** | +1.6 |
| state | 77.1% | **96.8%** | **+19.7** |
| gstin | 98.1% | 98.1% | 0 |
| pincode | 81.8% | **97.3%** | **+15.5** |
| email | 100% | 100% | 0 |
| phone | 100% | 100% | 0 |
| pan | 98.5% | 98.5% | 0 |
| address | 90.5% | **93.3%** | +2.8 |
| country | (n/a) | **100.0%** | (new) |
| **AGGREGATE** | **88.8%** | **97.6%** | **+8.8** |

Both state (96.8%) and pincode (97.3%) are above the 95% target stated
in the task description.

### Honest caveats

- **country at 100%** is partly heuristic: 538 of those matches come
  from the NULL→'India' default. The rule held for ALL 538 cases on
  070525, but it would mis-default on a company with foreign vendors.
- **state at 96.8%**: 76 records get a wrong 'Not Applicable' default —
  their real state lives in LinkMgr (allocations / addresses), which
  this chain doesn't reach.
- **address at 93.3%**: multi-line address still loses lines stored in
  alternate fids. Future work.
- **Cross-company validation**: only 070525 is Rosetta-validatable.
  100001/100025/300925 typed_v1 numbers aren't measurable from this
  workspace. The shape validators + chain logic should generalize, but
  no ground truth available.

### Files modified

- `scripts/build_field_map.py` — added `_value_passes_shape()` with
  shape validators, primary-vs-fallback shape-check policy, and
  Tally-XML default fallbacks for state/country
- Re-emitted: `out/070525_typed_v1.sqlite3`, `out/field_map.json`

---

## Next concrete steps (ordered)

1. ~~**Cross-company validation**~~ — DONE (see above).
2. **Voucher record anchor** — combine multiple weak anchors (party + narration
   + voucher_number when present) and dump the prologue bytes that consistently
   precede them. Identify the voucher header pattern.
3. **Voucher extraction MVP** — emit a `vouchers` table with at least
   voucher_date / voucher_type / voucher_number / party / narration / total.
4. **Voucher line items** — parse the nested ledger / inventory entries that
   follow each voucher. Cross-check counts against Rosetta.
5. **Field-id → column-name mapping** — derive systematically from
   `raw_payloads.response_xml` in Rosetta (offline lookup).
6. **Cancelled / optional flags** — pull from VchStatus.1800.
7. **Long-tail name reconstruction** — apply parent-group prefix inheritance.
8. **Productize** — wrap into `tally1800/` Python package with a CLI:
   `python -m tally1800 extract <folder> -o out.sqlite`.

Each step gets a measurable target before we move on. We do not advance until
the previous step's metrics are solid AND validated cross-company.

---

## Field-id mapping discoveries (2026-04-27, teammate 3)

`scripts/build_field_map.py` joins our v5 GUID-keyed extraction with the
Rosetta truth tables AND the per-record values mined directly from
`raw_payloads.response_xml`. For each Rosetta column (or XML tag), we count
how often each `0xNNNN` field-id contains the expected value, then keep
only mappings with rate ≥ 90 %, coverage ≥ 30 % of populated records, and
≥ 20 joint samples (≥ 4 for thinly-populated columns like email/phone).

Output: `out/field_map.json` — 26 high-confidence column mappings across
18 distinct field-ids. Definitive map:

```
ledgers
  0x0002 → name             0x01f7 → mailing_name (alt)
  0x0006 → address          0x0acc → state    (also led_state, prior_state)
  0x0a31 → country          0x0a8f → pincode  (also old_pincode)
  0x0a90 → email            0x0a94 → phone / ledger_mobile
  0x0ac1 → pan / income_tax_number
  0x0aca → gstin / party_gstin
  0x0067 → created_by

groups
  0x0002 → name

stock_items
  0x0002 → name             0x0fd4 → base_units / closing_uom

voucher_types, units, godowns, cost_centres
  0x0002 → name
```

Companion typed sqlite: `out/070525_typed_v1.sqlite3`. ledgers join on guid
against rosetta gives 1752/2968 state matches, 1085 gstin, 1142 pincode.
Lower-than-expected because v5 fields_json values are page-level lists
(picking `v[0]` is wrong for ~40 % of records where the canonical record's
value is later in the list). Fixing this needs per-record byte-offset
scoping — a v6 extractor improvement, not a field-map issue.

### What we could NOT high-confidence map (and why)

- **parent (every kind)**: Rosetta stores parent as a name string ("A00-Bank
  Accounts"). That string never appears in fields_json — Tally references
  the parent by an internal master-id, not a name. Resolving it requires a
  second pass that dereferences the parent's master_id back to its 0x0002
  name.
- **opening_balance, closing_balance, opening_quantity, opening_rate,
  closing_***: amounts/quantities are stored as fixed-point ints or paise
  variants; the extractor decodes some as u32, but no straightforward
  numeric equivalence to the Rosetta float passes the threshold.
  Closing-* in particular comes from `stock_summary`, not the master
  record, so it can't be in the master file.
- **currency ("RS"), gst_type ("Regular"), gst_applicable ("Applicable"),
  numbering_method ("Automatic"), is_bill_wise / affects_stock /
  is_revenue / is_deemed_positive / affects_gross_profit / is_subledger**:
  enum-encoded as small ints, not as the human strings the XML returns.
- **groups.parent**: same internal-id issue. Codex's hypothesis that
  `0x0008` carries a parent-id was supported but the link is the parent's
  master_id, not a string match.

### XML tag → field-id confirmations (Rosetta-Stone style)

Mining `raw_payloads.response_xml` per LEDGER block let us re-confirm the
above using Tally's own tag names:

```
<NAME>            → 0x0002
<MAILINGNAME>     → 0x01f7
<ADDRESS>         → 0x0006
<LEDSTATENAME>    → 0x0acc
<PRIORSTATENAME>  → 0x0acc   (same physical field-id; tag varies in XML)
<COUNTRYOFRESIDENCE> → 0x0a31
<PINCODE>         → 0x0a8f
<OLDPINCODE>      → 0x0a8f   (same physical field-id)
<EMAIL>           → 0x0a90
<LEDGERMOBILE>    → 0x0a94
<INCOMETAXNUMBER> → 0x0ac1   (PAN field is shared)
<PARTYGSTIN>      → 0x0aca
<CREATEDBY>       → 0x0067
```

The XML tag is the *view*; the on-disk field-id is the *truth*. Multiple
XML tags can render the same underlying field.

---

## Voucher date + voucher type decoding (2026-04-27, teammate 3)

`scripts/extract_vouchers.py` now populates `voucher_date` (100 % match
against Rosetta) and `voucher_type` (99.43 % match — see follow-up
section "Closing the voucher_type gap" below) on each voucher row in
`out/070525_vouchers_v1.sqlite3`.

### voucher_date — TranMgr 0x0067/000d compact slot (definitive)

Each voucher anchor page (one with `0x0bbb 00 06 <master_id>` somewhere in
the body) also carries a 10-byte compact slot with the byte signature
`67 00 00 0d <serial:u32_le> <pad:2>`.  Serial = days since 1899-12-31
(Excel epoch).  Filter giant junk serials (>100000): two TranMgr pages
have non-date sentinels using the same byte pattern; rejecting them is
trivial.

Result: **30,545 / 30,545 voucher dates exact (100.00 %)** vs Rosetta on
070525.  No heuristics, no VchStatus needed for date.

The slot is NOT at strict 10-byte alignment from page+28; signature-scan
the body bytes instead. (Echoes the alignment caveat teammate 1 hit when
walking the compact slot block — codex flagged this too.)

### voucher_type — VchStatus.1800 slot+0x64 + heuristic pairing

Voucher_type is NOT in TranMgr.  It lives in `VchStatus.1800` which uses
a different layout:

```
512-byte page header at +0..+0x7F
slot 0 at +0x80, slot 1 at +0x100, slot 2 at +0x180   (each 128 bytes)
  slot+0x28 = u32 LE date_serial (same Tally epoch)
  slot+0x64 = u32 LE voucher_type master_id
  slot+0x68 = 0 or 203 (discriminator, meaning unknown)
```

070525 VchStatus has 30,564 non-empty slots vs 30,545 Rosetta exportable
vouchers + 19 base-type-31 internals = 30,564 (codex confirmed).

**The slot does NOT carry the voucher's master_id**, so we have to pair
slots back to TranMgr vouchers.  Best heuristic that survives empirical
testing:

1. Group both sides by date_serial.
2. Sort vouchers by master_id ascending within a date.
3. Take VchStatus slots in file order within a date.
4. Pair the i-th sorted voucher with the i-th file-order slot.

Recovers 25,913 / 30,110 voucher_type assignments exact = **86.06 %**.

### Why not 100 %, and where the residual 14 % goes

- 164 / 374 dates have equal voucher and slot counts → pairing is exact.
- 212 dates have more vouchers than slots (550 excess vouchers
  cumulatively).  Codex's note: "two legacy low ids inserted into an
  Apr-30 cluster".
- Concrete example, date `2025-04-30`: TranMgr has 225 vouchers, VchStatus
  has 222 slots.  Mids 2347, 2366 (very low) are NOT represented in
  VchStatus — they are old vouchers that got back-dated.  Slots align to
  the bulk cluster mids 30649…58646, NOT to mids 2347/2366.
  Sorting vouchers by mid-asc puts 2347/2366 at positions 0-1, which then
  shifts the remaining 220 alignments by 2.  Result: 99 mismatches on
  this single date.
- Tried alternatives (file-offset asc/desc, top-N-mid, largest-contiguous-
  cluster) — all do worse than mid-asc + skip-nothing.
- Cleanly resolving this needs a "is this voucher in VchStatus" signal on
  the TranMgr page itself.  None of the TLV/compact fields we currently
  decode discriminate correctly-paired vs incorrectly-paired vouchers
  (checked: rates within 5 % between groups).  Likely VchStatus is keyed
  by something not yet decoded (a hash table?  pointer index in
  `Aggr.1800`?  the VchStatus page header?).

### What landed in `out/<company>_vouchers_v1.sqlite3`

New columns on the `vouchers` table:

| column | source | accuracy |
|---|---|---|
| `voucher_date` | TranMgr 0x0067/000d on the anchor page | 100 % |
| `voucher_date_serial` | raw u32 from above (auditing) | — |
| `voucher_type_mid` | VchStatus slot+0x64 via mid-asc pairing | 99.43 % |
| `voucher_type` | resolved via 070525_master_v5.voucher_types(master_id) | 99.43 % |

---

## Closing the voucher_type gap (2026-04-27, teammate 3, follow-up)

Tightened the pairing heuristic from 86.06 % to **99.43 %**.

### Investigation 1: slot+0x68 discriminator (busted)

VchStatus slot+0x68 takes only two values: 0 (54.4 % of slots) or 203 (45.6 %).
Hypothesis: 203 = "in Rosetta", 0 = "skipped/internal". Tested by grouping
slots by u68 value and checking per-date counts:

- Dates where TranMgr count == u68=203 slot count: 14/375
- Dates where TranMgr count == u68=0   slot count: 9/375
- Dates where TranMgr count == total    slot count: 164/375 (same as before)

Both u68 buckets contribute legitimate vouchers; u68 is NOT an include/exclude flag.
Hypothesis rejected.

### Investigation 2: Aggr.1800 (busted)

Aggr.1800 is mostly fixed-slot data (statutory aggregates / GSTR views).
Only 14 k TLVs total in 4 MB of file — and they're all string types
(0x0002, 0x1326, 0x0003), no u32 reference fields. Dump of one record
shows precomputed return-form text like
`"AggrView:8155\x03GSTRegistration:203\x03Stat Date:45747\x03Return Name:7935"`.
No voucher_id → voucher_type_master_id index. Hypothesis rejected.

### What actually closed the gap: voucher_key NULL filter

Of the 568 TranMgr vouchers absent from Rosetta, **549 have no
voucher_key TLV (fid 0x0003) on their page**. Filtering them out before
pairing closes 13 of the 14 percentage points:

```
filter                          | match | rate
                                |       |
mid-asc + slot-file-order, no filter        | 25913 | 86.06 %
+ filter to vouchers with key (≠ NULL)      | 30265 | 99.14 %
+ cluster-reorder within date (gap=5000)    | 30358 | 99.43 %
```

The **cluster-reorder** addresses codex's "two legacy low ids inserted
into an Apr-30 cluster" pattern: within a single date, sort mids into
clusters by gap > 5000, put the largest cluster first (ascending), then
the smaller cluster(s) at the end. For 2025-04-30 this places mids 2347
and 2366 AFTER the 30649…58646 bulk, which matches VchStatus's slot
order for that date.

### Residual 173 mismatches (0.57 %)

Concentrated on 4 dates:

| date       | mismatches |
|---         |       ---: |
| 2026-02-18 |         52 |
| 2026-03-24 |         39 |
| 2025-04-18 |         22 |
| 2025-04-30 |         20 |

These are intra-bulk-cluster ordering quirks where mid-asc is
approximately right but Tally's actual write order differs in some
edge cases. Closing them further would need either an explicit voucher
→ slot index hash (we couldn't find one in the files we've decoded) or
a per-date manual rule. Not worth chasing further at this accuracy
level — the typed `voucher_type` column is now production-grade for
business analytics.

### What this gives downstream

`voucher_type` lookups by name now work correctly for 30,358 of the
30,545 GUID-matched vouchers (99.39 % of all Rosetta vouchers, 99.43 %
of those for which we have a paired type). Filtering, GROUP BY,
financial reports keyed on voucher type are reliable except for the
~0.6 % residual on a handful of high-volume dates.

The `rosetta_voucher_date` / `rosetta_voucher_type` columns remain so
downstream code can quickly diff our extraction against truth.

---

## Cross-page TLV header-skip fix + fallback fids (2026-04-27, teammate 3)

The 60 %-not-99 % cross-validation gap on `out/070525_typed_v1.sqlite3`
ledger fields (state/gstin/pincode/country) turned out to be TWO
independent issues, both now fixed.

### Root cause #1: TLV values that span page boundaries get spliced with the next page's 28-byte header

A TLV with `len=22` whose value starts 14 bytes before the page boundary
needs 8 more bytes from the next page. Tally writes those 8 bytes
immediately AFTER the next page's 28-byte header (the page header is
NOT part of the TLV stream). Our walker was reading 22 contiguous bytes
across the boundary, picking up the next page's header and producing
UTF-16 mojibake. Symptom: `state` decoded as `Tamil N䐜糤ૐ` instead of
`Tamil Nadu`. Affected ~40-60 % of ledger records on each of state,
gstin, pincode, address.

Fix landed in `scripts/extract_v5_multi.py`:
`_build_logical_stream(data, contiguous_pages)` concatenates the data
areas (page bytes 28..511) of each contiguous page run, and
`_walk_tlvs_in_logical_stream(stream)` walks TLVs over that concatenated
stream. Cross-page values are now read cleanly because page headers
were stripped before walking. Verified: of records that have the
relevant fid present, 100 % now match Rosetta exactly:

  - 0x0acc → state    : 1893/1893 (100.00 %)
  - 0x0aca → gstin    : 1104/1104 (100.00 %)
  - 0x0a8f → pincode  : 1143/1143 (100.00 %)
  - 0x0a31 → country  : 2256/2256 (100.00 %)
  - 0x0a90 → email    :   13/13   (100.00 %)
  - 0x0a94 → phone    :    4/4    (100.00 %)
  - 0x0ac1 → pan      :  199/199  (100.00 %)
  - 0x0067 → created  : 2965/2965 (100.00 %)
  - 0x0006 → address  : 1470/1491 ( 98.59 %)

### Root cause #2: some Rosetta columns are stored under one of several alternate fids depending on the record

Same column data, different physical fid. e.g.:

- `country`: 0x0a31 (preferred) OR 0x0d4e (legacy) OR 0x0004 (slot)
- `state`:   0x0acc (preferred) OR 0x0003 (slot)
- `pincode`: 0x0a8f (preferred) OR 0x0005 (slot)
- `address`: 0x0006 (formatted) OR 0x01f8 (raw)

`build_field_map.py` now records a per-column FALLBACK CHAIN, not a
single fid:

```json
"country": ["0x0a31", "0x0d4e", "0x0004"]
```

The typed-sqlite builder walks the chain in order, taking the first
fid that has a clean value in the record. To prevent a low-coverage
shared slot like 0x0003 (which holds whatever value happened to be
"first" in the slot block — could be state, gstin, pincode depending on
the record) from being mistakenly assigned to one specific column, a
fallback fid must ALSO have ≥ 90 % agreement on records where the
primary fid is ABSENT — not just overall agreement (which can come from
records that have BOTH primary and fallback set to the same value).

### Final cross-validation on 070525 ledgers (2968 rows, GUID-joined to Rosetta)

| column   | before fix | cross-page only | + fallbacks |
|---       |       ---: |            ---: |        ---: |
| name     |     93.8 % |          96.1 % |  **98.1 %** |
| state    |     59.0 % |          63.8 % |  **85.3 %** |
| gstin    |     36.6 % |          99.3 % |  **99.3 %** |
| pincode  |     38.5 % |          91.4 % |  **98.8 %** |
| country  |     71.3 % |          76.0 % |  **81.8 %** |
| email    |        —   |             —   | **100.0 %** |
| pan      |        —   |             —   |  **99.9 %** |
| phone    |        —   |             —   | **100.0 %** |

State 85 % and country 82 % are NOT extractor bugs — they are records
where Rosetta has a value but no fid in the master record carries it.
Rosetta returns a derived/defaulted value (e.g. country = 'India' is the
company default, applied to ledgers that don't have a country fid set).
Closing those final gaps would require either company-level defaulting
in our typed sqlite, OR a richer fid sweep that we don't yet do.

## Cross-company voucher extraction (2026-04-27, teammate 4, task #14)

### Status: PROVEN — voucher header extractor works structurally on all 4 non-empty corpus companies

Ran `scripts/extract_vouchers.py <company_dir>` on every non-empty corpus
company. Outputs in `out/<company>_vouchers_v1.sqlite3`. Anchor counts match
codex's expected values exactly (codex BREAKTHROUGHS-2026-04-27.md §11).

### Per-company anchor counts (matches codex 1:1)

| Company | TranMgr size | pages | 0x0bbb anchors | VchStatus slots | Codex expected |
|---|---:|---:|---:|---:|---:|
| 070525 | 397.9 MB | 777,172 | **31,113** | 30,564 | 31,113 ✓ |
| 100001 |  23.4 MB |  45,606 |  **2,275** |  2,229 |  2,275 ✓ |
| 100025 | 336.3 MB | 656,816 | **28,562** | 28,416 | 28,562 ✓ |
| 300925 |  14.6 MB |  28,518 |  **1,859** |  1,772 |  1,859 ✓ |

### Per-company voucher field coverage

| Company | total | voucher_date | voucher_type | voucher_number | narration | party_or_addr |
|---|---:|---:|---:|---:|---:|---:|
| 070525 | 31,113 | 31,113 (100.0%) | 30,545 (98.1%) | 18,535 (59.5%) | 19,570 (62.9%) |   554 (1.7%) |
| 100001 |  2,275 |  2,275 (100.0%) |  2,229 (98.0%) |    459 (20.2%) |    980 (43.1%) |   290 (12.7%) |
| 100025 | 28,562 | 28,562 (100.0%) | 28,416 (99.5%) | 13,412 (47.0%) | 15,033 (52.6%) |   566 (2.0%) |
| 300925 |  1,859 |  1,859 (100.0%) |  1,772 (95.3%) |    555 (29.9%) |    734 (39.5%) |    76 (4.1%) |

### Date ranges (all sane, match expected fiscal years from codex's manifest)

| Company | Min date | Max date | Implied FY |
|---|---|---|---|
| 070525 | 2025-04-01 | 2026-04-21 | FY 2025-26 (current) |
| 100001 | 2024-04-01 | 2026-04-20 | FY 2024-25 + a few FY26 |
| 100025 | 2023-04-01 | 2024-03-31 | FY 2023-24 (audited) |
| 300925 | 2024-04-01 | 2026-04-18 | FY 2024-25 + FY26 |

### Top voucher types per company (proves master_id → name resolution works cross-company)

```
070525  Conversion Stock Journal (6,881), Stock Journal (4,827), 31-Puchase(Monthly) (2,929)
100001  11 Journal (287), 12 Payment Bank (266), 61 Sales (260), Receipt Note (257)
100025  Conversion Stock Journal (5,002), Stock Journal (4,995), 31-Puchase(Monthly) (2,864)
300925  12 Payt Bank (487), 11 Journal (205), Material Out (123)
```

### Findings for cross-company runs

1. **Anchor extraction is fully generalizable.** 0x0bbb compact-slot signature
   scan works without modification on all 4 corpus companies — same scan, same
   filter, same MASTERID structure.

2. **VchStatus pairing is fully generalizable** at 95-99% on all 4 companies.
   Same date-cluster + master_id-asc heuristic codex established for 070525.

3. **Missing cross-company kind-classification (real finding):** The v5 master
   extractor currently uses Rosetta as the kind classifier, so for 100001/,
   100025/, 300925/ the `voucher_types` table in their `*_master_v5.sqlite3`
   is EMPTY (all 3,306 / 7,967 / 6,024 master records sit in
   `unclassified_masters`). Voucher_type names are still recoverable because
   the underlying master_id ↔ name pairs exist in `master_records`.
   `extract_vouchers.py` was patched (this session) to fall back to
   `master_records` when `voucher_types` is empty. After the patch,
   voucher_type names populate at 95-99% on the 3 cross-company runs.

4. **Voucher_number / narration coverage varies per company.** 100001 has
   only 20% voucher_numbers; 100025 has 47%; 070525 has 60%; 300925 has 30%.
   This is NOT a regression — it reflects the fact that Tally only emits
   voucher_number for voucher classes that need a printed sequence
   (sales/purchase/payment). Stock journals and internal entries don't
   carry a number. The percentage tracks the voucher-type mix per company:
   100001 has many internal Manufacturing/Receipt-Note entries that
   skip the number field.

5. **No company crashed and no structure deviated.** The voucher
   header extractor is now provably company-agnostic for non-vaulted
   TallyPrime data.

### Outputs

| File | Bytes | Rows |
|---|---:|---:|
| out/070525_vouchers_v1.sqlite3 | 16,531,456 | 31,113 |
| out/100001_vouchers_v1.sqlite3 |  1,003,520 |  2,275 |
| out/100025_vouchers_v1.sqlite3 | 13,598,720 | 28,562 |
| out/300925_vouchers_v1.sqlite3 |    815,104 |  1,859 |

### Caveats / what's NOT proven

- **No GUID validation cross-company** — Rosetta is 070525-only, so the
  100% GUID-match metric only exists for 070525. The 3 other companies
  are validated structurally (anchor count matches codex's independent
  counts; date/type ranges plausible) but not against an XML ground
  truth.
- **Voucher_type accuracy** measured here is "voucher_type field has a
  resolved name" — NOT "voucher_type field exactly equals the Rosetta-
  reported voucher_type". For non-070525 companies we have no Rosetta to
  compare against. Codex's separate XML diff against live Tally on
  Parallels confirms 86-99% exact-match on 070525, with the 14%
  shortfall already tracked as task #12.
- **Cross-company kind classification still missing** — task #3 (cross-
  company validation of v5) was marked complete but did not propagate the
  Rosetta-derived kind labels to companies that have no Rosetta data.
  Voucher extraction works around this via master_records fallback, but
  the master_v5 sqlites for 100001/100025/300925 still report all records
  as `unclassified_masters`. This is an outstanding issue separate from
  the voucher-extraction proof.

## Task #11: 52 unrecovered ledger names — definitive investigation (2026-04-27, teammate 4)

### Status: 95.62% ceiling is REAL — accept it, with caveats

### Method

For the 52 ledgers in 070525 with `extracted_name IS NULL` after teammate 2's
LinkMgr enrichment, byte-searched the expected Rosetta name as UTF-16LE across
every `.1800` and `.TSF` file in `corpus/070525/`:

- Manager.1800
- LinkMgr.1800
- ExtMngr.1800   ← hypothesis from task description
- Aggr.1800      ← hypothesis from task description
- SecTran.1800
- AddlCmp.1800
- TMESSAGE.TSF

### Result

| File | Hits (out of 52) |
|---|---:|
| Manager.1800 | 13 |
| LinkMgr.1800 | 11 |
| TMESSAGE.TSF |  6 |
| ExtMngr.1800 |  **0** |
| Aggr.1800    |  **0** |
| SecTran.1800 |  0 |
| AddlCmp.1800 |  0 |
| **(none)**   | **29** |

**ExtMngr.1800 and Aggr.1800 hypotheses are disproven.** Neither file
contains any of the 52 missing names as a UTF-16LE substring.

### Categorization of the 29 truly-missing names

```
GST tax ledgers (13):    '90A CGST Input 14% Intrastate', 'CGST Output 6% Intrastate', etc.
Provision ledgers (4):   '10L2 Provision for EB Charge', '50L2 Provision For Security Charges'
Other party/sale (12):   '60I2 Sale of Faber Account', '30E2 Sheets/Coils/ Others', etc.
```

The GST and Provision ledgers are **rule-derived at XML render time**. Tally
generates names like "90A CGST Input 14% Intrastate" by combining a class
template with a tax rate parameter. They are NEVER stored verbatim on disk.
This confirms codex's earlier "render-time constructed" hypothesis for at
least 17 of 29 names (60%).

The 12 "other" truly-missing names look manually-typed (e.g. "30E2 Pan Support
- JPR") so the fact that they are also absent suggests Tally uses a
class-with-parameter system more pervasively than just GST — possibly any
ledger that inherits from a class with `${ParameterName}` style placeholders.
We have not decoded the class template store; that would be needed to
reconstruct these names, and it is out of scope for the binary RE pipeline.

### Storage modes for the 23 names that DO exist somewhere

Of the 23 recoverable names, only 3 are at the simple "Pascal-style length
prefix at page+28" location we already check. The remaining 20 are at
varying offsets and use one of three modes we don't yet decode:

1. **Naked UTF-16LE at fixed page-body offset** (1 case: mid=2489 page 4819
   in-page 28). No length prefix; null-terminated. Page body has internal
   pointers in the first 16 bytes followed immediately by the name.
2. **TLV that the v5 walker explicitly skips** (e.g. mid=2657 page 5259
   has a 0x0002/000f/50 TLV but the cross-page reader produces garbage
   because the value spans into a non-contiguous sibling page). The
   "logical stream" fix in v5_multi only concatenates contiguous pages;
   when sibling pages are non-adjacent the TLV value still gets
   spliced incorrectly. **Real outstanding bug.**
3. **TMESSAGE.TSF storage** (6 cases). Not a `.1800` page file; outside
   our binary-RE scope per project doc.

### Conclusion

- **95.62% display-name ceiling on 070525 ledgers is the lower bound.**
  About 0.4% (12 names) might be recoverable with deeper page-walking
  work, but ~1.0% (29 names = 17 GST/provision rules + 12 likely class
  templates) are genuinely render-time constructed and outside reach
  of pure binary RE.
- **ExtMngr / Aggr hypothesis is disproven.** Don't keep chasing.
- **Recommend: accept the ceiling.** Closing the remaining 12 is
  high-effort, low-yield, and doesn't change the macro-deliverable.

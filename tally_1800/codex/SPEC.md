# Tally `.1800` Working Specification

This is a living spec. Every claim should eventually have either an official
source, an observed sample offset, or a reproducible test case.

## 1. Company Folder

A Tally company folder is identified by a numeric directory name and contains a
set of database files.

Known TallyPrime 3.0+ files:

| File | Role |
| --- | --- |
| `Company.1800` | Company profile, security controls, F11 features |
| `AddlCmp.1800` | Additional company information |
| `CmpSave.1800` | Saved copy/checkpoint of company metadata |
| `Manager.1800` | Accounting masters |
| `ExtMngr.1800` | Edit log and dependency recomputation info |
| `LinkMgr.1800` | Bill-wise/order-reference/link info |
| `TranMgr.1800` | Transactions/vouchers |
| `SecTran.1800` | Summary/exchange/export/return transaction info |
| `CfgRept.1800` | Saved report configurations |
| `Aggr.1800` | Aggregate/precalculated report values |
| `VchStatus.1800` | Voucher status |
| `StatStatus.1800` | Statutory status |
| `TSTATE.TSF` | Database state |
| `TMESSAGE.TSF` | Database update queue |
| `TINTMSG.TSF` | Intent/special-operation queue |
| `TACCESS.TSF` | Multi-client synchronization access |
| `TEXCL.TSF`, `TUPDATE.TSF` | Exclusive operations and rollback/update state |

## 2. Candidate Physical Model

Observed in `avinash-tally-live-2026-04-21/100025`:

- Important `.1800` files are exact multiples of 512 bytes.
- Page 0 appears to be a file metadata/root page and is often sparse.
- Many data pages start with a 4-byte value that behaves like a checksum or page
  marker.
- Offsets `+4`, `+8`, `+12`, `+16`, etc. contain little-endian page/header
  words.
- In `Manager.1800`, page number is commonly visible at offset `+4`.
- In `Company.1800`, page numbers commonly appear at offset `+12` after page 1.
- A simple zlib CRC32 over page bytes `[4:512]` did not match the first word, so
  the checksum is either a different CRC, seeded CRC, or another page identifier.

Current candidate structure:

```text
file
  page[0]  root/header metadata
  page[1..n]
    u32 checksum_or_marker
    u32 page_id_or_zero
    u32 page_kind_or_generation
    u32 page_no_or_link
    u32 record_family_or_flags
    u32 link_or_sentinel
    u32 unknown
    u32 payload_descriptor
    payload
```

Candidate page sizes:

- 512 bytes
- 1024 bytes
- 2048 bytes
- 4096 bytes
- 8192 bytes
- 16384 bytes

Old `.900` public research points to 512-byte pages, CRC at offset 0, fixed
metadata at offset 4, and possible page number at offset 12.

## 2.1 String Encoding

Many business strings are encoded as 2-byte-length-prefixed UTF-16LE:

```text
u16 length_bytes
u16 chars[(length_bytes / 2) - 1]
u16 null_terminator
```

Example from `Manager.1800` page 1:

```text
24 00
4c 00 2d 00 43 00 61 00 70 00 69 00 74 00 61 00
6c 00 20 00 41 00 63 00 63 00 6f 00 75 00 6e 00
74 00
00 00
```

`0x0024 = 36` bytes. Payload decodes to `L-Capital Account` plus the null
terminator. The leading `$` seen by naive UTF-16 string extraction is therefore
the length word misread as a printable character.

Observed examples:

- `Company.1800`: company name, address, GSTIN, email.
- `Manager.1800`: groups, ledgers, users, GUID-like IDs.
- `TranMgr.1800`: GSTINs, party names, addresses, vehicle numbers, narration.

## 2.2 Field String Tag

Semantic fields are commonly encoded as TLV frames:

```text
02 10 <field_id:u16_le> <type_code:u16> <length_bytes:u16_le> <value>
```

The string specialization is:

```text
02 10 <field_id:u16_le> 00 0f <length_bytes:u16_le> <utf16le payload>
```

Examples:

```text
02 10 fb 01 00 0f 4a 00 ... = field `0x01fb`, 74-byte GUID string
02 10 02 00 00 0f 24 00 ... = field `0x0002`, 36-byte master name
02 10 69 00 00 0f 10 00 ... = field `0x0069`, user name like `sathish`
```

Current field meaning hypotheses:

| Field ID | Observed meaning |
| --- | --- |
| `0x0002` | Master name in `Manager.1800`; also many names/descriptions elsewhere |
| `0x01fb` | GUID-like company/object id |
| `0x0069` | User/creator text in `Manager.1800` |
| `0x00ce` | Party/name-like field in early `TranMgr.1800` voucher pages |
| `0x00cf` | Address/location-like field in early `TranMgr.1800` voucher pages |
| `0x01fc` | GSTIN-like field in `TranMgr.1800` |

Candidate type-code map:

| Type code | Hypothesis | Confidence |
| --- | --- | --- |
| `00 0f` | UTF-16LE string, length includes trailing null | high |
| `00 0d` / `00 06` / `00 08` | u32-like | low/needs more proof |
| `00 0a` | i32-like | low/needs more proof |
| `00 09` | u8/bool-like | low/needs more proof |
| `00 0b` | f64-like | low/needs more proof |

Current scanner: `python3 -m tally1800 tlv-hist <file>`.

Compact numeric fields are also present outside the TLV stream:

```text
<field_id:u16_le> <type_code:u16> <value:u32_le> 00 00
```

Current scanner:

```bash
python3 -m tally1800 compact-scan TranMgr.1800 --field-id 0x0bbb --type-hex 0006
```

Important: a strict page-bounded interpretation (`page + 28`, then 10-byte
aligned slots until first TLV marker) is incomplete for `TranMgr.1800`.
Re-testing on `070525` found only 15,791 `0x0bbb/0006` values with that
alignment, missing 14,754 Rosetta voucher IDs. The comprehensive signature scan
is currently the authoritative identity scanner.

Master name anchors observed so far:

| Field ID | Role |
| --- | --- |
| `0x0002` | common master-name anchor |
| `0x01f7` | candidate alternate master-name anchor; also appears as normal field, so context matters |
| `0x0af3` | rare candidate alternate ledger-name anchor |

Voucher fields to prioritize:

| Field ID | Working meaning |
| --- | --- |
| `0x00cd` | narration-like |
| `0x00ce` | party/name-like in some regions |
| `0x00cf` | address/location-like |
| `0x07d5` | voucher-number-like |
| `0x0bbc` | company GUID-like |
| `0x0bbe` | composite voucher-key-like |

`0x0bbe` voucher-key bridge:

```text
raw:     <uuid>-<high_hex_8>:<low_hex_8>
sqlite:  (int(high_hex_8, 16) << 32) + int(low_hex_8, 16)
```

Observed example:

```text
raw 0x0bbe = 1d5c3b6b-afb3-463d-8790-eeb9e5b670b9-0000b2b3:00000010
decimal    = 196481868890128
Rosetta    = voucher_key for master_id 28724,
             Receipt Note - Stock Alignment #1 on 2025-04-01
```

Coverage on `070525/TranMgr.1800` against Claude Rosetta:

```text
0x0bbe occurrences:       2,371
parseable distinct keys:  2,357
Rosetta voucher keys:    30,545
matched distinct keys:    1,935
```

This is a partial identity bridge, not a full voucher-header solution.

`0x0bbb` compact voucher `MASTERID` bridge:

```text
bb 0b 00 06 <MASTERID:u32_le> 00 00
```

Independent `070525/TranMgr.1800` scan:

```text
compact 0x0bbb/0006 hits:       31,113
distinct compact values:        31,113
Rosetta voucher MASTERIDs:      30,545
matched Rosetta MASTERIDs:      30,545
missing Rosetta MASTERIDs:           0
extra compact values:              568
```

This is the strongest voucher-header identity bridge so far. It locates every
Rosetta voucher `MASTERID` in `TranMgr.1800`, but the 568 extras still need
classification as deleted/non-voucher/other transaction records before typed
voucher extraction can be considered complete.

Nearby compact date field:

```text
67 00 00 0d <date_serial:u32_le> 00 00
```

For voucher headers this appears to be the voucher date serial. For `TranMgr`,
serial `0x0000b2b3` / `45747` maps to `2025-04-01` using base `1899-12-31`.

Header-key refinement:

- `0x0bbb/0006` alone gives 31,113 distinct compact IDs in `070525`.
- Requiring same-page TLV key field `0x0003` reduces this to 30,564 key-bearing
  candidates.
- Filtering out base type code `31` (`0x0000001f`, observed as
  `Stat Only Outwards` in `VchStatus`) leaves exactly 30,545 exportable voucher
  headers, matching Rosetta `30,545/30,545` by `MASTERID`, GUID, and voucher
  date.

Current header reconciliation:

```text
decoded_candidates_all=31113
decoded_candidates_with_key=30564
decoded_exportable_base_type_not_31=30545
rosetta_vouchers=30545
matched_master_ids=30545
missing_rosetta_master_ids=0
extra_exportable_master_ids=0
guid_exact=30545/30545
date_exact=30545/30545
```

Same-page text coverage is still partial:

```text
same_page_number_exact=18527/28248
same_page_party_exact=527/13857
same_page_narration_contains=19505/29584
```

This proves header identity/date/count for `070525`, not full voucher content.
Party, narration, voucher number, exact custom voucher type per row, and child
lines still require page-chain and side-file decoding.

Voucher page grouping:

- `TranMgr.1800` page header word at offset `+4` is not voucher `MASTERID`.
- It behaves like a page-family/group id.
- Collecting all decoded text from pages with the same group id improves
  voucher number, party, and narration coverage, but can overinclude pages and
  is not yet a proven exact record chain.

Voucher child references:

- Many inventory and accounting line references are not stored as item/ledger
  strings in `TranMgr.1800`.
- The line reference is commonly a `Manager.1800` `MASTERID` integer embedded
  in the voucher page group.
- Sampled `070525` inventory lines found stock item `MASTERID` references at
  100% for several voucher types, including stock journals, purchase orders,
  purchase vouchers, sales vouchers, and delivery challans.
- Sampled `070525` accounting lines found ledger `MASTERID` references at 100%
  for tested sales, bank payment, and purchase voucher samples.
- Money amounts are stored as signed little-endian integers scaled by `100000`.
  Verified `070525` voucher-tree searches found Rosetta amounts such as
  `-2004486.00`, `1698717.34`, `783308.00`, and `152884.56` as scaled i64
  values and found zero IEEE-754 f64 hits in those trees.
- The amount type-code rule is not final. Claude reported `00 0c` as the amount
  type, but local byte contexts and Boole's scan show scaled i64 amounts under
  compact fields such as `0x006e/0009`, `0x0002/0009`, `0x0004/0008`, and
  `0x0007/0008`. `00 0c` is a rate candidate in sales rows, not a global
  amount type.
- Quantity candidates in sales rows use `0x0001/0008 = quantity * 100000`.
- Rate candidates in sales rows use `0x0002/000c = rate * 100000`, with
  duplicates at `0x0003/0008`.
- Signs by semantic row, row multiplicity, and exact line boundaries remain
  unknown.

Voucher child anchor families:

```text
ledger direct block:
  00 50 06 00 00 10 <len:u32le>
  ...
  02 00 00 03 <ledger_master:u32le>
  ...
  02 00 00 09 <amount_i64_x100000>

ledger summary row:
  00 04 d3 52 00 09 <ledger_master:u32le> <amount_i64_x100000>

inventory identity row:
  <flags:u16> 2e 01 42 00 <stock_item_master:u32le>

inventory value rows:
  <flags:u16> f6 01 44 00 <stock_item_master:u32le> ...
  <flags:u16> f6 01 46 00 <stock_item_master:u32le> ...
```

Measured against `070525` Rosetta:

```text
Zeno direct ledger exact hits:             26,311 / 40,781
Zeno direct + summary ledger exact hits:   35,091 / 40,781
Zeno inventory item presence hits:        115,436 / 115,505

Codex conservative probe inventory presence: 115,505 / 115,505
Codex conservative probe inventory value:    114,411 / 115,505
```

Sales inventory compact-run family:

```text
0x0002/0003 <stock_item_master:u32>
0x0069/0003 <ledger_master:u32>
0x006d/0003 <stock_item_master:u32>
0x0072/0003 <unit_master:u32>
0x006e/0009 <amount_i64_x100000>
0x0066/000b <quantity_i64_x100000> <unit_master:u32>
0x0067/000b <quantity_i64_x100000> <unit_master:u32>
... optional row-local payload ...
0x0002/000c <rate_i64_x100000>
```

Current strict probe on `61 Sales`:

```text
compact parser exact row tuple matches: 2,042 / 2,068
compact + matrix union exact row tuple matches: 2,064 / 2,068
item+amount-only leftovers after union: 0 / 2,068
```

This is a strong proof for one voucher type in one Rosetta company, not a
universal `.1800` decoder. Fixed offsets are insufficient. The successful
parser has to scan row-local markers, handle page-split values, resolve missing
inline ledgers from unique field-coded ledger/amount pairs, and union the compact
row family with matrix rows.

Additional verified sales row variants:

```text
stock + 0068 stock-repeat + 006d stock-repeat + unit + amount + qty + rate
stock + 0069 ledger + 006d stock-repeat + rate + 00 50 f6 01 block(amount, qty)
stock + 0068 stock-repeat + 006d stock-repeat + rate + 00 50 f6 01 block(amount, qty)
```

Remaining `61 Sales` misses after union are four rows:

```text
30652 Glass Stove 2 Burner Sample
34667 L3994B00000 BUTTERFLY MAGNUM 3B LPG STOVE
55395 PRPSCOMN01077-Batti Stand Coated Luxe ( 3 Wing)
60124 BTRDTCOMN000876- Butterfly Magnum Drip Tray Jumbo
```

At least one remaining miss has the compact field marker itself split across a
page boundary. Page-split repair must operate on logical compact cells, not only
the final i64 payload.

Nested voucher sub-record candidates:

```text
00 50 <subrec_id:u16le> 00 10 <length:u32le>
```

Observed sub-record ids:

| Sub-record | Candidate role |
| --- | --- |
| `0x01f6` | primary line-item content |
| `0x01fe` | GST classification metadata: stock id, opaque kind tag, GST rate scaled by 100000 |
| `0x2713` | observed in synthetic Payment ledger-row evidence; not yet generalized |
 
Retracted: `0x1327` (`00 50 27 13 00 10`) is not a proven sub-record id and
has zero hits in the currently checked corpora. Do not confuse it with `0x2713`
(`00 50 13 27 00 10`), which is a different little-endian id.

These sub-records are part of the current path to pairing item/ledger refs with
quantity, rate, amount, GST, and batch details. GST tax ledgers should not be
decoded through a fake GST-class master kind; use ledger/group/rate evidence and
the inline GST TLVs instead.

Status side file:

- `VchStatus.1800` appears to use fixed slots, not TLV.
- Current model: 512-byte pages with three 128-byte slots at page offsets
  `0x80`, `0x100`, and `0x180`.
- Slot offset `+0x28` is a voucher date serial with base `1899-12-31`.
- Slot offset `+0x64` is voucher type `MASTERID`; in `070525`, counts match
  Rosetta except for the 19 internal base-type-31 rows.
- `VchStatus` non-empty slot count is `30,564`, exactly equal to
  `voucher_header_candidates where key_raw is not null`.
- The `(date_serial, voucher_type_master_id)` multiset equals Rosetta's 30,545
  exportable vouchers plus 19 base-type-31 `Stat Only Outwards` candidates.
- Slot physical order is not solved. Current best ordering is close to
  `MASTERID` order with two legacy low ids inserted into an Apr-30 cluster, but
  duplicate date/type rows make some links ambiguous.

Link/allocation side file:

- `LinkMgr.1800` is TLV/compact-readable and carries bill/bank allocation
  bridges.
- Compact `0x0002/0007` is a voucher-key bridge: `<low:u32><high:u32>`.
  `(high << 32) + low` matches Rosetta voucher keys.
- TLV `0x0002/000f` is bill allocation/reference name.
- `0x0642/0009` and `0x0644/0009` carry signed fixed-point bill amounts scaled
  by `100000`; `0x0644` also carries a date serial.
- Bank allocation party is `0x232e/000f`; bank allocation amount is
  `0x232e/0009`, signed fixed-point scaled by `100000`.
- `0x2346/000f` carries raw payment mode strings such as `NEFT`/`RTGS`.

The field ID map is not final. It should be built empirically per file role and
validated against known exports.

## 2.3 Manager Page `MASTERID`

Observed in `070525/Manager.1800`:

- 512-byte pages contain a little-endian numeric object id at page offset `+4`.
- For many master records, this value equals Tally XML `MASTERID`.
- Tally XML `GUID` for masters is composed as:

```text
<company_guid>-<MASTERID as 8 lowercase hex digits>
```

Examples:

| XML name | Manager page | Word at `+4` | Hex | XML GUID suffix |
| --- | ---: | ---: | --- | --- |
| `00A0 CUB -CCOD-20054930` | 4655 | 2431 | `0000097f` | `0000097f` |
| `S.S.Normal Scraps` | 21577 | 7868 | `00001ebc` | `00001ebc` |
| `E10.1 Accounts / Admin` | 3791 | 2189 | `0000088d` | `0000088d` |

This is the first strong bridge from raw `.1800` pages to Tally XML object
identity. It should become the primary key for typed master extraction.

Current caveat: name alone is not a safe key. Some names appear in multiple
master/object classes; comparing by name can map to the wrong page. Typed
extraction must classify record type and use `MASTERID`/GUID, not only name.

Manager classification fields observed from the cross-agent pass:

| Kind | Discriminator |
| --- | --- |
| groups | field `0x0963` present; observed `283/283` groups in `070525` |
| ledgers | combined ledger-only fields such as `0x0067`, `0x0068`, `0x0004`, `0x0a31`, `0x0d4e`, `0x0acc`, `0x0005`, `0x0aca` |
| stock_items | field `0x0fd4` base-unit/UOM, e.g. `Nos`; observed `2747/2754` stock items |
| units | field `0x1132`, or unit-shaped `0x01f7` thin record |
| voucher_types | voucher-shaped `0x01f7` abbreviation and page-chain shape |
| godowns/simple masters | still ambiguous; thin `{0x0002,0x01fb,...}` overlaps stock groups and simple voucher types |

High-confidence Manager field mappings:

| Field ID | XML/logical meaning |
| --- | --- |
| `0x0002` | primary name for most masters |
| `0x01fb` | company GUID prefix; full GUID is `<0x01fb>-<page+4:08x>` |
| `0x01f7` | alias/original/abbreviation depending record kind |
| `0x0af3` | rare ledger name/mailing-name anchor |
| `0x0067` | ledger `created_by` |
| `0x0aca` | ledger `gstin` |
| `0x0acc` | ledger `state` |
| `0x0005`, `0x0a8f` | ledger `pincode` |
| `0x0ac1` | ledger `pan` |
| `0x0a90` | ledger `email` |
| `0x0a94` | ledger `phone` |
| `0x0a31`, `0x0d4e` | ledger `country` |
| `0x0006`, `0x01f8` | ledger address lines |
| `0x0fd4` | stock item `base_units` |
| `0x1132` | unit compound/formal unit string |

Validation note: in `070525/Manager.1800`, every GUID-bearing group, ledger,
stock item, and stock group from both gold DBs matched a page `+4` value:
`6346/6346`.

## 2.4 Current Success Bar

Do **not** claim `.1800` is cracked until the decoder can produce typed SQLite
tables that reconcile to live/XML exports:

```text
companies
groups
ledgers
stock_groups
stock_items
units
godowns
voucher_types
vouchers
voucher_ledger_entries
voucher_inventory_entries
voucher_unknown_sections or equivalent raw child payloads
raw_payloads/raw_records
```

Required proof:

- master IDs/GUIDs match
- names match after Tally-compatible whitespace normalization
- voucher GUIDs and `MASTERID`s match
- voucher counts by type/date match
- ledger/inventory line totals match
- raw record coverage has an audit trail

Current decoder is **not 100%**. It is a non-vaulted `.1800` tagged-field
extractor with partial typed inference.

## 3. Vault Model

Input: vault password.

Unknowns:

- Key derivation for `.1800`
- Cipher for `.1800`
- Block/page IV rules
- CRC/checksum polynomial and endian
- Whether compression happens before or after encryption

Old `.900` public clue:

```text
page[0:4] = CRC(page[4:512])
page[4:8] = little-endian 0x00000001 after decryption
page[12:16] = possible page number after decryption
```

## 4. Logical Model

Target normalized output tables:

```text
companies
groups
ledgers
stock_groups
stock_items
units
godowns
voucher_types
vouchers
voucher_accounting_lines
voucher_inventory_lines
bill_allocations
order_allocations
batch_allocations
tax_allocations
raw_files
raw_pages
raw_strings
raw_records
```

## 5. Validation Rules

Decoded data must reconcile against independent totals:

- Company name and period.
- Count of ledgers/groups/stock items.
- Voucher counts by type.
- Trial balance totals.
- Day book voucher totals.
- Stock summary item quantities/values.

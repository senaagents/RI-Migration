# Breakthroughs - 2026-04-27

This is the current shareable state for the `.1800 -> SQLite` reverse
engineering work. It is intentionally strict: these are breakthroughs, not a
claim that Tally is fully cracked.

## Current Claim

We can directly read meaningful non-vaulted `.1800` data without the Tally
client.

We cannot yet claim full `.1800 -> SQLite 100%`.

The strongest proven surface is `070525`, validated against Claude Rosetta and
the existing XML-derived SQLite where comparable.

## 1. TLV Field Frame

Most readable business fields use this frame:

```text
02 10 <field_id:u16le> <type_code:u16> <length:u16le> <value>
```

String specialization:

```text
02 10 <field_id:u16le> 00 0f <length:u16le> <utf16le null-terminated string>
```

Confirmed string examples:

- `Company.1800`: company name, GSTIN, address, email.
- `Manager.1800`: master names, GUID prefix, ledger/stock/unit fields.
- `TranMgr.1800`: voucher parties, GSTINs, addresses, narrations, vehicle
  fields.
- `LinkMgr.1800`: bill/bank/reference allocation fields.

## 2. Manager `MASTERID`

`Manager.1800` page offset `+4` is the Tally master `MASTERID` for GUID-bearing
master records.

GUID reconstruction:

```text
<company_guid>-<master_id:08x>
```

In `070525`, all GUID-bearing groups, ledgers, stock items, and stock groups
checked against gold data matched this rule: `6346/6346`.

Important field map:

| Field | Meaning |
| --- | --- |
| `0x0002` | primary name |
| `0x01fb` | company GUID prefix |
| `0x01f7` | alias/original/abbreviation depending kind |
| `0x0af3` | rare ledger name/mailing-name anchor |
| `0x0aca` | ledger GSTIN |
| `0x0acc` | ledger state |
| `0x0ac1` | ledger PAN |
| `0x0a90` | ledger email |
| `0x0a94` | ledger phone |
| `0x0006`, `0x01f8` | ledger address lines |
| `0x0fd4` | stock item base unit |
| `0x1132` | formal unit string |

## 3. Voucher Header `MASTERID`

`TranMgr.1800` contains compact numeric fields outside TLV:

```text
<field_id:u16le> <type_code:u16> <value:u32le> 00 00
```

Voucher `MASTERID` bridge:

```text
bb 0b 00 06 <MASTERID:u32le> 00 00
```

For `070525`:

```text
compact 0x0bbb/0006 hits: 31,113
distinct values:          31,113
Rosetta voucher IDs:      30,545
matched Rosetta IDs:      30,545
missing Rosetta IDs:           0
extra compact IDs:            568
```

This locates every Rosetta voucher `MASTERID` in raw `.1800`.

## 4. Exportable Voucher Header Filter

Same-page TLV field `0x0003` carries a key:

```text
<date_hex>-<base_type_code_hex>-<key_offset_hex>
```

Filtering rule on `070525`:

```text
0x0bbb/0006 compact id
+ same-page 0x0003 key
+ base_type_code != 31
= exportable voucher header
```

Result:

```text
decoded candidates with key:       30,564
base type 31 internal rows:            19
exportable decoded headers:        30,545
Rosetta voucher headers:           30,545
matched master IDs:                30,545 / 30,545
GUID exact:                        30,545 / 30,545
voucher date exact:                30,545 / 30,545
```

This proves header identity and date for `070525`. It does not prove full
voucher content.

## 5. Voucher Date Field

Compact field `0x0067` type `000d` sits near the voucher `MASTERID` and is the
voucher date serial.

Date base:

```text
1899-12-31
```

Example:

```text
serial 45747 -> 2025-04-01
```

Across measured company folders, `0x0067/000d` date hits match
`0x0bbb/0006` voucher id hits.

## 6. Voucher Key Partial Bridge

Field `0x0bbe` sometimes contains:

```text
<uuid>-<high_hex_8>:<low_hex_8>
```

Tally/XML `voucher_key`:

```text
(int(high_hex_8, 16) << 32) + int(low_hex_8, 16)
```

For `070525`:

```text
0x0bbe occurrences:       2,371
parseable distinct keys:  2,357
Rosetta voucher keys:    30,545
matched distinct keys:    1,935
```

This is useful but incomplete.

## 7. VchStatus Fixed Slots

`VchStatus.1800` is not TLV. It appears to be 512-byte pages with three
128-byte slots per data page at offsets:

```text
0x80, 0x100, 0x180
```

In `070525`:

```text
non-empty slots: 30,564
keyed voucher candidates: 30,564
```

Slot fields:

| Slot offset | Meaning |
| --- | --- |
| `+0x28` | voucher date serial, base `1899-12-31` |
| `+0x64` | voucher type `MASTERID` |

`+0x64` voucher-type counts match Rosetta exactly except the 19 internal
`Stat Only Outwards` rows.

Stronger alignment:

- `(date_serial, voucher_type_master_id)` is an exact multiset match for the
  30,545 Rosetta exportable vouchers plus 19 internal base-type-31 candidates.
- Physical slot order is not solved. Naive `MASTERID` order matches
  `29,236 / 30,564`; a near-order with low ids `2347` and `2366` inserted into
  an Apr-30 cluster reaches `30,558 / 30,564`, but duplicate date/type rows make
  some links ambiguous.
- `decode-sqlite` now emits `vch_status_slots` with all slot words preserved.

## 8. TranMgr Page Group Clue

`TranMgr.1800` page header word at page offset `+4` is not voucher `MASTERID`;
it behaves like a page-family/group id.

Using all pages with the same group id improves text recovery:

```text
same-page voucher number: 18,527 / 28,248
group-page voucher number: 26,395 / 28,248

same-page party: 527 / 13,857
group-page party: 8,842 / 13,857

same-page narration: 19,505 / 29,584
group-page narration: 26,319 / 29,584
```

Caveat: page group is a strong clue, not a proven exact record chain.

## 9. Voucher Line Reference Breakthrough

Raw string search undercounts voucher lines because many child lines store
Manager `MASTERID` integers instead of names.

Inventory lines:

```text
stock item reference = Manager.1800 stock item MASTERID as integer in TranMgr page group
```

Sample results from `070525`:

```text
Conversion Stock Journal:          490 / 490 item refs found
Stock Journal:                     500 / 500 item refs found
31-Puchase(Monthly):               473 / 473 item refs found
Store Movement Stk Jrl:            429 / 429 item refs found
Purchase Order:                    477 / 477 item refs found
61 Sales:                          482 / 482 item refs found
Purchase Order -Others:            497 / 497 item refs found
Delivery Challan - Stock Alignment:497 / 497 item refs found
```

Accounting ledger lines use the same idea:

```text
ledger reference = Manager.1800 ledger MASTERID as integer in TranMgr page group
```

Sample results:

```text
61 Sales ledger refs:          486 / 486
12 Payt Bank ledger refs:      583 / 583
31-Puchase(Monthly) refs:      579 / 579
```

This is the biggest current path toward typed voucher lines. The money amount
encoding is now partially decoded, but quantities, rates, signs by semantic
line, and exact row boundaries remain unresolved.

## 10. Voucher Child Anchors

Zeno identified concrete child-row anchor families in `TranMgr.1800`.

Ledger direct block:

```text
00 50 06 00 00 10 <len:u32le> ...
  02 00 00 03 <ledger_master:u32le>
  ...
  02 00 00 09 <amount_i64_x100000>
```

Ledger summary row:

```text
00 04 d3 52 00 09 <ledger_master:u32le> <amount_i64_x100000>
```

Inventory identity row:

```text
<flags:u16> 2e 01 42 00 <stock_item_master:u32le>
```

Inventory value rows:

```text
<flags:u16> f6 01 44 00 <stock_item_master:u32le> ...
<flags:u16> f6 01 46 00 <stock_item_master:u32le> ...
```

Zeno's richer matcher:

```text
direct ledger exact hits:             26,311 / 40,781
direct + summary ledger exact hits:   35,091 / 40,781
inventory item presence hits:        115,436 / 115,505
inventory any amount/qty/rate hit:    88,489 / 115,505
```

Our conservative reproducible script:

```bash
PYTHONPATH=. python3 scripts/probe_voucher_child_anchors.py \
  out/070525_decoded_headers.sqlite3 \
  --tranmgr /Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/TranMgr.1800 \
  --rosetta /Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3
```

Current output:

```text
voucher_header_groups=31113
ledger_union_exact=29764/40781
inventory_presence_exact=115505/115505
inventory_any_value_exact=114411/115505
```

The script is intentionally broad/conservative; it proves the anchors are
reproducible but is not a typed line extractor.

## 11. Voucher Amount, Quantity, Rate Encoding

Cross-learning from Claude/Boole plus local verification on Rosetta values
shows voucher numeric money/quantity/rate values are signed little-endian
integers scaled by `100000`, not IEEE-754 floats.

Examples verified in `070525/TranMgr.1800` voucher trees:

| Voucher | Rosetta amount | Scaled integer | Tree hits | f64 tree hits |
| --- | ---: | ---: | ---: | ---: |
| `28776` | `-2004486.00` | `-200448600000` | `7` | `0` |
| `28776` | `1698717.34` | `169871734000` | `11` | `0` |
| `28776` | `783308.00` | `78330800000` | `16` | `0` |
| `28776` | `152884.56` | `15288456000` | `22` | `0` |
| `28952` | `-41615.00` | `-4161500000` | `5` | `0` |
| `28952` | `35266.80` | `3526680000` | `11` | `0` |

Important correction: Claude's note says amount type bytes are `00 0c`, but
Boole and local byte-context checks show that is too broad. In verified voucher
contexts, line amounts appear under compact fields such as:

```text
6e 00 00 09 <i64 scaled by 100000>
02 00 00 09 <i64 scaled by 100000>
04 00 00 08 <i64 scaled by 100000>
07 00 00 08 <i64 scaled by 100000>
```

`00 0c` behaves like a compact-slot local type code and is a strong rate
candidate in sales rows, not a generic amount type.

Sales compact run example:

```text
02 00 00 03 <stock_item_master>
69 00 00 03 <ledger_master>
6d 00 00 03 <stock_item_master_repeat>
72 00 00 03 <unit_master>
6e 00 00 09 <amount_i64_x100000>
66 00 00 0b <quantity_i64_x100000> <unit_master>
67 00 00 0b <quantity_i64_x100000> <unit_master>
02 00 00 0c <rate_i64_x100000>
```

Boole's quantity/rate candidates:

| Voucher | Item | Qty field | Rate field |
| ---: | --- | --- | --- |
| `28776` | Sparkle Power Duo | `0x0001/0008 = 20000000` | `0x0002/000c = 391654000` |
| `28776` | Valentino Carbon | `0x0001/0008 = 22500000` | `0x0002/000c = 402551000` |
| `28776` | Luxe 2B | `0x0001/0008 = 700000` | `0x0002/000c = 138137000` |
| `28952` | CRCA Coil | `0x0001/0008 = 11020000` | `0x0002/000c = 6800000` |
| `28952` | Borecutting Circle | `0x0001/0008 = 30520000` | `0x0002/000c = 9100000` |

Cross-team correction to share back to Claude: scaled i64 amount encoding is
confirmed, but the global `00 0c = amount` type-code claim is not.

`scripts/probe_sales_inventory_runs.py` now tests this as full row tuples for
`61 Sales`:

```text
truth_rows=2068
type_sales_pattern_runs=12075
exact_qty_rate_amount_ledger_matches=2042/2068
```

With the matrix row probe:

```text
compact runs 52149 exact 2042/2068 item_amount_only 12 missing 14
matrix  runs  1529 exact 1501/2068 item_amount_only 28 missing 539
union   runs 53678 exact 2064/2068 item_amount_only 0  missing 4
```

This is now a near-complete measured path from raw `.1800` bytes to exact
`voucher_inventory_entries` rows for one voucher type in one company. It is
not a universal 100% claim.

New safe rules added:

- Field-coded ledger/amount pairs are safe for resolving rows with no inline
  `0x0069` ledger:
  `0x0002/0003 <ledger> ... 0x0002/0009 <amount>`.
- Do not use raw "nearby u32" ledger guesses; they create false positives.
- Some rows are `stock + rate`, followed by
  `00 50 f6 01 00 10 <len>` carrying amount and quantity.
- Page-split i64 values can resume after a 28-byte next-page header.
- The four remaining `61 Sales` misses are:
  `30652 Glass Stove 2 Burner Sample`,
  `34667 L3994B00000 BUTTERFLY MAGNUM 3B LPG STOVE`,
  `55395 PRPSCOMN01077-Batti Stand Coated Luxe ( 3 Wing)`,
  `60124 BTRDTCOMN000876- Butterfly Magnum Drip Tray Jumbo`.

## 12. TranMgr Sub-Records

Claude found nested sub-record headers inside `TranMgr.1800` pages:

```text
00 50 <subrec_id:u16le> 00 10 <length:u32le>
```

Observed IDs:

| Sub-record | Candidate role |
| --- | --- |
| `0x01f6` | primary line-item content |
| `0x01fe` | GST classification metadata: stock id, opaque kind tag, GST rate scaled by 100000 |
| `0x2713` | observed in synthetic Payment ledger-row evidence; not yet generalized |

Correction from the Claude handoff and local synthetic checks: `0x1327`
(`00 50 27 13 00 10`) is retracted and currently absent; `00 50 13 27 00 10`
is `0x2713`, not `0x1327`. Also, `0x01fe` is not tax/batch allocation and is
not proof of a GST-class master table.

The remaining route from "all refs and amounts exist somewhere in the voucher
tree" to exact per-row pairing is `0x01f6`, `0x0006`, `0x0002`, `0x52d3`,
columnar `f6 01 44/46`, and source-specific de-duplication.

## 12A. Typed Journal Inventory Rows

Added `scripts/probe_journal_inventory_rows.py` as the reproducible probe for
the two biggest inventory families.

Current verified coverage on `070525`:

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

Two row families are now typed:

```text
02 00 00 03 <stock_id>
02 00 00 0c <rate_i64_x100000> ...
00 50 f6 01 00 10 <len>
  ... 02 00 00 09 <amount_i64_x100000>
  ... 02 00 00 0b <quantity_i64_x100000> <unit_id>
```

and:

```text
<flags:u16> f6 01 44 00 <stock_id> ... amount-like i64s
<flags:u16> f6 01 46 00 <stock_id> ... quantity-like i64s
```

For strict `0x04` columnar rows, amount `+24` pairs with quantity `+32`, and
amount `+32` pairs with quantity `+24`. Rate is derived from
`abs(amount) / abs(quantity)` using scale `100000`.

Important page-boundary fix: a compact field marker can end exactly at page byte
`511`, and the i64 value resumes at offset `0x1c` on the next page. The shared
`repair_split_i64()` now handles this marker-edge split. It fixed the largest
observed Stock Journal miss:

```text
42385 Stock Journal WIP Blank Stage Hilux 3b qty=150 rate=1400 amount=210000
```

Heisenberg's page-boundary prototype has now been ported into
`scripts/probe_journal_inventory_rows.py` as a logical F6 block reader. It
reconstructs the F6 header and body through page-continuation bytes instead of
trusting physical contiguity.

Largest remaining misses after the fix:

```text
58414 Conversion Stock Journal PRBBOSMTB00781 Brass Burner Jumbo with OD +196020.00
31156 Conversion Stock Journal SS Sheets - 2mm                         +179055.00
54473 Conversion Stock Journal WIP Channel Sparkle 3B Unpainted - TP    +177487.36
53607 Stock Journal            WIP Blank Stage Hilux 3b                 +126000.00
```

These misses still have their raw values visible in the voucher page groups in
many cases; the open problem is safe association/ordinal rules for additional
layout variants, not value encoding.

Secondary columnar opportunity: large misses show additional matrix families
with prefixes like `04 0c`, `08 0c`, `00 0c`, `08 04`; opcodes/forms like
`f7 01`, `f8 01`, `f9 01`, `fa 01`, `06 02`; and kind `64` beside kind `44`.
This should be second phase because false-match risk is higher than the logical
page reader. Require same-stock pairing, sane signs, and derived-rate agreement.

## 12B. Purchase Monthly Hybrid Row Shape

Averroes tested `31-Puchase(Monthly)`, the next large inventory family.

Current probe results on `070525`:

```text
truth_rows=7682
journal union exact item/qty/rate/amount=7667/7682
purchase hybrid exact with ledger=7645/7682
```

Rosetta check:

```text
inventory rows=7682
ledger rows=12415
vouchers with more than one inventory ledger=0
```

Every Purchase Monthly voucher has one inventory ledger across its inventory
rows in Rosetta. Still, the safer raw rule is row amount -> matching
ledger/amount block, not voucher-wide assignment.

Hybrid parser needed:

```text
02 00 00 03 <stock_id>

Variant A:
68 00 00 03 <same stock_id>
6d 00 00 03 <same stock_id>
ledger from nearest preceding:
  02 00 00 03 <ledger_id> ... 02 00 00 09 <same amount>

Variant B:
69 00 00 03 <ledger_id>
6e 00 00 03 <ledger_id>

Then:
02 00 00 0c <rate_i64_x100000>
00 50 f6 01 00 10 <len>
  ... 02 00 00 09 <amount_i64_x100000>
  ... 02 00 00 0b <quantity_i64_x100000>
```

Concrete examples:

```text
MID 28738 stock-repeat variant:
02 00 00 03 7e 1b 00 00
68 00 00 03 7e 1b 00 00
6d 00 00 03 7e 1b 00 00
02 00 00 0c 30 57 05 00 00 00 00 00  # rate 3.50
00 50 f6 01 00 10 98 00 ...
02 00 00 09 80 45 6a c1 ff ff ff ff  # amount -10500.00
02 00 00 0b 00 a3 e1 11 00 00 00 00  # qty 3000.00

MID 28739 inline ledger variant:
02 00 00 03 78 1a 00 00
69 00 00 03 fe 0e 00 00
6e 00 00 03 fe 0e 00 00
02 00 00 0c e0 4d e0 00 00 00 00 00  # rate 147.00
02 00 00 09 80 33 ef 5e fe ff ff ff  # amount -69972.00
02 00 00 0b 80 51 d6 02 00 00 00 00  # qty 476.00
```

Important split case: MID `28738` has F6 starting 8 bytes before page end. The
next page header/noise appears before the real `0x0002/0009` amount marker and
`0x0002/000b` quantity marker. A robust Purchase parser should search a bounded
logical continuation window instead of rejecting on corrupt apparent length.

## 12C. Claude Implementation Wins Ported

Ported from Claude into Codex:

- `tally1800/probe.py`: page-aware TLV walking for page-group runs, stripping
  each 28-byte page header before decoding TLVs. This addresses the cross-page
  TLV mojibake bug.
- `tally1800/decoded_export.py`: voucher type pairing from `VchStatus.1800`
  using voucher-key-present filtering and date-cluster reorder.
- `tally1800/typed_fields.py`: fid-chain fallback and shape validators for
  typed master fields.

Verified on `070525`:

```text
voucher_type pairing: 30464 / 30545 exact = 99.73%
ledger state with defaults:   99.87%
ledger country with defaults: 100.00%
ledger pincode:               99.50%
ledger GSTIN:                 98.30%
ledger PAN:                   98.45%
```

## 13. LinkMgr Allocation Surface

`LinkMgr.1800` is TLV-readable and likely carries bill/bank/order allocation
details.

Confirmed useful fields:

| Field | Meaning candidate |
| --- | --- |
| `0x0002` | bill allocation/reference names; matched all 5,364 distinct gold bill names |
| `0x232e` | party/ledger for link/bank rows; matched 413/452 bank parties |
| `0x2346` | `NEFT` / `RTGS` |
| `0x232f` | `A/c Payee` |
| `0x232b` | UTR/instrument strings |
| `0x2331` | bank name |
| `0x2332` | branch |
| `0x2333` | account number |
| `0x2348` | print status |

New allocation bridges from the parallel Erdos pass:

- Compact `0x0002/0007` stores voucher key as `<low:u32><high:u32>`.
- `(high << 32) + low` matches Rosetta numeric voucher keys.
- `30,768` LinkMgr key hits matched `10,144` distinct Rosetta vouchers.
- Bill name is TLV `0x0002/000f`.
- Bill amount fields:
  - `0x0642/0009`: signed scaled amount, scale `100000`, likely original
    reference amount.
  - `0x0644/0009`: `<date_serial:u32><amount:i64 scaled 100000>`, likely
    application amount paired to following `0x0002/0007`.
- Exact bill allocation matches by `(voucher_key, bill_name, amount)`:
  `10,265 / 12,304`.
- Bank amount field: `0x232e/0009`, signed scaled amount with scale `100000`.
- Bank pages map to vouchers either directly via `0x0002/0007`, or through
  `0x0003/0006` application id joined to the unique `0x0002/0006` before a
  `0x0002/0007`.
- Exact bank matches by `(voucher_key, party, amount)`:
  `2,667 / 3,601` unique Rosetta bank allocation entries.

Open limits: bill pages can aggregate multiple applications, zero application
amount cases exist, and bank transaction type is not fully decoded.

## 14. Cross-Company Header Shape

Measured structural consistency:

| Company | `0x0bbb/0006` hits | Distinct | key rows | Date range | Base type 31 |
| --- | ---: | ---: | ---: | --- | ---: |
| `070525` | 31,113 | 31,113 | 30,564 | 2025-04-01..2026-04-21 | 19 |
| `100001` | 2,275 | 2,275 | 2,229 | 2024-04-01..2026-04-20 | 6 |
| `100025` | 28,576 | 28,562 | 28,416 | 2023-04-01..2024-03-31 | 29 |
| `300925` | 1,859 | 1,859 | 1,772 | 2024-04-01..2026-04-18 | 4 |

This supports generality of the header shape, but only `070525` has Rosetta
ground truth in hand.

## Not Done

Remaining blockers before any 100% claim:

- Decode exact voucher line boundaries and multiplicity.
- Finish amount/quantity/rate role mapping across all voucher types.
- Pair line references, quantities, rates, and fixed-point amounts inside
  sub-records.
- Map VchStatus slots to voucher headers one-to-one.
- Classify the 568 extra `0x0bbb/0006` IDs in `070525`.
- Build typed master tables from Manager without name-only joins.
- Build typed voucher, accounting-line, inventory-line, and allocation tables.
- Reconcile row counts and totals against same-snapshot XML/live exports.
- Vault/encryption support is out of scope until passworded samples are
  provided and decrypted output reconciles.

## 15. Direct Inventory SQLite Integration

`decode-sqlite` now emits direct inventory tables from `.1800` without Rosetta
or Tally XML:

- `manager_master_kind_guesses`: high-confidence stock item ids from Manager
  field `0x0fd4`, high-confidence ledger ids from Manager field `0x0067`.
- `voucher_inventory_entry_candidates`: broad decoded candidates with
  `source`, `confidence`, page evidence, master ids, names, and scaled values.
- `voucher_inventory_entries_1800`: de-duplicated high-confidence row surface.
- `voucher_decode_audit`: per-voucher evidence audit. Confidence means internal
  rule strength, not truth in a new production company.
- `xml_repair_queue`: hard-gap-only targeted XML repair list.

Regenerated artifact:

```text
out/070525_decoded_inventory.sqlite3
```

`voucher_inventory_entries_1800` validation against Rosetta:

```text
Conversion Stock Journal: 71016 / 71518 value tuples = 99.30%, extra unique 415
Stock Journal:            19214 / 19831 value tuples = 96.89%, extra unique 196
31-Puchase(Monthly):       7583 / 7682  full tuples  = 98.71%, extra unique 136
61 Sales:                  2048 / 2068  full tuples  = 99.03%, extra unique 83
```

Broader candidate coverage:

```text
Conversion Stock Journal: 71208 / 71518 = 99.57%, extra unique 48455
Stock Journal:            19554 / 19831 = 98.60%, extra unique 15301
31-Puchase(Monthly):       7642 / 7682  = 99.48%, extra unique 5193
61 Sales:                  2048 / 2068  = 99.03%, extra unique 83
```

Important production choice: for journals, nearest preceding stock row with its
own row rate is the low-noise direct rule. Allowing top-2/top-3 preceding stocks
or derived rates barely improves exact matches and creates very large false
candidate sets.

Current hybrid repair signal on `070525`:

```text
voucher_decode_audit rows: 30564
needs_xml_review:         15322
needs_xml_repair:            65
xml_repair_queue rows:       65
```

The `65` hard repairs are inventory vouchers with no high-confidence direct
inventory rows. The broader `needs_xml_review` set is mostly ambiguity from
extra/medium candidates and should be handled by optional XML sampling, balance
checks, or improved direct rules.

## 16. Guarded Synthetic Lab

Read `tally-db-pipeline` docs, Sena playbooks, and scars before live writes.
The practical rules are: one Tally request at a time, always use
`SVCURRENTCOMPANY`, avoid broad computed/full-detail requests, and never treat
HTTP reachability as proof Tally is healthy.

Live synthetic company now exists:

```text
name:       Test Synthetic
GUID:       f776087f-35d2-4bee-bb80-6307328dbfb9
folder id:  100000
```

`scripts/synthetic_tally_lab.py` is the guarded runner for the synthetic
differential corpus. It verifies the synthetic company, uses a local lock,
requires a readable company folder before writing, supports safe ledger-only and
payment-voucher modes, snapshots before/after `.1800` files, then runs
`synthetic-diff`.

Live file access is now working through:

```text
live-tally-data/100000
```

First unsafe broad synthetic import hit a Tally internal duplicate-unit error
on `CSUH001`, so the lab now avoids custom units until that path is isolated.
Use ledger-only and payment modes first.

Detailed continuation instructions are in:

```text
SYNTHETIC-MAPPING-PLAYBOOK.md
```

## 17. Live Synthetic Mapping Proof

This is the first controlled proof that the synthetic method is useful, not just
a smoke test.

Ledger proof:

```text
run:       LEDGER_EXP001
object:    CODX_SYNTH_LEDGER_EXP1846A
parent:    Indirect Expenses
Tally:     CREATED=1, ERRORS=0
SQLite:    manager_master_records.master_id = 207
XML:       MASTERID=207, GUID suffix=000000cf
```

Direct `.1800` evidence:

```text
Manager.1800 page 371 header word +4 = 207
Manager.1800 field 0x0002/000f = CODX_SYNTH_LEDGER_EXP1846A
```

This independently re-proves the Manager identity rule:

```text
Manager.1800 page header +4 = Tally master MASTERID
Manager.1800 field 0x0002/000f = primary master name for this ledger shape
GUID = <company_guid>-<master_id:08x>
```

Payment voucher proof:

```text
PAY001: Payment, MASTERID=1, amount=123.45, narration=CODX_SYNTH_PAYMENT_PAY1847A
PAY002: Payment, MASTERID=2, amount=987.65, narration=CODX_SYNTH_PAYMENT_PAY1850B
```

Direct `.1800` SQLite decoded from `TranMgr.1800`:

```text
0x0bbb/0006 = voucher MASTERID
0x0067/000d = date serial 46112 -> 2026-04-01
0x00cd/000f = narration
0x07d5/000f = rendered voucher number
0x0003/000f = key string, e.g. 0000b420-00000002-00000010
```

Live XML confirmed the same Payment vouchers, `MASTERID`s, date, narration,
amounts, and ledger rows.

New accounting-row target exposed by the same runs:

```text
ledger ids:
  207 = CODX_SYNTH_LEDGER_EXP1846A
  31  = Cash

amount encoding:
  123.45  ->  12345000 -> a8 5e bc 00 00 00 00 00
 -123.45  -> -12345000 -> 58 a1 43 ff ff ff ff ff
  987.65  ->  98765000 -> c8 08 e3 05 00 00 00 00
 -987.65  -> -98765000 -> 38 f7 1c fa ff ff ff ff
```

Visible ledger-line families:

```text
<flags> f6 01 44 00 <ledger_master_id> ... <amount_i64_x100000>
<flags> d3 52 00 09 <ledger_master_id> <amount_i64_x100000>
00 50 02 00 00 10 <len> ...
  02 00 00 03 <ledger_master_id>
  ...
  02 00 00 09 <amount_i64_x100000>

00 50 13 27 00 10 <len> ...
  02 00 00 03 <ledger_master_id>
  ...
  02 00 00 09 <amount_i64_x100000>
```

`decode-sqlite` now emits these into `voucher_ledger_entry_candidates` and
`voucher_ledger_entries_1800` with exact source labels. Synthetic PAY001/PAY002/
PAY003 validates the six expected Payment rows exactly via `52d3_anchor`,
`subrecord_0002`, and `subrecord_2713`. The user-facing entries table marks
that combo `high` only for accounting voucher types; the same raw combo is
blocked for inventory/order voucher types where it is noisy.

Do not promote `f6_01_44` alone for accounting Payment rows. PAY003 proved a
false positive cumulative Cash amount (`-1111.10`) from that source. Keep it as
raw evidence until a source-specific gate is proven.

`070525` Rosetta validation:

```text
truth unique ledger rows: 40,777
raw candidates:            459,453
raw unique union:            64,606 tuples
raw union exact:            40,379 = 99.02% recall, 62.50% precision

gated high entries:         38,132
gated exact:                38,132 = 93.51% recall, 100.00% precision
remaining misses:            2,645
remaining extras:                0
```

High-confidence source rules:

```text
has subrecord_0006 plus any corroborating source
OR 52d3_anchor + subrecord_0002 without 0x2713 context
OR lone subrecord_0002 outside 0x2713 context
```

Medium-confidence source rule:

```text
none currently promoted to voucher_ledger_entries_1800; noisy rows remain in
voucher_ledger_entry_candidates
```

Rejected from `voucher_ledger_entries_1800` but retained as raw candidates:

```text
wide/non-corroborated 0x0002/0x2713 candidates remain noisy in aggregate
subrecord_2713 only                   ->  0.52% precision
52d3_anchor only and subrecord_0006 only are retained for audit/fallback work,
not final promotion
```

Accounting voucher-type gate:

```text
trust 0x2713-correlated rows only for Payt/Payment/Receipt/Receipts/Journal/
Contra-like voucher names, excluding Stock Journal, Receipt Note, Purchase
Order, Job Order, and Delivery Challan families.
```

## 21. Manager Kind-Code Classifier Port

Manager `0x01f7` sub-records carry a stable kind code:

```text
00 50 f7 01 00 10 <len>
  ...
  02 00 00 06 <kind_code:u32le>
```

The port is page-list scoped, not `page_start + page_count` scoped, because some
master pages are non-contiguous. The earlier contiguous read accidentally pulled
neighbor-master bytes; the fixed classifier reads `manager_records.page_no` for
each `master_id`.

Current `070525` direct classifications:

```text
ledger 2968
stock_item 2754
group 283
voucher_type 125
godown 111
cost_centre 308 high + 2 medium
unit 23
currency 2
```

This keeps every previous high-confidence ledger and adds 3 more ledgers, so
the master classifier now removes a major Rosetta dependency for direct decode.

## 22. Extended Numeric Slot Port

Added a walker for:

```text
80 00 <field_id:u16le> <type:u16> <value:i64le>
```

Synthetic PAY003 finds exactly:

```text
field=0x0002 type=0009 -12345000
field=0x0002 type=0009 -98765000
field=0x0002 type=0009 -32109000
```

These are the tested Payment negative amounts at Tally scale `100000`, and are
emitted to `extended_numeric_fields`.

## 23. Synthetic Accounting Writes Unlocked

`scripts/synthetic_tally_lab.py` now supports guarded one-voucher writes for:

```text
payment
receipt
journal
contra
```

The script still requires the synthetic company guard (`Test Synthetic`,
company number `100000`), a serial lock, and before/after snapshots unless
explicitly forced.

New live-write proofs:

```text
RCT001 Receipt:
  Tally CREATED=1, LASTVCHID=4
  direct SQLite voucher MASTERID=4, type=Receipt
  high rows: Cash +222.22, CODX_SYNTH_LEDGER_EXP1846A -222.22

RCT002 Receipt:
  Tally CREATED=1, LASTVCHID=7
  direct SQLite voucher MASTERID=7, type=Receipt
  high rows: Cash +555.55, CODX_SYNTH_LEDGER_EXP2025C -555.55

JRN001 Journal:
  Tally CREATED=1, LASTVCHID=5
  direct SQLite voucher MASTERID=5, type=Journal
  high rows: CODX_SYNTH_LEDGER_EXP1846A +333.33,
             CODX_SYNTH_LEDGER_EXP2025C -333.33

JRN002 Journal:
  Tally CREATED=1, LASTVCHID=8
  direct SQLite voucher MASTERID=8, type=Journal
  high rows: CODX_SYNTH_LEDGER_EXP2025C +666.66,
             CODX_SYNTH_LEDGER_EXP1846A -666.66

BANK001 ledger:
  CODX_SYNTH_LEDGER_BANK2149A under Bank Accounts

CTR001 Contra:
  Tally CREATED=1, LASTVCHID=6
  direct SQLite voucher MASTERID=6, type=Contra
  high rows: CODX_SYNTH_LEDGER_BANK2149A +444.44, Cash -444.44

CTR002 Contra:
  Tally CREATED=1, LASTVCHID=9
  direct SQLite voucher MASTERID=9, type=Contra
  high rows: CODX_SYNTH_LEDGER_BANK2149A +777.77, Cash -777.77
```

All new accounting voucher rows were corroborated by:

```text
52d3_anchor
subrecord_0002
subrecord_0002_wide
subrecord_2713
```

Parser update from this proof: built-in `Receipt` is an accounting voucher type
for the trusted ledger-row gate. `Receipt Note` remains excluded before the
generic receipt match.

## 24. StatStatus, LinkMgr, and E-Invoice Tables

Three proven handoff surfaces are now emitted by `decode-sqlite` as sibling
tables, without changing the trusted ledger-row gate.

`voucher_statutory_status` from `StatStatus.1800`:

```text
070525 slots: 8,350
reportable rows: 8,350
warning rows: 0
```

`linkmgr_allocation_pages` from `LinkMgr.1800`:

```text
070525 pages: 44,688
allocation pages: 3,610
voucher-pointer pages: 3,610
ledger-pointer pages: 3,453
both pointers: 3,453
ledger pointers resolved: 3,453 / 3,453
voucher pointers resolved in direct voucher table: 3,589 / 3,610
```

`voucher_invoice_metadata` from TranMgr view=1:

```text
070525 invoice metadata rows: 741
unique IRNs: 724
records with IRN: 741
conflicting IRNs: 0
```

Accepted IRN sources are record-level gated:

```text
0x01f8 only when submission ack field 0x01f7 is present
0x0019 only when buyer fields 0x000d and 0x0017 are present
0x0001 only when transporter field 0x0007 is present
```

## 25. Sales Stock-Rate-F6 Inventory Row

Synthetic `SALES_XML001` proved a new Sales inventory row family and made the
synthetic inventory workflow scalable without UI entry for vouchers.

Live Tally routing at the time of proof:

```text
10.211.55.3:9000 = Test Synthetic / 100000
10.211.55.3:9001 = Avinash Industries - Chennai Unit - 2025-26 / 70525
```

Artifacts:

```text
out/synthetic-live/SALES_XML001/
out/synthetic-live/SALES_XML001/after_decoded.sqlite3
out/live-xml-diff-070525/sales-stock-rate-f6-live-check/
```

Synthetic truth:

```text
Voucher MASTERID 10, Sales
Item 211 CODX_SYNT_ITEM_MANUAL1
Unit 210 PCS
Sales ledger 212 CODX_SYNTH_LEDGER_SALXML1A
Qty 3.00, rate 44.44, amount 133.32
```

Byte rule:

```text
02 00 00 03 <stock_id>
02 00 00 0c <rate_i64_x100000>
... <unit_id:u32> near rate scale marker
00 50 f6 01 00 10 <len>
  02 00 00 09 <amount_i64_x100000>
  02 00 00 0b <qty_i64_x100000>
```

Ledger pairing is not taken from a following party ledger. It uses the closest
preceding positive amount allocation for the same amount and chooses the
nearest preceding ledger reference.

Validation:

```text
Synthetic after decode:
  voucher 10 / Sales / item 211 / ledger 212 / unit 210 / 3.0 / 44.44 / 133.32

070525 local decode:
  +5 best rows, all source=sales_stock_rate_f6
  all 5 matched Rosetta exact tuple
  hard inventory repair vouchers improved 86 -> 84

070525 live Object export on port 9001:
  MASTERIDs 54605, 55395, 56421, 57536, 59270
  all 5 matched live XML item/ledger/qty/rate/amount
```

This rule is implemented in `tally1800/inventory_decode.py` and emitted through
`voucher_inventory_entries_1800` as high-confidence `sales_stock_rate_f6`.

## 26. Purchase Stock-Rate-F6 Inventory Row

Synthetic `PURCHASE_XML001` proved the Purchase-side version of the
stock-rate-F6 row shape. It is now backed by both controlled synthetic evidence
and Avinash Rosetta replay.

Artifacts:

```text
out/synthetic-live/PURCHASE_XML001/
out/synthetic-live/PURCHASE_XML001/after_decoded.sqlite3
out/070525_decoded_purchase_stock_rate_f6.sqlite3
```

Synthetic truth:

```text
Voucher MASTERID 11, Purchase
Item 211 CODX_SYNT_ITEM_MANUAL1
Unit 210 PCS
Purchase ledger 214 CODX_SYNTH_LEDGER_PURXML1A
Supplier ledger 215 CODX_SYNTH_LEDGER_SUPXML1A
Qty 4.00, rate 55.55, amount -222.20
```

Byte rule:

```text
02 00 00 03 <stock_id>
02 00 00 0c <rate_i64_x100000>
... <unit_id:u32> near rate scale marker
00 50 f6 01 00 10 <len>
  02 00 00 09 <negative_amount_i64_x100000>
  02 00 00 0b <qty_i64_x100000>
```

Ledger pairing uses the closest preceding signed ledger allocation carrying
the same negative amount:

```text
02 00 00 03 <purchase_ledger_id> ... 02 00 00 09 <same_negative_amount>
```

Validation:

```text
Synthetic after decode:
  voucher 11 / Purchase / item 211 / ledger 214 / unit 210 / 4.0 / 55.55 / -222.2

070525 local decode:
  75 rows emitted, all under 31-Puchase(Monthly)
  75 / 75 exact full-tuple matches against Rosetta
  0 extras from this source

31-Puchase(Monthly) trusted table after this rule:
  7583 / 7682 exact full tuples
  136 extras, unchanged from prior trusted table

Hard XML repair queue:
  total inventory repair vouchers: 84 -> 65
  31-Puchase(Monthly) repair vouchers: 36 -> 17
```

This rule is implemented in `tally1800/inventory_decode.py` and emitted through
`voucher_inventory_entries_1800` as high-confidence
`purchase_stock_rate_f6`.

## 27. Lane A Sales Logical Compact Intake

Lane A promoted one narrow Sales page-split parser and rejected the broader
logical F6 promotion.

Accepted source:

```text
sales_logical_compact
  direct source rows in 070525: 1,148
  exact source rows vs Rosetta: 1,128
  source-local extras: 20
```

Productized final table result in
`out/070525_decoded_laneA_sales_logical.sqlite3`:

```text
61 Sales final full tuples:            2,051 / 2,068, extras 83
31-Puchase(Monthly) final full tuples: 7,583 / 7,682, extras 136
xml_repair_queue rows:                    64
```

Rule:

```text
For 61 Sales only, if the already-known compact row shape crosses a 512-byte
page boundary, read a logical row window with page headers stripped. Require:
  stock 0x0002/0003
  inline ledger 0x0069/0003 or resolvable same-amount ledger allocation
  stock repeat 0x0068/0003 or 0x006d/0003
  unit 0x0072/0003
  positive amount 0x006e/0009
  quantity 0x0066/000b
  nearby rate 0x0002/000c or scaled fallback
```

Rejected companion rules:

```text
sales_logical_post_rate_f6 broad promotion
journal f6 flag 0x0c high promotion
journal f6 flag 0x08 / f7 / f8 / f9 / fa / 06 02 tested high promotions
purchase_voucher_unique_ledger high promotion
```

Reason: each rejected rule recovered some real values but admitted enough
Rosetta extras to be unsafe for the final table. Keep them in probe/audit mode.

## 28. Lane C Typed Master and E-Invoice Surface

Lane C promoted typed master tables and a strict e-invoice archive table in
`decode-sqlite`.

Typed master tables:

```text
companies
groups
ledgers
stock_items
units
godowns
cost_centres
voucher_types
```

Classifier:

```text
Manager.1800 master pages:
  00 50 f7 01 00 10 <len> ... 02 00 00 06 <kind:u32le>

core kinds:
  2 group
  3 ledger
  5 cost_centre
  6 godown
  9 stock_item
  10 voucher_type
  12 unit
```

Cross-company validation:

```text
070525: groups 283, ledgers 2968, stock_items 2754, units 23,
        godowns 111, cost_centres 310, voucher_types 125
100001: groups 118, ledgers 417, stock_items 374, units 6,
        godowns 34, cost_centres 6, voucher_types 70
100025: groups 278, ledgers 2456, stock_items 1878, units 23,
        godowns 112, cost_centres 310, voucher_types 119
300925: groups 374, ledgers 1673, stock_items 625, units 22,
        godowns 65, cost_centres 197, voucher_types 108
```

`voucher_types.export_policy` is required. `kind=10` alone is not equivalent to
exportable voucher type:

```text
exportable_observed
status_observed
internal_candidate
```

Statutory and invoice surfaces:

```text
voucher_statutory_status:
  070525 rows=8350, warnings=0, date/type multiset=8345/8350
  100001 rows=1324, warnings=0, date/type multiset=1324/1324
  100025 rows=8198, warnings=0, date/type multiset=8195/8198
  300925 rows=388,  warnings=0, date/type multiset=388/388

voucher_invoice_metadata:
  070525 rows=741, unique_irns=724, conflicts=0
  100025 rows=931, unique_irns=897, conflicts=0

voucher_einvoice_archive:
  070525 rows=16, unique_irns=13, all archive IRNs overlap view=1 metadata
```

Rejected/audit-only:

```text
render-time/default master fields as direct fields
ungated 0x0019 IRN extraction
broad view>=3 archive scans
e-way-only 12-digit scans
StatStatus as unique voucher MASTERID resolver
```

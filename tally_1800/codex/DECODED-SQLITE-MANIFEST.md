# Decoded SQLite Manifest

Generated from:

`office/_workspace-admin/data-dumps/avinash-tally-live-2026-04-21`

Decoder:

```bash
python3 -m tally1800 decode-sqlite <company-folder> out/<company-id>_decoded.sqlite3
```

## Outputs

| Company folder | SQLite | Size | Source files | Tagged strings | Manager records with names | Voucher text pages | Company guess | GSTIN guess |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `070525` | `out/070525_decoded.sqlite3` | 206.4 MiB | 17 | 1,054,473 | 19,128 | 181,653 | Avinash Industries - Chennai Unit - 2025-26 | 33AAPFA0733K1ZJ |
| `100001` | `out/100001_decoded.sqlite3` | 20.2 MiB | 17 | 105,754 | 4,613 | 17,999 | Avinash Precision Components - 2024-2025 | |
| `100011` | `out/100011_decoded.sqlite3` | <0.1 MiB | 8 | 60 | 0 | 0 | Avinash Industries 2019-20&2020-21 | |
| `100025` | `out/100025_decoded.sqlite3` | 194.8 MiB | 18 | 1,011,719 | 16,109 | 167,643 | Avinash Industries - Chennai Unit - 2023-24 - (Audited) | 33AAPFA0733K1ZJ |
| `300925` | `out/300925_decoded.sqlite3` | 15.4 MiB | 14 | 82,387 | 9,597 | 7,421 | Avinash Appliances | |

## Tables

Each decoded SQLite file contains:

- `source_files`
- `raw_tlvs` in outputs regenerated after the TLV scanner update
- `compact_numeric_fields` in outputs regenerated after the compact scanner
  update
- `voucher_header_candidates` in outputs regenerated after the compact scanner
  update
- `vch_status_slots` in outputs regenerated after the VchStatus fixed-slot
  update
- `field_strings`
- `company_strings`
- `company_guess`
- `manager_records`
- `manager_master_records`
- `manager_master_kind_guesses`
- `voucher_text_pages`
- `voucher_inventory_entry_candidates` in outputs regenerated after the
  inventory decoder update
- `voucher_inventory_entries_1800` in outputs regenerated after the inventory
  decoder update
- `voucher_ledger_entry_candidates` and `voucher_ledger_entries_1800` in
  outputs regenerated after the ledger decoder update
- `linkmgr_allocation_pages`
- `voucher_bill_allocation_candidates_1800`
- `voucher_bank_allocation_candidates_1800`
- `voucher_order_allocation_candidates_1800`
- `voucher_statutory_status`
- `voucher_invoice_metadata`
- `voucher_einvoice_archive`
- `voucher_decode_audit` in outputs regenerated after the audit update
- `xml_repair_queue` in outputs regenerated after the audit update

Current regenerated outputs also include production selector views:

- `production_voucher_headers_1800`
- `production_ledger_entries_1800`
- `production_inventory_entries_1800`
- `production_no_row_hard_misses_1800`
- `production_review_queue_1800`
- `production_xml_repair_queue_1800`

`raw_tlvs` preserves high-confidence typed TLV evidence with `file_name`,
page/offset, `field_id`, `type_hex`, length, confidence, and decoded text/number
columns. Existing large decoded outputs listed above were generated before this
schema addition and should be regenerated when this table is needed.

`compact_numeric_fields` captures compact fields such as
`0x0bbb/0006 = voucher MASTERID` and `0x0067/000d = voucher date serial
candidate`, plus current diagnostic fields such as `0x00cb/0003`,
`0x0bbb/0003`, and sparse `0x0bbf/0006`. `voucher_header_candidates` derives
one row per compact `0x0bbb/0006` hit.

`vch_status_slots` captures non-empty `VchStatus.1800` fixed slots with physical
slot sequence, page/offset, flag words, date serial, voucher type `MASTERID`,
and all 32 raw words as JSON. This is evidence only; slot-to-voucher order is
not solved.

Current regenerated compact artifact:

| SQLite | compact fields | voucher header candidates | VchStatus slots | inventory candidates | inventory best | audit rows | repair queue |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `out/070525_decoded_inventory.sqlite3` | 130,913 | 31,113 | 30,564 | 242,431 | 100,417 | 30,564 | 86 |

Inventory validation for `out/070525_decoded_inventory.sqlite3` against the
Rosetta XML SQLite:

| Table | Voucher type | Exact | Extra unique |
| --- | --- | ---: | ---: |
| `voucher_inventory_entries_1800` | Conversion Stock Journal | 71,016 / 71,518 | 415 |
| `voucher_inventory_entries_1800` | Stock Journal | 19,214 / 19,831 | 196 |
| `voucher_inventory_entries_1800` | 31-Puchase(Monthly) | 7,536 / 7,682 | 136 |
| `voucher_inventory_entries_1800` | 61 Sales | 2,048 / 2,068 | 83 |
| `voucher_inventory_entry_candidates` | Conversion Stock Journal | 71,208 / 71,518 | 48,455 |
| `voucher_inventory_entry_candidates` | Stock Journal | 19,554 / 19,831 | 15,301 |
| `voucher_inventory_entry_candidates` | 31-Puchase(Monthly) | 7,642 / 7,682 | 5,193 |
| `voucher_inventory_entry_candidates` | 61 Sales | 2,048 / 2,068 | 83 |

Audit semantics:

- `confidence` is internal evidence strength, not guaranteed truth.
- `needs_xml_repair=1` is a hard gap suitable for targeted XML fetch.
- `needs_xml_review=1` is broader ambiguity; it can drive sampling, balance
  checks, or lower-priority XML repair.

Current `070525` audit counts:

```text
voucher_decode_audit rows: 30564
needs_xml_review:         15322
needs_xml_repair:            86
xml_repair_queue rows:       86
```

## Example Queries

Company identity:

```sql
select * from company_guess;
```

Master-like names:

```sql
select page_no, name, guid, user_text
from manager_records
where name is not null
limit 100;
```

Voucher/transaction text pages:

```sql
select page_no, party_or_name, gstin, narration_or_address
from voucher_text_pages
where party_or_name is not null or gstin is not null
limit 100;
```

All decoded tagged fields for a page:

```sql
select field_id, page_offset, text
from field_strings
where file_name = 'TranMgr.1800' and page_no = 51
order by page_offset;
```

Typed TLV histogram inside regenerated SQLite:

```sql
select file_name, field_id, type_hex, count(*) as n
from raw_tlvs
group by file_name, field_id, type_hex
order by n desc
limit 50;
```

Voucher header candidates:

```sql
select master_id, guid_guess, page_no, voucher_date_guess
from voucher_header_candidates
order by master_id
limit 100;
```

VchStatus slots:

```sql
select slot_seq, date_guess, voucher_type_master_id, flag_word0, flag_word1
from vch_status_slots
order by slot_seq
limit 100;
```

High-confidence decoded inventory rows:

```sql
select voucher_master_id, voucher_type_name, stock_item_name, ledger_name,
       quantity_value, rate_value, amount_value, sources
from voucher_inventory_entries_1800
order by voucher_master_id, entry_id
limit 100;
```

Broader inventory candidates for targeted repair:

```sql
select voucher_master_id, voucher_type_name, stock_item_name, ledger_name,
       quantity_value, rate_value, amount_value, source, confidence
from voucher_inventory_entry_candidates
where confidence != 'high'
limit 100;
```

Hard XML repair queue:

```sql
select voucher_master_id, voucher_type_name, reason_codes_json, request_hint_json
from xml_repair_queue
order by repair_priority desc, voucher_master_id;
```

Review ambiguous vouchers:

```sql
select voucher_master_id, voucher_type_name, issue_codes_json, evidence_json
from voucher_decode_audit
where needs_xml_review = 1 and needs_xml_repair = 0
order by repair_priority desc, voucher_master_id
limit 100;
```

Production-facing direct rows:

```sql
select *
from production_inventory_entries_1800
order by voucher_master_id, entry_id
limit 100;
```

Allocation repair hints:

```sql
select page_no, voucher_master_id, ledger_name, bill_name,
       original_amount_scaled, application_amount_scaled, confidence
from voucher_bill_allocation_candidates_1800
limit 100;

select page_no, voucher_master_id, party, amount_scaled, payment_mode,
       utr_or_instrument, confidence
from voucher_bank_allocation_candidates_1800
limit 100;
```

## Caveat

This is a first-pass offline decode. It is not yet a full accounting-grade
voucher/ledger/stock parser. The current output is already useful for search,
inspection, party/master discovery, GSTIN extraction, address/narration mining,
and format reverse engineering.

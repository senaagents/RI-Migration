# tallydatacrack-codex

Research and migration toolkit for reading Tally/TallyPrime company data files
directly, with a targeted TDL/XML repair path for gaps when Tally is available.

Status: reverse-engineering workspace plus emerging migration pipeline. The
core decoder is read-only and emits direct SQLite tables, production selector
views, candidate/audit tables, and an XML repair queue. The guarded synthetic
lab can write only to a disposable synthetic Tally company for controlled
mapping discovery.

## Goals

- Accept a Tally company folder containing `.1800`, `.900`, and `.TSF` files.
- Detect file roles, release family, encryption/compression signals, and page layout.
- Build a documented binary specification from observed data.
- Export raw pages, strings, and candidate records to SQLite for analysis.
- Decode masters, vouchers, ledger lines, stock lines, and allocation
  candidates with explicit confidence/provenance.
- Use targeted read-only TDL/XML only for hard gaps and parser learning.

## Non-goals

- No Tally client, XML API, ODBC, or TDL dependency for the primary direct
  `.1800` decode path.
- No broad XML export as the main migration strategy.
- No password brute forcing. If TallyVault is enabled, the user supplies the vault
  password.
- No writes to source company data.

## Quick Start

```bash
python3 -m tally1800 inspect /path/to/company-folder
python3 -m tally1800 strings /path/to/company-folder --min-len 5
python3 -m tally1800 sqlite /path/to/company-folder out/raw_probe.sqlite3
python3 -m tally1800 decode-sqlite /path/to/company-folder out/decoded.sqlite3
python3 -m tally1800 tlv-hist /path/to/company-folder/TranMgr.1800 --top 50
python3 -m tally1800 compact-scan /path/to/company-folder/TranMgr.1800 --field-id 0x0bbb --type-hex 0006
python3 -m tally1800 compact-scan /path/to/company-folder/TranMgr.1800 --extended --type-hex 0009
PYTHONPATH=. python3 scripts/probe_voucher_keys.py /path/to/company-folder/TranMgr.1800 --rosetta /path/to/rosetta.sqlite3
PYTHONPATH=. python3 scripts/probe_voucher_masterids.py /path/to/company-folder/TranMgr.1800 --rosetta /path/to/rosetta.sqlite3
PYTHONPATH=. python3 scripts/probe_status_slots.py /path/to/company-folder/VchStatus.1800 --rosetta /path/to/rosetta.sqlite3
PYTHONPATH=. python3 scripts/probe_inventory_item_refs.py out/decoded_headers.sqlite3 --manager-decoded out/decoded_compact.sqlite3 --tranmgr /path/to/company-folder/TranMgr.1800 --rosetta /path/to/rosetta.sqlite3
PYTHONPATH=. python3 scripts/probe_voucher_child_anchors.py out/decoded_headers.sqlite3 --tranmgr /path/to/company-folder/TranMgr.1800 --rosetta /path/to/rosetta.sqlite3
PYTHONPATH=. python3 scripts/probe_sales_inventory_runs.py out/decoded_headers.sqlite3 --tranmgr /path/to/company-folder/TranMgr.1800 --rosetta /path/to/rosetta.sqlite3 --voucher-type '61 Sales'
PYTHONPATH=. python3 scripts/probe_journal_inventory_rows.py out/decoded_headers.sqlite3 --tranmgr /path/to/company-folder/TranMgr.1800 --rosetta /path/to/rosetta.sqlite3 --voucher-type 'Conversion Stock Journal' --voucher-type 'Stock Journal'
PYTHONPATH=. python3 scripts/probe_purchase_inventory_rows.py out/decoded_headers.sqlite3 --tranmgr /path/to/company-folder/TranMgr.1800 --rosetta /path/to/rosetta.sqlite3
PYTHONPATH=. python3 -m tally1800 synthetic-diff before-company after-company --file TranMgr.1800 --sentinel SYNTH_LEDGER_001 --number 1234.56 --date 2026-04-01 --out out/synth-diff.json
PYTHONPATH=. python3 scripts/synthetic_tally_lab.py --verify-only
PYTHONPATH=. python3 scripts/synthetic_tally_lab.py --company-dir live-tally-data/100000 --run-id LEDGER_EXP001 --suffix EXP1846A --mode ledger --ledger-parent 'Indirect Expenses' --file Manager.1800 --file Index.1800 --file TMESSAGE.TSF --file Company.1800 --file CmpSave.1800 --file TSTATE.TSF
```

## Layout

- `MISSION-CONTRACT.md`: canonical product mission, success definitions, and
  `.1800` plus TDL/XML migration contract.
- `SPEC.md`: working binary-format notes.
- `STATUS-2026-04-27.md`: current honest progress against the user's 100%
  `.1800 -> SQLite` definition.
- `BREAKTHROUGHS-2026-04-27.md`: compact cross-team handoff of current
  breakthroughs and blockers.
- `RESEARCH.md`: public-source research notes and URLs.
- `RECONCILIATION-2026-04-27.md`: live XML / XML SQLite / `.1800` SQLite
  reconciliation notes.
- `WAVE2-PRODUCTIZATION-2026-04-28.md`: reviewed Wave 2 handoffs, ported
  production surfaces, and rejected/research-only rules.
- `SYNTHETIC-MAPPING-PLAYBOOK.md`: proven live synthetic mapping method,
  reproducible commands, known artifacts, and exact continuation rules.
- `PARALLEL-HYPOTHESIS-PLAN.md`: how to split byte-rule discovery into
  independent proof/rejection cards for Codex and Claude-style research agents.
- `tally1800/`: Python package.
- `scripts/probe_voucher_keys.py`: reproducible probe for the partial
  `TranMgr.1800` field `0x0bbe` to XML/Rosetta `voucher_key` bridge.
- `scripts/probe_voucher_masterids.py`: reproducible probe for compact
  `TranMgr.1800` field `0x0bbb/0006` to XML/Rosetta voucher `MASTERID`.
- `scripts/probe_voucher_child_anchors.py`: reproducible probe for current
  ledger/inventory child anchor families and scaled numeric values.
- `scripts/probe_sales_inventory_runs.py`: strict probe for the current sales
  inventory compact row family.
- `scripts/probe_sales_matrix_rows.py`: targeted matrix-row probe used to union
  additional exact sales inventory rows.
- `scripts/probe_journal_inventory_rows.py`: typed probe for non-sales stock
  journal inventory row families.
- `scripts/probe_purchase_inventory_rows.py`: hybrid typed probe for
  `31-Puchase(Monthly)` inventory rows including ledger association.
- `tally1800/inventory_decode.py`: production-path direct inventory decoders
  used by `decode-sqlite`.
- `tally1800/synthetic_diff.py` and `scripts/synthetic_diff.py`: controlled
  snapshot diff tool for synthetic experiments. It searches injected sentinel
  names, numbers, dates, TLV deltas, compact numeric hits, changed pages, and
  local byte windows.
- `scripts/synthetic_tally_lab.py`: guarded live synthetic runner. It can verify
  the synthetic Tally company, snapshot a readable company folder, import
  sentinel masters plus Payment/Receipt/Journal/Contra vouchers, snapshot again,
  and run `synthetic-diff`. It refuses to write unless the target company name
  looks synthetic and a readable company folder is supplied, unless explicitly
  forced.

## Decoded SQLite Tables

`decode-sqlite` writes first-pass semantic tables:

- `source_files`: source file inventory and hashes.
- `field_strings`: tagged Tally field strings decoded from
  `02 10 <field_id:u16> 00 0f <length:u16> <utf16le>`.
- `raw_tlvs`: typed TLV evidence decoded from
  `02 10 <field_id:u16> <type:u16> <length:u16> <value>`.
- `compact_numeric_fields`: compact numeric fields such as voucher
  `MASTERID` and date candidates.
- `extended_numeric_fields`: 14-byte extended numeric slots
  `80 00 <field_id> <type> <i64>`, currently emitted for `TranMgr.1800`
  `type=0009` signed/scaled accounting values.
- `voucher_header_candidates`: candidate voucher headers from compact
  `0x0bbb/0006`, with GUID/date guesses.
- `vch_status_slots`: non-empty `VchStatus.1800` fixed slots with date serial,
  voucher type `MASTERID`, flags, and raw slot words.
- `voucher_statutory_status`: fixed-slot `StatStatus.1800` statutory voucher
  status rows. On `070525`, this emits 8,350 clean reportable slots.
- `linkmgr_allocation_pages`: `LinkMgr.1800` allocation pages with typed
  voucher/ledger back-pointers and heuristic bill type.
- `voucher_bill_allocation_candidates_1800`,
  `voucher_bank_allocation_candidates_1800`, and
  `voucher_order_allocation_candidates_1800`: candidate/audit allocation rows
  from LinkMgr raw fields. These are repair hints, not final allocation tables.
- `company_strings`: decoded strings from `Company.1800`.
- `company_guess`: guessed company name, GSTIN, email, state, country, pincode.
- `manager_records`: one row per `Manager.1800` page with best-effort master
  name/GUID/user text.
- `manager_master_records`: one row per grouped `Manager.1800` master id.
- `manager_master_kind_guesses`: direct master-kind guesses from Manager
  `0x01f7` kind-code sub-records, with field-anchor fallback evidence.
- `voucher_text_pages`: one row per `TranMgr.1800` page with best-effort party,
  GSTIN, address/narration, and all decoded strings as JSON.
- `voucher_ledger_entry_candidates`: direct ledger row candidates from
  `0x52d3`, `0x0002`, `0x0006`, and traced `0x2713` sources.
- `voucher_ledger_entries_1800`: de-duplicated direct ledger rows. This table
  is source-gated for high-confidence rows. On `070525`, it is `93.51%`
  recall / `100.00%` precision against Rosetta.
- `voucher_inventory_entry_candidates`: broader direct inventory row candidates
  with source/confidence/evidence columns.
- `voucher_inventory_entries_1800`: de-duplicated high-confidence direct
  inventory rows. This table is useful, but it is not complete yet.
- `voucher_invoice_metadata`: TranMgr view=1 e-invoice/e-way metadata. On
  `070525`, this emits 741 IRN records, 724 unique IRNs, and 0 conflicts.
- `voucher_decode_audit`: per-voucher confidence/evidence audit. Confidence
  means internal evidence strength, not guaranteed truth.
- `xml_repair_queue`: hard-gap voucher list for targeted XML repair.

Production-facing views:

- `production_voucher_headers_1800`
- `production_ledger_entries_1800`
- `production_inventory_entries_1800`
- `production_no_row_hard_misses_1800`
- `production_review_queue_1800`
- `production_xml_repair_queue_1800`

Use these views as the app boundary. Do not expose `_candidates` tables as final
business rows.

This is not a final accounting-grade decoder yet, but it is already a useful
offline SQLite extraction from `.1800` files without the Tally client.

## Current Truth

This project has **not** fully cracked `.1800` yet.

Working:

- non-vaulted `.1800` file inventory
- 512-byte page probing
- tagged UTF-16 field extraction
- typed TLV scanning and histogramming
- first-pass decoded SQLite
- `Manager.1800` page `MASTERID` discovery for master records
- `TranMgr.1800` compact `0x0bbb/0006` voucher `MASTERID` bridge
- `070525` exportable voucher header identity/date proof:
  `30,545/30,545` Rosetta `MASTERID`, GUID, and voucher-date matches after the
  `0x0003` key and base-type-31 filter
- `VchStatus.1800` fixed-slot voucher date and voucher-type evidence
- Claude's voucher-type date/cluster pairing ported into `decode-sqlite` as
  `paired_voucher_type_master_id`; current `070525` validation is
  `30,464/30,545` exact against Rosetta voucher type names
- page-aware TLV walking for multi-page records, avoiding the old cross-page
  mojibake/header-read issue
- `Manager.1800` `0x01f7` kind-code classifier for core master kinds
  (`group`, `ledger`, `stock_item`, `voucher_type`, `unit`, `godown`,
  `cost_centre`), replacing Rosetta-name lookup for these ids
- field-chain typed master guesses with shape validators in
  `manager_master_records`
- voucher child-line stock item and ledger references as Manager `MASTERID`
  integers inside `TranMgr` page groups
- partial voucher child anchors: inventory identity is now reproducible at
  `115,505/115,505` Rosetta rows on `070525`; values are present for
  `114,411/115,505` in the conservative probe
- `61 Sales` full inventory tuple probe on `070525`: compact parser
  `2,042/2,068`, matrix probe `1,501/2,068`, union `2,064/2,068` exact with
  zero item+amount-only leftovers
- Conversion Stock Journal / Stock Journal typed inventory probe on `070525`:
  `71,257/71,518` and `19,577/19,831` exact respectively after the logical F6
  body reader
- `31-Puchase(Monthly)` on `070525`: `7,667/7,682` exact item/qty/rate/amount
  tuples and `7,645/7,682` exact full tuples including ledger after unioning
  Sales compact rows with the purchase hybrid ledger rules
- `decode-sqlite` now writes direct inventory SQLite tables. On `070525`,
  high-confidence `voucher_inventory_entries_1800` validates against Rosetta at:
  `71,016/71,518` Conversion Stock Journal value tuples, `19,214/19,831`
  Stock Journal value tuples, `7,536/7,682` Purchase Monthly full tuples, and
  `2,048/2,068` Sales full tuples. The broader
  `voucher_inventory_entry_candidates` table reaches higher coverage but has
  known extra candidates and must not be treated as final accounting rows.
- `decode-sqlite` also writes `voucher_decode_audit` and `xml_repair_queue`.
  On `070525`, the audit marks `15,322/30,564` vouchers for review, but only
  `86` vouchers are hard XML-repair gaps (`no high-confidence inventory rows`
  for inventory voucher types).
- Synthetic-diff tooling is now proven useful on controlled live mutations, not
  only smoke tests. See `SYNTHETIC-MAPPING-PLAYBOOK.md`.
- Live synthetic lab target exists in Tally and its `.1800` folder is readable
  from macOS through `live-tally-data/100000`:
  `Test Synthetic`, GUID `f776087f-35d2-4bee-bb80-6307328dbfb9`, company number
  `100000`.
- Live synthetic proof so far:
  - `LEDGER_EXP001`: created ledger `CODX_SYNTH_LEDGER_EXP1846A`; direct
    `.1800` SQLite decoded `Manager.1800` master id `207`, and live XML
    confirmed `MASTERID=207`.
  - `PAY001`/`PAY002`: created two Payment vouchers; direct `.1800` SQLite
    decoded voucher `MASTERID` `1` and `2`, date `2026-04-01`, Payment type
    `MASTERID=34`, voucher number, key, and narration; live XML confirmed the
    same objects and amounts.

RAM scraping note:

- Worth exploring as a research microscope if we can dump Windows
  `TallyPrime.exe` memory or the Parallels VM memory after loading a controlled
  company.
- Not a production foundation. It is brittle across Tally builds, Windows
  versions, VM state, and object lifetimes.
- Best use: search memory for synthetic sentinel names/numbers and compare
  in-memory decoded object layout with the on-disk `.1800` bytes.

Not solved:

- full typed voucher headers beyond identity/date
- complete voucher ledger/inventory amounts, quantities, rates, signs, and row
  boundaries across all voucher types
- full master record classification
- vaulted/encrypted `.1800`
- universal support for all Tally companies without a vault password

The success target is full typed SQLite with reconciliation, not string dumps.

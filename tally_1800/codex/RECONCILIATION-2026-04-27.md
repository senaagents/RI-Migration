# Reconciliation - 2026-04-27

Company: `Avinash Industries - Chennai Unit - 2025-26`

Fiscal window: `2025-04-01` to `2026-03-31`

## Inputs

- Live TallyPrime XML endpoint: `10.211.55.3:9000`
- Existing XML-derived SQLite:
  `/Users/aakashchid/workshop/sena/office/avinash/tally-migration-2025-04-01/avinash_tally_live_2026-04-25.sqlite3`
- Direct `.1800` decoded SQLite:
  `out/070525_decoded.sqlite3`
- Raw `.1800` source snapshot:
  `office/_workspace-admin/data-dumps/avinash-tally-live-2026-04-21/070525`

## Scars/Rules Applied

Read and applied:

- `senacode/scars/tally-db-pipeline-cli-options.md`
- `senacode/scars/tally-full-fetch-voucher-extraction-stalls.md`
- `senacode/scars/tally-sqlite-parser-coverage-vs-raw-capture.md`
- `senacode/scars/codex-sandbox-blocks-tally-http.md`
- `senacode/scars/tally-http-requires-ui-toggle.md`
- `senacode/scars/tally-date-window-out-of-range.md`
- `senacode/scars/tally-malformed-xml-crashes-server.md`
- `senacode/scars/tally-stock-opening-date-extraction.md`
- `senacode/scars/tally-company-folders-encrypted.md`
- `senacode/playbooks/tally-sqlite-first-leg-audit.md`

Important constraints:

- No parallel Tally HTTP requests.
- Do not broad-fetch detailed vouchers.
- Use full-period lightweight voucher profile for live reconciliation.
- Use pipeline's XML cleaning rules for invalid character references.
- Compare fiscal-window data consistently; the old XML DB includes later April
  2026 rows, so it must be filtered for FY 2025-26.

## Live Tally Health

Command:

```bash
TALLY_HOST=10.211.55.3 \
TALLY_PORT=9000 \
TALLY_TIMEOUT_SECONDS=30 \
DATABASE_URL='sqlite:////Users/aakashchid/workshop/sena/tallydatacrack-codex/out/live_profile_scratch.sqlite3' \
/Users/aakashchid/workshop/tally-db-pipeline/.venv/bin/tally-db-pipeline doctor \
  --company 'Avinash Industries - Chennai Unit - 2025-26'
```

Result:

```text
Connected to http://10.211.55.3:9000
Health status: healthy
Companies discovered: 1
Company discovery ok: True
Voucher-type count: 118
Master-data group count: 284
Master-data ledger count: 2969
```

The raw live XML includes one CMPINFO counter node per type, so parsed real
master counts are one less for those object collections.

## Live XML vs Existing XML SQLite

Fresh live XML and the existing XML-derived SQLite match 100% for the comparable
FY 2025-26 surface:

| Surface | Live XML | XML SQLite | Match |
| --- | ---: | ---: | ---: |
| Groups | 283 | 283 | 100% |
| Ledgers | 2,968 | 2,968 | 100% |
| Stock Items | 2,754 | 2,754 | 100% |
| Voucher Types | 117 | 117 | 100% |
| Godowns | 111 | 111 | 100% |
| Units | 23 | 23 | 100% |
| Voucher GUIDs | 29,686 | 29,686 | 100% |
| Voucher Master IDs | 29,686 | 29,686 | 100% |
| Voucher composite keys | 29,686 | 29,686 | 100% |

The existing XML SQLite is internally complete for this FY window:

```text
SQLite integrity_check: ok
FY voucher headers: 29,686
FY detailed vouchers: 29,686
FY missing details: 0
missing_guid: 0
missing_master_id: 0
```

Voucher type count diffs: none.

## XML SQLite vs Direct `.1800` SQLite

The `.1800` decoder is not yet a full typed accounting parser. Current output is
tagged string/page-level SQLite. Therefore this comparison is a coverage check,
not a final equality proof.

Coverage against XML SQLite:

| Surface | Coverage |
| --- | ---: |
| Groups via `manager_records.name` | 100.00% |
| Ledgers via `manager_records.name` | 94.04% |
| Ledgers via all decoded text | 96.43% |
| Stock Items via `manager_records.name` | 99.53% |
| Stock Items via all decoded text | 99.78% |
| Voucher Types | 100.00% |
| Godowns | 100.00% |
| Units | 100.00% |
| Voucher party GSTINs in `TranMgr.1800` text | 98.22% |
| Voucher numbers in `TranMgr.1800` text | 96.61% |
| Voucher party names in `TranMgr.1800` text | 72.02% |

Voucher GUIDs and Master IDs do not appear as plain tagged UTF-16 strings in
`TranMgr.1800`, so string-only decoding does not match them.

After the compact-field pass, voucher `MASTERID` has a strong direct bridge:

| Surface | Direct `.1800` evidence | XML/Rosetta | Result |
| --- | ---: | ---: | --- |
| Compact `0x0bbb/0006` values | 31,113 | | found |
| Rosetta voucher `MASTERID`s | | 30,545 | |
| Matched voucher `MASTERID`s | 30,545 | 30,545 | 100% of Rosetta |
| Missing Rosetta `MASTERID`s | 0 | | none |
| Extra compact IDs | 568 | | unresolved |

This is still not full voucher extraction: the 568 extras must be classified,
and voucher child lines/boundaries are not decoded.

Additional status/type evidence now captured in regenerated
`out/070525_decoded_headers.sqlite3`:

| Surface | Direct `.1800` evidence | Result |
| --- | ---: | --- |
| `VchStatus.1800` non-empty slots | 30,564 | equals keyed voucher candidates |
| Internal base-type-31 candidates | 19 | explain Rosetta slot surplus |
| `(date, voucher_type_master_id)` multiset | 30,545 + 19 | matches Rosetta exportable vouchers plus internal rows |

Slot-to-voucher ordering is not proven. The best current mapping is close to
`MASTERID` order with two legacy low ids inserted into an Apr-30 cluster, but
duplicate date/type rows keep some assignments ambiguous.

For voucher child lines, raw string search is the wrong model. Stock items and
ledgers appear as Manager `MASTERID` integers inside the voucher page group.
Sampled stock/ledger reference probes hit 100% on several voucher types.
Money amounts are now partially decoded as signed little-endian integers scaled
by `100000`, but row pairing, quantities, rates, and signs still block typed
line tables.

Fresh child-anchor probes:

| Surface | Current best evidence |
| --- | ---: |
| Rich direct+summary ledger anchors | 35,091 / 40,781 |
| Conservative scripted ledger anchors | 29,764 / 40,781 |
| Conservative scripted inventory identity | 115,505 / 115,505 |
| Conservative scripted inventory any value | 114,411 / 115,505 |

Sales rows now have strong quantity/rate candidates:
`0x0001/0008 = quantity * 100000`, `0x0002/000c = rate * 100000`, and
inventory amount commonly at `0x006e/0009 = amount * 100000`.

## New `.1800` Finding From Reconciliation

`Manager.1800` page offset `+4` maps to Tally XML `MASTERID` for many master
records. The XML GUID suffix is the same value as 8-digit hex.

Examples:

| Name | Manager page | `u32(+4)` | XML GUID suffix |
| --- | ---: | ---: | --- |
| `00A0 CUB -CCOD-20054930` | 4655 | 2431 | `0000097f` |
| `S.S.Normal Scraps` | 21577 | 7868 | `00001ebc` |
| `E10.1 Accounts / Admin` | 3791 | 2189 | `0000088d` |

This raises the path forward: build typed master tables from `Manager.1800`
using page `MASTERID`, page classification, and field IDs, then compare by GUID
instead of by name.

`TranMgr.1800` also has a compact numeric field vector. The strongest field is:

```text
bb 0b 00 06 <MASTERID:u32le> 00 00
```

This appears on voucher header pages and gives the XML voucher `MASTERID`.
GUID can then be guessed as `<company_guid>-<master_id:08x>`.

## Not 100% Yet

Do not interpret the current `.1800` decoded DB as a full replacement for Tally
or for the XML SQLite.

Current known blockers:

- typed master classification is incomplete
- duplicate names across master classes can join to the wrong page
- voucher `MASTERID` location is now known for `070525`, but extra compact IDs,
  record liveness, and page-chain boundaries are not solved
- voucher ledger and inventory child lines are not decoded into accounting
  tables
- vault/encryption support is not implemented

The correct bar is `.1800 -> typed SQLite -> reconciliation with live/XML at
master, voucher header, ledger-line, inventory-line, and raw-record coverage
levels`.

## Snapshot Caveat

The direct `.1800` input is an older filesystem snapshot:

```text
070525/Company.1800: 2026-04-21 18:00:58
070525/TranMgr.1800: 2026-04-21 18:00:58
XML SQLite:          2026-04-26 19:41:18
Live Tally:          queried 2026-04-27
```

So direct `.1800` vs XML/live mismatch is not necessarily pure parser failure.
Some differences may be real business-data changes after `2026-04-21`.

## Artifacts

- Full report:
  `out/reconcile-070525/reconcile-report.md`
- Machine-readable report:
  `out/reconcile-070525/reconcile-report.json`
- Missing/extra sample CSVs:
  `out/reconcile-070525/*.csv`
- Live XML captures:
  `out/live-xml-diff-070525/*-response.xml`
- Pipeline profile scratch DB:
  `out/live_profile_scratch.sqlite3`

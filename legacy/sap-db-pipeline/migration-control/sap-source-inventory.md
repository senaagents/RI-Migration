# SAP Source Inventory

The first live profiling run should update this file with command output from:

```bash
sap-db-pipeline profile-documents --from-date 2025-04-01 --to-date 2026-04-25
sap-db-pipeline report
```

Known SAP B1 source tables in scope:

- `OACT`, `OCRG`, `OCRD`, `CRD1`, `CRD7`, `OCPR`
- `OITB`, `OUOM`, `OWHS`, `OITM`, `ITM1`, `OITW`
- `OINV`, `INV1`, `ORIN`, `RIN1`
- `OPCH`, `PCH1`, `ORPC`, `RPC1`
- `ORCT`, `OVPM`
- `OJDT`, `JDT1`
- `ORDR`, `RDR1`, `OPOR`, `POR1`
- `ODLN`, `DLN1`, `OPDN`, `PDN1`
- `OIGN`, `IGN1`, `OIGE`, `IGE1`, `OWTR`, `WTR1`
- `OWOR`, `WOR1`

## Live Profile: 2026-04-25

Window: `2025-04-01` through `2026-04-25`

| Family | Header | Total | Window | Min Date | Max Date |
| --- | ---: | ---: | ---: | --- | --- |
| Sales invoices | `OINV` | 11,502 | 7,089 | 2018-02-22 | 2026-04-24 |
| Sales credit memos | `ORIN` | 3,259 | 2,560 | 2024-11-01 | 2026-04-24 |
| Purchase invoices | `OPCH` | 17,343 | 11,141 | 2018-08-31 | 2026-04-24 |
| Purchase credit memos | `ORPC` | 889 | 667 | 2024-11-22 | 2026-04-24 |
| Incoming payments | `ORCT` | 5,837 | 4,512 | 2024-11-01 | 2026-04-23 |
| Outgoing payments | `OVPM` | 9,823 | 7,153 | 2024-11-04 | 2026-04-24 |
| Journals | `OJDT` | 143,977 | 109,593 | 2018-02-22 | 2026-04-24 |
| Sales orders | `ORDR` | 5,366 | 3,774 | 2024-10-04 | 2026-04-24 |
| Purchase orders | `OPOR` | 4,921 | 3,671 | 2023-08-11 | 2026-04-24 |
| Deliveries | `ODLN` | 8,855 | 6,682 | 2024-11-12 | 2026-04-24 |
| Purchase receipts | `OPDN` | 6,630 | 4,937 | 2024-10-14 | 2026-04-24 |
| Goods receipts | `OIGN` | 24,414 | 19,957 | 2024-10-31 | 2026-04-24 |
| Goods issues | `OIGE` | 32,281 | 26,416 | 2024-10-31 | 2026-04-24 |
| Inventory transfers | `OWTR` | 9,098 | 8,493 | 2024-11-26 | 2026-04-24 |
| Production orders | `OWOR` | 24,474 | 19,746 | 2024-11-01 | 2026-04-24 |

## Live Master Stage: 2026-04-25

| Table | Staged Rows |
| --- | ---: |
| `OBPL` | 6 |
| `OACT` | 600 |
| `OCRG` | 13 |
| `OCRD` | 2,274 |
| `CRD1` | 6,686 |
| `CRD7` | 5,372 |
| `OCPR` | 21 |
| `OITB` | 13 |
| `OUOM` | 7 |
| `OWHS` | 29 |
| `OITM` | 4,969 |
| `ITM1` | 49,690 |
| `OITW` | 144,101 |

## Live Transaction Stage: 2026-04-25

Window: `2025-04-01` through `2026-04-25`

Staged SQLite totals:

- SAP document headers: 236,404
- SAP document lines: 727,503

| Family | Headers | Lines |
| --- | ---: | ---: |
| Sales invoices | 7,089 | 47,602 |
| Sales credit memos | 2,560 | 5,454 |
| Purchase invoices | 11,141 | 30,642 |
| Purchase credit memos | 667 | 1,454 |
| Incoming payments | 4,512 | 0 |
| Outgoing payments | 7,153 | 0 |
| Journals | 109,594 | 318,538 |
| Sales orders | 3,774 | 48,588 |
| Purchase orders | 3,671 | 10,360 |
| Deliveries | 6,682 | 47,180 |
| Purchase receipts | 4,946 | 12,162 |
| Goods receipts | 19,957 | 29,880 |
| Goods issues | 26,416 | 73,716 |
| Inventory transfers | 8,493 | 31,534 |
| Production orders | 19,749 | 70,393 |

Post-run source profile changed while staging was in progress:

- `OJDT` window count became 109,598.
- `OPDN` window count became 4,951.
- `OWOR` window count became 19,749.

This is treated as live-source movement, not a final reconciliation failure. Final extraction needs a posting freeze or cutoff.

## Full Raw SAP Clean Sweep: 2026-04-25

Raw staging table: `sap_raw_rows`

Profile table: `sap_raw_table_profiles`

Result:

- Non-empty SAP tables: 820
- SAP source rows: 13,741,507
- SQLite raw rows: 13,741,507
- Non-empty table errors: 0
- Zero-row HANA table-type artifacts skipped: 30

This means every non-empty SAP table visible in schema `LLM_LIVENEW` has been staged into SQLite as raw JSON rows.

Important: SAP was live during extraction, so these counts represent the completed clean-sweep snapshot from this run. Final cutover should still use a source freeze or agreed cutoff.

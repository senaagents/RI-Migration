# SAP Lean Staging Policy

Purpose: future SAP B1 migrations should not start by keeping a full
`sap_raw_rows` copy of every non-empty SAP table. The default working SQLite
must match the recommended migration shape: relevant masters, relevant
transactions, required source sidecars, and reconciliation evidence.

This policy came from the LLM migration cleanup on `2026-04-27`. The first full
raw capture produced a 41 GB SQLite. After removing full-SAP archive/log data
and duplicate raw copies already represented in curated staging, the active
working DB was reduced to 11 GB while preserving the migration-relevant rows.

## Principle

Recommended historical migration requires source evidence for the migrated
business records. It does not require a portable backup of every SAP technical
table.

Keep:

- SAP masters needed for ERPNext setup and mapping.
- SAP document headers for selected migration families.
- SAP document lines for selected migration families.
- Raw sidecar/source-detail tables not represented in the curated header/line
  staging but needed for tax, freight, withholding, payment, bank, dimensions,
  resources, and active customer add-ons.
- Source identity fields and payloads required for drillback, reconciliation,
  or importer idempotence.

Do not keep by default:

- SAP archive/history mirror tables such as `A*` and `@A*`.
- SAP change/event/internal logs such as `CGEV`.
- Duplicate raw copies of tables already staged into `sap_master_records`,
  `sap_documents`, or `sap_document_lines`.
- Heavy stock valuation/log internals such as `OILM`, `ILM*`, `OITL`, `ITL*`,
  `OIVL`, `IVL*`, and `OIVK`, unless the migration scope explicitly requires
  SAP valuation drillback.

## Required Curated Tables

Always preserve these application tables:

- `sync_runs`
- `table_profiles`
- `sap_master_records`
- `sap_documents`
- `sap_document_lines`
- `sap_raw_table_profiles` for retained raw tables

`sap_raw_rows` should contain only retained raw support tables.

## Raw Support Tables To Keep

Keep these categories when present and non-empty.

### Setup And Mapping

- `OACT`, `OCRG`, `OCRD`, `OITB`, `OITM`
- `OPRC`, `OOCR`
- `ORSC`
- `OSTC`, `OEXD`
- `ODSC`, `DSC1`, `OBNK`
- Country, currency, terms, numbering, fiscal/setup references where used:
  `OCRY`, `OCRN`, `OCTG`, `ONNM`, `OFPR`, `OSLP`, `OHEM`

### Tax, Freight, Withholding

- Sales: `INV3`, `INV4`, `INV5`, `RIN4`, `RIN5`
- Purchase: `PCH3`, `PCH4`, `PCH5`, `RPC4`, `RPC5`
- Additional tax references: `OSTA`, `OTAX`

### Payments And Banking

- Payment headers if importer/preflight reads fields not normalized elsewhere:
  `ORCT`, `OVPM`
- Payment children: `RCT1`, `RCT2`, `RCT3`, `RCT4`, `RCT6`,
  `VPM1`, `VPM2`, `VPM3`, `VPM4`, `VPM6`

### Document Sidecars Not In Curated Lines

Keep child tables for migrated document families when they are not already
captured in `sap_document_lines`, for example:

- Sales/order/delivery/invoice sidecars: `RDR3`, `RDR4`, `RDR6`, `RDR12`,
  `RDR26`, `DLN3`, `DLN4`, `DLN6`, `DLN12`, `DLN26`, `INV6`, `INV12`,
  `INV26`, `RIN6`, `RIN12`, `RIN26`
- Purchase sidecars: `POR3`, `POR4`, `POR6`, `POR12`, `POR26`, `PDN3`,
  `PDN4`, `PDN6`, `PDN12`, `PDN26`, `PCH6`, `PCH9`, `PCH11`, `PCH12`,
  `PCH26`, `RPC3`, `RPC6`, `RPC12`, `RPC26`
- Returns, requests, down payments, and transfer request sidecars only if they
  are in migration scope.

Do not keep raw copies of the main header or main line table when that table is
already represented in curated staging, unless current scripts still read it.
Example: `OINV` and `INV1` should normally be represented by `sap_documents`
and `sap_document_lines`, not duplicated in `sap_raw_rows`.

### Active Add-On Tables

Keep active customer add-on / UDT tables only when they are business-facing and
non-archive. For LLM, active examples were:

- `@BRT*`, `@OBRT*`
- `@MIG*`
- `@INUR_*`, `@INURC_*`
- lookup UDTs such as `@REQBY`, `@DEPT`, `@LOCS`, `@PRJS`,
  `@ITEMPROPERTY`, `@ITEMSUBGROUP`, `@SUBCATEGORY`, `@BPDIVISION`,
  `@BPSUBGRP`, `@TAXEICON*`

Skip archive variants beginning with `@A`.

## Raw Tables To Drop From Working SQLite

Drop these from the default working DB:

- `CGEV`
- `CPRF`
- `A*` archive/history tables
- `@A*` archive/history UDTs
- `OILM`, `ILM1`, `ILM2`
- `OITL`, `ITL1`
- `OIVL`, `IVL1`, `IVL2`, `OIVK`
- raw duplicates of curated header/line/master tables

If the customer explicitly asks for a full technical SAP archive, create it as
a separate artifact and do not use it as the importer working DB.

## Future Extractor Rule

The pipeline should grow a selective raw sync mode before the next SAP
migration:

```bash
sap-db-pipeline sync-raw-tables --mode migration-support
```

or:

```bash
sap-db-pipeline sync-raw-tables --include-file migration-control/raw-keep-list.txt
```

Until that exists, do not treat `sync-raw-tables` as a default migration step.
If a full sweep is performed for discovery, immediately compact into a lean
working DB and remove the full raw copy after approval.

## Verification Gate

Before deleting any larger DB, verify:

- `sap_master_records` count is unchanged.
- `sap_documents` count is unchanged.
- `sap_document_lines` count is unchanged.
- Retained raw support tables are present.
- `PRAGMA quick_check` returns `ok`.
- The DB contains notes or a decision table recording what was kept/dropped.

For LLM after cleanup:

- `sap_master_records`: `213,781`
- `sap_documents`: `301,483`
- `sap_document_lines`: `924,382`
- `sap_raw_rows`: `927,832`
- Working DB size: about `11G`

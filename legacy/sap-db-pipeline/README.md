# sap-db-pipeline

Standalone SAP Business One on HANA extraction pipeline for LLM/RFGB migration into SenaERP / ERPNext.

The design is intentionally staged:

`SAP B1 HANA read-only SQL -> local SQLite staging database -> mapping/reconciliation -> guarded ERPNext import`

This repo is not a direct SAP-to-ERPNext writer. It preserves staged SAP
masters, document headers/lines, selected raw source sidecars, date-window
counts, run history, and reconciliation evidence before import commands are
allowed.

## Current scope

- Connect to SAP HANA with `hdbcli`
- Profile the migration window `2025-04-01` through `2026-04-25`
- Stage SAP B1 master records into SQLite
- Stage SAP B1 document headers and lines into SQLite
- Stage selected raw SAP sidecar/setup rows into SQLite when they are needed for
  migration mapping, taxes, payments, dimensions, stock/document detail, or
  drillback
- Track every run and source table count
- Keep migration plan/progress notes in `migration-control/`

Do not use a full raw sweep of every non-empty SAP table as the default working
DB for future migrations. See `migration-control/sap-lean-staging-policy.md`.

ERPNext import commands will be added after staged counts and mappings are reconciled.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
```

Fill `.env` with the SAP HANA password. Do not commit `.env`.

## Commands

```bash
sap-db-pipeline init-db
sap-db-pipeline doctor
sap-db-pipeline profile-documents --from-date 2025-04-01 --to-date 2026-04-25
sap-db-pipeline sync-masters
sap-db-pipeline sync-documents --from-date 2025-04-01 --to-date 2026-04-25 --family all
sap-db-pipeline profile-all-tables
sap-db-pipeline report
```

Use `--limit N` on sync commands for smoke tests.

`sync-raw-tables` is a full technical raw sweep. Use it only for explicit
discovery/archive work, not as the default migration working DB path.

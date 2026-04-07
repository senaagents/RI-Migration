# RI-Migration: Tally to ERPNext Migration Guide

## Overview

RI-Migration migrates data from TallyPrime into ERPNext. It uses a 4-layer pipeline:

1. **Connector** (`connectors/tally.py`) -- HTTP/XML client that fetches data from TallyPrime
2. **Parser** (`parsers/tally.py`) -- Converts raw Tally XML into Python dicts
3. **Transformer** (`transformers/tally_to_erpnext.py`) -- Maps Tally structures to ERPNext doctypes
4. **Importer** (`importers/erpnext.py`) -- Creates ERPNext documents with duplicate detection

## Prerequisites

- TallyPrime running with HTTP server enabled (Settings > Connectivity > default port 9000)
- Network access from ERPNext server to Tally machine (e.g. `10.211.55.3:9000`)
- ERPNext site with `custom_app_migration` installed
- Company created in ERPNext with matching abbreviation (e.g. "Avinash Industries", abbr "AI")
- Fiscal Year created matching Tally's active period

## Quick Start

Full master data migration (accounts, customers, suppliers, items, warehouses, cost centres):

```bash
bench --site <site> execute custom_app_migration.custom_app_migration.api.execute_migration \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

This runs as a background job. Poll status with:
```bash
bench --site <site> execute custom_app_migration.custom_app_migration.api.get_migration_status \
  --kwargs '{"job_id": "<job_id_from_above>"}'
```

## Step-by-Step Migration

Order matters. Each step depends on the previous.

### Step 1: Master Data

Imports accounts (chart of accounts), customer groups, supplier groups, item groups, items, warehouses, customers, suppliers, and addresses.

```bash
# Full master data from live Tally
bench --site <site> execute custom_app_migration.custom_app_migration.api.execute_migration \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'

# Or from a saved XML file (List of Accounts export)
bench --site <site> execute custom_app_migration.custom_app_migration.api.execute_migration_from_file \
  --kwargs '{"file_path": "/path/to/list-of-accounts.xml", "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

Stock data can be imported separately if needed:
```bash
bench --site <site> execute custom_app_migration.custom_app_migration.api.import_stock_data \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

Cost centres (filters out payroll/employee entries automatically):
```bash
bench --site <site> execute custom_app_migration.custom_app_migration.api.import_cost_centres \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

### Step 2: Opening Stock

Creates a Stock Reconciliation from item-level closing balances (TDL Collection API, not the Stock Summary report which only has group-level data).

```bash
bench --site <site> execute custom_app_migration.custom_app_migration.api.import_opening_stock_from_tally \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries"}'
```

Items with zero valuation rate get `allow_zero_valuation_rate=1` automatically.

### Step 3: Opening Account Balances

Creates a Journal Entry from leaf-level ledger opening balances. Skips group accounts, receivable/payable accounts (handled in Step 4), and stock-related accounts.

```bash
bench --site <site> execute custom_app_migration.custom_app_migration.api.import_ledger_opening_balances \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

### Step 4: Opening Receivable/Payable

Creates Opening Sales Invoices (for customer debtors) and Opening Purchase Invoices (for supplier creditors) with `is_opening=Yes`.

```bash
bench --site <site> execute custom_app_migration.custom_app_migration.api.import_opening_invoices_from_tally \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

### Step 5: Transaction Vouchers

Imports Day Book vouchers as Sales Invoices, Purchase Invoices, Payment Entries, and Journal Entries. Requires all master data from Steps 1-4 to exist first.

```bash
# From live Tally with date range
bench --site <site> execute custom_app_migration.custom_app_migration.api.execute_voucher_migration \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI", "from_date": "20260401", "to_date": "20260430"}'

# From saved Day Book XML
bench --site <site> execute custom_app_migration.custom_app_migration.api.execute_voucher_migration_from_file \
  --kwargs '{"file_path": "/path/to/day-book.xml", "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

Voucher type resolution: custom Tally types (e.g. "61 Sales") are resolved to base types ("Sales") using the voucher type hierarchy. If the hierarchy is unavailable, substring matching is used as fallback.

## API Reference

### Whitelisted Endpoints (callable from frontend)

| Endpoint | Purpose | Key Params |
|----------|---------|------------|
| `test_tally_connection` | Test connectivity | `host`, `port` |
| `get_target_companies` | List ERPNext companies | (none) |
| `fetch_tally_data` | Preview Tally data counts | `host`, `port` |
| `preview_migration` | Dry-run transform preview | `host`, `port`, `company_name`, `company_abbr` |
| `execute_migration` | Full master data (background) | `host`, `port`, `company_name`, `company_abbr`, `dry_run` |
| `execute_migration_from_file` | Master data from XML file (background) | `file_path`, `company_name`, `company_abbr`, `dry_run` |
| `get_migration_status` | Poll background job progress | `job_id` |
| `execute_voucher_migration` | Transaction vouchers (background) | `host`, `port`, `company_name`, `company_abbr`, `from_date`, `to_date`, `dry_run` |
| `execute_voucher_migration_from_file` | Vouchers from XML file (background) | `file_path`, `company_name`, `company_abbr`, `dry_run` |
| `audit_migration` | Validate Tally vs ERPNext | `host`, `port`, `company_name`, `company_abbr` |

### CLI-only Helpers (bench execute)

| Function | Purpose |
|----------|---------|
| `import_stock_data` | Stock groups, items, godowns only |
| `import_cost_centres` | Cost centres (skips payroll) |
| `import_opening_data` | Trial balance + stock summary (legacy) |
| `import_opening_stock_from_tally` | Stock Reconciliation from item-level TDL |
| `import_ledger_opening_balances` | JE from leaf-level ledger OB |
| `import_opening_invoices_from_tally` | Opening SI/PI per customer/supplier |

All CLI helpers follow the same kwargs pattern: `'{"host": "...", "port": 9000, "company_name": "...", "company_abbr": "..."}'`

## Auditing

Run after migration to verify data accuracy:

```bash
bench --site <site> execute custom_app_migration.custom_app_migration.audit.audit_migration \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

The audit checks:
- **Count comparison** -- Tally vs ERPNext counts for all entity types
- **Data quality** -- Orphaned accounts, missing UOMs, items without groups, address coverage, orphaned warehouses
- **Opening balance reconciliation** -- JE amounts vs Tally trial balance
- **Stock reconciliation** -- SR total vs Tally item-level stock value
- **Name matching** -- Customer, supplier, account, item, warehouse name mismatches between Tally and ERPNext

Expected results for a clean migration: 0 mismatches on amounts, minor count deltas from ERPNext defaults (5 default warehouses, 5 default item groups, ~69 default accounts).

## Idempotency

Safe to re-run any step:
- **Master data**: `DuplicateEntryError` caught and logged as "skipped". Existing records are not modified.
- **Opening entries**: Journal Entries and Stock Reconciliations create new documents each time. Cancel and amend the previous one before re-running, or delete it first.
- **Vouchers**: Each voucher stores `_tally_ref` (format: `TALLY-{type}-{number}`) in the `remarks` field. The importer checks for existing documents with the same ref before creating.

## Known Limitations

- **Depreciation entries**: Tally depreciation accounts are imported as regular accounts. Depreciation schedules and asset registers must be set up manually in ERPNext.
- **Payroll cost centres**: 232 employee-named cost centres from Tally are skipped. ERPNext uses the HR module for payroll, not cost centres.
- **Multi-currency**: All amounts imported in base currency (INR). Foreign currency transactions need manual adjustment.
- **Bill-wise references**: Payment Entry references to invoices store the Tally bill number, not the ERPNext invoice name. Reconciliation must be done manually after both invoices and payments are imported.
- **Inventory vouchers without items**: Sales/Purchase vouchers with no `ALLINVENTORYENTRIES.LIST` (service invoices) are skipped by the transformer. These need the Journal Entry path.
- **Stock availability for Sales Invoices**: Sales Invoices with `update_stock=1` require sufficient stock. Import opening stock (Step 2) before vouchers (Step 5).

## Troubleshooting

**"Valuation Rate required for Item X"** on Stock Reconciliation submit
: Items with qty > 0 but rate = 0 need `allow_zero_valuation_rate=1`. The importer sets this automatically. If submitting manually, set the flag on affected rows.

**Item names with `\r` don't match**
: Fixed in parser -- `_clean_xml()` strips `\r` from all XML input. If you see mismatches, ensure you're using the latest parser code.

**UOM "Kgs" not found**
: Tally uses "Kgs", ERPNext uses "Kg". The `UOM_MAP` in the transformer handles this. If a new UOM appears, add it to `UOM_MAP` in `transformers/tally_to_erpnext.py`.

**"Account X - AI not found"** during voucher import
: The account must exist before vouchers can reference it. Run master data migration (Step 1) first. The importer uses `_resolve_account_fuzzy()` which tries both exact name match and account_name match.

**Supplier "X" already exists (DuplicateEntryError)**
: Tally can have multiple ledgers with the same mailing name under different groups. The first one is imported, subsequent duplicates are skipped. This is correct -- ERPNext uses a flat supplier list.

**Opening JE only has 5 rows (expected hundreds)**
: If using the Trial Balance report, most entries are group-level (skipped). Use `import_ledger_opening_balances` instead, which reads individual ledger `opening_balance` fields.

**Warehouses have no parent**
: Tally godowns are flat. The transformer creates warehouses under the company root. To set a different parent, update the warehouse records after import.

**Payment Entry submission fails with "No permission for X"**
: Payment Entries need valid paid_from/paid_to accounts. The importer resolves accounts by name. If the account doesn't exist, the Payment Entry is created but may fail submission. Check the error log in the importer results.

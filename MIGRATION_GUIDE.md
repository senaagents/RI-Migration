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
- ERPNext site with `agentapp_migration` installed
- Company created in ERPNext with matching abbreviation (e.g. "Avinash Industries", abbr "AI")
- Fiscal Year created matching Tally's active period

## Quick Start

Full master data migration (accounts, customers, suppliers, items, warehouses, cost centres):

```bash
bench --site <site> execute agentapp_migration.agentapp_migration.api.execute_migration \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

This runs as a background job. Poll status with:
```bash
bench --site <site> execute agentapp_migration.agentapp_migration.api.get_migration_status \
  --kwargs '{"job_id": "<job_id_from_above>"}'
```

## Step-by-Step Migration

Order matters. Each step depends on the previous.

### Step 1: Master Data

Imports accounts (chart of accounts), customer groups, supplier groups, item groups, items, warehouses, customers, suppliers, and addresses.

```bash
# Full master data from live Tally
bench --site <site> execute agentapp_migration.agentapp_migration.api.execute_migration \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'

# Or from a saved XML file (List of Accounts export)
bench --site <site> execute agentapp_migration.agentapp_migration.api.execute_migration_from_file \
  --kwargs '{"file_path": "/path/to/list-of-accounts.xml", "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

Stock data can be imported separately if needed:
```bash
bench --site <site> execute agentapp_migration.agentapp_migration.api.import_stock_data \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

Cost centres (filters out payroll/employee entries automatically):
```bash
bench --site <site> execute agentapp_migration.agentapp_migration.api.import_cost_centres \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

### Step 2: Opening Stock

Creates a Stock Reconciliation from item-level closing balances (TDL Collection API, not the Stock Summary report which only has group-level data).

```bash
bench --site <site> execute agentapp_migration.agentapp_migration.api.import_opening_stock_from_tally \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries"}'
```

Items with zero valuation rate get `allow_zero_valuation_rate=1` automatically.

### Step 3: Opening Account Balances

Creates a Journal Entry from leaf-level ledger opening balances. Skips group accounts, receivable/payable accounts (handled in Step 4), and stock-related accounts.

```bash
bench --site <site> execute agentapp_migration.agentapp_migration.api.import_ledger_opening_balances \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

### Step 4: Opening Receivable/Payable

Creates Opening Sales Invoices (for customer debtors) and Opening Purchase Invoices (for supplier creditors) with `is_opening=Yes`.

```bash
bench --site <site> execute agentapp_migration.agentapp_migration.api.import_opening_invoices_from_tally \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI"}'
```

### Step 5: Transaction Vouchers

Imports Day Book vouchers as Sales Invoices, Purchase Invoices, Payment Entries, and Journal Entries. Requires all master data from Steps 1-4 to exist first.

```bash
# From live Tally with date range
bench --site <site> execute agentapp_migration.agentapp_migration.api.execute_voucher_migration \
  --kwargs '{"host": "10.211.55.3", "port": 9000, "company_name": "Avinash Industries", "company_abbr": "AI", "from_date": "20260401", "to_date": "20260430"}'

# From saved Day Book XML
bench --site <site> execute agentapp_migration.agentapp_migration.api.execute_voucher_migration_from_file \
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
bench --site <site> execute agentapp_migration.agentapp_migration.audit.audit_migration \
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

**Day Book returns only a few vouchers (expected thousands)**
: The Day Book report export is locked to Tally's internal UI period (set via F2 in Gateway of Tally). SVFROMDATE/SVTODATE do NOT override it. Use the TDL Collection approach described below in "Full-Year Voucher Export" instead.

**Opening JE duplicated after re-run**
: `import_ledger_opening_balances` creates a new JE every run. It does not check for existing opening JEs. If you re-run it, cancel the duplicate JE manually to avoid double-counting.

## Full-Year Voucher Export (TDL Collection)

The Day Book report API is unreliable for full-year exports because it respects Tally's UI period setting. Use a TDL Collection query instead:

```xml
<ENVELOPE>
<HEADER>
  <VERSION>1</VERSION>
  <TALLYREQUEST>Export</TALLYREQUEST>
  <TYPE>Collection</TYPE>
  <ID>AllVouchers</ID>
</HEADER>
<BODY><DESC>
  <STATICVARIABLES>
    <SVCURRENTCOMPANY>Avinash Industries - Chennai Unit - 2025-26</SVCURRENTCOMPANY>
    <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
  </STATICVARIABLES>
  <TDL><TDLMESSAGE>
    <COLLECTION NAME="AllVouchers" ISINITIALIZE="Yes">
      <TYPE>Voucher</TYPE>
      <NATIVEMETHOD>Date</NATIVEMETHOD>
      <NATIVEMETHOD>VoucherTypeName</NATIVEMETHOD>
      <NATIVEMETHOD>VoucherNumber</NATIVEMETHOD>
      <NATIVEMETHOD>Amount</NATIVEMETHOD>
      <NATIVEMETHOD>PartyLedgerName</NATIVEMETHOD>
      <NATIVEMETHOD>Narration</NATIVEMETHOD>
    </COLLECTION>
  </TDLMESSAGE></TDL>
</DESC></BODY>
</ENVELOPE>
```

Key points:
- `ISINITIALIZE="Yes"` bypasses the Tally UI period filter
- `SVCURRENTCOMPANY` is required -- without it, voucher queries return empty
- For Avinash Industries 2025-26, this returns **29,562 vouchers** (35.7 MB XML)
- The Day Book report with the same company returns only the current UI period's vouchers

For large datasets (29K+), **batch by voucher type** (NOT by month, NOT NATIVEMETHOD=*). Use Tally's built-in type predicates as filters:

```xml
<!-- Sales vouchers with full detail -->
<COLLECTION NAME="SalesVch" ISINITIALIZE="Yes">
  <TYPE>Voucher</TYPE>
  <FILTER>SalesFilter</FILTER>
  <FETCH>*, ALLLEDGERENTRIES, ALLINVENTORYENTRIES</FETCH>
</COLLECTION>
<SYSTEM TYPE="Formulae" NAME="SalesFilter">$$IsSales:$VoucherTypeName</SYSTEM>

<!-- Purchase vouchers -->
<SYSTEM TYPE="Formulae" NAME="PurchFilter">$$IsPurchase:$VoucherTypeName</SYSTEM>

<!-- Payments and Receipts -->
<SYSTEM TYPE="Formulae" NAME="PayFilter">$$IsPayment:$VoucherTypeName OR $$IsReceipt:$VoucherTypeName</SYSTEM>

<!-- Journals and Contras -->
<SYSTEM TYPE="Formulae" NAME="JrnFilter">$$IsJournal:$VoucherTypeName OR $$IsContra:$VoucherTypeName</SYSTEM>

<!-- Debit/Credit Notes -->
<SYSTEM TYPE="Formulae" NAME="NoteFilter">$$IsDebitNote:$VoucherTypeName OR $$IsCreditNote:$VoucherTypeName</SYSTEM>
```

Proven results for Avinash Industries 2025-26:
- Sales: 703 vouchers, 87MB
- Purchase: 2,928 vouchers, 353MB
- Payments/Receipts: 3,473 vouchers, 148MB
- Journals/Contras: 2,325 vouchers, 126MB
- Debit/Credit Notes: 241 vouchers, 32MB

**NEVER use `NATIVEMETHOD=*`** — it crashes Tally with Memory Access Violation on large datasets. Use `FETCH=*, ALLLEDGERENTRIES, ALLINVENTORYENTRIES` instead.

**NEVER batch by month** with SVFROMDATE/SVTODATE — these date params are ignored by TDL Collection. The ISINITIALIZE flag already gets everything.

## Voucher Type Mapping

Tally uses custom voucher type names (e.g. "61 Sales", "32 Payt Bank"). The transformer resolves these to base types using the Voucher Type hierarchy (Parent field).

### Handled types (mapped to ERPNext doctypes):

| Base Type | Example Tally Names | ERPNext DocType | Count (Avinash) |
|-----------|-------------------|-----------------|-----------------|
| Sales | 61 Sales | Sales Invoice | 702 |
| Purchase | 31-Puchase(Monthly) | Purchase Invoice | 2,928 |
| Credit Note | 6/3 Credit Note, 3-Credit Note | Sales Invoice (return) | 25 |
| Debit Note | 3- Debit Note (Rejection), 3/6 -Debit Note | Purchase Invoice (return) | 179 |
| Receipt | 72/62/12/3/1/5/2 Receipts Bank/Cash | Payment Entry (Receive) | 573 |
| Payment | 32/12/52/72/22/83/92/42/29 Payt Bank/Cash | Payment Entry (Pay) | 2,603 |
| Journal | 11/51/31/21/41/71/91/113 Journal, Provision Journal | Journal Entry | 2,099 |
| Contra | 72 Contra | Journal Entry | 91 |
| **Total handled** | | | **~9,200** |

### Unhandled types (silently dropped by transformer):

| Base Type | Example Tally Names | Needed ERPNext DocType | Count (Avinash) |
|-----------|-------------------|----------------------|-----------------|
| Stock Journal | Stock Journal, Conversion Stock Journal, MFG variants | Stock Entry (Repack/Manufacture) | 15,037 |
| Material In | Material In | Stock Entry (Material Receipt) | 907 |
| Material Out | Material Out | Stock Entry (Material Issue) | 1,024 |
| Store Movement | Store Movement Stk Jrl | Stock Entry (Material Transfer) | 150 |
| Receipt Note | Receipt Note, Receipt Note - Stock Alignment | Purchase Receipt | 240 |
| Delivery Challan | Delivery Challan (various) | Delivery Note | 300 |
| Purchase Order | Purchase Order, Purchase Order -Others/ForeCast | Purchase Order | 1,024 |
| Sales Order | Sales Order | Sales Order | 41 |
| Job Order | Job Order | Job Card / Work Order | 81 |
| **Total unhandled** | | | **~18,804** |

## Production Sync

The `prod_sync.py` module exports local ERPNext data as JSON files and imports them on a production site.

### Export (run on dev):
```bash
bench --site dev.localhost execute agentapp_migration.agentapp_migration.prod_sync.export_company_data \
  --kwargs '{"company_name": "Avinash Industries", "company_abbr": "AI"}'
```
Writes JSON files to `/tmp/avinash_export/` in dependency order (UOM, groups, accounts, items, customers, suppliers, addresses, then submitted transactions).

### Import (run on prod):
```bash
bench --site <prod_site> execute agentapp_migration.agentapp_migration.prod_sync.import_company_data \
  --kwargs '{"import_dir": "/tmp/avinash_export"}'
```
Imports in dependency order, skips duplicates, rebuilds nested set trees (Account, Cost Center, Warehouse, Item Group), and submits submittable documents (JE, SI, PI, PE, SR).

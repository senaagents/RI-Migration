# Monolithic Runs Hide Progress

## Trigger

`sync-documents --family all` ran successfully, but the CLI printed no per-family progress until the end. During journals and inventory movement staging, the command appeared silent for long periods.

## Migration Impact

Large SAP families can take several minutes while preserving raw JSON and upserting rows into SQLite. A silent all-family run makes it hard to distinguish healthy progress from a stalled extraction.

## Rule

For final extraction, run document staging per family and record each family result independently.

Preferred operational flow:

```bash
sap-db-pipeline sync-documents --family sales_invoices ...
sap-db-pipeline sync-documents --family purchase_invoices ...
sap-db-pipeline sync-documents --family journals ...
```

The all-family command is acceptable for smoke tests and exploratory staging, not as the final controlled runbook.

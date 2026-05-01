# Stage Before ERPNext Write

## Trigger

The SAP source has high document volume in the target window:

- Journals: over 100,000 headers
- Goods issues: over 26,000 headers
- Goods receipts: nearly 20,000 headers
- Sales and purchase invoices: over 18,000 combined headers

## Migration Impact

Direct SAP-to-ERPNext writes would make failures hard to restart, audit, or reconcile. ERPNext document submission also has side effects, validations, naming, GL/stock ledger behavior, and cancellation semantics that must be controlled.

## Rule

For LLM, SAP must be extracted into a local staging database first. ERPNext import commands should only run after:

- Source counts are recorded.
- Raw SAP rows are preserved.
- Mappings are reviewed.
- Idempotence keys are defined.
- Exception reporting exists.
- A dry run has passed.

## Preferred Flow

`SAP HANA -> SQLite staging -> transform/dry-run -> guarded ERPNext batch import -> reconciliation`

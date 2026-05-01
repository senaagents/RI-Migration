# Staging Keys Must Be Reconciled

## Trigger

The first master staging run printed 5,372 extracted `CRD7` tax-ID rows but SQLite held only 4,465 `CRD7` staged records.

The staging key used `CardCode`, `Address`, and `TaxId0`, which was not unique enough for SAP B1 tax identifier rows.

## Migration Impact

Staging databases can drop or collapse rows even before any ERPNext import if source keys are guessed too loosely. This is especially risky for SAP child/detail tables where the visible business fields are not always a primary key.

## Rule

Every staged table must reconcile:

- SAP extracted row count
- SQLite staged row count
- Unique source key count

If those do not match, stop and fix the staging key before mapping or importing.

## Fix Applied

`CRD7` staging now uses a stronger composite key plus a source-row payload fingerprint. Full master sync also rehydrates each master table so stale rows from old key strategies do not remain.

After the fix, master staging reconciled to 213,781 rows exactly.

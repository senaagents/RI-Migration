# Reconciliation Checklist

- SAP source table counts captured.
- Date-window document counts captured for `2025-04-01` through `2026-04-25`.
- Master staged counts match or explain ERPNext counts.
- Every staged document family has header and line counts.
- Out-of-window SAP rows are rejected from dated staging commands.
- Source idempotence keys are unique in staging.
- ERPNext import batches have checkpoints.
- Failed rows have exception records.
- Sales register totals reconcile.
- Purchase register totals reconcile.
- Journal debit and credit totals reconcile.
- AR/AP outstanding reconcile.
- Cash and bank balances reconcile.
- Stock quantity and value reconcile.
- Opening balances and opening stock are handled as explicit approved phases.

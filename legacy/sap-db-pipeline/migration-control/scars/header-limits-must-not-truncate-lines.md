# Header Limits Must Not Truncate Lines

## Trigger

A limited sales-invoice smoke test staged 10 headers and only 10 lines because the smoke-test limit was applied to both header and line queries.

## Migration Impact

For SAP documents, a test limit may limit the number of headers selected, but it must never limit the child rows for those selected headers. Truncated lines make document totals, taxes, stock movement, and GL impact unreconcilable.

## Rule

`--limit` applies only to document headers.

Once a SAP header is selected, all lines for that header must be staged.

## Verification

After the fix, the same 10-header sales-invoice smoke test staged 13 invoice lines.

# SAP AR Invoices Have Header-Level Value

SAP B1 AR invoice value is not always just `INV1.LineTotal + INV4.TaxSum`.

During the `OINV` import, exact ERPNext reconciliation required:

- `INV3` freight/expense rows, mapped through `OEXD.RevAcct`.
- `INV5` TCS/withholding rows, posted as actual tax/charge rows.
- `OINV.RoundDif`, applied as a residual tax/charge adjustment.

For future transaction importers, always reconcile against the header total and then inspect document companion tables before assuming a line/tax mismatch is rounding noise.

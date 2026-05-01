# SAP Tax-Only And Service Lines Need Placeholders

SAP B1 can store AR invoice rows that ERPNext cannot submit as ordinary item rows:

- `INV1.TaxOnly = Y` rows with zero quantity and taxable base value.
- Service-style rows with blank `ItemCode`, zero quantity, and real revenue value.
- Fractional quantity rows where the ERPNext item master has stock UOM `Nos` with integer validation.

For the financial AR invoice import, these rows were represented with support items:

- `SAP-TAX-ONLY` for zero-net tax-only documents.
- `SAP-SERVICE-LINE` for amount-only or fractional-quantity financial lines.

This keeps AR financial totals exact while leaving physical stock quantity migration to inventory documents.

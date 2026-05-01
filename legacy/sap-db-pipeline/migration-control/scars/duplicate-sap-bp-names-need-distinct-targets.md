# Duplicate SAP BP Names Need Distinct Targets

## Trigger

The first missing-BP import attempt tried to map SAP CardCode `V00679` to an existing Supplier named `R R MARKETING`.

ERPNext already had that Supplier name, and other SAP CardCodes also shared existing Supplier names. A single `custom_sap_card_code` field cannot hold multiple SAP CardCodes.

## Migration Impact

If duplicate-name SAP BPs are collapsed into one ERPNext Supplier/Customer, transactions can lose source identity and idempotence. Payment allocations and party ledgers may also reconcile against the wrong SAP CardCode.

## Rule

One SAP CardCode must map to one ERPNext party identity.

If an exact ERPNext party name exists:

- If it has no SAP CardCode, assign the SAP CardCode.
- If it already has a different SAP CardCode, create a distinct target party with the SAP code in the name.

Example:

`R R MARKETING (V01167)`

## Verification

After applying this rule:

- SAP customer BPs: 849
- ERPNext SAP-mapped customers: 849
- SAP supplier BPs: 1,425
- ERPNext SAP-mapped suppliers: 1,425
- Missing party CardCodes: 0

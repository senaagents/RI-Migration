# Zero-Row HANA Table Types Are Not Source Data

## Trigger

The SAP schema inventory included HANA table-type artifacts such as `ATP_*`, `DA_*`, `PAL_*`, and forecasting output types. These returned no source rows and failed `SELECT *` with "cannot select user-defined type".

## Migration Impact

These objects are not business data tables for the migration. Treating them as extraction failures creates noise and hides real non-empty table errors.

## Rule

If a HANA object has zero source rows and cannot be selected because it is a user-defined table type, mark it as skipped metadata, not a failed source table.

Non-empty tables must still be staged or explicitly explained.

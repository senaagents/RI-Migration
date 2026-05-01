# Binary Columns Must Be JSON Safe

## Trigger

The raw SAP sweep initially failed on 9 non-empty tables because some HANA columns arrived in Python as `memoryview`, which JSON cannot serialize directly.

Affected tables included:

- `IRD1`
- `OCPC`
- `OFRM`
- `OHMM`
- `OIRD`
- `OSRC`
- `OUSR`
- `OWPK`
- `RDOC`

## Migration Impact

Raw staging must preserve every source row, including tables with binary/blob-like fields. A JSON staging layer cannot assume values are only strings, dates, numbers, or bytes.

## Rule

Convert `memoryview` values to hex strings before writing raw payload JSON.

## Verification

After the fix, all 9 non-empty error tables staged successfully and the clean sweep reconciled:

- Non-empty SAP tables: 820
- SAP source rows: 13,741,507
- SQLite raw rows: 13,741,507
- Non-empty table errors: 0

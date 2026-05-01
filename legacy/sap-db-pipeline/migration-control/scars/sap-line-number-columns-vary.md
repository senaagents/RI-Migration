# SAP Line Number Columns Vary

## Trigger

An all-family document staging smoke test failed on a line-table query that assumed every SAP B1 detail table has `LineNum`.

`JDT1` journal lines use `Line_ID` instead.

## Migration Impact

Line identity and ordering are part of the source idempotence key. A wrong line column can fail extraction or overwrite distinct lines.

## Rule

Line number columns must be defined per SAP document family, not inferred globally.

Current confirmed line identity columns:

- Marketing and inventory document lines: `LineNum`
- Journal lines: `Line_ID`
- Production order lines: `LineNum`

## Verification

After defining the journal line column explicitly, `sync-documents --family all --limit 2` succeeded for all planned document families.

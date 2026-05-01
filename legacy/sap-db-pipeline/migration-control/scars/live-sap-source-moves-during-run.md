# Live SAP Source Moves During Run

## Trigger

The full transaction staging run started from one profile count, but a post-run profile showed changed SAP counts for journals, purchase receipts, and production orders.

Examples:

- `OJDT` journal window count moved from 109,593 to 109,598.
- `OPDN` purchase receipt window count moved from 4,937 to 4,951.
- `OWOR` production order window count moved from 19,746 to 19,749.

## Migration Impact

When SAP remains live during extraction, a staged snapshot can be internally valid but not match a later source profile. Chasing "today" while users are posting in SAP can create moving reconciliation targets.

## Rule

For final migration, define one of these controls:

- Source freeze during extraction.
- Cutoff timestamp/date that excludes current live posting.
- Repeat per-family extraction after freeze and compare final source counts.

Do not claim final reconciliation against a moving SAP source.

# LLM Option 3 Historical Journal Import

Date: 2026-04-27 23:08 IST

Target site: `llmnew.localhost`

Scope: import SAP B1 accounting history as ERPNext Journal Entries for the
recommended Option 3 historical archive migration.

## Window

- From: `2018-02-22`
- To: `2026-03-31`
- April 2026 and later excluded.

## Strategy

Option 3 imports the accounting layer, not every old operational SAP document.
SAP `OJDT` journal headers and `JDT1` debit/credit rows are imported into
ERPNext as submitted `Journal Entry` records with SAP drillback fields:

- `Journal Entry.custom_sap_trans_id`
- `Journal Entry.custom_sap_trans_type`
- `Journal Entry.custom_sap_base_ref`
- `Journal Entry Account.custom_sap_line_id`
- `Journal Entry Account.custom_sap_short_name`

This avoids native-replaying old invoices, deliveries, production orders,
goods issues/receipts, and payments while still preserving the historical GL
movement needed for Trial Balance, P&L, Balance Sheet, and account ledgers.

## Script

- `scripts/import_llm_historical_journals.py`

The script is idempotent by SAP `TransId`.

## Counts

SQLite source:

- SAP `OJDT` headers with `JDT1` lines in window: `140,825`
- All-zero movement headers skipped: `1,610`
- Expected postable journals: `139,215`

ERPNext target:

- Submitted SAP historical Journal Entries: `139,215`
- Distinct SAP `TransId`s in ERPNext: `139,215`
- Missing postable SAP `TransId`s in ERPNext: `0`
- Extra ERPNext SAP `TransId`s not expected from SQLite: `0`
- Earliest posting date: `2018-02-22`
- Latest posting date: `2026-03-31`
- April 2026 or later SAP journals: `0`
- GL Entry rows created for imported SAP Journal Entries: `385,687`

ERPNext year split:

| Year | Journal Entries |
|---|---:|
| 2018 | 7 |
| 2019 | 51 |
| 2020 | 16 |
| 2021 | 38 |
| 2022 | 128 |
| 2023 | 187 |
| 2024 | 12,862 |
| 2025 | 97,471 |
| 2026 | 28,455 |

## Reconciliation

- SQLite `JDT1` source line totals:
  - Debit: `33,956,629,719.60`
  - Credit: `33,956,629,719.60`
- ERPNext `Journal Entry Account` totals for imported SAP journals:
  - Debit: `33,956,629,719.60`
  - Credit: `33,956,629,719.60`
- Account-level net movement by SAP account code:
  - Source accounts with movement: `399`
  - ERPNext accounts with movement: `399`
  - Differences: `0`

## Notes

Two SAP manual journal headers, `141900` and `141901`, have negative debit
lines and positive debit lines. They net to zero at header total level but have
non-zero source line movement, so they are included. The skip rule is therefore
"all line debit and credit values are zero", not "header net is zero".

## 2026-04-28 Late March SAP Delta

After the initial import, live SAP was checked directly through HANA and showed
10 additional postable `OJDT` headers inside the March cutoff window. These were
created in SAP on `2026-04-27` after the staging snapshot, but their posting
dates are `2026-03-20` and `2026-03-31`, so they belong inside the Option 3
`2026-03-31` cutoff.

Action taken:

- Re-staged SAP `journals` from `2026-03-20` through `2026-03-31` with
  `--since 2026-04-27`.
- Added the 10 newly staged postable `TransId`s to ERPNext `llmnew.localhost`.
- Confirmed ERPNext has no SAP Journal Entries with posting date
  `2026-04-01` or later.

Added SAP `TransId`s:

| TransId | Posting Date | TransType | BaseRef | ERPNext Journal Entry |
|---:|---|---:|---|---|
| 218607 | 2026-03-20 | 30 | 252706432 | ACC-JV-2026-139206 |
| 218618 | 2026-03-31 | 30 | 252706433 | ACC-JV-2026-139207 |
| 218633 | 2026-03-31 | 30 | 252706434 | ACC-JV-2026-139208 |
| 218651 | 2026-03-31 | 67 | 2656104 | ACC-JV-2026-139209 |
| 218707 | 2026-03-31 | 59 | 26209291 | ACC-JV-2026-139210 |
| 218719 | 2026-03-31 | 67 | 2612015 | ACC-JV-2026-139211 |
| 218738 | 2026-03-31 | 67 | 2656105 | ACC-JV-2026-139212 |
| 218745 | 2026-03-31 | 67 | 2612016 | ACC-JV-2026-139213 |
| 218752 | 2026-03-31 | 67 | 2612017 | ACC-JV-2026-139214 |
| 218753 | 2026-03-31 | 67 | 2612018 | ACC-JV-2026-139215 |

# LLM Opening Cutover Preflight

Date generated: 2026-04-28

Source: SAP Business One HANA schema `LLM_LIVENEW`

Target: `llmnew.localhost`, company `LLM`

Cutoff: `2026-03-31`

No ERPNext writes were performed by this report.

## Decision

Use the hybrid cutover path:

- Keep the `2018-02-22` through `2026-03-31` historical SAP GL archive already imported as Journal Entries.
- Create April 1 operational opening records only for balances still open as of `2026-03-31`.
- Use ERPNext opening invoices/party advances/stock opening against cutover clearing accounts so these records can exist operationally without double-counting the historical GL.
- Import April 2026 onward as native ERPNext documents.

## Why The Earlier Header Check Looked Wrong

SAP internal reconciliation is line-based, not header-only. For example, a purchase invoice can reconcile advance/TDS/other journal lines under the same source document. Summing `ITR1.ReconSum` by `SrcObjAbs` alone can exceed the invoice header amount. The correct as-of calculation uses the `JDT1` control-account line and subtracts only the matching `ITR1.TransId + TransRowId` reconciliations dated on or before the cutoff.

## Party Opening Balance By Source

| Account | Account Name | Source | Open Lines | Original Signed | Reconciled To Cutoff | Open Signed |
| --- | --- | --- | --- | --- | --- | --- |
| 1202101 | DEBTORS (DOMESTIC) | Incoming Payment / Customer Advance | 80 | -51,214,016.36 | -2,423,334.72 | -48,790,681.64 |
| 1202101 | DEBTORS (DOMESTIC) | Manual Party Journal | 62 | -6,621,142.61 | 66,444.46 | -6,687,587.07 |
| 1202101 | DEBTORS (DOMESTIC) | Outgoing Payment / Supplier Advance | 3 | 30,527,394.00 | 338,756.60 | 30,188,637.40 |
| 1202101 | DEBTORS (DOMESTIC) | Sales Credit Memo | 25 | -1,168,159.09 | -469,613.03 | -698,546.06 |
| 1202101 | DEBTORS (DOMESTIC) | Sales Invoice | 1278 | 222,126,110.61 | 113,351,527.87 | 108,774,582.74 |
| 1202102 | DEBTORS (FOREIGN) | Incoming Payment / Customer Advance | 1 | -514,660.50 | 0.00 | -514,660.50 |
| 1202102 | DEBTORS (FOREIGN) | Manual Party Journal | 7 | -1,027,924.59 | 0.00 | -1,027,924.59 |
| 1202102 | DEBTORS (FOREIGN) | Sales Invoice | 6 | 5,386,500.81 | 0.00 | 5,386,500.81 |
| 2201001 | SUNDRY CREDITORS - DOMESTIC | Incoming Payment / Customer Advance | 11 | -185,940.00 | 0.00 | -185,940.00 |
| 2201001 | SUNDRY CREDITORS - DOMESTIC | Manual Party Journal | 136 | 39,939,222.55 | 153,963.00 | 39,785,259.55 |
| 2201001 | SUNDRY CREDITORS - DOMESTIC | Outgoing Payment / Supplier Advance | 411 | 64,374,970.97 | 1,356,241.91 | 63,018,729.06 |
| 2201001 | SUNDRY CREDITORS - DOMESTIC | Purchase Credit Memo | 25 | 378,774.60 | 0.00 | 378,774.60 |
| 2201001 | SUNDRY CREDITORS - DOMESTIC | Purchase Invoice | 2149 | -157,378,159.95 | -2,908,380.37 | -154,469,779.58 |
| 2201002 | SUNDRY CREDITOR - IMPORT | Manual Party Journal | 1 | 42,315.00 | 0.00 | 42,315.00 |

## Party Opening Balance By Account

These signed balances reconcile to the SAP control-account GL balances as of `2026-03-31`.

| Account | Account Name | Open Signed Balance |
| --- | --- | --- |
| 1202101 | DEBTORS (DOMESTIC) | 82,786,405.37 |
| 1202102 | DEBTORS (FOREIGN) | 3,843,915.72 |
| 2201001 | SUNDRY CREDITORS - DOMESTIC | -51,472,956.37 |
| 2201002 | SUNDRY CREDITOR - IMPORT | 42,315.00 |

## Stock Opening By Inventory Account

Stock is reconstructed from `OIVL` movements through `2026-03-31`. The source extract includes SAP valuation/account anomalies, so the ERPNext opening import uses item/warehouse quantities and values for the stock ledger, then posts stock-opening clearing Journal Entries so the historical GL already imported through `2026-03-31` is not double-counted.

| Inventory Account | Account Name | Rows | Qty | Stock Value | SAP GL Balance | Value Minus GL |
| --- | --- | --- | --- | --- | --- | --- |
| (blank) |  | 447 | 342,182.059360 | 0.00 |  |  |
| 1201001 | INVENTORY RAW MATERIAL | 142 | 83,667.804780 | 20,509,092.14 | 21,030,814.14 | -521,722.00 |
| 1201002 | INVENTORY FINISHED PRODUCTS | 2590 | 524,106.744820 | 54,762,525.07 | 54,838,414.51 | -75,889.44 |
| 1201003 | SEMI FINISHED GOODS | 1453 | 1,485,576.710420 | 67,758,132.22 | 67,758,132.22 | 0.00 |
| 1201004 | CONSUMABLE MATERIAL | 615 | 1,924,678.209450 | 17,837,602.82 | 17,837,602.82 | -0.00 |
| 1201005 | TRADING GOODS | 343 | 41,378.185000 | 8,261,453.04 | 8,591,851.33 | -330,398.29 |
| 1201006 | PACKING MATERIAL | 976 | 2,619,923.202700 | 9,900,507.78 | 9,900,507.78 | 0.00 |
| 1201008 | SCRAP | 15 | -78,113.418950 | 101.81 | 101.81 | -0.00 |
| 1201010 | BROUGHT OUT STOCK | 304 | 196,118.624760 | 11,665,692.27 | 18,544,672.07 | -6,878,979.80 |
| 1201011 | INVENTORY TRANSIT | 14 | 41.272000 | 7,206.28 | 28,593.38 | -21,387.10 |
| 1201012 | SAMPLE MATERIAL | 21 | 67.210000 | 25,095.07 | 25,095.07 | 0.00 |
| 4101001 | SALES - FG | 23 | 5,835.000000 | 584,085.10 | -512,173,165.36 | 512,757,250.46 |

## Generated Files

- `migration-control/opening-cutover/party-open-lines-as-of-2026-03-31.csv`
- `migration-control/opening-cutover/opening-invoice-source-lines-as-of-2026-03-31.csv`
- `migration-control/opening-cutover/stock-opening-as-of-2026-03-31.csv`

## Import Rules From This Report

1. `TransType 13` and `14` become opening customer invoices/credit notes for the open signed amount only, using ERPNext opening invoice behavior and a Temporary Opening/Cutover Clearing account.
2. `TransType 18` and `19` become opening supplier invoices/credit notes for the open signed amount only.
3. `TransType 24`, `46`, and `30` party lines are not normal invoices. They are advances/manual party balances and need opening Payment Entry or party Journal Entry treatment against the same cutover clearing account.
4. Stock opening is posted as April 1 operational inventory only. Its GL effect must be walked back with cutover clearing Journal Entries because the SAP GL through `2026-03-31` is already present in ERPNext historical Journal Entries.
5. After opening docs are submitted, post one cutover clearing Journal Entry if needed so the net GL remains equal to SAP at `2026-03-31` while operational ledgers exist for April settlement.

## Applied To `llmnew.localhost`

Applied on 2026-04-28.

ERPNext writes performed:

| ERPNext document | Count | Amount |
|---|---:|---:|
| Opening Sales Invoice | 1,284 | 114,161,083.55 outstanding |
| Opening Purchase Invoice | 2,149 | 154,469,779.58 outstanding |
| Opening party-balance Journal Entry | 762 | see source lines |
| Walk-back clearing Journal Entry | 42 | nets opening GL effect to zero |
| Opening Stock Reconciliation | 66 | 6,577 positive item/warehouse groups |
| Opening negative Stock Entry | 1 | 19 negative item/warehouse groups |
| Stock walk-back clearing Journal Entry | 67 | nets stock-opening GL effect to zero |

Opening invoice split:

| Account | Docs | Outstanding |
|---|---:|---:|
| `1202101 - DEBTORS (DOMESTIC) - L` | 1,278 | 108,774,582.74 |
| `1202102 - DEBTORS (FOREIGN) - L` | 6 | 5,386,500.81 |
| `2201001 - SUNDRY CREDITORS - DOMESTIC - L` | 2,149 | 154,469,779.58 |

Verification:

- Duplicate opening keys: 0 for Sales Invoice, Purchase Invoice, and Journal Entry.
- Non-opening April transactional documents created by this step: 0.
- Net GL effect from opening invoices, opening party JEs, and walk-back clearing JEs: 0 rows with non-zero net balance.
- Control-account balances are unchanged from `2026-03-31` to `2026-04-01` after opening import:

| Account | Balance at 2026-03-31 | Balance at 2026-04-01 |
|---|---:|---:|
| `1202101 - DEBTORS (DOMESTIC) - L` | 82,786,405.37 | 82,786,405.37 |
| `1202102 - DEBTORS (FOREIGN) - L` | 3,843,915.72 | 3,843,915.72 |
| `2201001 - SUNDRY CREDITORS - DOMESTIC - L` | -51,472,956.37 | -51,472,956.37 |
| `2201002 - SUNDRY CREDITOR - IMPORT - L` | 42,315.00 | 42,315.00 |

Stock opening verification:

- Source rows from SAP `OIVL`: 6,943.
- Aggregated item/warehouse groups: 6,705.
- Imported non-zero stock groups: 6,596; 6,577 positive groups via Stock Reconciliation and 19 negative groups via opening Material Issue.
- Skipped zero-quantity/zero-value groups: 109.
- Active opening Stock Ledger Entry rows: 6,596.
- Canceled Stock Ledger Entry rows from the abandoned first partial stock attempt: 200. These are canceled and do not affect active stock.
- Stock opening plus stock walk-back clearing has zero net GL effect.
- No non-opening April Stock Entries were created.
- Source quantity: 7,145,461.60434. ERPNext active opening quantity: 7,145,461.604000.
- Source stock value: 191,311,493.60. ERPNext active opening stock value: 191,312,829.46. Difference: +1,335.86 from ERPNext item/warehouse valuation-rate precision.

Setup changes made for opening stock:

- Enabled 3 disabled stock items that had SAP opening quantity.
- Allowed negative stock on 128 items that had negative SAP opening balances.
- Seeded valuation rates on 19 negative-opening items so ERPNext could post the opening Material Issue.

Stop point: do not start April sales, purchase, payment, delivery, receipt, production, or stock replay until this April 1 opening baseline is audited.

# LLM Setup And Master Audit

Date: 2026-04-25

Scope: audit SAP staging against ERPNext site `llm.localhost` before any transaction migration.

## Position

Do not migrate transactions yet.

The correct sequence is:

1. Company and accounting setup
2. COA and financial dimensions
3. Core masters
4. Tax, payment, bank, warehouse, and stock setup
5. Opening balances / opening stock
6. Transactions

The current `llm.localhost` site is partly set up, but not clean enough for transaction import.

## COA Audit

SAP source:

- `OACT`: 600 accounts
- SAP groups: 104
- SAP postable accounts: 496

ERPNext target:

- `Account`: 662 accounts
- ERPNext groups: 127
- ERPNext leaves: 535
- Numbered accounts: 586
- Unnumbered accounts: 76

Findings:

- 586 ERPNext account numbers match SAP `OACT.AcctCode`.
- 14 SAP account codes are not present as ERPNext `account_number`.
- 10 of the missing codes are SAP top-level/root group buckets and can be mapped to ERPNext root accounts instead of created as normal accounts.
- 4 missing codes are postable SAP accounts and must be created before journal migration.
- SAP/ERPNext group-vs-leaf flags match for all mapped numbered accounts.
- One mapped account has a name mismatch: SAP `1204202` is `SALARY ADVANCE`; ERPNext account name is `SALARY wa`.
- There is no `custom_sap_acct_code` field on `Account`; current mapping relies on `account_number`.

Missing postable accounts:

| SAP Code | SAP Name | Parent | JDT1 Refs |
| --- | --- | --- | ---: |
| `1103207` | ICICI PRUDENTIAL INDIA OPPORTUNITIES FUND | `1103200` | 1 |
| `1103208` | NIPPON INDIA LARGE CAP FUND | `1103200` | 1 |
| `1103209` | NIPPON INDIA MIDCAP FUND | `1103200` | 1 |
| `1103210` | ADITYA BIRLA SUNLIFE FLEXICAP FUND | `1103200` | 1 |

COA recommendation:

- Keep ERPNext root accounts as ERPNext roots.
- Create explicit mapping for the 10 SAP root bucket codes to the corresponding ERPNext roots.
- Add the 4 missing postable accounts under `1103200 - KOTAK INVESTMENT-MUTUAL FUNDS - L`.
- Fix account `1204202` name.
- Add either a custom field or a separate account mapping table for SAP account code to ERPNext account. Relying only on `account_number` is workable but brittle.

Detailed exception file:

- `migration-control/exceptions/missing-sap-accounts.csv`

## Master Audit

| Area | SAP Staged | ERPNext Current | Gap |
| --- | ---: | ---: | --- |
| Branches | 6 | 30, 6 SAP-mapped | SAP branches mapped; extra ERPNext branches need review |
| Warehouses | 29 | 34, 29 SAP-mapped | SAP warehouses mapped; extra ERPNext warehouses need review |
| Cost centers / dimensions | 48 `OPRC`, 43 `OOCR` | 18 cost centers, 16 SAP-mapped | 32 SAP cost/profit center codes missing from ERPNext mapping |
| Customers | 849 SAP customer BPs | 844 mapped customers | 5 missing SAP customers |
| Suppliers | 1,425 SAP supplier BPs | 1,408 mapped suppliers | 17 missing SAP suppliers |
| Items | 4,969 SAP items | 4,173 ERPNext items | 906 SAP items missing; 110 ERPNext items not in SAP |
| Item groups | 13 SAP groups | 24 ERPNext item groups | Needs mapping review |
| UOMs | 7 SAP UOMs | 248 ERPNext UOMs | SAP UOMs likely covered, but mapping must be explicit |
| Tax codes | 49 `OSTC`; 33,099 `OTAX` rows | 0 sales tax templates, 0 purchase tax templates | Tax setup not ready |
| Bank accounts | SAP bank/account tables staged | 0 ERPNext Bank Account records | Bank/payment setup not ready |
| Mode of payment | SAP payments staged | 5 modes | Needs account mapping |

Reference impact:

- Missing postable accounts are referenced in `JDT1`.
- 506 of the 906 missing SAP items are referenced by staged document lines.
- 11 missing business partners are referenced by staged transaction headers.
- Missing cost/profit center dimension codes are heavily referenced in `JDT1`.

Detailed exception files:

- `migration-control/exceptions/missing-sap-items.csv`
- `migration-control/exceptions/missing-sap-business-partners.csv`
- `migration-control/exceptions/missing-sap-cost-center-dimensions.csv`

## Required Setup Before Transactions

### 1. Accounting Setup

- Confirm company `LLM` fiscal year and default currency.
- Lock the SAP root bucket mapping to ERPNext root accounts.
- Add missing postable COA accounts.
- Fix the account name mismatch for `1204202`.
- Decide whether to add `custom_sap_acct_code` on `Account` or maintain a staging mapping table.

### 2. Dimensions

- Decide ERPNext representation for SAP dimensions:
  - `ProfitCode`
  - `OcrCode2`
  - `OcrCode3`
  - `OcrCode4`
  - `OcrCode5`
- Create or map the missing cost/profit center codes before GL import.
- Treat `NONE` explicitly. Do not accidentally create analytical noise if it means no dimension.

### 3. Items

- Import the full SAP `OITM` item universe, not the older XLSX subset.
- Resolve the 110 ERPNext-only items before using item codes in transactions.
- Confirm item groups, UOM conversion, stock/non-stock flags, valuation method, and disabled/frozen flags.

### 4. Parties

- Import missing SAP customers and suppliers, including transaction-referenced parties.
- Confirm BP group mapping.
- Confirm GST/PAN/address/contact import from `CRD1`, `CRD7`, `OCPR`, and `OCRB`.

### 5. Warehouses And Branches

- Keep the 29 SAP warehouse mappings.
- Review 5 extra ERPNext warehouses.
- Keep the 6 SAP branch mappings.
- Review extra ERPNext branch rows and avoid using unmapped branches in imported transactions.

### 6. Taxes, Banks, And Payments

- Build sales and purchase tax templates from SAP tax code/tax row behavior.
- Map `RCT*` and `VPM*` payment child rows to ERPNext payment entries.
- Create ERPNext Bank Account records and mode-of-payment accounts before payment import.

## Gate Decision

Transactions are not ready to import.

Minimum blockers to close first:

1. COA postable account gaps
2. Cost/profit center dimension mapping
3. Missing referenced items
4. Missing referenced customers/suppliers
5. Tax template setup
6. Bank/payment account setup

After these are resolved, run a dry-run transaction transform from SQLite before writing anything into `llm.localhost`.

## 2026-04-25 COA Update

The COA gap portion of this audit has been remediated.

See `migration-control/audits/2026-04-25-coa-setup-application.md`.

Remaining blockers:

- Cost/profit center dimension mapping
- Missing referenced items
- Tax template setup
- Bank/payment account setup

## 2026-04-25 Business Partner Update

The missing customer/supplier gap has been remediated.

See `migration-control/audits/2026-04-25-business-partner-setup-application.md`.

Remaining blockers:

- Cost/profit center dimension mapping
- Missing referenced items
- Tax template setup
- Bank/payment account setup

## 2026-04-25 Item Update

The SAP item master gap has been remediated.

See `migration-control/audits/2026-04-25-item-setup-application.md`.

Remaining blockers:

- Cost/profit center dimension mapping
- Tax template setup
- Bank/payment account setup

## 2026-04-25 Dimension Update

The SAP Dim 2 and Dim 3 accounting dimension gap has been remediated.

See `migration-control/audits/2026-04-25-dimension-setup-application.md`.

Remaining blockers:

- Tax template setup
- Bank/payment account setup

## 2026-04-25 Tax, Bank, And Payment Setup Update

The tax template and bank/payment setup blockers have been remediated.

See `migration-control/audits/2026-04-25-tax-bank-payment-setup-application.md`.

Remaining blocker:

- Transaction dry-run/import validation

## 2026-04-25 Transaction Preflight And Manual Journal Update

The full transaction mapping preflight is clean, SAP resources have been created as non-stock operation items, and SAP manual journals have been imported.

See `migration-control/audits/2026-04-25-transaction-preflight-and-manual-journals.md`.

Remaining transaction families:

- Sales invoices and credit memos
- Purchase invoices and credit memos
- Payments
- Stock movements and inventory transfers
- Orders, deliveries, purchase receipts
- Production orders

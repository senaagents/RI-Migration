# LLM Tax, Bank, And Payment Setup Application

Date: 2026-04-25 12:34 IST

Scope: close tax, bank, and payment setup blockers before importing staged SAP transactions into `llm.localhost`.

## Tax Templates

Script: `scripts/apply_llm_tax_templates.py`.

Applied from staged SAP tax rows:

- Sales/AR sources: `INV4`, `RIN4`.
- Purchase/AP sources: `PCH4`, `RPC4`.
- Tax accounts mapped through `Account.custom_sap_acct_code`.

Created:

- SAP AR tax templates: 17.
- SAP AR tax rows: 26.
- SAP AP tax templates: 24.
- SAP AP tax rows: 41.

Verification dry-run after application:

- Sales templates remaining to create: 0.
- Purchase templates remaining to create: 0.

## Bank And Payment Setup

Script: `scripts/apply_llm_bank_payment_setup.py`.

Applied:

- Created 44 ERPNext `Bank` records from SAP `ODSC`.
- Created 2 company `Bank Account` records from SAP `DSC1`.
- Marked SAP house-bank accounts `2101012` and `2101001` as ERPNext `Bank` accounts.
- Marked SAP cash accounts `1203101`, `1203102`, and `1203104` as ERPNext `Cash` accounts.
- Set Mode of Payment account defaults for:
  - `Cash` -> `1203104 - MAIN CASH - L`
  - `Cheque` -> `2101012 - HDFC CC 50200086879083 - L`
  - `Wire Transfer` -> `2101012 - HDFC CC 50200086879083 - L`
  - `Bank Draft` -> `2101012 - HDFC CC 50200086879083 - L`
  - `Credit Card` -> `2101012 - HDFC CC 50200086879083 - L`

Verification dry-run after application:

- Banks remaining to create: 0.
- Bank accounts remaining to create: 0.
- Account type fixes remaining: 0.
- Mode defaults remaining: 0.

## Notes

- Payment transaction import must still preserve the SAP payment header account (`CashAcct`, `TrsfrAcct`, `CheckAcct`) per document. Mode of Payment defaults are only safe fallbacks.
- SAP payment headers use clearing, discount, cash, and direct bank/OD accounts beyond the 2 `DSC1` house bank accounts; those should be handled during payment transform, not flattened into a single bank default.

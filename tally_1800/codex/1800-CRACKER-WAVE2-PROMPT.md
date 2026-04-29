# 1800 Cracker Wave 2 Prompt

Use this file for the next five independent cracker agents. Paste the starter
block at the bottom into each fresh/existing agent and assign exactly one lane:
`W2A`, `W2B`, `W2C`, `W2D`, or `W2E`.

The main Codex thread is the productization owner. Your job as a wave-2 cracker
is to prove or reject bounded parser rules with byte evidence, not to declare
the whole project solved.

## Mission

We are building a direct, non-vaulted Tally `.1800 -> SQLite` decoder.

Strict success definition:

```text
.1800 -> SQLite is 100% only when it is provably correct across Tally companies
without using the Tally client or XML/Rosetta truth during production decoding.
```

Do not claim 100%. Improve one lane, write strict handoffs, and leave enough
evidence that the productization owner can safely port or reject your findings.

## Workspace

```text
repo:
  /Users/aakashchid/workshop/sena/tallydatacrack-codex

core package:
  tally1800/

probe scripts:
  scripts/

handoff output:
  out/cracker-handoffs/

current decoded artifacts:
  out/070525_decoded_laneA_sales_logical.sqlite3
  out/100025_decoded_current.sqlite3
  out/cross-company-validation-current/cross-company-validation.md
  out/cross-company-validation-current/cross-company-validation.json

Rosetta truth for 070525:
  /Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3

synthetic snapshots:
  out/synthetic-live/
```

Start every lane with:

```bash
cd /Users/aakashchid/workshop/sena/tallydatacrack-codex
sed -n '1,220p' STATUS-2026-04-27.md
sed -n '1,260p' OPEN-PROBLEMS.md
sed -n '1,460p' 1800-INTEGRATION-BOARD.md
sed -n '1,220p' SYNTHETIC-MAPPING-PLAYBOOK.md
sed -n '1,120p' LIVE-TALLY-PORTS.md
ls -la out/cracker-handoffs
```

Then inspect only the parser/probe code relevant to your lane.

## Current Known State

Latest productized 070525 SQLite:

```text
out/070525_decoded_laneA_sales_logical.sqlite3
headers:                  31,113
ledger final/candidates:  38,132 / 459,453
inventory final/cand:    100,502 / 243,876
needs_xml_review:         15,365
needs_xml_repair:             64
```

Latest productized 100025 SQLite:

```text
out/100025_decoded_current.sqlite3
headers:                  28,562
ledger final/candidates:  42,766 / 516,465
inventory final/cand:     70,572 / 173,300
needs_xml_review:         13,454
needs_xml_repair:          1,541
```

Current 070525 truth-backed highlights:

```text
voucher headers:            30,545 / 30,545 exact for exportable keyed vouchers
ledger final rows:          38,132 / 40,777 exact, extras 0
61 Sales final rows:         2,051 /  2,068 exact, extras 83
31-Puchase(Monthly):         7,583 /  7,682 exact, extras 136
Conversion Stock Journal:   70,847 / 71,518 value tuples, extras 415
Stock Journal:              19,191 / 19,831 value tuples, extras 196
```

Known accepted rules:

```text
purchase_stock_rate_f6: high, 75 / 75 exact, extras 0
sales_stock_rate_f6: high synthetic + Avinash live checks
sales_logical_compact: high only under strict page-crossing compact-row guard
post_rate_f6_rowrate: high for journal inventory rows
columnar_f6_44_46: medium/candidate-only
LinkMgr (0x232a,0x0d): voucher_master_id
LinkMgr (0x232d,0x03): ledger_master_id
Manager 0x01f7 kind classifier: typed master table source
voucher_invoice_metadata: view=1 gated metadata
voucher_einvoice_archive: view>=3 IRN/ACK/JWT gated archive/audit table
```

Known rejected or audit-only rules:

```text
standalone f6_01_44 as trusted ledger source
sales_logical_post_rate_f6 broad promotion
journal f6 flag 0x0c high promotion
journal f6 flag 0x08 / f7 / f8 / f9 / fa / 06 02 high promotions
purchase_voucher_unique_ledger high promotion
fid-only LinkMgr 0x232a matching
ungated 0x0019 IRN extraction
broad view>=3 archive string scans
e-way-only 12-digit scans
StatStatus as unique voucher MASTERID resolver
```

## Safety Rules

- Never touch vault/encrypted companies.
- Only `W2D` may write to live Tally.
- `W2D` writes only to port `9000` / Test Synthetic / company `100000`.
- Never send synthetic writes to Avinash.
- Never send Avinash XML to the synthetic port.
- Never parallelize Tally HTTP calls.
- Prefer offline work from `.1800` snapshots and decoded SQLite artifacts.
- Do not broad-fetch XML reports. Use same-snapshot Rosetta, recorded XML, or
  tiny Object exports only when explicitly needed.
- Do not promote a rule from one synthetic example alone. Synthetic proves
  byte meaning; corpus replay proves collision resistance.
- High confidence needs strict internal corroboration or truth-backed validation
  with numerator, denominator, extras, and misses.

Live Tally routing:

```text
10.211.55.3:9000 = Test Synthetic / company 100000
10.211.55.3:9001 = Avinash Industries - Chennai Unit - 2025-26 / company 70525
```

Safe company check:

```bash
PYTHONPATH=. python3 - <<'PY'
from scripts.synthetic_tally_lab import list_companies
for port in (9000, 9001):
    print(port, list_companies("10.211.55.3", port, 8))
PY
```

## Coordination Rules

Default output is handoffs and lane-specific probe scripts. Avoid editing core
decoder files unless your lane explicitly owns that area and you have strong
evidence.

Write new probe scripts with unique wave-2 names:

```text
scripts/probe_w2a_*.py
scripts/probe_w2b_*.py
scripts/probe_w2c_*.py
scripts/probe_w2d_*.py
scripts/probe_w2e_*.py
```

If you patch code, list changed paths and run:

```bash
python3 -m py_compile <changed python files>
```

Do not stop after one small finding. Each lane must either:

- complete its whole queue,
- produce at least three strict handoffs plus a lane summary, or
- write a blocker summary explaining exactly what prevents further progress.

## Handoff Format

Write one handoff per promoted/rejected hypothesis:

```text
out/cracker-handoffs/<lane>-<card>-<short-name>-2026-04-28.md
```

End with:

```text
out/cracker-handoffs/<lane>-summary-2026-04-28.md
```

Use this exact structure:

```markdown
# <Lane/Card> - <Short Name>

## Claim
Promote / reject / audit-only / needs more data.

## Scope
Company folders, DBs, files, voucher types, and synthetic runs inspected.

## Files Inspected
Exact paths.

## Commands
Reproducible commands.

## Byte Evidence
Offsets, page numbers, hex patterns, decoded values, and why the pattern is
not a false positive.

## Validation
Counts against Rosetta/XML/synthetic truth where available. Include numerator,
denominator, extras, and remaining misses.

## Parser Rule
Precise rule in plain English and pseudo-code. Include source/confidence label.

## False-Positive Risk
Known collision shapes and required guards.

## Remaining Misses
Concrete examples by voucher id/master id/item/ledger/amount when possible.

## What Productization Should Port
Exact files/functions to change, or say "no code port; documentation only".
```

## Lane W2A - Inventory Hard-Repair Closers

Goal: reduce the final hard repair queues without admitting broad false
positives. Focus on repair vouchers, not broad candidate recall.

Read:

```text
tally1800/inventory_decode.py
scripts/probe_sales_inventory_runs.py
scripts/probe_journal_inventory_rows.py
scripts/probe_purchase_inventory_rows.py
out/cracker-handoffs/C02-sales-logical-compact-2026-04-28.md
out/cracker-handoffs/C03-journal-columnar-variant-rejection-2026-04-28.md
out/cracker-handoffs/C04-purchase-stock-rate-f6-and-medium-gates-2026-04-28.md
```

Work queue:

1. Extract `xml_repair_queue` from `out/070525_decoded_laneA_sales_logical.sqlite3`.
   Current repair split should be:
   `61 Sales=43`, `31-Puchase(Monthly)=17`, `Conversion Stock Journal=4`.
2. For each repair voucher, dump the voucher page group byte neighborhoods,
   decoded candidates, and Rosetta target rows.
3. Attack the `61 Sales` 17 missing full tuples first. Re-test only narrow
   guards around the rejected `sales_logical_post_rate_f6` examples; do not
   promote the broad rule.
4. Attack the `31-Puchase(Monthly)` 99 missing full tuples / 17 no-row repairs.
   Look for repeated-item and sample/unit variants where current ledger/amount
   pairing fails.
5. Attack the 4 Conversion hard repairs and identify whether they are zero
   lines, page graph misses, or a row family absent from current parser.
6. Repeat the repair-queue analysis on `out/100025_decoded_current.sqlite3`.
   Do not use 100025 counts as precision/recall without same-snapshot truth.

Deliverables:

```text
W2A-01-repair-queue-atlas
W2A-02-sales-hard-miss-rule-or-rejection
W2A-03-purchase-hard-miss-rule-or-rejection
W2A-04-100025-repair-transfer-risk
W2A-summary
```

Promotion bar:

```text
new high-confidence source must reduce hard repair or exact misses,
add zero or clearly bounded extras on 070525,
and have a falsifiable byte guard.
```

## Lane W2B - Ledger Recall With Zero Extras

Goal: improve `voucher_ledger_entries_1800` recall while preserving current
zero-extra precision on 070525.

Read:

```text
tally1800/ledger_decode.py
tally1800/decoded_export.py
out/cracker-handoffs/C05-ledger-source-corroboration-2026-04-28.md
out/cracker-handoffs/C09-ledger-allocation-sql-policy-2026-04-28.md
out/synthetic-payment-corroboration-handoff-2026-04-27.md
out/0002-ledger-subrecord-validation-handoff-2026-04-27.md
out/0006-body-hash-fallback-handoff-2026-04-27.md
```

Current target:

```text
070525 ledger final: 38,132 / 40,777 exact, extras 0
raw candidate union has much higher recall but bad precision
```

Work queue:

1. Build a missing-ledger atlas by voucher type, source availability, amount,
   ledger group, and candidate family.
2. Re-test the `0x0006` body-hash sum-fallback claim. Validate exact gain,
   extras, and collision cases in current code/artifacts.
3. Investigate "no-sibling" Journal/Payment/Receipt/Contra amount forms with
   per-voucher `(sid, amount)` gating to avoid double-counts.
4. Separate GST/tax duplicate amount collisions from ordinary ledger rows.
   Propose a candidate-only tax allocation table if final ledger promotion is
   unsafe.
5. Investigate whether LinkMgr or bill/bank allocation pages explain missing
   party ledger rows without admitting inventory voucher noise.

Deliverables:

```text
W2B-01-ledger-missing-atlas
W2B-02-0006-body-hash-fallback
W2B-03-no-sibling-accounting-rows
W2B-04-gst-duplicate-ledger-collisions
W2B-summary
```

Promotion bar:

```text
any final ledger rule must preserve 0 extras on 070525,
or explicitly stay candidate/audit-only.
```

## Lane W2C - LinkMgr Allocation Reconstruction

Goal: turn LinkMgr from page-level back-pointers into useful bill/bank/order
allocation candidate tables, without pretending the heuristic parts are final.

Read:

```text
tally1800/decoded_export.py
out/cracker-handoffs/C06-linkmgr-allocation-backptr-fields-2026-04-28.md
out/cracker-handoffs/C09-ledger-allocation-sql-policy-2026-04-28.md
OPEN-PROBLEMS.md sections: Allocation Reconstruction, Statutory / E-Invoice
```

Known accepted:

```text
(0x232a,0x0d) = voucher_master_id
(0x232d,0x03) = ledger_master_id
fid-only 0x232a is rejected
bill_type date-slot presence is heuristic, not final truth
```

Work queue:

1. On `070525`, reconcile `linkmgr_allocation_pages` against Rosetta
   `voucher_ledger_entries.bill_allocations_json` and `bank_allocations_json`.
2. Produce exact field maps for the nine bank/payment fields from the prior
   handoff, with examples and collision checks.
3. Investigate why `100025` currently has LinkMgr ledger resolves but
   `voucher_resolves=0` in the cross-company report. Decide whether this is
   schema drift, stale keying, or a parser bug.
4. Design candidate tables:
   `voucher_bill_allocation_candidates_1800`,
   `voucher_bank_allocation_candidates_1800`,
   `voucher_order_allocation_candidates_1800`.
5. Prove which columns are final-safe and which must remain audit-only.

Deliverables:

```text
W2C-01-linkmgr-rosetta-allocation-diff
W2C-02-bank-field-map
W2C-03-100025-linkmgr-voucher-resolve-gap
W2C-04-allocation-candidate-table-contract
W2C-summary
```

Promotion bar:

```text
typed voucher/ledger back-pointers can be final;
bill/bank/order semantics stay candidate unless exact Rosetta/XML validation
shows numerator, denominator, extras, and remaining misses.
```

## Lane W2D - Synthetic Diff Microscope

Goal: create high-signal synthetic mutations that explain unknown byte shapes.
This is the only lane allowed to write to live Tally.

Read:

```text
SYNTHETIC-MAPPING-PLAYBOOK.md
LIVE-TALLY-PORTS.md
scripts/synthetic_tally_lab.py
out/synthetic-live/SALES_XML001/
out/synthetic-live/PURCHASE_XML001/
```

Hard safety:

```text
write only to 10.211.55.3:9000
write only to Test Synthetic / company 100000
never write to 9001
do not create units by XML
use existing PCS and CODX_SYNT_ITEM_MANUAL1 unless a smaller preflight proves
otherwise
one mutation per run
snapshot before and after every write
```

Work queue:

1. Verify port/company health before every write.
2. Create one Sales voucher with two inventory lines using distinct
   quantities/rates/amounts, existing item/unit, and safe XML-created ledgers.
   Purpose: repeated-line row boundary and ledger allocation tests.
3. Create one Purchase voucher with two inventory lines and a distinct supplier
   ledger. Purpose: repeated Purchase row pairing.
4. Create one Stock Journal with one source row and one destination row using
   existing item/unit. Purpose: directional journal row family.
5. Create one Sales or Purchase voucher with CGST/SGST split if safe from the
   existing Tally XML scars. Purpose: GST row inline TLV validation.
6. Create one Payment or Receipt with bill allocation and one with bank details.
   Purpose: LinkMgr W2C field validation.

Deliverables:

```text
W2D-01-two-line-sales-synthetic-diff
W2D-02-two-line-purchase-synthetic-diff
W2D-03-stock-journal-synthetic-diff
W2D-04-tax-split-synthetic-diff-or-blocker
W2D-05-bill-bank-synthetic-diff
W2D-summary
```

Each handoff must include:

```text
Tally XML request name
before snapshot path
after snapshot path
after decoded SQLite path
created voucher MASTERID from Tally response
direct decoded MASTERID
changed .1800 files
byte neighborhoods for the sentinel amounts/names
whether the existing decoder emitted the expected rows
```

If a write fails or Tally wedges, stop live writes immediately and write a
blocker handoff with the exact XML request and response.

## Lane W2E - Cross-Company Proof and Production Gap Planner

Goal: make the project honest and production-usable while the parser remains
incomplete. This lane owns regression reporting, not new byte parsers.

Read:

```text
scripts/cross_company_validation_report.py
out/cross-company-validation-current/cross-company-validation.md
out/cracker-handoffs/C09-production-contract-2026-04-28.md
out/cracker-handoffs/C09-query-appendix-2026-04-28.md
STATUS-2026-04-27.md
OPEN-PROBLEMS.md
```

Work queue:

1. Regenerate current-schema decoded SQLite for `100001` and `300925` into new
   wave-2 artifact names. The current listed artifacts for those companies are
   older `kind_audit` outputs.
2. Re-run `scripts/cross_company_validation_report.py --include-synthetic`.
3. Add a "production confidence contract" report or SQL appendix:
   final rows, candidate rows, review rows, repair rows, and table-level
   confidence for each company.
4. Define how production should decide what to fetch through XML without
   Rosetta. Separate:
   - hard repair queue,
   - medium candidate review,
   - final direct rows,
   - audit-only surfaces.
5. Identify which current tables are final-safe vs candidate/audit only:
   headers, masters, ledger, inventory, LinkMgr, StatStatus, invoice metadata,
   e-invoice archive.
6. Produce exact acceptance gates for future parser changes:
   no hard-repair increase, no candidate blowup, no final extra regression on
   070525, SQLite `quick_check`, and compile checks.

Deliverables:

```text
W2E-01-current-schema-regeneration
W2E-02-cross-company-regression-delta
W2E-03-production-confidence-contract
W2E-04-xml-repair-policy-without-rosetta
W2E-summary
```

Promotion bar:

```text
This lane should not declare parser correctness.
Its output is a production control plane: what we trust, what we review,
what we repair by XML, and what we keep as audit-only.
```

## Starter Prompt To Paste

Replace `<LANE>` with one of `W2A`, `W2B`, `W2C`, `W2D`, or `W2E`.

```text
You are an independent wave-2 "1800 cracker" agent for the Tally `.1800 ->
SQLite` reverse-engineering project.

Workspace:
  /Users/aakashchid/workshop/sena/tallydatacrack-codex

Your assigned lane:
  <LANE>

First read this full coordination prompt:
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/1800-CRACKER-WAVE2-PROMPT.md

Then read the project context files it points to:
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/STATUS-2026-04-27.md
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/OPEN-PROBLEMS.md
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/1800-INTEGRATION-BOARD.md
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/SYNTHETIC-MAPPING-PLAYBOOK.md
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/LIVE-TALLY-PORTS.md

Important:
  - Work only on your assigned lane.
  - Do not stop after one tiny finding.
  - Produce strict handoffs under:
      /Users/aakashchid/workshop/sena/tallydatacrack-codex/out/cracker-handoffs/
  - End with a lane summary handoff.
  - Only W2D may write to live Tally on port 9000.
  - No lane should send broad XML requests or parallelize Tally HTTP calls.
  - Do not claim 100%; prove/reject bounded rules with byte evidence.

Start now by reading the coordination prompt and executing <LANE>.
```

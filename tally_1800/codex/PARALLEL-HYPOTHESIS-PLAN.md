# Parallel Hypothesis Plan

This is the working strategy after the live synthetic proof and Claude handoff.

I agree with the user's framing: stop trying to "solve Tally" as one monolithic
decoder. Run many strict byte hypotheses independently, prove or reject each,
then combine only the rules that survive false-positive checks.

## Core Loop

For every hypothesis:

```text
1. State one byte-level claim.
2. Choose one tiny synthetic mutation or one Rosetta-backed corpus slice.
3. Snapshot before/after `.1800`.
4. Decode after-snapshot to SQLite.
5. Ask live XML only for labels/truth on the controlled object.
6. Measure false positives on the same snapshot.
7. Repeat with a second value or second voucher shape.
8. Promote only if the rule still works cross-sample.
```

Never promote a rule from one raw byte hit. A hit becomes a rule only after it
survives both:

```text
same-object truth check
same-snapshot false-positive check
```

## Important Lesson From PAY003

PAY003 is the first useful warning case.

Truth:

```text
voucher 3, ledger 208, amount  321.09
voucher 3, ledger 31,  amount -321.09
```

The synthetic payment probe found all true rows, but it also showed:

```text
voucher 2, ledger 31, amount -1111.10, source=f6_01_44
```

That is not a PAY002 truth row. It is likely a cumulative/summary/stale value.

Conclusion:

```text
d3_52_00_09 and subrecord_2713 agree on true Payment rows.
f6_01_44 is useful evidence but not trusted alone for accounting vouchers.
Final combine rule should require corroboration or source-specific gating.
```

This is exactly why parallel hypotheses must stay separate until combination.

## Hypothesis Card Format

Every teammate should write findings in this format:

```text
Hypothesis:
Byte pattern:
Mutation or corpus slice:
Files inspected:
Expected truth:
Direct SQLite evidence:
Live XML/Rosetta evidence:
False positives:
Promote / reject / needs more data:
Exact parser rule:
Validation command:
```

## Parallel Workstreams

### A. Synthetic Accounting Rows

Goal: finish `voucher_ledger_entries` for accounting vouchers first.

Inputs:

```text
PAY001: voucher 1, ledger 207 +123.45, ledger 31 -123.45
PAY002: voucher 2, ledger 207 +987.65, ledger 31 -987.65
PAY003: voucher 3, ledger 208 +321.09, ledger 31 -321.09
```

Hypotheses to test independently:

```text
d3_52_00_09 anchors are trusted direct ledger rows.
00 50 13 27 00 10 (subrecord id 0x2713) carries trusted direct ledger rows.
f6_01_44 candidates include true rows but can emit cumulative/stale extras.
source corroboration count >= 2 gives the trusted row set on synthetic Payment.
```

Next mutations:

```text
Receipt voucher with Cash as debit and synthetic ledger as credit.
Contra voucher between two Cash-in-Hand ledgers.
Journal voucher between two non-Cash ledgers.
Payment voucher where credit ledger is not Cash.
Payment voucher with amount 0.01 and amount 100000.99.
```

Success metric:

```text
direct rows exactly equal XML ledger rows for each synthetic voucher
no extra final rows after source-combination filtering
```

### B. Claude Handoff Correctness Fixes

Port before broad features:

```text
Remove/relabel 0x1327 as proven subrecord. 00 50 27 13 is absent.
Keep 00 50 13 27 as subrecord id 0x2713 only where directly observed.
Use page-aware TLV continuation: values resume at byte 28 of next page.
Do not use amount sign as is_deemed_positive; use prefix byte where proven.
Treat 0x01fe as GST classification metadata, not allocation.
Do not use f6_01_44 flag bytes for sign.
```

### C. Master Classification

Status: ported into Codex `decode-sqlite`; needs cross-company metrics beyond
070525/live synthetic.

Hypothesis:

```text
Manager.1800 0x01f7 subrecord body contains fid=0x0002/type=0x0006 u32 kind.
```

Kind map:

```text
2 groups
3 ledgers
4 cost_categories
5 cost_centres
6 godowns
9 stock_items
10 voucher_types
11 currencies
12 units
```

Success metric:

```text
typed master tables populated cross-company without Rosetta name lookup
070525 master kind classification near 99.97%
```

### D. Ledger Decode Port

Status: first table ported into Codex `decode-sqlite`; synthetic Payment rows
are exact. Large-corpus recall/precision and the full 0x0006 body-hash
sum-fallback are still open.

Independent sources:

```text
0x52d3 summary anchors with pmid_to_vmid attribution.
0x0006 subrecords with body-hash dedup.
0x0002 subrecords for Journal/Payment/Receipt/Contra.
synthetic 0x2713 evidence as a separate observed family, not yet generalized.
```

Combination:

```text
source-label every row
dedupe by (voucher_master_id, ledger_master_id, amount_scaled, sign)
mark corroborated rows where two independent sources agree
keep raw candidates for audit
```

Validation:

```text
0x0002 ledger rows: 12,920 / 12,921 on Claude's 10 voucher-type set
0x52d3 attribution: 100% anchor resolution across 4 corpora
0x0006 fallback wrong-amount rate: 0.19% after body-hash dedup
```

### E. Inventory Gaps

Keep direct inventory rows as a separate track.

Parallel hypotheses:

```text
NEITHER-F6 means all-zero journal row.
Zero-rated rows have qty != 0 and amount = 0; do not skip just because amount is zero.
80 00 02 00 00 09 and 00 00 02 00 00 09 fallbacks recover no-sibling rows.
Store Movement Stk Jrl belongs in journal-like parser family.
Purchase Order / Purchase Order-Others / Job Order can use purchase-hybrid parser.
```

Guardrail:

```text
Do not revive Track 37-B intra-row columnar pairing unless cross-company proof
beats the existing inter-row interpretation.
```

### F. LinkMgr Allocations

Port as its own typed surface.

Hypotheses:

```text
0x232a/type 0x0d = voucher_master_id
0x232d/type 0x03 = ledger_master_id
both pointers can exist on the same allocation page
fid 0x0005/type 0x0d date slot present => Agst Ref
absence => New Ref
```

Success metric:

```text
bill allocations 95%+
bank/payment fields populated with voucher and ledger back-pointers
```

### G. Statutory And E-Invoice

Emit useful tables without blocking core accounting decode.

Tables:

```text
voucher_statutory_status from StatStatus.1800
voucher_invoice_metadata from TranMgr view=1
voucher_einvoice_archive from view=1/view=3..6
voucher_inventory_gst_classification from 0x01fe
```

Guardrails:

```text
store JWT payload opaquely
do not validate NIC signatures
do not claim GST class master join
```

## Suggested Claude Delegation Prompt

```text
Read /Users/aakashchid/workshop/sena/tallydatacrack-codex/PARALLEL-HYPOTHESIS-PLAN.md
and /Users/aakashchid/workshop/sena/tallydatacrack-codex/SYNTHETIC-MAPPING-PLAYBOOK.md.

Pick exactly one hypothesis card. Do not solve the whole decoder. For your
chosen hypothesis, run the smallest possible synthetic or Rosetta-backed test,
write down byte evidence, false positives, and a promote/reject decision.

Use this strict format:
Hypothesis:
Byte pattern:
Mutation or corpus slice:
Files inspected:
Expected truth:
Direct SQLite evidence:
Live XML/Rosetta evidence:
False positives:
Promote / reject / needs more data:
Exact parser rule:
Validation command:

High-value hypotheses to pick from:
1. Payment/Receipt/Journal ledger rows: compare d3_52, 0x2713, f6_01_44, and source-corroboration.
2. Manager 0x01f7 kind-code classifier on all company folders.
3. 0x0002 ledger subrecord rows on Journal/Payment/Receipt/Contra.
4. LinkMgr 0x232a/0x232d allocation back-pointers.
5. StatStatus fixed-slot table.
6. E-invoice view=1 block-aware IRN extraction.

Do not port retracted rules:
0x1327 inventory subrecord, f6 flag-byte sign, amount-sign dp, GST-class master join,
Track 37-B intra-row columnar pairing.
```

## Ownership Recommendation

Codex should keep productization ownership:

```text
tally1800/ package structure
SQLite schema
CLI
trusted/raw candidate separation
validation scripts
docs
```

Claude/research teammates should keep hypothesis ownership:

```text
one byte rule at a time
strict proof/rejection cards
synthetic mutations
Rosetta slices
false-positive hunts
```

That division keeps discovery parallel without letting half-proven rules leak
into the production decoder.

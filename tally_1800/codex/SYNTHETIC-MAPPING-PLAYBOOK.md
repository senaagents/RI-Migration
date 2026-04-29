# Synthetic Mapping Discovery Playbook

This is a real mapping-discovery tool, not a gimmick, but it must be used
strictly: one tiny Tally mutation at a time, before/after `.1800` snapshots,
direct decode to SQLite, then live XML only as the microscope/truth check.

It does **not** prove full `.1800 -> SQLite 100%`. It proves individual byte
rules that can be promoted into the direct decoder after repeat validation.

## Proven Finding 1: Manager Master Identity

Controlled write:

```text
company:   Test Synthetic
folder:    live-tally-data/100000
endpoint:  http://10.211.55.3:9000
run:       LEDGER_EXP001
object:    Ledger CODX_SYNTH_LEDGER_EXP1846A
parent:    Indirect Expenses
```

Command:

```bash
PYTHONPATH=. python3 scripts/synthetic_tally_lab.py \
  --company-dir live-tally-data/100000 \
  --run-id LEDGER_EXP001 \
  --suffix EXP1846A \
  --mode ledger \
  --ledger-parent 'Indirect Expenses' \
  --flush-wait-seconds 2 \
  --file Manager.1800 \
  --file Index.1800 \
  --file TMESSAGE.TSF \
  --file Company.1800 \
  --file CmpSave.1800 \
  --file TSTATE.TSF
```

Result:

```text
Tally import: CREATED=1, ERRORS=0
Manager.1800 size: 189952 -> 190976
sentinel field: Manager.1800 page 371 offset 236, field 0x0002
decoded SQLite: manager_master_records.master_id = 207
live XML: MASTERID = 207, GUID suffix = 000000cf, parent = Indirect Expenses
```

Rule proved by synthetic mutation:

```text
Manager.1800 page header word at offset +4 = Tally master MASTERID
Manager.1800 field 0x0002/000f = primary master name for this ledger
GUID = <company_guid>-<master_id:08x>
```

This same rule was already measured at scale on Avinash/Rosetta; the synthetic
case proves it with a live controlled write where we know the exact mutation.

## Proven Finding 2: Payment Voucher Header

Controlled writes:

```text
PAY001: Payment voucher, amount 123.45, narration CODX_SYNTH_PAYMENT_PAY1847A
PAY002: Payment voucher, amount 987.65, narration CODX_SYNTH_PAYMENT_PAY1850B
PAY003: Payment voucher, amount 321.09, narration CODX_SYNTH_PAYMENT_PAY2027C
debit ledger:  CODX_SYNTH_LEDGER_EXP1846A (MASTERID 207)
credit ledger: Cash (MASTERID 31)
date:          2026-04-01
voucher type:  Payment (MASTERID 34)
```

PAY002 command:

```bash
PYTHONPATH=. python3 scripts/synthetic_tally_lab.py \
  --company-dir live-tally-data/100000 \
  --run-id PAY002 \
  --suffix PAY1850B \
  --mode payment \
  --debit-ledger CODX_SYNTH_LEDGER_EXP1846A \
  --credit-ledger Cash \
  --amount 987.65 \
  --flush-wait-seconds 2 \
  --file TranMgr.1800 \
  --file VchStatus.1800 \
  --file StatStatus.1800 \
  --file TMESSAGE.TSF \
  --file TSTATE.TSF \
  --file Manager.1800 \
  --file Index.1800 \
  --file Company.1800 \
  --file CmpSave.1800
```

Direct decode:

```bash
PYTHONPATH=. python3 -m tally1800 decode-sqlite \
  out/synthetic-live/PAY002/after \
  out/synthetic-live/PAY002/after_decoded.sqlite3
```

SQLite evidence:

```text
voucher_header_candidates:
  master_id=1 page=2 key=0000b420-00000002-00000008 date=2026-04-01
  voucher_number=1 narration=CODX_SYNTH_PAYMENT_PAY1847A type_master_id=34

  master_id=2 page=7 key=0000b420-00000002-00000010 date=2026-04-01
  voucher_number=2 narration=CODX_SYNTH_PAYMENT_PAY1850B type_master_id=34

TranMgr compact fields:
  0x00cb/0003 = 34       voucher type id
  0x0bbb/0003 = 34       voucher type id repeat/near header
  0x0bbb/0006 = 1 or 2   voucher MASTERID
  0x0067/000d = 46112    date serial -> 2026-04-01

TranMgr strings:
  0x0003/000f = key string
  0x00cd/000f = narration
  0x07d5/000f = voucher number
  0x01f7/000f = voucher number series, Default
```

Live XML confirmed the same objects:

```text
PAY001: MASTERID=1, GUID suffix=00000001, amount=123.45
PAY002: MASTERID=2, GUID suffix=00000002, amount=987.65
PAY003: MASTERID=3, GUID suffix=00000003, amount=321.09
VoucherTypeName=Payment
Date=20260401
Ledger entries:
  CODX_SYNTH_LEDGER_EXP1846A +amount
  Cash                       -amount
```

Rule proved by synthetic mutation:

```text
TranMgr.1800 compact 0x0bbb/0006 = voucher MASTERID
TranMgr.1800 compact 0x0067/000d = voucher date serial, base 1899-12-31
TranMgr.1800 field 0x00cd/000f = narration for these payment vouchers
TranMgr.1800 field 0x07d5/000f = rendered voucher number for these vouchers
VchStatus.1800 slot word +0x28 = date serial
VchStatus.1800 slot word +0x64 = voucher type MASTERID
```

## Active Finding: Accounting Ledger Lines

The payment runs also expose the next mapping to promote.

Known scaled amounts:

```text
123.45  ->  12345000  -> a8 5e bc 00 00 00 00 00
-123.45 -> -12345000  -> 58 a1 43 ff ff ff ff ff

987.65  ->  98765000  -> c8 08 e3 05 00 00 00 00
-987.65 -> -98765000  -> 38 f7 1c fa ff ff ff ff

321.09  ->  32109000  -> c8 f1 e9 01 00 00 00 00
-321.09 -> -32109000  -> 38 0e 16 fe ff ff ff ff
```

Known ledger ids:

```text
207 = CODX_SYNTH_LEDGER_EXP1846A
208 = CODX_SYNTH_LEDGER_EXP2025C
31  = Cash
```

Repeated binary families visible in `TranMgr.1800`:

```text
<flags> f6 01 44 00 <ledger_master_id:u32le> ... <amount_i64_x100000>
<flags> d3 52 00 09 <ledger_master_id:u32le> <amount_i64_x100000>

00 50 02 00 00 10 <len> ...
  02 00 00 03 <ledger_master_id:u32le>
  ...
  02 00 00 09 <amount_i64_x100000>

00 50 13 27 00 10 <len> ...
  02 00 00 03 <ledger_master_id:u32le>
  ...
  02 00 00 09 <amount_i64_x100000>
```

For PAY001, PAY002, and PAY003, `decode-sqlite` now promotes the gated rows to
`voucher_ledger_entry_candidates` and de-duplicates them into
`voucher_ledger_entries_1800`. The six tested Payment rows are exact:

```text
PAY001 Cash -123.45, CODX_SYNTH_LEDGER_EXP1846A +123.45
PAY002 Cash -987.65, CODX_SYNTH_LEDGER_EXP1846A +987.65
PAY003 Cash -321.09, CODX_SYNTH_LEDGER_EXP2025C +321.09
```

PAY003 exposed an important false-positive guard:

```text
f6_01_44 emitted voucher=2, ledger=31, amount=-1111.10
```

That is not a PAY002 XML truth row. It appears to be a cumulative/stale/summary
candidate. Therefore `f6_01_44` is useful raw evidence but not trusted alone for
accounting Payment rows. In the synthetic samples, `d3_52_00_09` and
`subrecord_2713` agree on the true rows; final combination should require
source-specific gating or at least two independent sources.

## Proven Finding 3: Core Accounting Voucher Writes Are Unlocked

The guarded writer now supports `payment`, `receipt`, `journal`, and `contra`.
Each mode writes exactly one accounting voucher to `Test Synthetic`, captures
before/after `.1800` snapshots, runs `synthetic-diff`, and can be decoded back
to SQLite.

Validated live runs:

```text
RCT001:
  Tally import CREATED=1, LASTVCHID=4, ERRORS=0
  voucher CODX-RCT-RCT2148A, Receipt, amount 222.22
  direct SQLite MASTERID=4, voucher_type_master_id=36
  trusted rows:
    Cash +222.22
    CODX_SYNTH_LEDGER_EXP1846A -222.22

RCT002:
  Tally import CREATED=1, LASTVCHID=7, ERRORS=0
  voucher CODX-RCT-RCT2212B, Receipt, amount 555.55
  direct SQLite MASTERID=7, voucher_type_master_id=36
  trusted rows:
    Cash +555.55
    CODX_SYNTH_LEDGER_EXP2025C -555.55

JRN001:
  Tally import CREATED=1, LASTVCHID=5, ERRORS=0
  voucher CODX-JRN-JRN2149A, Journal, amount 333.33
  direct SQLite MASTERID=5, voucher_type_master_id=38
  trusted rows:
    CODX_SYNTH_LEDGER_EXP1846A +333.33
    CODX_SYNTH_LEDGER_EXP2025C -333.33

JRN002:
  Tally import CREATED=1, LASTVCHID=8, ERRORS=0
  voucher CODX-JRN-JRN2212B, Journal, amount 666.66
  direct SQLite MASTERID=8, voucher_type_master_id=38
  trusted rows:
    CODX_SYNTH_LEDGER_EXP2025C +666.66
    CODX_SYNTH_LEDGER_EXP1846A -666.66

BANK001:
  Tally import CREATED=1, ERRORS=0
  ledger CODX_SYNTH_LEDGER_BANK2149A under Bank Accounts

CTR001:
  Tally import CREATED=1, LASTVCHID=6, ERRORS=0
  voucher CODX-CTR-CTR2150A, Contra, amount 444.44
  direct SQLite MASTERID=6, voucher_type_master_id=32
  trusted rows:
    CODX_SYNTH_LEDGER_BANK2149A +444.44
    Cash -444.44

CTR002:
  Tally import CREATED=1, LASTVCHID=9, ERRORS=0
  voucher CODX-CTR-CTR2212B, Contra, amount 777.77
  direct SQLite MASTERID=9, voucher_type_master_id=32
  trusted rows:
    CODX_SYNTH_LEDGER_BANK2149A +777.77
    Cash -777.77
```

All three new voucher runs changed 8 of the 10 snapshot files and produced a
`synthetic-diff.json` under their run directory. Their trusted ledger rows were
seen through the same corroborating source family:

```text
52d3_anchor
subrecord_0002
subrecord_0002_wide
subrecord_2713
```

Important parser adjustment from `RCT001`: plain built-in `Receipt` must be
treated as an accounting voucher type, while `Receipt Note` remains explicitly
excluded as an inventory/order document.

## How To Continue Mapping Discovery

1. Verify Tally health and the synthetic guard.

```bash
PYTHONPATH=. python3 scripts/synthetic_tally_lab.py --verify-only
```

2. Make exactly one mutation. Prefer boring accounting vouchers before
inventory. Use unique sentinel narration/name and unique numeric values.

3. Snapshot only the files needed for the suspected surface. For voucher header
and ledger lines, use:

```text
TranMgr.1800
VchStatus.1800
StatStatus.1800
Manager.1800
Index.1800
TMESSAGE.TSF
TSTATE.TSF
Company.1800
CmpSave.1800
```

4. Decode the after snapshot to SQLite.

```bash
PYTHONPATH=. python3 -m tally1800 decode-sqlite \
  out/synthetic-live/<RUN>/after \
  out/synthetic-live/<RUN>/after_decoded.sqlite3
```

5. Query the exact sentinel in the direct SQLite output.

```bash
sqlite3 out/synthetic-live/<RUN>/after_decoded.sqlite3 \
  "select * from field_strings where text like '%CODX_SYNTH%';"

sqlite3 out/synthetic-live/<RUN>/after_decoded.sqlite3 \
  "select * from compact_numeric_fields where file_name='TranMgr.1800';"

sqlite3 out/synthetic-live/<RUN>/after_decoded.sqlite3 \
  "select * from voucher_header_candidates order by master_id;"
```

6. Use live XML only to label the controlled object. Do not let XML become the
decoder. XML answers questions like "what did Tally call the object id, amount,
date, and ledger rows for this one mutation?"

7. Repeat with a second value. A one-sample byte pattern is a clue; two
different values with the same shape become a candidate rule.

8. Promote a rule only after it passes:

```text
same mutation type, two or more unique values
same-snapshot direct SQLite sees the bytes
live XML agrees on object identity/values
rule does not create obvious extra rows in the synthetic snapshot
rule can be replayed on Avinash/Rosetta without increasing false positives
```

9. Record every run here or in `BREAKTHROUGHS-2026-04-27.md` with:

```text
run id
Tally object created
before/after directory
direct SQLite query result
live XML confirmation
byte rule learned
open caveat
```

## Immediate Next Experiments

1. Implement a probe for payment/accounting ledger rows using PAY001 and PAY002.
   It should emit candidate `(voucher_master_id, ledger_master_id, amount)`.

2. De-duplicate direct rows versus summary rows. For these synthetic payments,
   the final truth should be exactly:

```text
voucher 1, ledger 207, amount  123.45
voucher 1, ledger 31,  amount -123.45
voucher 2, ledger 207, amount  987.65
voucher 2, ledger 31,  amount -987.65
```

3. Use the second-value Receipt/Journal/Contra runs above as the baseline for
future accounting-row probes. Any new source-specific generalization must still
replay against Avinash/Rosetta without increasing false positives.

4. After accounting vouchers work, create one sales/purchase inventory voucher
   using existing built-in units only. Avoid custom unit creation until the
   duplicate-unit crash is understood.

Current inventory-write caveat:

```text
FULL001 attempted a unique custom unit/item/sales voucher bundle.
The master import timed out, no after snapshot was produced, and Manager/
TranMgr mtimes did not advance beyond the prior accounting runs. Treat custom
unit creation as still blocked until Tally is reset/cleared and a smaller
unit-only import is proven.

INVXML01_UNIT then tried only one simple Unit XML (`CUI2231A`) after reading
the scars and restarting Tally. It also timed out. A local after-timeout diff
against the before snapshot found no `CUI2231A` sentinel in Manager/TranMgr and
only `TSTATE.TSF` page 0 changed. Conclusion: custom Unit XML itself is unsafe
for this lab/company, even when isolated. Do not send more Unit XML.
```

## Proven Finding 4: Scalable Sales Voucher XML Write

After splitting live Tally into two ports, the current safe routing is:

```text
10.211.55.3:9000 = Test Synthetic, company 100000
10.211.55.3:9001 = Avinash Industries - Chennai Unit - 2025-26, company 70525
```

The canonical note is `LIVE-TALLY-PORTS.md`. Always verify the loaded company
before sending company-specific XML.

Controlled write:

```text
run:      SALES_XML001
company:  Test Synthetic / 100000 on port 9000
voucher:  CODX-SAL-SALXML1A, Sales, MASTERID 10
item:     CODX_SYNT_ITEM_MANUAL1, MASTERID 211
unit:     PCS, MASTERID 210
ledger:   CODX_SYNTH_LEDGER_SALXML1A, MASTERID 212
party:    CODX_SYNTH_LEDGER_PARTYXML1A, MASTERID 213
qty/rate/amount: 3.00 / 44.44 / 133.32
```

Prerequisites were created by XML, not UI:

```text
LEDGER_SALES_XML001: CODX_SYNTH_LEDGER_SALXML1A under Sales Accounts
LEDGER_PARTY_XML001: CODX_SYNTH_LEDGER_PARTYXML1A under Sundry Debtors
```

Tally result:

```text
Sales voucher import: CREATED=1, LASTVCHID=10, ERRORS=0
```

Direct `.1800 -> SQLite` result after parser update:

```text
voucher_master_id: 10
voucher_type_name: Sales
stock_item:        211 / CODX_SYNT_ITEM_MANUAL1
ledger:            212 / CODX_SYNTH_LEDGER_SALXML1A
unit:              210 / PCS
quantity/rate/amt: 3.0 / 44.44 / 133.32
source:            sales_stock_rate_f6
confidence:        high
```

Byte rule promoted:

```text
Within a Sales voucher page group:
  02 00 00 03 <stock_id>
  02 00 00 0c <rate_i64_x100000>
  <unit_id:u32> near the rate scale marker
  00 50 f6 01 00 10 <len>
    02 00 00 09 <amount_i64_x100000>
    02 00 00 0b <qty_i64_x100000>

Ledger allocation is the nearest preceding positive amount allocation for the
same amount, choosing the closest preceding ledger reference. This avoids
incorrectly picking the later party ledger.
```

Avinash validation:

```text
local 070525 decode added exactly 5 best rows, all source=sales_stock_rate_f6
all 5 matched Rosetta exactly by voucher id, item, ledger, qty, rate, amount
live Avinash Object export on port 9001 matched the same 5 rows
hard inventory XML repair count improved: 86 -> 84 vouchers
```

Evidence:

```text
out/synthetic-live/SALES_XML001/
out/synthetic-live/SALES_XML001/after_decoded.sqlite3
out/live-xml-diff-070525/sales-stock-rate-f6-live-check/
```

Custom Unit XML remains unsafe. The scalable path is: preflight existing
masters, write any missing safe ledgers by XML, use existing/proven units and
stock items, then create one voucher per request.

## Proven Finding 5: Purchase Stock-Rate-F6 Voucher Row

Controlled write:

```text
run:      PURCHASE_XML001
company:  Test Synthetic / 100000 on port 9000
voucher:  CODX-PUR-PURXML1A, Purchase, MASTERID 11
item:     CODX_SYNT_ITEM_MANUAL1, MASTERID 211
unit:     PCS, MASTERID 210
ledger:   CODX_SYNTH_LEDGER_PURXML1A, MASTERID 214
supplier: CODX_SYNTH_LEDGER_SUPXML1A, MASTERID 215
qty/rate/amount: 4.00 / 55.55 / -222.20
```

Prerequisites were created by XML:

```text
LEDGER_PURCHASE_XML001: CODX_SYNTH_LEDGER_PURXML1A under Purchase Accounts
LEDGER_SUPPLIER_XML001: CODX_SYNTH_LEDGER_SUPXML1A under Sundry Creditors
```

Tally result:

```text
Purchase voucher import: CREATED=1, LASTVCHID=11, ERRORS=0
```

Direct `.1800 -> SQLite` result after parser update:

```text
voucher_master_id: 11
voucher_type_name: Purchase
stock_item:        211 / CODX_SYNT_ITEM_MANUAL1
ledger:            214 / CODX_SYNTH_LEDGER_PURXML1A
unit:              210 / PCS
quantity/rate/amt: 4.0 / 55.55 / -222.20
source:            purchase_stock_rate_f6
confidence:        high
```

Byte rule promoted:

```text
Within a Purchase-like voucher page group:
  02 00 00 03 <stock_id>
  02 00 00 0c <rate_i64_x100000>
  <unit_id:u32> near the rate scale marker
  00 50 f6 01 00 10 <len>
    02 00 00 09 <negative_amount_i64_x100000>
    02 00 00 0b <qty_i64_x100000>

Ledger allocation is the closest preceding signed ledger allocation carrying
the same negative amount:
  02 00 00 03 <purchase_ledger_id> ... 02 00 00 09 <same_negative_amount>
```

Avinash validation:

```text
local 070525 decode emitted 75 rows, all source=purchase_stock_rate_f6
all 75 were under 31-Puchase(Monthly)
all 75 matched Rosetta exact tuple by voucher id, item, ledger, qty, rate, amount
extras from this source: 0
31-Puchase(Monthly) trusted coverage: 7583 / 7682, extras still 136
hard inventory XML repair count improved: 84 -> 65 vouchers
31-Puchase(Monthly) XML repair vouchers improved: 36 -> 17
```

Evidence:

```text
out/synthetic-live/PURCHASE_XML001/
out/synthetic-live/PURCHASE_XML001/after_decoded.sqlite3
out/070525_decoded_purchase_stock_rate_f6.sqlite3
```

## Safety Rules

- Never run synthetic lab commands in parallel. Tally HTTP is effectively
  single-threaded for these experiments, and the script lock is there for a
  reason.
- Respect `LIVE-TALLY-PORTS.md`: current routing is synthetic on `9000`,
  Avinash on `9001`.
- Do not write to Avinash or any non-synthetic company.
- Keep each import tiny. One object or one voucher per run.
- If Tally returns a `LINEERROR`, stop and inspect before retrying.
- Avoid custom Unit XML for now. The first broad synthetic import hit an
  internal duplicate-unit error on `CSUH001`.
- Treat the synthetic company as disposable, but treat its before/after
  artifacts as the source of truth for mapping discovery.

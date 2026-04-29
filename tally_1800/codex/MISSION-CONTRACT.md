# Tally Migration Mission Contract

This is the cut-and-dry product contract for this repo.

## Mission

Build a practical Tally migration pipeline that turns Tally company data into a
production SQLite database.

The pipeline has two modes:

```text
primary path:
  direct .1800/.900/.TSF file decode into SQLite

repair path:
  targeted read-only TDL/XML against a running Tally company only for gaps
```

The direct `.1800` decoder is the core product. TDL/XML is the repair oracle and
learning loop, not the default export strategy.

## Success Definitions

Pure offline success means:

```text
input:
  non-vaulted Tally company folder

output:
  accounting-grade SQLite

dependency:
  no Tally client, no XML, no TDL, no Rosetta

claim allowed only when:
  the result is provably complete across representative companies
```

Hybrid production success means:

```text
input:
  Tally company folder
  optionally, matching company open in Tally for repair

output:
  accounting-grade SQLite
  confidence report
  XML/TDL repair log
  parser-learning handoffs for every repaired gap
```

Hybrid success is allowed before pure offline success, as long as the final
SQLite clearly records which rows came from direct bytes and which rows came
from XML/TDL repair.

## Pipeline Contract

Every migration run should follow this order:

```text
1. Decode .1800 files directly.
2. Write SQLite direct tables, candidate tables, audit tables, and repair queue.
3. Mark every business row/field with confidence and provenance.
4. For hard gaps only, fetch targeted truth through TDL/XML by MASTERID/GUID/name.
5. Patch the production SQLite with XML-backed repairs.
6. Save the raw XML, repair provenance, and byte-neighborhood evidence.
7. Promote a new direct parser rule only when it reduces misses without adding
   false positives.
```

The XML/TDL path must be narrow:

```text
allowed:
  voucher by MASTERID
  voucher by GUID
  ledger/stock item/voucher type by exact name or id
  small custom read-only TDL reports

avoid:
  broad Day Book dumps
  full-company XML exports
  write/mutation TDL in real companies
```

## SQLite Contract

Final consumer-facing SQLite must separate direct, repaired, candidate, and raw
evidence.

```text
direct final:
  high-confidence rows from .1800 bytes

xml repaired:
  rows fetched from Tally only for explicit repair/review targets

candidate/review:
  plausible direct evidence not safe for final export

raw/audit:
  strings, TLVs, pages, low-level candidates, XML payloads, provenance
```

Candidate tables are never final export tables by themselves.

## Confidence Contract

```text
high:
  direct byte rule passed strict local guards and current corpus validation

medium:
  plausible byte evidence, useful for reconciliation, not final without another
  guard or XML confirmation

repair:
  direct decoder knows this object/row is missing or unsafe and should request
  targeted TDL/XML if Tally is available

audit:
  preserved evidence only; never exposed as accounting truth
```

`high` does not mean universal 100% truth on unseen companies. It means safe
enough for direct output under the current contract, with provenance retained.

## Non-Negotiables

```text
Do not declare pure .1800 -> SQLite 100% until Tally/XML/Rosetta are no longer
needed across representative non-vaulted companies.

Do not use broad XML as the main migration path.

Do not write to production/customer/Avinash companies from synthetic tooling.

Do not expose _candidates or raw byte tables as final business rows.

Do not let XML repairs disappear as one-off fixes; every repaired gap must feed
the parser-learning loop.
```

## Why Both Paths Exist

Direct `.1800` decode is fast, offline, and scalable, but not yet complete for
every Tally shape.

TDL/XML is slower and requires Tally to be running, but it gives exact truth for
specific gaps and makes production migration possible while the direct decoder
continues improving.

The intended end state is that XML/TDL usage keeps shrinking as more repaired
gaps become direct `.1800` parser rules.

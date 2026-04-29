# TDL Helpers

This directory contains experimental TDL helpers for the `.1800 -> SQLite`
hybrid repair loop.

## Files

```text
CODX1800Bridge.tdl
```

`CODX1800Bridge.tdl` is deliberately read-only in v0. It defines small
health/voucher/master probes that can be loaded in lab Tally once we are ready
to test persistent helper TDL.

## Current Policy

- Use this only in `Test Synthetic` until validated.
- Do not load helper TDL into customer production without explicit approval.
- Do not add write/mutation helpers until the read-only reports are proven.
- Production repair can already use narrow object XML exports by `MASTERID`
  through `tally-db-pipeline`; the TDL helper is for safer lab microscopy and
  future targeted exports.

## Intended Loop

```text
1. Decode .1800 into SQLite.
2. Read voucher_decode_audit and xml_repair_queue.
3. Fetch only the hard gaps via tally-db-pipeline / Tally XML.
4. Patch the final SQLite with XML-backed rows and provenance.
5. Diff XML truth against the raw .1800 bytes around the same MASTERID.
6. Promote a new direct parser rule only if it reduces misses without false
   positives.
```

The helper TDL should make steps 3 and 5 less brittle after it is loaded and
validated.

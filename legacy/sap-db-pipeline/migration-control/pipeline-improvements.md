# Pipeline Improvements

Backlog of `sap-db-pipeline` extractor improvements identified during the 2026-04-27 full-history backfill (window widened from `2025-04-01 → 2026-04-25` to `2018-02-22 → 2026-03-31`).

The pipeline works correctly — these are throughput and operability issues, not correctness bugs. Each item names the symptom we observed, the root cause, and the change.

## Observed cost

| Run | Window | Headers | Lines | Wall time |
| --- | --- | ---: | ---: | ---: |
| 2026-04-25 first-leg | `2025-04-01 → 2026-04-25` | 236,404 | 727,503 | ~24 min |
| 2026-04-27 backfill | `2018-02-22 → 2025-03-31` | 72,278 (added) | 188,K (added) | ~13 min |
| 2026-04-27 refresh | `2025-04-01 → 2026-03-31` (re-extract) | 227,240 (re-staged) | ~895K (re-staged) | ~25 min |

For a daily incremental top-up the realistic delta is ~2K headers / ~10K lines. Re-running the full window to capture that takes 20-25 minutes. The bottleneck is per-row Python work + per-row SQLite I/O against unique-constraint indexes, not HANA fetch.

## Improvements

### 1. Incremental sync mode (highest value)

**Symptom**: every refresh re-extracts the full window from HANA and re-UPSERTs every row in SQLite, even when only a few thousand rows changed.

**Root cause**: `sync-documents` filters by `DocDate`/`RefDate`/`PostDate`, not by SAP audit/update timestamps. There is no "since last successful run" mode.

**Change**: add `sync-documents --since-last-run` (and an explicit `--since YYYY-MM-DD HH:MM:SS`). Implementation:
- Per family, pick the SAP audit column where one exists (`UpdateDate` on document tables, `LogInstanc` on JDT1, fall back to `CreateDate`).
- Read `MAX(extracted_at)` per family from the previous successful `sync_runs` row and use that as the floor.
- Staging is already idempotent via `UNIQUE(source_table, source_key)`, so the upsert path doesn't change.
- Cancellations / amendments: detect via `payload_sha256` mismatch and update in place.

**Expected impact**: a daily refresh drops from ~25 min to ~30-60 sec.

### 2. HANA EXPORT → SQLite bulk load (full-window path)

**Symptom**: full-window re-extracts (which we still need at cutover and during disaster recovery) take 20-25 min.

**Root cause**: rows are pulled with hdbcli row-by-row, sha-hashed and JSON-encoded in Python, then UPSERTed one by one through SQLAlchemy.

**Change**: add a `--bulk` mode for `sync-documents` and `sync-raw-tables`:
- Stream HANA result set straight to a temp Parquet/CSV per table.
- Compute `payload_sha256` and `payload_json` in a vectorised pass (pandas / pyarrow).
- `INSERT ... ON CONFLICT DO UPDATE` from a temporary in-memory table or `.import` for a clean rebuild.

**Expected impact**: full-window pull from ~25 min to ~3-5 min.

### 3. Parallel families

**Symptom**: families run strictly sequentially. The 25-min wall time is roughly the sum of per-family times.

**Root cause**: single-threaded driver loop in `sync.sync_documents`.

**Change**: run independent families on a worker pool (4-8 threads). Each family writes to its own SQLite transactions; the unique constraints are at `(source_table, source_key)` so there is no cross-family contention. SAP HANA happily accepts parallel selects from one user.

**Expected impact**: 3-5× speedup on full-window pulls. Compounds with #2.

### 4. Skip per-row hash + full JSON for trusted tables

**Symptom**: every staged row carries a `payload_sha256` + a `payload_json` of the whole row. For rarely-mutating reference tables this is overhead with no audit value above row-count + key reconciliation.

**Root cause**: pipeline applies the same audit envelope to every staged row regardless of mutability.

**Change**: add a per-table policy in `sap_b1.py`:
- `audit=full` (default for documents, lines, masters with U_* fields) — keep sha + JSON.
- `audit=keyed` — store source key + extract timestamp only; skip sha/JSON.
- Mostly applies to `sap_raw_rows` for tables we know are static / framework metadata.

**Expected impact**: 30-50% smaller SQLite + faster raw sweep.

## Order

1. **#1 Incremental sync mode** — biggest day-to-day value, smallest blast radius. Do first.
2. **#3 Parallel families** — easy win once we have #1 in place; helps both incremental and full-window paths.
3. **#2 HANA EXPORT bulk path** — only matters at cutover / DR. Do when we lock the cutover date.
4. **#4 Audit-envelope policy** — last; pure optimisation.

## Non-improvements / explicit rejections

- **No "skip already-staged rows" via local index check.** That's what the unique constraints already guarantee. Adding a Python-side pre-check would just double the work.
- **No multi-process workers.** Threads are enough for hdbcli + SQLite; multi-process adds connection setup cost and SQLite write contention.
- **No moving away from SQLite.** A 39-50 GB SQLite is well within sqlite limits, and a single file beats Postgres for portability of the evidence pack to the customer.

## When

After the 2026-04-27 full-history snapshot is locked (sync run 12 complete, reconciliation written). These improvements then unblock daily incremental refresh during the Phase 2 ERPNext-mapping work, and again at final cutover.

#!/usr/bin/env python3
"""Decode option — read TallyPrime .1800 binary files into a local SQLite.

This is invoked by sena_tally_bridge.handle_bridge_command when the user
picks "Decode .1800 files" in the migration UI. It wraps the codex
decoder (bench/apps/migration/tally_1800/codex/tally1800/decoded_export.py)
and writes a self-contained SQLite file at extract_output_path() so the
bridge can ack with a path the rest of the toolchain can pick up.

No Frappe push, no record transformation — just .1800 → SQLite.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

# Re-exported helpers from the bridge (sibling module).
from sena_tally_bridge import (
	ack_bridge_command,
	discover_1800_companies,
	extract_output_path,
	heartbeat,
	log,
)

DECODE_KIND = "decode_1800"


def decoded_table_counts(db_path: Path) -> dict:
	"""Quick row-count summary across the decoder's known tables.

	Done as a separate pass so the bridge can ack with counts even if the
	caller doesn't want to read the SQLite directly. We import sqlite3 here
	(rather than at module top) to keep import-time cost off the bridge's
	hot path — discovery and heartbeat don't need this.
	"""
	import sqlite3
	tables = (
		"companies", "groups", "ledgers", "stock_items", "units", "godowns",
		"cost_centres", "voucher_types",
		"production_voucher_headers_1800", "production_ledger_entries_1800",
		"production_inventory_entries_1800",
		"production_xml_repair_queue_1800", "production_review_queue_1800",
	)
	counts: dict[str, int] = {}
	with sqlite3.connect(db_path) as conn:
		for table in tables:
			try:
				counts[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
			except sqlite3.Error:
				counts[table] = 0
	return counts


def run(config, connection_id: str, bridge_token: str, command_id: str, command: dict) -> None:
	"""Run the .1800 decode for the company named in `command['company_id']`.

	Output: a SQLite at extract_output_path(company_id, run_id, 'decode').
	Ack payload: {sqlite_path, counts, elapsed_seconds, summary}.
	"""
	try:
		from tally1800.decoded_export import export_decoded_sqlite
	except Exception as exc:
		raise RuntimeError(f"tally1800 decoder is not available in this bridge build: {exc}") from exc

	company_id = str(command.get("company_id") or "").strip()
	if not company_id:
		raise RuntimeError("decode_1800 command requires a company_id")

	companies = discover_1800_companies(config)
	company = next((item for item in companies if str(item.get("company_id")) == company_id), None)
	if not company:
		raise RuntimeError(f"Company folder {company_id!r} was not found")

	source_dir = Path(company["folder"])
	run_id = uuid.uuid4().hex[:12]
	out_path = extract_output_path(company_id, run_id, DECODE_KIND)
	log(f"Running .1800 decode for {company.get('company_name') or company_id} → {out_path}")
	heartbeat(config, connection_id, bridge_token, status="Syncing", last_error=f"decode started for {company_id}")

	# Throttle progress heartbeats to once per stage transition so tight inner
	# loops don't hammer the backend. Each callback also drops a log line so
	# tailing the bridge log shows live progress.
	stage_start = time.time()
	last_heartbeat_stage = -1

	def _on_progress(stage_idx: int, total: int, name: str, detail: str) -> None:
		nonlocal stage_start, last_heartbeat_stage
		now = time.time()
		elapsed = now - stage_start
		stage_start = now
		log(f"  [decode {stage_idx}/{total}] {name} (+{elapsed:.1f}s) {detail}".rstrip())
		if stage_idx != last_heartbeat_stage:
			last_heartbeat_stage = stage_idx
			try:
				heartbeat(
					config, connection_id, bridge_token,
					status="Syncing",
					last_error=f"decode {stage_idx}/{total}: {name}",
				)
			except Exception:
				# Progress is best-effort. A heartbeat hiccup must never break
				# the decode itself.
				pass

	started = time.time()
	export_decoded_sqlite(source_dir, out_path, progress=_on_progress)
	elapsed = time.time() - started
	counts = decoded_table_counts(out_path)
	summary = {
		"decode_id": run_id,
		"company_id": company_id,
		"company_name": company.get("company_name"),
		"folder": company.get("folder"),
		"counts": counts,
		"sqlite_path": str(out_path),
		"sqlite_size": out_path.stat().st_size,
		"elapsed_seconds": round(elapsed, 1),
		"decoded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
	}
	log(f"Decode finished in {elapsed:.1f}s — {sum(counts.values())} rows across {len(counts)} tables")
	ack_bridge_command(
		config, connection_id, bridge_token, command_id, "Success",
		{"summary": summary, "kind": DECODE_KIND, "sqlite_path": str(out_path)},
	)

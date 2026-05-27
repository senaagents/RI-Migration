#!/usr/bin/env python3
"""HTTP option — pull masters and vouchers over TDL/XML into a local SQLite.

Invoked by sena_tally_bridge.handle_bridge_command when the user picks
"HTTP / XML extract" in the migration UI. Talks to one or more running
TallyPrime instances and writes the results into a SQLite at
extract_output_path(company_id, run_id, 'http_extract').

This module owns:
  - The master-collection definitions (groups, ledgers, stock items, …)
  - The SQLite schema (masters, voucher_batches, failed_batches, meta)
  - Master pulls (cheap, sequential) + meta bookkeeping
  - The orchestrator (`run`) that wires it all together

Voucher pulling, parallelism, work-queue, and the writer thread live in
``tally_http_pool``.

The bridge does NOT push records to the Sena server — output ends in a
SQLite file. Surfacing it back to Sena (upload, syncthing, etc.) is a
separate concern.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from sena_tally_bridge import (
	ack_bridge_command,
	build_collection_xml,
	child_texts,
	clean_xml,
	element_text,
	extract_output_path,
	heartbeat,
	log,
	object_name,
	post_tally_xml_with_retries,
)
from tally_http_pool import (
	parallel_voucher_extract,
	probe_active_ports,
	voucher_types_from_db,
)

HTTP_KIND = "http_extract"
MINIMAL_COLLECTION_FIELDS = ["Name", "Guid", "Parent", "AlterId", "MasterId"]


# ---------------------------------------------------------------------------
# Master collection definitions
# ---------------------------------------------------------------------------
# Each entry produces one TDL/XML COLLECTION request. `default_collection_name`
# is the built-in Tally collection used as a fallback when our custom one
# returns nothing (some Tally releases reject custom collections; the default
# always works but exposes fewer fields).
MASTER_STREAMS: list[dict] = [
	{
		"object_type": "Group",
		"collection_name": "Groups",
		"default_collection_name": "List of Groups",
		"tally_type": "Group",
		"tag_name": "GROUP",
		"fields": ["Name", "Guid", "Parent", "AlterId", "IsRevenue", "IsDeemedPositive"],
	},
	{
		"object_type": "Ledger",
		"collection_name": "Ledgers",
		"default_collection_name": "List of Ledgers",
		"tally_type": "Ledger",
		"tag_name": "LEDGER",
		"fields": [
			"Name", "Guid", "Parent", "AlterId", "MasterId", "OpeningBalance",
			"ClosingBalance", "IsBillWiseOn", "IsCostCentresOn",
			"GstRegistrationType", "PartyGstin", "MailingName", "Address",
			"StateName", "CountryName",
		],
	},
	{
		"object_type": "Voucher Type",
		"collection_name": "VoucherTypes",
		"default_collection_name": "List of Voucher Types",
		"tally_type": "Voucher Type",
		"tag_name": "VOUCHERTYPE",
		"fields": ["Name", "Guid", "Parent", "AlterId", "NumberingMethod", "UseForJobWork"],
	},
	{
		"object_type": "Stock Group",
		"collection_name": "StockGroups",
		"default_collection_name": "List of Stock Groups",
		"tally_type": "Stock Group",
		"tag_name": "STOCKGROUP",
		"fields": ["Name", "Guid", "Parent", "AlterId"],
	},
	{
		"object_type": "Stock Category",
		"collection_name": "StockCategories",
		"tally_type": "Stock Category",
		"tag_name": "STOCKCATEGORY",
		"fields": ["Name", "Guid", "Parent", "AlterId"],
	},
	{
		"object_type": "Stock Item",
		"collection_name": "StockItems",
		"default_collection_name": "List of Stock Items",
		"tally_type": "Stock Item",
		"tag_name": "STOCKITEM",
		"fields": [
			"Name", "Guid", "Parent", "AlterId", "BaseUnits", "OpeningBalance",
			"ClosingBalance", "OpeningValue", "ClosingValue", "GstApplicable",
			"Description",
		],
	},
	{
		"object_type": "Unit",
		"collection_name": "Units",
		"tally_type": "Unit",
		"tag_name": "UNIT",
		"fields": ["Name", "Guid", "OriginalName", "DecimalPlaces", "AlterId"],
	},
	{
		"object_type": "Godown",
		"collection_name": "Godowns",
		"tally_type": "Godown",
		"tag_name": "GODOWN",
		"fields": ["Name", "Guid", "Parent", "AlterId"],
	},
	{
		"object_type": "Cost Category",
		"collection_name": "CostCategories",
		"tally_type": "Cost Category",
		"tag_name": "COSTCATEGORY",
		"fields": ["Name", "Guid", "AlterId", "AllocateRevenue", "AllocateNonRevenue"],
	},
	{
		"object_type": "Cost Centre",
		"collection_name": "CostCentres",
		"default_collection_name": "List of Cost Centres",
		"tally_type": "Cost Centre",
		"tag_name": "COSTCENTRE",
		"fields": ["Name", "Guid", "Parent", "AlterId", "Category"],
	},
	{
		"object_type": "Currency",
		"collection_name": "Currencies",
		"tally_type": "Currency",
		"tag_name": "CURRENCY",
		"fields": ["Name", "Guid", "OriginalName", "FormalName", "AlterId", "DecimalPlaces"],
	},
]


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS extract_meta (
	key TEXT PRIMARY KEY,
	value TEXT
);
CREATE TABLE IF NOT EXISTS masters (
	object_type TEXT NOT NULL,
	source_id TEXT NOT NULL,
	guid TEXT,
	name TEXT,
	alter_id TEXT,
	raw_xml TEXT NOT NULL,
	fields_json TEXT NOT NULL,
	PRIMARY KEY (object_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_masters_guid ON masters (guid);
CREATE INDEX IF NOT EXISTS idx_masters_name ON masters (name);
CREATE TABLE IF NOT EXISTS voucher_batches (
	batch_id TEXT PRIMARY KEY,
	from_date TEXT NOT NULL,
	to_date TEXT NOT NULL,
	voucher_type TEXT,
	port INTEGER,
	bytes INTEGER,
	voucher_count INTEGER,
	xml_gz BLOB NOT NULL,
	pulled_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voucher_batches_range
	ON voucher_batches (from_date, to_date);
CREATE INDEX IF NOT EXISTS idx_voucher_batches_type
	ON voucher_batches (voucher_type);
CREATE TABLE IF NOT EXISTS failed_batches (
	from_date TEXT NOT NULL,
	to_date TEXT NOT NULL,
	voucher_type TEXT,
	retry_count INTEGER,
	error TEXT,
	failed_at TEXT NOT NULL
);
"""


def open_sqlite(path: Path) -> sqlite3.Connection:
	path.parent.mkdir(parents=True, exist_ok=True)
	conn = sqlite3.connect(path)
	conn.executescript(SCHEMA_SQL)
	return conn


def write_meta(conn: sqlite3.Connection, key: str, value) -> None:
	if not isinstance(value, str):
		value = json.dumps(value, ensure_ascii=False, sort_keys=True)
	conn.execute(
		"INSERT INTO extract_meta(key, value) VALUES (?, ?) "
		"ON CONFLICT(key) DO UPDATE SET value=excluded.value",
		(key, value),
	)


# ---------------------------------------------------------------------------
# Master pull (sequential — these are tiny, parallelism not worth it)
# ---------------------------------------------------------------------------
def fetch_collection_xml(
	config,
	stream: dict,
	static_variables: dict[str, str] | None = None,
) -> str:
	"""Cascade through several COLLECTION shapes until one returns a response.

	Different Tally releases reject different collection shapes silently —
	custom may yield empty, default may need ISMODIFY, etc. Each attempt
	loosens constraints (drop fields → drop type → switch to default
	collection) so the firehose keeps going on uneven Tally fleets.
	"""
	minimal = [field for field in MINIMAL_COLLECTION_FIELDS
	           if field in set(stream.get("fields") or MINIMAL_COLLECTION_FIELDS)]
	attempts = [
		{"label": "configured custom", "name": stream["collection_name"],
		 "object_type": stream["tally_type"], "fields": stream.get("fields") or [], "is_modify": False},
		{"label": "minimal custom", "name": stream["collection_name"],
		 "object_type": stream["tally_type"], "fields": minimal, "is_modify": False},
		{"label": "bare custom", "name": stream["collection_name"],
		 "object_type": stream["tally_type"], "fields": [], "is_modify": False},
	]
	if stream.get("default_collection_name"):
		attempts.extend([
			{"label": "configured default", "name": stream["default_collection_name"],
			 "object_type": None, "fields": stream.get("fields") or [], "is_modify": True},
			{"label": "minimal default", "name": stream["default_collection_name"],
			 "object_type": None, "fields": minimal, "is_modify": True},
			{"label": "bare default", "name": stream["default_collection_name"],
			 "object_type": None, "fields": [], "is_modify": True},
		])
	last_error: Exception | None = None
	for attempt in attempts:
		try:
			log(f"  fetching {stream['object_type']} via {attempt['label']}...")
			return post_tally_xml_with_retries(
				config,
				build_collection_xml(
					attempt["name"], attempt["object_type"], attempt["fields"],
					static_variables, is_modify=attempt["is_modify"],
				),
			)
		except Exception as exc:  # noqa: BLE001 — retry-and-fall-back loop
			last_error = exc
			log(f"  {stream['object_type']} {attempt['label']} failed: {exc}")
			time.sleep(2)
	if last_error:
		raise last_error
	raise RuntimeError(f"No collection attempts ran for {stream['object_type']}")


def parse_master_records(xml_text: str, object_type: str, tag_name: str) -> list[dict]:
	root = ET.fromstring(clean_xml(xml_text))
	records: list[dict] = []
	for element in root.iter(tag_name):
		name = object_name(element)
		guid = element_text(element, "GUID")
		alter_id = element_text(element, "ALTERID") or element_text(element, "MASTERID")
		if not name and not guid:
			continue
		source_id = guid or f"{object_type}:{name}"
		records.append({
			"object_type": object_type,
			"source_id": source_id,
			"guid": guid,
			"name": name,
			"alter_id": alter_id,
			"raw_xml": ET.tostring(element, encoding="unicode"),
			"fields": child_texts(element),
		})
	return records


def insert_masters(conn: sqlite3.Connection, records: list[dict]) -> int:
	conn.executemany(
		"INSERT INTO masters(object_type, source_id, guid, name, alter_id, raw_xml, fields_json) "
		"VALUES (?, ?, ?, ?, ?, ?, ?) "
		"ON CONFLICT(object_type, source_id) DO UPDATE SET "
		"guid=excluded.guid, name=excluded.name, alter_id=excluded.alter_id, "
		"raw_xml=excluded.raw_xml, fields_json=excluded.fields_json",
		[
			(r["object_type"], r["source_id"], r["guid"], r["name"], r["alter_id"],
			 r["raw_xml"], json.dumps(r["fields"], ensure_ascii=False))
			for r in records
		],
	)
	return len(records)


def extract_masters(config, conn: sqlite3.Connection, company_name: str) -> dict:
	"""Pull each master collection once, write to SQLite, return per-stream counts."""
	statics = {"SVCURRENTCOMPANY": company_name} if company_name else {}
	counts: dict[str, int] = {}
	for stream in MASTER_STREAMS:
		try:
			xml_text = fetch_collection_xml(config, stream, statics)
			records = parse_master_records(xml_text, stream["object_type"], stream["tag_name"])
			n = insert_masters(conn, records)
			conn.commit()
			counts[stream["object_type"]] = n
			log(f"  master {stream['object_type']}: {n} records")
		except Exception as exc:  # noqa: BLE001
			counts[stream["object_type"]] = 0
			log(f"  master {stream['object_type']} failed: {exc}")
	return counts


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def _resolve_ports(config, command: dict) -> list[int]:
	"""Pick the port list. Command can override; otherwise fall back to config.

	Accepted shapes:
	  - command["tally_ports"]: [9000, 9001, 9002]
	  - command["tally_port_range"]: {"start": 9000, "count": 8}
	  - neither set → [config.tally_port]
	"""
	raw_list = command.get("tally_ports")
	if isinstance(raw_list, list) and raw_list:
		return [int(p) for p in raw_list]
	rng = command.get("tally_port_range") or {}
	start = rng.get("start")
	count = rng.get("count")
	if isinstance(start, int) and isinstance(count, int) and count > 0:
		return [start + i for i in range(count)]
	return [int(getattr(config, "tally_port", 9000))]


def run(config, connection_id: str, bridge_token: str, command_id: str, command: dict) -> None:
	"""Run the HTTP/XML extract for the company in `command`.

	Required command keys:
	  - company_name        — the Tally-side company; used as SVCURRENTCOMPANY
	  - from_date, to_date  — voucher date range, YYYY-MM-DD or YYYYMMDD

	Optional:
	  - tally_ports: [9000, 9001, ...]      — explicit port list
	  - tally_port_range: {start, count}    — alternative shape, easier from UI
	  - company_id                          — used to scope the output path
	  - host                                 — Tally host (defaults to config)
	"""
	company_name = str(command.get("company_name") or "").strip()
	if not company_name:
		raise RuntimeError("http_extract command requires a company_name")
	from_date = str(command.get("from_date") or "").strip()
	to_date = str(command.get("to_date") or "").strip()
	if not from_date or not to_date:
		raise RuntimeError("http_extract command requires from_date and to_date")

	host = str(command.get("host") or getattr(config, "tally_host", "localhost") or "localhost")
	requested_ports = _resolve_ports(config, command)
	company_id = str(command.get("company_id") or "").strip()
	if not company_id:
		company_id = "".join(c if c.isalnum() else "_" for c in company_name).strip("_")[:80]
	run_id = uuid.uuid4().hex[:12]
	out_path = extract_output_path(company_id, run_id, HTTP_KIND)
	log(f"HTTP extract: {company_name} ({from_date}..{to_date}) → {out_path}")
	log(f"  ports requested: {requested_ports}")

	# Probe ports first so we don't fan out workers against dead instances.
	heartbeat(config, connection_id, bridge_token, status="Syncing",
	          last_error=f"http extract probing {len(requested_ports)} port(s)")
	active_ports = probe_active_ports(host, requested_ports)
	if not active_ports:
		raise RuntimeError(
			f"No TallyPrime instance answered on {host} ports {requested_ports}. "
			"Ensure Tally is running with HTTP server enabled (Server=Yes in tally.ini)."
		)
	log(f"  ports active: {active_ports}")

	# Phase 1: masters (sequential — small, fits in seconds against one instance).
	# Use the first active port for masters; all instances share the same data folder.
	from dataclasses import replace
	master_config = replace(config, tally_host=host, tally_port=active_ports[0])

	conn = open_sqlite(out_path)
	try:
		write_meta(conn, "kind", HTTP_KIND)
		write_meta(conn, "company_id", company_id)
		write_meta(conn, "company_name", company_name)
		write_meta(conn, "from_date", from_date)
		write_meta(conn, "to_date", to_date)
		write_meta(conn, "tally_host", host)
		write_meta(conn, "tally_ports_active", active_ports)
		write_meta(conn, "tally_ports_requested", requested_ports)
		write_meta(conn, "started_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
		conn.commit()

		log("Pulling masters...")
		heartbeat(config, connection_id, bridge_token, status="Syncing",
		          last_error="http extract: masters")
		master_counts = extract_masters(master_config, conn, company_name)
		write_meta(conn, "master_counts", master_counts)
		conn.commit()

		voucher_types = voucher_types_from_db(conn)
		write_meta(conn, "voucher_types_pulled", voucher_types)
		conn.commit()
		log(f"  voucher types in masters: {len(voucher_types)}")
	finally:
		# Close the master conn so the writer thread can open its own without
		# fighting for the same file handle.
		conn.close()

	# Phase 2: parallel voucher pull. Workers feed a queue; one writer thread
	# owns the SQLite handle.
	def _progress(done: int, total: int, summary: dict) -> None:
		try:
			heartbeat(
				config, connection_id, bridge_token, status="Syncing",
				last_error=f"vouchers {done}/{total} ({summary.get('voucher_count', 0):,} so far)",
			)
		except Exception:  # noqa: BLE001 — heartbeat must never break extract
			pass

	pool_config = replace(config, tally_host=host)
	parallel_summary = parallel_voucher_extract(
		pool_config, out_path, company_name, from_date, to_date,
		active_ports, voucher_types, progress_cb=_progress,
	)

	# Phase 3: final meta + ack.
	conn = open_sqlite(out_path)
	try:
		write_meta(conn, "voucher_summary", parallel_summary)
		write_meta(conn, "finished_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
		conn.commit()
	finally:
		conn.close()

	summary = {
		"run_id": run_id,
		"company_id": company_id,
		"company_name": company_name,
		"from_date": from_date,
		"to_date": to_date,
		"sqlite_path": str(out_path),
		"sqlite_size": out_path.stat().st_size,
		"master_counts": master_counts,
		"voucher_types_count": len(voucher_types),
		"voucher_summary": parallel_summary,
		"active_ports": active_ports,
	}
	log(f"HTTP extract finished: {parallel_summary.get('ok_count', 0)} ok, "
	    f"{parallel_summary.get('fail_count', 0)} fail, "
	    f"{parallel_summary.get('voucher_count', 0):,} vouchers")
	ack_bridge_command(
		config, connection_id, bridge_token, command_id, "Success",
		{"summary": summary, "kind": HTTP_KIND, "sqlite_path": str(out_path)},
	)

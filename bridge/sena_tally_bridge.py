#!/usr/bin/env python3
"""Standalone Phase 1 bridge for a Tally source connection.

This script is intentionally dependency-free so the first Windows bridge can be
packaged with PyInstaller or run with a stock Python install. It performs the
minimum production path:

1. Claim a user-created pairing code from Sena.
2. Probe the local Tally HTTP/XML server.
3. Push heartbeat metadata.
4. Export a few master-data collections and ingest them into the Integration
   source store with durable checkpoints.
"""

from __future__ import annotations

import argparse
import http.client
from html import escape
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable


__version__ = "0.1.0"

# PyInstaller's `--windowed` / `console=False` exe runs without an attached
# console: `sys.stdout` exists but every write to it raises
# `OSError [Errno 22] Invalid argument`, which crashes the bridge on the
# first print() before any logging gets surfaced. Redirect to NUL up front
# so prints become silent no-ops; main() will swap to the real log file
# once it's parsed --log-file. Only do this when frozen — running the
# script under a real Python keeps the normal terminal output.
if getattr(sys, "frozen", False):
	sys.stdout = open(os.devnull, "w", encoding="utf-8")
	sys.stderr = open(os.devnull, "w", encoding="utf-8")

DEFAULT_METHOD_PREFIX = "migration.core.api"
INVALID_XML_CHARS = re.compile(r"&#(?:[0-8]|1[0-1]|1[4-9]|2[0-9]|3[01]);")
MINIMAL_COLLECTION_FIELDS = ["Name", "Guid", "Parent", "AlterId", "MasterId"]
BRIDGE_CAPABILITIES = {
	"bridge_version": __version__,
	"protocols": ["tally_http_xml", "tally_1800_decode"],
	"streams": [],
}
DECODER_ROOT = Path(__file__).resolve().parents[1] / "tally_1800" / "codex"
if DECODER_ROOT.exists() and str(DECODER_ROOT) not in sys.path:
	sys.path.insert(0, str(DECODER_ROOT))

TALLY_STREAMS = [
	{
		"stream_name": "tally_company_metadata",
		"kind": "collection",
		"object_type": "Company",
		"collection_name": "CompanyInfo",
		"tally_type": "Company",
		"tag_name": "COMPANY",
		"fields": [
			"Name", "Guid", "Parent", "BasicCurrencyCode", "FormalName", "Country",
			"StateName", "PINCode", "Phone", "Email", "GSTN", "IncomeTaxNumber",
			"MaintainedAccounts", "MaintainInventory",
		],
	},
	{
		"stream_name": "tally_groups",
		"kind": "collection",
		"object_type": "Group",
		"collection_name": "Groups",
		"tally_type": "Group",
		"tag_name": "GROUP",
		"fields": ["Name", "Guid", "Parent", "AlterId", "IsRevenue", "IsDeemedPositive"],
	},
	{
		"stream_name": "tally_ledgers",
		"kind": "collection",
		"object_type": "Ledger",
		"collection_name": "Ledgers",
		"default_collection_name": "List of Ledgers",
		"tally_type": "Ledger",
		"tag_name": "LEDGER",
		"fields": [
			"Name", "Guid", "Parent", "AlterId", "MasterId", "OpeningBalance",
			"ClosingBalance", "IsBillWiseOn", "IsCostCentresOn", "GstRegistrationType",
			"PartyGstin", "MailingName", "Address", "StateName", "CountryName",
		],
	},
	{
		"stream_name": "tally_voucher_types",
		"kind": "collection",
		"object_type": "Voucher Type",
		"collection_name": "VoucherTypes",
		"default_collection_name": "List of Voucher Types",
		"tally_type": "Voucher Type",
		"tag_name": "VOUCHERTYPE",
		"fields": ["Name", "Guid", "Parent", "AlterId", "NumberingMethod", "UseForJobWork"],
	},
	{
		"stream_name": "tally_vouchers",
		"kind": "collection",
		"object_type": "Voucher",
		"collection_name": "Vouchers",
		"tally_type": "Voucher",
		"tag_name": "VOUCHER",
		"fields": [
			"Date", "VoucherTypeName", "VoucherNumber", "Guid", "AlterId", "MasterId",
			"PartyLedgerName", "PartyName", "Narration", "EffectiveDate", "IsInvoice",
			"IsCancelled", "IsDeleted", "AllLedgerEntries",
		],
		"requires_company": True,
	},
	{
		"stream_name": "tally_stock_groups",
		"kind": "collection",
		"object_type": "Stock Group",
		"collection_name": "StockGroups",
		"default_collection_name": "List of Stock Groups",
		"tally_type": "Stock Group",
		"tag_name": "STOCKGROUP",
		"fields": ["Name", "Guid", "Parent", "AlterId"],
	},
	{
		"stream_name": "tally_stock_categories",
		"kind": "collection",
		"object_type": "Stock Category",
		"collection_name": "StockCategories",
		"tally_type": "Stock Category",
		"tag_name": "STOCKCATEGORY",
		"fields": ["Name", "Guid", "Parent", "AlterId"],
	},
	{
		"stream_name": "tally_stock_items",
		"kind": "collection",
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
		"stream_name": "tally_units",
		"kind": "collection",
		"object_type": "Unit",
		"collection_name": "Units",
		"tally_type": "Unit",
		"tag_name": "UNIT",
		"fields": ["Name", "Guid", "OriginalName", "DecimalPlaces", "AlterId"],
	},
	{
		"stream_name": "tally_godowns",
		"kind": "collection",
		"object_type": "Godown",
		"collection_name": "Godowns",
		"tally_type": "Godown",
		"tag_name": "GODOWN",
		"fields": ["Name", "Guid", "Parent", "AlterId"],
	},
	{
		"stream_name": "tally_cost_categories",
		"kind": "collection",
		"object_type": "Cost Category",
		"collection_name": "CostCategories",
		"tally_type": "Cost Category",
		"tag_name": "COSTCATEGORY",
		"fields": ["Name", "Guid", "AlterId", "AllocateRevenue", "AllocateNonRevenue"],
	},
	{
		"stream_name": "tally_cost_centres",
		"kind": "collection",
		"object_type": "Cost Centre",
		"collection_name": "CostCentres",
		"default_collection_name": "List of Cost Centres",
		"tally_type": "Cost Centre",
		"tag_name": "COSTCENTRE",
		"fields": ["Name", "Guid", "Parent", "AlterId", "Category"],
	},
	{
		"stream_name": "tally_currencies",
		"kind": "collection",
		"object_type": "Currency",
		"collection_name": "Currencies",
		"tally_type": "Currency",
		"tag_name": "CURRENCY",
		"fields": ["Name", "Guid", "OriginalName", "FormalName", "AlterId", "DecimalPlaces"],
	},
	{
		"stream_name": "tally_trial_balance",
		"kind": "report_rows",
		"report_name": "Trial Balance",
		"object_type": "Trial Balance Entry",
		"parser": "parse_trial_balance_rows",
		"row_name_fields": ["account"],
		"row_id_fields": ["account"],
		"explode": True,
	},
	{
		"stream_name": "tally_stock_summary",
		"kind": "report_rows",
		"report_name": "Stock Summary",
		"object_type": "Stock Summary Item",
		"parser": "parse_stock_summary_rows",
		"row_name_fields": ["item"],
		"row_id_fields": ["item"],
	},
	{
		"stream_name": "tally_outstanding",
		"kind": "report_snapshot",
		"report_name": "Ledger Outstandings",
		"object_type": "Outstanding Snapshot",
		"requires_company": True,
	},
	{
		"stream_name": "tally_day_book",
		"kind": "report_rows",
		"report_name": "Day Book",
		"object_type": "Day Book Voucher",
		"parser": "parse_day_book_rows",
		"row_name_fields": ["number", "party", "vch_type"],
		"row_id_fields": ["guid", "number", "party", "date"],
		"requires_company": True,
	},
]
BRIDGE_CAPABILITIES["streams"] = ["Company", *[stream["object_type"] for stream in TALLY_STREAMS]]

DECODE_TABLES = [
	("companies", "Tally 1800 Company Profile", ("company_id", "name", "company_guid")),
	("groups", "Tally 1800 Group", ("master_id", "name", "guid")),
	("ledgers", "Tally 1800 Ledger", ("master_id", "name", "guid")),
	("stock_items", "Tally 1800 Stock Item", ("master_id", "name", "guid")),
	("units", "Tally 1800 Unit", ("master_id", "name", "guid")),
	("godowns", "Tally 1800 Godown", ("master_id", "name", "guid")),
	("cost_centres", "Tally 1800 Cost Centre", ("master_id", "name", "guid")),
	("voucher_types", "Tally 1800 Voucher Type", ("master_id", "name", "guid")),
	("production_voucher_headers_1800", "Tally 1800 Voucher Header", ("voucher_master_id", "voucher_number_guess", "voucher_date_guess")),
	("production_ledger_entries_1800", "Tally 1800 Ledger Entry", ("voucher_master_id", "entry_id", "ledger_master_id")),
	("production_inventory_entries_1800", "Tally 1800 Inventory Entry", ("voucher_master_id", "entry_id", "stock_item_master_id")),
	("production_xml_repair_queue_1800", "Tally 1800 XML Repair Queue", ("voucher_master_id", "voucher_type_name")),
	("production_review_queue_1800", "Tally 1800 Review Queue", ("voucher_master_id", "voucher_type_name")),
]

DISCOVERY_OBJECT_TYPE = "Tally 1800 Company"
SUMMARY_OBJECT_TYPE = "Tally 1800 Decode Summary"


@dataclass
class BridgeConfig:
	server: str
	pairing_code: str
	tally_host: str = "localhost"
	tally_port: int = 9000
	tally_data_dir: str = ""
	bridge_id: str = ""
	connection_id: str = ""
	bridge_token: str = ""
	config_path: str = ""
	timeout: int = 120
	once: bool = False

	@property
	def tally_url(self) -> str:
		return f"http://{self.tally_host}:{self.tally_port}"


def log(message: str) -> None:
	timestamp = time.strftime("%H:%M:%S")
	print(f"[{timestamp}] {message}", flush=True)


def clean_xml(text: str) -> str:
	text = INVALID_XML_CHARS.sub("", text)
	return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def hash_text(value: str) -> str:
	return sha256(value.encode("utf-8")).hexdigest()


def post_json(server: str, method: str, payload: dict, timeout: int = 120) -> dict:
	url = f"{server.rstrip('/')}/api/method/{DEFAULT_METHOD_PREFIX}.{method}"
	body = json.dumps(payload).encode("utf-8")
	req = urllib.request.Request(
		url,
		data=body,
		headers={"Content-Type": "application/json"},
		method="POST",
	)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			data = json.loads(resp.read().decode("utf-8"))
	except urllib.error.HTTPError as exc:
		detail = exc.read().decode("utf-8", errors="replace")
		raise RuntimeError(f"{method} failed with HTTP {exc.code}: {detail}") from exc
	except urllib.error.URLError as exc:
		raise RuntimeError(f"{method} failed: {exc}") from exc

	if data.get("exc"):
		raise RuntimeError(f"{method} failed: {data['exc']}")
	return data.get("message", data)


def build_collection_xml(
	name: str,
	object_type: str | None,
	fields: list[str] | None = None,
	static_variables: dict[str, str] | None = None,
	is_modify: bool = False,
	child_of: str = "",
) -> str:
	methods = "".join(f"<NATIVEMETHOD>{field}</NATIVEMETHOD>" for field in fields or [])
	collection_attrs = f'NAME="{escape(name)}"'
	if is_modify:
		collection_attrs += ' ISMODIFY="Yes"'
	type_xml = f"<TYPE>{escape(object_type)}</TYPE>" if object_type else ""
	child_xml = f"<ADD>CHILD OF : {escape(child_of)}</ADD>" if child_of else ""
	static_xml = "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
	for key, value in (static_variables or {}).items():
		static_xml += f"<{key}>{escape(str(value))}</{key}>"
	return (
		"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>"
		f"<TYPE>COLLECTION</TYPE><ID>{name}</ID></HEADER><BODY><DESC>"
		f"<STATICVARIABLES>{static_xml}</STATICVARIABLES>"
		"<TDL><TDLMESSAGE>"
		f"<COLLECTION {collection_attrs} ISINITIALIZE=\"Yes\">"
		f"{type_xml}{child_xml}{methods}</COLLECTION>"
		"</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"
	)


def post_tally_xml(config: BridgeConfig, xml_payload: str) -> str:
	req = urllib.request.Request(
		config.tally_url,
		data=xml_payload.encode("utf-8"),
		headers={"Content-Type": "application/xml"},
		method="POST",
	)
	try:
		with urllib.request.urlopen(req, timeout=config.timeout) as resp:
			return clean_xml(resp.read().decode("utf-8", errors="replace"))
	except http.client.RemoteDisconnected as exc:
		raise RuntimeError("Tally closed the HTTP connection without sending a response") from exc
	except http.client.IncompleteRead as exc:
		return clean_xml(exc.partial.decode("utf-8", errors="replace"))
	except urllib.error.URLError as exc:
		raise RuntimeError(f"Could not read from Tally: {exc}") from exc


def post_tally_xml_with_retries(config: BridgeConfig, xml_payload: str, attempts: int = 2) -> str:
	last_error = None
	for attempt in range(1, attempts + 1):
		try:
			return post_tally_xml(config, xml_payload)
		except RuntimeError as exc:
			last_error = exc
			if attempt >= attempts:
				break
			log(f"Tally did not respond cleanly; retrying in {attempt + 1}s ({exc})")
			time.sleep(attempt + 1)
	raise last_error


def fetch_collection_xml(
	config: BridgeConfig,
	stream: dict,
	static_variables: dict[str, str] | None = None,
) -> str:
	minimal_fields = [field for field in MINIMAL_COLLECTION_FIELDS if field in set(stream.get("fields") or MINIMAL_COLLECTION_FIELDS)]
	attempts = [
		{
			"label": "configured custom collection",
			"name": stream["collection_name"],
			"object_type": stream["tally_type"],
			"fields": stream.get("fields") or [],
			"is_modify": False,
		},
		{
			"label": "minimal custom collection",
			"name": stream["collection_name"],
			"object_type": stream["tally_type"],
			"fields": minimal_fields,
			"is_modify": False,
		},
		{
			"label": "bare custom collection",
			"name": stream["collection_name"],
			"object_type": stream["tally_type"],
			"fields": [],
			"is_modify": False,
		},
	]
	if stream.get("default_collection_name"):
		attempts.extend([
			{
				"label": "configured Tally default collection",
				"name": stream["default_collection_name"],
				"object_type": None,
				"fields": stream.get("fields") or [],
				"is_modify": True,
			},
			{
				"label": "minimal Tally default collection",
				"name": stream["default_collection_name"],
				"object_type": None,
				"fields": minimal_fields,
				"is_modify": True,
			},
			{
				"label": "bare Tally default collection",
				"name": stream["default_collection_name"],
				"object_type": None,
				"fields": [],
				"is_modify": True,
			},
		])
	last_error = None
	for attempt in attempts:
		try:
			log(f"Requesting {stream['object_type']} using {attempt['label']}...")
			return post_tally_xml_with_retries(
				config,
				build_collection_xml(
					attempt["name"],
					attempt["object_type"],
					attempt["fields"],
					static_variables,
					is_modify=attempt["is_modify"],
				),
			)
		except Exception as exc:
			last_error = exc
			log(f"{stream['object_type']} {attempt['label']} failed: {exc}")
			time.sleep(2)
	raise last_error


def element_text(element: ET.Element, name: str) -> str:
	found = element.find(f".//{name}")
	if found is not None and found.text:
		return found.text.strip()
	return ""


def object_name(element: ET.Element) -> str:
	return element.attrib.get("NAME", "").strip() or element_text(element, "NAME")


def child_texts(element: ET.Element) -> dict:
	values = {}
	for child in list(element):
		if len(list(child)):
			continue
		text = (child.text or "").strip()
		if text:
			values[child.tag.lower()] = text
	return values


def first_nonempty(*values) -> str:
	for value in values:
		if value is None:
			continue
		text = str(value).strip()
		if text:
			return text
	return ""


def compact_json(value: dict) -> str:
	return json.dumps(value, sort_keys=True, separators=(",", ":"))


def save_config_value(config_path: str, key: str, value: str) -> None:
	if not config_path:
		return
	path = Path(config_path)
	try:
		data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
	except Exception:
		data = {}
	data[key] = value
	path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def discover_tally_data_roots(config: BridgeConfig) -> list[Path]:
	candidates = []
	configured = getattr(config, "tally_data_dir", "") or ""
	if configured:
		candidates.append(Path(configured))
	for env_name in ("TALLY_DATA_DIR", "TALLYPRIME_DATA"):
		if os.environ.get(env_name):
			candidates.append(Path(os.environ[env_name]))
	local = os.environ.get("LOCALAPPDATA")
	roaming = os.environ.get("APPDATA")
	userprofile = os.environ.get("USERPROFILE")
	if local:
		candidates.extend([
			Path(local) / "TallyPrime" / "Data",
			Path(local) / "Tally" / "Data",
		])
	if roaming:
		candidates.extend([
			Path(roaming) / "TallyPrime" / "Data",
			Path(roaming) / "Tally" / "Data",
		])
	if userprofile:
		candidates.extend([
			Path(userprofile) / "Documents" / "TallyPrime" / "Data",
			Path(userprofile) / "Documents" / "Tally" / "Data",
		])

	seen = set()
	roots = []
	for candidate in candidates:
		try:
			resolved = candidate.expanduser().resolve()
		except Exception:
			continue
		if resolved in seen or not resolved.exists() or not resolved.is_dir():
			continue
		seen.add(resolved)
		roots.append(resolved)
	return roots


def quick_company_name(company_dir: Path) -> str:
	try:
		from tally1800.probe import extract_tagged_field_strings
	except Exception:
		return company_dir.name
	company_file = company_dir / "Company.1800"
	if not company_file.exists():
		return company_dir.name
	try:
		hits = extract_tagged_field_strings(company_file, min_chars=1)
	except Exception:
		return company_dir.name
	for hit in hits:
		text = (hit.text or "").strip()
		if text:
			return text
	return company_dir.name


def discover_1800_companies(config: BridgeConfig) -> list[dict]:
	companies = []
	for root in discover_tally_data_roots(config):
		for child in sorted(root.iterdir()):
			if not child.is_dir():
				continue
			files = list(child.glob("*.1800"))
			if not files:
				continue
			size = sum(path.stat().st_size for path in files if path.exists())
			companies.append({
				"company_id": child.name,
				"company_name": quick_company_name(child),
				"folder": str(child),
				"root": str(root),
				"file_count": len(files),
				"total_bytes": size,
				"has_company": (child / "Company.1800").exists(),
				"has_manager": (child / "Manager.1800").exists(),
				"has_tranmgr": (child / "TranMgr.1800").exists(),
			})
	return companies


def discovery_objects(companies: list[dict]) -> list[dict]:
	objects = []
	for company in companies:
		source_id = company.get("company_id") or sha256(compact_json(company).encode("utf-8")).hexdigest()[:16]
		objects.append(build_ingest_object(
			DISCOVERY_OBJECT_TYPE,
			source_id,
			company.get("company_name") or source_id,
			compact_json(company),
			{"source_type": "Tally", "object_type": DISCOVERY_OBJECT_TYPE, **company},
			payload_format="json",
		))
	return objects


def sqlite_row_dicts(db_path: Path, table: str) -> list[dict]:
	with sqlite3.connect(db_path) as conn:
		conn.row_factory = sqlite3.Row
		try:
			rows = conn.execute(f"select * from {table}").fetchall()
		except sqlite3.Error:
			return []
		return [dict(row) for row in rows]


def decoded_table_counts(db_path: Path) -> dict:
	counts = {}
	with sqlite3.connect(db_path) as conn:
		for table, _, _ in DECODE_TABLES:
			try:
				counts[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
			except sqlite3.Error:
				counts[table] = 0
	return counts


def decoded_row_source_id(table: str, row: dict, keys: tuple[str, ...], index: int) -> str:
	parts = [str(row.get(key) or "").strip() for key in keys]
	parts = [part for part in parts if part]
	if parts:
		return f"{table}:{':'.join(parts)}"
	return f"{table}:{index}"


def decoded_rows_to_objects(company: dict, db_path: Path, summary: dict) -> Iterable[list[dict]]:
	yield [build_ingest_object(
		SUMMARY_OBJECT_TYPE,
		f"{company['company_id']}:{summary['decode_id']}",
		company.get("company_name") or company["company_id"],
		compact_json(summary),
		{"source_type": "Tally", "object_type": SUMMARY_OBJECT_TYPE, **summary},
		payload_format="json",
	)]
	for table, object_type, keys in DECODE_TABLES:
		batch = []
		for index, row in enumerate(sqlite_row_dicts(db_path, table), 1):
			normalized = {
				"source_type": "Tally",
				"object_type": object_type,
				"company_id": company["company_id"],
				"company_name": company.get("company_name"),
				"decoder_table": table,
				"decoder_version": "tally1800-local",
				"record": row,
			}
			source_id = decoded_row_source_id(table, row, keys, index)
			name = row.get("name") or row.get("voucher_number_guess") or row.get("voucher_type_name") or source_id
			batch.append(build_ingest_object(
				object_type,
				source_id,
				str(name),
				compact_json(row),
				normalized,
				payload_format="json",
			))
			if len(batch) >= 200:
				yield batch
				batch = []
		if batch:
			yield batch


def build_report_xml(
	report_name: str,
	static_variables: dict[str, str] | None = None,
	explode: bool = False,
) -> str:
	static_xml = "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
	if explode:
		static_xml += "<EXPLODEFLAG>Yes</EXPLODEFLAG>"
	for key, value in (static_variables or {}).items():
		static_xml += f"<{key}>{escape(str(value))}</{key}>"
	return (
		"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>"
		f"<TYPE>Data</TYPE><ID>{escape(report_name)}</ID></HEADER><BODY><DESC>"
		f"<STATICVARIABLES>{static_xml}</STATICVARIABLES>"
		"</DESC></BODY></ENVELOPE>"
	)


def parse_trial_balance_rows(xml_string: str | bytes) -> list[dict]:
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8", errors="replace")
	xml_string = clean_xml(xml_string)
	root = ET.fromstring(xml_string)

	entries = []
	current_name = None
	for elem in root:
		if elem.tag == "DSPACCNAME":
			current_name = element_text(elem, "DSPDISPNAME")
		elif elem.tag == "DSPACCINFO" and current_name:
			dr_el = elem.find("DSPCLDRAMT")
			cr_el = elem.find("DSPCLCRAMT")
			debit = abs(_float_from_element(dr_el, "DSPCLDRAMTA")) if dr_el is not None else 0.0
			credit = _float_from_element(cr_el, "DSPCLCRAMTA") if cr_el is not None else 0.0
			entries.append({"account": current_name, "debit": debit, "credit": credit})
			current_name = None
	return entries


def parse_stock_summary_rows(xml_string: str | bytes) -> list[dict]:
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8", errors="replace")
	xml_string = clean_xml(xml_string)
	root = ET.fromstring(xml_string)

	items = []
	current_name = None
	for elem in root:
		if elem.tag == "DSPACCNAME":
			current_name = element_text(elem, "DSPDISPNAME")
		elif elem.tag == "DSPSTKINFO" and current_name:
			stkcl = elem.find("DSPSTKCL")
			if stkcl is not None:
				qty_raw = element_text(stkcl, "DSPCLQTY") or "0"
				qty, uom = parse_qty_uom(qty_raw)
				rate = _float_from_element(stkcl, "DSPCLRATE")
				value = abs(_float_from_element(stkcl, "DSPCLAMTA"))
				items.append({"item": current_name, "qty": qty, "uom": uom, "rate": rate, "value": value})
			current_name = None
	return items


def parse_day_book_rows(xml_string: str | bytes) -> list[dict]:
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8", errors="replace")
	xml_string = clean_xml(xml_string)
	root = ET.fromstring(xml_string)
	return [parse_day_book_voucher(voucher) for voucher in root.iter("VOUCHER")]


def parse_qty_uom(raw: str) -> tuple[float, str]:
	if not raw or not raw.strip():
		return 0.0, ""
	parts = raw.strip().split()
	try:
		qty = float(parts[0])
	except (ValueError, IndexError):
		return 0.0, raw.strip()
	uom = " ".join(parts[1:]) if len(parts) > 1 else ""
	return qty, uom


def parse_rate_uom(raw: str) -> tuple[float, str]:
	if not raw or not raw.strip():
		return 0.0, ""
	if "/" in raw:
		parts = raw.strip().split("/", 1)
		try:
			return float(parts[0]), parts[1].strip()
		except (ValueError, IndexError):
			pass
	try:
		return float(raw.strip()), ""
	except ValueError:
		return 0.0, ""


def _float_from_element(element: ET.Element | None, tag: str, default: float = 0.0) -> float:
	if element is None:
		return default
	for child in element.iter(tag):
		if child.text:
			try:
				return float(child.text.strip())
			except ValueError:
				return default
	return default


def parse_day_book_voucher(element: ET.Element) -> dict:
	raw_date = element_text(element, "DATE")
	date = ""
	if raw_date and len(raw_date) == 8:
		date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"

	voucher = {
		"vch_type": element.attrib.get("VCHTYPE", "") or element_text(element, "VOUCHERTYPENAME"),
		"date": date,
		"number": element_text(element, "VOUCHERNUMBER"),
		"party": element_text(element, "PARTYLEDGERNAME") or element_text(element, "PARTYNAME"),
		"narration": element_text(element, "NARRATION"),
		"party_gstin": element_text(element, "PARTYGSTIN"),
		"place_of_supply": element_text(element, "PLACEOFSUPPLY"),
		"is_cancelled": element_text(element, "ISCANCELLED").lower() in ("yes", "true", "1"),
		"is_optional": element_text(element, "ISOPTIONAL").lower() in ("yes", "true", "1"),
		"guid": element_text(element, "GUID"),
		"inventory_entries": [],
		"ledger_entries": [],
	}

	for inv_el in element.findall("ALLINVENTORYENTRIES.LIST"):
		entry = parse_day_book_inventory_entry(inv_el)
		if entry:
			voucher["inventory_entries"].append(entry)

	for led_el in element.findall("LEDGERENTRIES.LIST"):
		entry = parse_day_book_ledger_entry(led_el)
		if entry:
			voucher["ledger_entries"].append(entry)

	for led_el in element.findall("ALLLEDGERENTRIES.LIST"):
		entry = parse_day_book_ledger_entry(led_el)
		if entry:
			voucher["ledger_entries"].append(entry)

	return voucher


def parse_day_book_inventory_entry(element: ET.Element) -> dict | None:
	item_name = element_text(element, "STOCKITEMNAME")
	if not item_name:
		return None

	qty, uom = parse_qty_uom(element_text(element, "ACTUALQTY") or element_text(element, "BILLEDQTY") or "0")
	rate, _ = parse_rate_uom(element_text(element, "RATE") or "0")
	amount = _float_from_element(element, "AMOUNT")

	ledger = ""
	ledger_amount = 0.0
	acct_el = element.find("ACCOUNTINGALLOCATIONS.LIST")
	if acct_el is not None:
		ledger = element_text(acct_el, "LEDGERNAME")
		ledger_amount = _float_from_element(acct_el, "AMOUNT")

	gst_rates = {}
	for rate_el in element.findall("RATEDETAILS.LIST"):
		duty_head = element_text(rate_el, "GSTRATEDUTYHEAD")
		gst_rate = _float_from_element(rate_el, "GSTRATE")
		if duty_head and gst_rate:
			gst_rates[duty_head] = gst_rate

	return {
		"item": item_name,
		"qty": qty,
		"uom": uom,
		"rate": rate,
		"amount": amount,
		"hsn": element_text(element, "GSTHSNNAME"),
		"ledger": ledger,
		"ledger_amount": ledger_amount,
		"is_deemed_positive": element_text(element, "ISDEEMEDPOSITIVE").lower() in ("yes", "true", "1"),
		"gst_rates": gst_rates,
	}


def parse_day_book_ledger_entry(element: ET.Element) -> dict | None:
	ledger = element_text(element, "LEDGERNAME")
	if not ledger:
		return None

	bill_allocations = []
	for bill_el in element.findall("BILLALLOCATIONS.LIST"):
		name = element_text(bill_el, "NAME")
		if not name:
			continue
		bill_allocations.append(
			{
				"name": name,
				"type": element_text(bill_el, "BILLTYPE"),
				"amount": _float_from_element(bill_el, "AMOUNT"),
			}
		)

	bank_allocations = []
	for bank_el in element.findall("BANKALLOCATIONS.LIST"):
		bank_party = element_text(bank_el, "BANKPARTYNAME") or element_text(bank_el, "PAYMENTFAVOURING")
		if not bank_party:
			continue
		bank_allocations.append(
			{
				"party": bank_party,
				"transaction_type": element_text(bank_el, "TRANSACTIONTYPE"),
				"instrument_number": element_text(bank_el, "INSTRUMENTNUMBER"),
				"amount": _float_from_element(bank_el, "AMOUNT"),
			}
		)

	tax_rate = 0.0
	rate_el = element.find("RATEOFINVOICETAX.LIST")
	if rate_el is not None:
		for child in rate_el:
			if child.text:
				try:
					tax_rate = float(child.text.strip())
				except ValueError:
					pass

	return {
		"ledger": ledger,
		"amount": _float_from_element(element, "AMOUNT"),
		"is_deemed_positive": element_text(element, "ISDEEMEDPOSITIVE").lower() in ("yes", "true", "1"),
		"is_party_ledger": element_text(element, "ISPARTYLEDGER").lower() in ("yes", "true", "1"),
		"bill_allocations": bill_allocations,
		"bank_allocations": bank_allocations,
		"tax_rate": tax_rate,
	}


def build_ingest_object(
	object_type: str,
	source_id: str,
	source_name: str,
	raw_payload: str | None,
	normalized_json: dict,
	source_guid: str = "",
	source_alter_id: str = "",
	payload_format: str = "xml",
) -> dict:
	raw_payload = raw_payload or ""
	return {
		"object_type": object_type,
		"source_id": source_id,
		"source_guid": source_guid,
		"source_name": source_name,
		"source_alter_id": source_alter_id,
		"sync_status": "Seen",
		"raw_hash": hash_text(raw_payload),
		"raw_payload": raw_payload,
		"payload_format": payload_format,
		"normalized_json": normalized_json,
	}


def row_source_name(stream: dict, row: dict, index: int) -> str:
	fields = stream.get("row_name_fields") or []
	for field in fields:
		value = row.get(field)
		if value not in (None, ""):
			return str(value).strip()
	return f'{stream.get("report_name", stream["stream_name"])} row {index}'


def row_source_id(stream: dict, row: dict, index: int) -> str:
	fields = stream.get("row_id_fields") or []
	for field in fields:
		value = row.get(field)
		if value not in (None, ""):
			return f'{stream["stream_name"]}:{str(value).strip()}'
	return f'{stream["stream_name"]}:{hash_text(compact_json(row))[:16]}:{index}'


def row_cursor(objects: list[dict]) -> str:
	return str(len(objects)) if objects else ""


def report_rows_to_objects(stream: dict, rows: list[dict], company_name: str = "") -> list[dict]:
	objects = []
	for index, row in enumerate(rows, 1):
		raw_payload = compact_json(row)
		normalized = {
			"source_type": "Tally",
			"stream_name": stream["stream_name"],
			"report_name": stream.get("report_name", stream["stream_name"]),
			"company_name": company_name,
			"row_index": index,
			**row,
		}
		objects.append(
			build_ingest_object(
				stream["object_type"],
				row_source_id(stream, row, index),
				row_source_name(stream, row, index),
				raw_payload,
				normalized,
				source_alter_id=str(index),
				payload_format="json",
			)
		)
	return objects


def report_snapshot_to_object(stream: dict, report_name: str, xml_text: str, company_name: str = "") -> list[dict]:
	if not xml_text or not xml_text.strip():
		return []
	normalized = {
		"source_type": "Tally",
		"stream_name": stream["stream_name"],
		"report_name": report_name,
		"company_name": company_name,
	}
	source_id = f'{stream["stream_name"]}:{company_name or "active"}:{report_name}'
	return [
		build_ingest_object(
			stream["object_type"],
			source_id,
			report_name,
			xml_text,
			normalized,
			source_alter_id="",
			payload_format="xml",
		)
	]


def parse_tally_objects(xml_text: str, object_type: str, tag_name: str) -> list[dict]:
	root = ET.fromstring(clean_xml(xml_text))
	items = []
	for element in root.iter(tag_name):
		name = object_name(element)
		guid = element_text(element, "GUID")
		alter_id = element_text(element, "ALTERID") or element_text(element, "MASTERID")
		if not name and not guid:
			continue
		source_id = guid or f"{object_type}:{name}"
		raw_payload = ET.tostring(element, encoding="unicode")
		normalized = {
			"source_type": "Tally",
			"object_type": object_type,
			"source_id": source_id,
			"source_guid": guid,
			"name": name,
			"alter_id": alter_id,
			**child_texts(element),
		}
		items.append(
			build_ingest_object(
				object_type,
				source_id,
				name,
				raw_payload,
				normalized,
				source_guid=guid,
				source_alter_id=alter_id,
			)
		)
	return items


def max_alter_id(items: Iterable[dict]) -> str:
	values = []
	for item in items:
		try:
			values.append(int(item.get("source_alter_id") or 0))
		except ValueError:
			pass
	return str(max(values)) if values else ""


def claim_pairing(config: BridgeConfig) -> dict:
	if config.connection_id and config.bridge_token:
		return {"connection_id": config.connection_id, "bridge_token": config.bridge_token, "status": "Active"}
	return post_json(
		config.server,
		"claim_integration_bridge_pairing",
		{
			"pairing_code": config.pairing_code,
			"bridge_id": config.bridge_id or f"sena-tally-{uuid.uuid4().hex[:10]}",
			"host": config.tally_host,
			"port": config.tally_port,
			"capabilities_json": json.dumps(BRIDGE_CAPABILITIES),
		},
		config.timeout,
	)


def heartbeat(config: BridgeConfig, connection_id: str, bridge_token: str, **updates) -> dict:
	payload = {
		"connection_id": connection_id,
		"bridge_token": bridge_token,
		"status": updates.pop("status", "Active"),
		**updates,
	}
	return post_json(config.server, "bridge_heartbeat", payload, config.timeout)


def update_checkpoint(
	config: BridgeConfig,
	connection_id: str,
	bridge_token: str,
	stream_name: str,
	object_type: str,
	status: str,
	cursor: str = "",
	error: str = "",
) -> dict:
	return post_json(
		config.server,
		"upsert_sync_checkpoint",
		{
			"connection_id": connection_id,
			"bridge_token": bridge_token,
			"stream_name": stream_name,
			"object_type": object_type,
			"status": status,
			"cursor": cursor,
			"error": error,
		},
		config.timeout,
	)


def ingest_objects(config: BridgeConfig, connection_id: str, bridge_token: str, objects: list[dict]) -> dict:
	return post_json(
		config.server,
		"ingest_source_objects",
		{
			"connection_id": connection_id,
			"bridge_token": bridge_token,
			"objects_json": json.dumps(objects),
		},
		config.timeout,
	)


def ack_bridge_command(config: BridgeConfig, connection_id: str, bridge_token: str, command_id: str, status: str, result: dict | None = None, error: str = "") -> dict:
	return post_json(
		config.server,
		"ack_bridge_command",
		{
			"connection_id": connection_id,
			"bridge_token": bridge_token,
			"command_id": command_id,
			"status": status,
			"result_json": json.dumps(result or {}),
			"error": error,
		},
		config.timeout,
	)


def handle_bridge_command(config: BridgeConfig, connection_id: str, bridge_token: str, command: dict | None) -> None:
	if not command:
		return
	if isinstance(command, str):
		try:
			command = json.loads(command)
		except json.JSONDecodeError:
			return
	command_id = command.get("id") or ""
	command_type = command.get("type") or ""
	try:
		if command_type == "discover_1800_companies":
			log("Discovering local Tally .1800 companies...")
			companies = discover_1800_companies(config)
			if companies:
				ingest_objects(config, connection_id, bridge_token, discovery_objects(companies))
			ack_bridge_command(config, connection_id, bridge_token, command_id, "Success", {"companies": len(companies)})
			log(f"Discovered {len(companies)} local Tally companies.")
			return
		if command_type == "extract_1800_company":
			company_id = str(command.get("company_id") or "").strip()
			run_1800_extract(config, connection_id, bridge_token, command_id, company_id)
			return
		ack_bridge_command(config, connection_id, bridge_token, command_id, "Error", error=f"Unknown command type: {command_type}")
	except Exception as exc:
		log(f"Command {command_type or command_id} failed: {exc}")
		if command_id:
			try:
				ack_bridge_command(config, connection_id, bridge_token, command_id, "Error", error=str(exc))
			except Exception:
				pass


def run_1800_extract(config: BridgeConfig, connection_id: str, bridge_token: str, command_id: str, company_id: str) -> None:
	try:
		from tally1800.decoded_export import export_decoded_sqlite
	except Exception as exc:
		raise RuntimeError(f"tally1800 decoder is not available in this bridge build: {exc}") from exc
	companies = discover_1800_companies(config)
	company = next((item for item in companies if str(item.get("company_id")) == company_id), None)
	if not company:
		raise RuntimeError(f"Company folder {company_id!r} was not found")
	source_dir = Path(company["folder"])
	log(f"Running .1800 decode for {company.get('company_name') or company_id}...")
	heartbeat(config, connection_id, bridge_token, status="Syncing", last_error=f"1800 decode started for {company_id}")
	with TemporaryDirectory(prefix="sena-tally-1800-") as tmp:
		out_path = Path(tmp) / f"{company_id}_decoded.sqlite3"
		export_decoded_sqlite(source_dir, out_path)
		counts = decoded_table_counts(out_path)
		summary = {
			"decode_id": uuid.uuid4().hex[:12],
			"company_id": company_id,
			"company_name": company.get("company_name"),
			"folder": company.get("folder"),
			"counts": counts,
			"sqlite_size": out_path.stat().st_size,
			"decoded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
		}
		total = 0
		for batch in decoded_rows_to_objects(company, out_path, summary):
			ingest_objects(config, connection_id, bridge_token, batch)
			total += len(batch)
			if total % 2000 == 0:
				log(f"Uploaded {total} decoded rows...")
		ack_bridge_command(config, connection_id, bridge_token, command_id, "Success", {"summary": summary, "uploaded_objects": total})
	heartbeat(config, connection_id, bridge_token, status="Active", last_error="")
	log(f".1800 decode uploaded {total} objects.")


def sync_stream(config: BridgeConfig, connection_id: str, bridge_token: str, stream: dict, company_name: str = "") -> int:
	stream_name = stream["stream_name"]
	object_type = stream["object_type"]
	update_checkpoint(config, connection_id, bridge_token, stream_name, object_type, "Running")
	try:
		static_variables = {}
		if stream.get("requires_company") and company_name:
			static_variables["SVCURRENTCOMPANY"] = company_name
		kind = stream.get("kind", "collection")
		if kind == "collection":
			xml_text = fetch_collection_xml(config, stream, static_variables)
			objects = parse_tally_objects(xml_text, object_type, stream["tag_name"])
		elif kind == "report_rows":
			xml_text = post_tally_xml_with_retries(
				config,
				build_report_xml(stream["report_name"], static_variables, explode=bool(stream.get("explode"))),
			)
			parser_name = stream["parser"]
			parser = globals()[parser_name] if isinstance(parser_name, str) else parser_name
			rows = parser(xml_text)
			objects = report_rows_to_objects(stream, rows, company_name)
		elif kind == "report_snapshot":
			xml_text = post_tally_xml_with_retries(
				config,
				build_report_xml(stream["report_name"], static_variables, explode=bool(stream.get("explode"))),
			)
			objects = report_snapshot_to_object(stream, stream["report_name"], xml_text, company_name)
		else:
			raise ValueError(f"Unknown stream kind: {kind}")
		if objects:
			ingest_objects(config, connection_id, bridge_token, objects)
		update_checkpoint(
			config,
			connection_id,
			bridge_token,
			stream_name,
			object_type,
			"Success",
			cursor=max_alter_id(objects) if kind == "collection" else row_cursor(objects),
		)
		return len(objects)
	except Exception as exc:
		update_checkpoint(config, connection_id, bridge_token, stream_name, object_type, "Error", error=str(exc))
		raise


def get_company_name(config: BridgeConfig) -> str:
	xml_text = post_tally_xml(config, build_collection_xml("CompanyInfo", "Company", ["Name"]))
	root = ET.fromstring(clean_xml(xml_text))
	return element_text(root, "NAME")


def run_once(config: BridgeConfig) -> dict:
	log("Claiming pairing code with Sena...")
	claim = claim_pairing(config)
	connection_id = claim["connection_id"]
	bridge_token = claim["bridge_token"]
	if not config.connection_id:
		config.connection_id = connection_id
		save_config_value(config.config_path, "connection_id", connection_id)
	if not config.bridge_token:
		config.bridge_token = bridge_token
		save_config_value(config.config_path, "bridge_token", bridge_token)
	log(f"Paired with Sena connection {connection_id}.")

	failures = {}
	try:
		log(f"Checking Tally at {config.tally_host}:{config.tally_port}...")
		company_name = get_company_name(config)
		log(f"Tally company detected: {company_name or '(blank)'}")
	except Exception as exc:
		company_name = ""
		failures["Company"] = str(exc)
		log(f"Could not read Tally company name: {exc}")

	log("Sending bridge heartbeat...")
	heartbeat_result = heartbeat(
		config,
		connection_id,
		bridge_token,
		status="Syncing",
		tally_company_name=company_name,
		capabilities_json=json.dumps(BRIDGE_CAPABILITIES),
	)
	handle_bridge_command(config, connection_id, bridge_token, heartbeat_result.get("command"))

	counts = {}
	for stream in TALLY_STREAMS:
		stream_name = stream["object_type"]
		try:
			log(f"Starting {stream_name}...")
			count = sync_stream(config, connection_id, bridge_token, stream, company_name)
			counts[stream_name] = count
			log(f"Finished {stream_name}: {count} records.")
		except Exception as exc:
			counts[stream_name] = 0
			failures[stream_name] = str(exc)
			log(f"Skipped {stream_name}: {exc}")

	log("Marking connection active...")
	heartbeat_result = heartbeat(
		config,
		connection_id,
		bridge_token,
		status="Active",
		last_error=json.dumps(failures) if failures else "",
	)
	handle_bridge_command(config, connection_id, bridge_token, heartbeat_result.get("command"))
	log("Discovery finished.")
	return {"connection_id": connection_id, "company": company_name, "counts": counts, "failures": failures}


def self_test() -> int:
	sample = """
	<ENVELOPE><BODY><DATA><COLLECTION>
	<LEDGER NAME="Cash"><GUID>abc</GUID><ALTERID>42</ALTERID></LEDGER>
	<LEDGER><NAME>Sales</NAME><GUID>def</GUID><ALTERID>43</ALTERID></LEDGER>
	</COLLECTION></DATA></BODY></ENVELOPE>
	"""
	objects = parse_tally_objects(sample, "Ledger", "LEDGER")
	tb_sample = """
	<ENVELOPE>
		<DSPACCNAME><DSPDISPNAME>Cash</DSPDISPNAME></DSPACCNAME>
		<DSPACCINFO><DSPCLDRAMT><DSPCLDRAMTA>125.00</DSPCLDRAMTA></DSPCLDRAMT><DSPCLCRAMT><DSPCLCRAMTA></DSPCLCRAMTA></DSPCLCRAMT></DSPACCINFO>
	</ENVELOPE>
	"""
	tb_rows = parse_trial_balance_rows(tb_sample)
	assert tb_rows and tb_rows[0]["account"] == "Cash"
	assert "TYPE>Data" in build_report_xml("Trial Balance", {"SVCURRENTCOMPANY": "Demo"}, explode=True)
	assert len(parse_stock_summary_rows("<ENVELOPE><DSPACCNAME><DSPDISPNAME>Widget</DSPDISPNAME></DSPACCNAME><DSPSTKINFO><DSPSTKCL><DSPCLQTY>5 Nos</DSPCLQTY><DSPCLRATE>10</DSPCLRATE><DSPCLAMTA>50</DSPCLAMTA></DSPSTKCL></DSPSTKINFO></ENVELOPE>")) == 1
	assert len(objects) == 2
	assert objects[0]["source_name"] == "Cash"
	assert objects[1]["source_id"] == "def"
	assert max_alter_id(objects) == "43"
	assert "TYPE>COLLECTION" in build_collection_xml("Ledgers", "Ledger", ["Name"])
	print(json.dumps({"ok": True, "objects": len(objects), "cursor": max_alter_id(objects), "trial_balance": len(tb_rows)}))
	return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Sena Tally Bridge for Migration")
	parser.add_argument("--config", help="Path to bridge-config.json - overrides individual flags")
	parser.add_argument("--log-file", default="", help="Append all bridge output to this file (auto-created)")
	parser.add_argument("--server", help="Sena/Frappe server base URL, for example http://localhost:8001")
	parser.add_argument("--pairing-code", help="Pairing code displayed in Migration")
	parser.add_argument("--tally-host", default="localhost")
	parser.add_argument("--tally-port", default=9000, type=int)
	parser.add_argument("--tally-data-dir", default="", help="Optional TallyPrime Data directory for .1800 fast path")
	parser.add_argument("--bridge-id", default="")
	parser.add_argument("--timeout", default=120, type=int)
	parser.add_argument("--once", action="store_true", help="Run one discovery sync and exit")
	parser.add_argument("--self-test", action="store_true", help="Run parser/envelope self-test without network")
	parser.add_argument("--version", action="version", version=f"sena-tally-bridge {__version__}")
	return parser.parse_args(argv)


def _load_config_file(path: str) -> dict:
	"""Read bridge-config.json written by the Inno Setup wizard.

	The installer drops this file in `%LOCALAPPDATA%\\SenaTallyBridge\\` with
	the server URL + pairing code captured from the wizard. Letting the
	scheduled task point at the file (rather than embedding flags in the
	task command) means the user can re-pair by editing the JSON without
	reinstalling.
	"""
	with open(path, "r", encoding="utf-8") as fh:
		return json.load(fh)


def main(argv: list[str] | None = None) -> int:
	args = parse_args(argv or sys.argv[1:])

	# Config file beats individual flags. CLI flags fill in anything the
	# JSON omits, so existing PowerShell installs that pass --server etc.
	# still work for the deprecation window.
	cfg = _load_config_file(args.config) if args.config else {}

	# Wire stdout/stderr to the requested log file. The frozen-exe redirect
	# at module import sent them to NUL so import-time prints don't crash;
	# this swap moves real runtime output to a durable location. Line-
	# buffered (buffering=1) so tailing the file shows progress live.
	log_path = (cfg.get("log_file") or args.log_file or "").strip()
	if log_path:
		os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
		fh = open(log_path, "a", encoding="utf-8", buffering=1)
		sys.stdout = fh
		sys.stderr = fh

	if args.self_test:
		return self_test()

	server = cfg.get("server") or args.server
	pairing_code = cfg.get("pairing_code") or args.pairing_code
	if not server or not pairing_code:
		raise SystemExit("server + pairing_code are required (via --config or --server/--pairing-code)")

	config = BridgeConfig(
		server=server,
		pairing_code=pairing_code,
		tally_host=cfg.get("tally_host") or args.tally_host,
		tally_port=int(cfg.get("tally_port") or args.tally_port),
		tally_data_dir=cfg.get("tally_data_dir") or args.tally_data_dir,
		bridge_id=cfg.get("bridge_id") or args.bridge_id,
		connection_id=cfg.get("connection_id") or "",
		bridge_token=cfg.get("bridge_token") or "",
		config_path=args.config or "",
		timeout=int(cfg.get("timeout") or args.timeout),
		once=args.once,
	)

	while True:
		result = run_once(config)
		print(json.dumps({"ok": True, **result}))
		if config.once:
			return 0
		time.sleep(60)


if __name__ == "__main__":
	raise SystemExit(main())

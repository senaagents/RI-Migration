#!/usr/bin/env python3
"""Standalone Phase 1 bridge for Talk to Your Data (Tally).

This script is intentionally dependency-free so the first Windows bridge can be
packaged with PyInstaller or run with a stock Python install. It performs the
minimum production path:

1. Claim a user-created pairing code from Sena.
2. Probe the local Tally HTTP/XML server.
3. Push heartbeat metadata.
4. Export a few master-data collections and ingest them into the TYD middle
   store with durable checkpoints.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable


DEFAULT_METHOD_PREFIX = "custom_app_migration.custom_app_migration.api"
INVALID_XML_CHARS = re.compile(r"&#(?:[0-8]|1[0-1]|1[4-9]|2[0-9]|3[01]);")


@dataclass
class BridgeConfig:
	server: str
	pairing_code: str
	tally_host: str = "localhost"
	tally_port: int = 9000
	bridge_id: str = ""
	timeout: int = 120
	once: bool = False

	@property
	def tally_url(self) -> str:
		return f"http://{self.tally_host}:{self.tally_port}"


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


def build_collection_xml(name: str, object_type: str, fields: list[str] | None = None) -> str:
	methods = "".join(f"<NATIVEMETHOD>{field}</NATIVEMETHOD>" for field in fields or ["*"])
	return (
		"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>"
		f"<TYPE>COLLECTION</TYPE><ID>{name}</ID></HEADER><BODY><DESC>"
		"<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>"
		"<TDL><TDLMESSAGE>"
		f'<COLLECTION NAME="{name}" ISINITIALIZE="Yes">'
		f"<TYPE>{object_type}</TYPE>{methods}</COLLECTION>"
		"</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"
	)


def post_tally_xml(config: BridgeConfig, xml_payload: str) -> str:
	req = urllib.request.Request(
		config.tally_url,
		data=xml_payload.encode("utf-8"),
		headers={"Content-Type": "application/xml"},
		method="POST",
	)
	with urllib.request.urlopen(req, timeout=config.timeout) as resp:
		return clean_xml(resp.read().decode("utf-8", errors="replace"))


def element_text(element: ET.Element, name: str) -> str:
	found = element.find(f".//{name}")
	if found is not None and found.text:
		return found.text.strip()
	return ""


def object_name(element: ET.Element) -> str:
	return element.attrib.get("NAME", "").strip() or element_text(element, "NAME")


def parse_tally_objects(xml_text: str, object_type: str, tag_name: str) -> list[dict]:
	root = ET.fromstring(clean_xml(xml_text))
	items = []
	for element in root.iter(tag_name):
		name = object_name(element)
		guid = element_text(element, "GUID")
		alter_id = element_text(element, "ALTERID") or element_text(element, "MASTERID")
		source_id = guid or f"{object_type}:{name}"
		raw_payload = ET.tostring(element, encoding="unicode")
		items.append(
			{
				"object_type": object_type,
				"source_id": source_id,
				"source_guid": guid,
				"source_name": name,
				"source_alter_id": alter_id,
				"sync_status": "Seen",
				"raw_hash": hash_text(raw_payload),
				"raw_payload": raw_payload,
				"payload_format": "xml",
			}
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
	return post_json(
		config.server,
		"claim_tyd_bridge_pairing",
		{
			"pairing_code": config.pairing_code,
			"bridge_id": config.bridge_id or f"sena-tally-{uuid.uuid4().hex[:10]}",
			"host": config.tally_host,
			"port": config.tally_port,
			"capabilities_json": json.dumps(
				{
					"bridge_version": "0.1.0",
					"protocols": ["tally_http_xml"],
					"streams": ["Company", "Ledger", "Group", "Stock Item"],
				}
			),
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


def sync_stream(config: BridgeConfig, connection_id: str, bridge_token: str, stream: dict) -> int:
	stream_name = stream["stream_name"]
	object_type = stream["object_type"]
	update_checkpoint(config, connection_id, bridge_token, stream_name, object_type, "Running")
	try:
		xml_text = post_tally_xml(
			config,
			build_collection_xml(stream["collection_name"], stream["tally_type"], stream["fields"]),
		)
		objects = parse_tally_objects(xml_text, object_type, stream["tag_name"])
		if objects:
			ingest_objects(config, connection_id, bridge_token, objects)
		update_checkpoint(
			config,
			connection_id,
			bridge_token,
			stream_name,
			object_type,
			"Success",
			cursor=max_alter_id(objects),
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
	claim = claim_pairing(config)
	connection_id = claim["connection_id"]
	bridge_token = claim["bridge_token"]

	try:
		company_name = get_company_name(config)
	except Exception as exc:
		heartbeat(config, connection_id, bridge_token, status="Error", last_error=f"Tally probe failed: {exc}")
		raise

	heartbeat(
		config,
		connection_id,
		bridge_token,
		status="Syncing",
		tally_company_name=company_name,
		capabilities_json=json.dumps({"bridge_version": "0.1.0", "protocols": ["tally_http_xml"]}),
	)

	streams = [
		{
			"stream_name": "tally_ledgers",
			"object_type": "Ledger",
			"collection_name": "Ledgers",
			"tally_type": "Ledger",
			"tag_name": "LEDGER",
			"fields": ["Name", "Guid", "Parent", "AlterId", "ClosingBalance"],
		},
		{
			"stream_name": "tally_groups",
			"object_type": "Group",
			"collection_name": "Groups",
			"tally_type": "Group",
			"tag_name": "GROUP",
			"fields": ["Name", "Guid", "Parent", "AlterId"],
		},
		{
			"stream_name": "tally_stock_items",
			"object_type": "Stock Item",
			"collection_name": "StockItems",
			"tally_type": "Stock Item",
			"tag_name": "STOCKITEM",
			"fields": ["Name", "Guid", "Parent", "AlterId", "BaseUnits", "ClosingBalance"],
		},
	]

	counts = {}
	for stream in streams:
		counts[stream["object_type"]] = sync_stream(config, connection_id, bridge_token, stream)

	heartbeat(config, connection_id, bridge_token, status="Active")
	return {"connection_id": connection_id, "company": company_name, "counts": counts}


def self_test() -> int:
	sample = """
	<ENVELOPE><BODY><DATA><COLLECTION>
	<LEDGER NAME="Cash"><GUID>abc</GUID><ALTERID>42</ALTERID></LEDGER>
	<LEDGER><NAME>Sales</NAME><GUID>def</GUID><ALTERID>43</ALTERID></LEDGER>
	</COLLECTION></DATA></BODY></ENVELOPE>
	"""
	objects = parse_tally_objects(sample, "Ledger", "LEDGER")
	assert len(objects) == 2
	assert objects[0]["source_name"] == "Cash"
	assert objects[1]["source_id"] == "def"
	assert max_alter_id(objects) == "43"
	assert "TYPE>COLLECTION" in build_collection_xml("Ledgers", "Ledger", ["Name"])
	print(json.dumps({"ok": True, "objects": len(objects), "cursor": max_alter_id(objects)}))
	return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Sena Tally Bridge for Talk to Your Data")
	parser.add_argument("--server", help="Sena/Frappe server base URL, for example http://localhost:8001")
	parser.add_argument("--pairing-code", help="Pairing code displayed in Talk to Your Data")
	parser.add_argument("--tally-host", default="localhost")
	parser.add_argument("--tally-port", default=9000, type=int)
	parser.add_argument("--bridge-id", default="")
	parser.add_argument("--timeout", default=120, type=int)
	parser.add_argument("--once", action="store_true", help="Run one discovery sync and exit")
	parser.add_argument("--self-test", action="store_true", help="Run parser/envelope self-test without network")
	return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
	args = parse_args(argv or sys.argv[1:])
	if args.self_test:
		return self_test()
	if not args.server or not args.pairing_code:
		raise SystemExit("--server and --pairing-code are required unless --self-test is used")

	config = BridgeConfig(
		server=args.server,
		pairing_code=args.pairing_code,
		tally_host=args.tally_host,
		tally_port=args.tally_port,
		bridge_id=args.bridge_id,
		timeout=args.timeout,
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

#!/usr/bin/env python3
"""Dependency-free e2e test for the standalone Tally bridge.

This tests the bridge orchestration without requiring Tally or Frappe:

- pairing claim
- company probe
- heartbeat transitions
- three stream syncs
- checkpoint writes
- source object ingest with raw payload hashes
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

import sena_tally_bridge as bridge


class FakeSena:
	def __init__(self):
		self.connection_id = "TYD-CONN-00001"
		self.bridge_token = "bridge-secret"
		self.heartbeats = []
		self.checkpoints = []
		self.ingested = []

	def post_json(self, server, method, payload, timeout=120):
		if method == "claim_tyd_bridge_pairing":
			assert payload["pairing_code"] == "PAIR1234"
			assert payload["bridge_id"] == "bridge-e2e"
			return {
				"connection_id": self.connection_id,
				"bridge_token": self.bridge_token,
				"status": "Active",
			}

		assert payload["connection_id"] == self.connection_id
		assert payload["bridge_token"] == self.bridge_token

		if method == "bridge_heartbeat":
			self.heartbeats.append(payload)
			return {"ok": True, "connection_id": self.connection_id, "status": payload["status"]}
		if method == "upsert_sync_checkpoint":
			self.checkpoints.append(payload)
			return {"ok": True, "checkpoint": f"CHK-{len(self.checkpoints):03d}"}
		if method == "ingest_source_objects":
			objects = json.loads(payload["objects_json"])
			self.ingested.extend(objects)
			return {"ok": True, "created": len(objects), "updated": 0, "payloads": len(objects)}

		raise AssertionError(f"Unexpected method: {method}")


def fake_tally_response(config, xml_payload):
	if "<TYPE>Company</TYPE>" in xml_payload:
		return """
		<ENVELOPE><BODY><DATA><COLLECTION>
		  <COMPANY><NAME>Bridge Test Co</NAME></COMPANY>
		</COLLECTION></DATA></BODY></ENVELOPE>
		"""
	if "<TYPE>Ledger</TYPE>" in xml_payload:
		return """
		<ENVELOPE><BODY><DATA><COLLECTION>
		  <LEDGER NAME="Cash"><GUID>ledger-cash</GUID><ALTERID>42</ALTERID></LEDGER>
		  <LEDGER><NAME>Sales</NAME><GUID>ledger-sales</GUID><ALTERID>43</ALTERID></LEDGER>
		</COLLECTION></DATA></BODY></ENVELOPE>
		"""
	if "<TYPE>Group</TYPE>" in xml_payload:
		return """
		<ENVELOPE><BODY><DATA><COLLECTION>
		  <GROUP NAME="Current Assets"><GUID>group-assets</GUID><ALTERID>9</ALTERID></GROUP>
		</COLLECTION></DATA></BODY></ENVELOPE>
		"""
	if "<TYPE>Stock Item</TYPE>" in xml_payload:
		return """
		<ENVELOPE><BODY><DATA><COLLECTION>
		  <STOCKITEM NAME="Widget"><GUID>stock-widget</GUID><ALTERID>7</ALTERID></STOCKITEM>
		</COLLECTION></DATA></BODY></ENVELOPE>
		"""
	raise AssertionError(f"Unexpected Tally request: {xml_payload}")


def main():
	fake = FakeSena()
	original_post_json = bridge.post_json
	original_post_tally_xml = bridge.post_tally_xml
	try:
		bridge.post_json = fake.post_json
		bridge.post_tally_xml = fake_tally_response
		result = bridge.run_once(
			bridge.BridgeConfig(
				server="http://sena.invalid",
				pairing_code="PAIR1234",
				bridge_id="bridge-e2e",
				timeout=5,
				once=True,
			)
		)
	finally:
		bridge.post_json = original_post_json
		bridge.post_tally_xml = original_post_tally_xml

	assert result == {
		"connection_id": "TYD-CONN-00001",
		"company": "Bridge Test Co",
		"counts": {"Ledger": 2, "Group": 1, "Stock Item": 1},
	}
	assert [item["status"] for item in fake.heartbeats] == ["Syncing", "Active"]
	assert fake.heartbeats[0]["tally_company_name"] == "Bridge Test Co"
	assert len(fake.checkpoints) == 6
	assert {(row["stream_name"], row["status"]) for row in fake.checkpoints} == {
		("tally_ledgers", "Running"),
		("tally_ledgers", "Success"),
		("tally_groups", "Running"),
		("tally_groups", "Success"),
		("tally_stock_items", "Running"),
		("tally_stock_items", "Success"),
	}
	assert len(fake.ingested) == 4
	assert {item["source_id"] for item in fake.ingested} == {
		"ledger-cash",
		"ledger-sales",
		"group-assets",
		"stock-widget",
	}

	print(json.dumps({"ok": True, **result, "ingested": len(fake.ingested)}))


if __name__ == "__main__":
	main()

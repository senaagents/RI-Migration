#!/usr/bin/env python3
"""Dependency-free e2e test for the standalone Tally bridge.

This tests the bridge orchestration without requiring Tally or Frappe:

- pairing claim
- company probe
- heartbeat transitions
- multi-stream syncs
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
		self.connection_id = "INT-CONN-00001"
		self.bridge_token = "bridge-secret"
		self.heartbeats = []
		self.checkpoints = []
		self.ingested = []

	def post_json(self, server, method, payload, timeout=120):
		if method == "claim_integration_bridge_pairing":
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
		  <COMPANY NAME="Bridge Test Co"><GUID>company-bridge</GUID><NAME>Bridge Test Co</NAME><BASICCURRENCYCODE>INR</BASICCURRENCYCODE></COMPANY>
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
	if "<ID>Trial Balance</ID>" in xml_payload:
		return """
		<ENVELOPE>
		  <DSPACCNAME><DSPDISPNAME>Cash</DSPDISPNAME></DSPACCNAME>
		  <DSPACCINFO><DSPCLDRAMT><DSPCLDRAMTA>125.00</DSPCLDRAMTA></DSPCLDRAMT><DSPCLCRAMT><DSPCLCRAMTA></DSPCLCRAMTA></DSPCLCRAMT></DSPACCINFO>
		  <DSPACCNAME><DSPDISPNAME>Sales</DSPDISPNAME></DSPACCNAME>
		  <DSPACCINFO><DSPCLDRAMT><DSPCLDRAMTA></DSPCLDRAMTA></DSPCLDRAMT><DSPCLCRAMT><DSPCLCRAMTA>250.00</DSPCLCRAMTA></DSPCLCRAMT></DSPACCINFO>
		</ENVELOPE>
		"""
	if "<ID>Stock Summary</ID>" in xml_payload:
		return """
		<ENVELOPE>
		  <DSPACCNAME><DSPDISPNAME>Widget</DSPDISPNAME></DSPACCNAME>
		  <DSPSTKINFO><DSPSTKCL><DSPCLQTY>5 Nos</DSPCLQTY><DSPCLRATE>12.00</DSPCLRATE><DSPCLAMTA>60.00</DSPCLAMTA></DSPSTKCL></DSPSTKINFO>
		</ENVELOPE>
		"""
	if "<ID>Day Book</ID>" in xml_payload:
		return """
		<ENVELOPE>
		  <VOUCHER VCHTYPE="Sales"><GUID>daybook-1</GUID><DATE>20260401</DATE><VOUCHERNUMBER>1</VOUCHERNUMBER><PARTYLEDGERNAME>Cash</PARTYLEDGERNAME><NARRATION>Sale one</NARRATION></VOUCHER>
		  <VOUCHER VCHTYPE="Receipt"><GUID>daybook-2</GUID><DATE>20260402</DATE><VOUCHERNUMBER>2</VOUCHERNUMBER><PARTYLEDGERNAME>Acme</PARTYLEDGERNAME><NARRATION>Receipt two</NARRATION></VOUCHER>
		</ENVELOPE>
		"""
	if "<ID>Ledger Outstandings</ID>" in xml_payload:
		raise RuntimeError("Ledger Outstandings not available in test stub")
	return "<ENVELOPE><BODY><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>"


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

	assert result["connection_id"] == "INT-CONN-00001"
	assert result["company"] == "Bridge Test Co"
	assert result["counts"]["Company"] == 1
	assert result["counts"]["Ledger"] == 2
	assert result["counts"]["Group"] == 1
	assert result["counts"]["Stock Item"] == 1
	assert result["counts"]["Trial Balance Entry"] == 2
	assert result["counts"]["Stock Summary Item"] == 1
	assert result["counts"]["Day Book Voucher"] == 2
	assert result["counts"]["Outstanding Snapshot"] == 0
	assert "Outstanding Snapshot" in result["failures"]
	assert [item["status"] for item in fake.heartbeats] == ["Syncing", "Active"]
	assert fake.heartbeats[0]["tally_company_name"] == "Bridge Test Co"
	assert len(fake.checkpoints) == len(bridge.TALLY_STREAMS) * 2
	for stream in bridge.TALLY_STREAMS:
		assert (stream["stream_name"], "Running") in {(row["stream_name"], row["status"]) for row in fake.checkpoints}
		if stream["stream_name"] == "tally_outstanding":
			assert (stream["stream_name"], "Error") in {(row["stream_name"], row["status"]) for row in fake.checkpoints}
		else:
			assert (stream["stream_name"], "Success") in {(row["stream_name"], row["status"]) for row in fake.checkpoints}
	assert len(fake.ingested) == 10
	assert {item["source_id"] for item in fake.ingested} == {
		"company-bridge",
		"ledger-cash",
		"ledger-sales",
		"group-assets",
		"stock-widget",
		"tally_trial_balance:Cash",
		"tally_trial_balance:Sales",
		"tally_stock_summary:Widget",
		"tally_day_book:daybook-1",
		"tally_day_book:daybook-2",
	}

	print(json.dumps({"ok": True, **result, "ingested": len(fake.ingested)}))


if __name__ == "__main__":
	main()

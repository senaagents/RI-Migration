"""Phase 1 site smoke test for Integration source connections.

Run after `bench --site dev.localhost migrate`:

	bench --site dev.localhost execute migration.agentapp_migration.phase1_e2e.run
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import now_datetime

from migration.agentapp_migration import api


def run():
	"""Exercise the real DocTypes and bridge control-plane APIs on the site DB."""
	label = f"Phase 1 E2E Tally {now_datetime().strftime('%Y%m%d%H%M%S')}"
	pairing = api.create_integration_tally_pairing(label)
	claim = api.claim_integration_bridge_pairing(
		pairing_code=pairing["pairing_code"],
		bridge_id="phase1-e2e-bridge",
		capabilities_json=json.dumps({
			"bridge_version": "e2e",
			"protocols": ["tally_http_xml"],
			"streams": ["Ledger", "Group", "Stock Item"],
		}),
	)
	api.bridge_heartbeat(
		connection_id=claim["connection_id"],
		bridge_token=claim["bridge_token"],
		tally_company_name="Phase 1 Demo Co",
		tally_version="TallyPrime test",
		capabilities_json=json.dumps({"bridge_version": "e2e"}),
	)
	api.upsert_sync_checkpoint(
		connection_id=claim["connection_id"],
		bridge_token=claim["bridge_token"],
		stream_name="tally_ledgers",
		object_type="Ledger",
		status="Success",
		cursor="43",
	)
	ingested = api.ingest_source_objects(
		connection_id=claim["connection_id"],
		bridge_token=claim["bridge_token"],
		objects_json=json.dumps([
			{
				"object_type": "Ledger",
				"source_id": "phase1-ledger-cash",
				"source_name": "Cash",
				"source_alter_id": "42",
				"raw_payload": '<LEDGER NAME="Cash"><ALTERID>42</ALTERID></LEDGER>',
				"payload_format": "xml",
			},
			{
				"object_type": "Ledger",
				"source_id": "phase1-ledger-sales",
				"source_name": "Sales",
				"source_alter_id": "43",
				"raw_payload": '<LEDGER NAME="Sales"><ALTERID>43</ALTERID></LEDGER>',
				"payload_format": "xml",
			},
		]),
	)
	status = api.get_integration_connection_status(claim["connection_id"])
	checkpoint_count = frappe.db.count(api.INTEGRATION_CHECKPOINT_DOCTYPE, {"connection": claim["connection_id"]})
	object_count = frappe.db.count(api.INTEGRATION_SOURCE_OBJECT_DOCTYPE, {"connection": claim["connection_id"]})
	payload_count = frappe.db.count(api.INTEGRATION_RAW_PAYLOAD_DOCTYPE, {"connection": claim["connection_id"]})
	normalized_count = frappe.db.count(api.INTEGRATION_NORMALIZED_RECORD_DOCTYPE, {"connection": claim["connection_id"]})

	return {
		"ok": True,
		"connection_id": claim["connection_id"],
		"pairing_code": pairing["pairing_code"],
		"ingested": ingested,
		"checkpoint_count": checkpoint_count,
		"object_count": object_count,
		"payload_count": payload_count,
		"normalized_count": normalized_count,
		"status": {
			"connection_status": status["connection"].get("status"),
			"tally_company_name": status["connection"].get("tally_company_name"),
			"object_counts": status["object_counts"],
			"normalized_counts": status["normalized_counts"],
		},
	}

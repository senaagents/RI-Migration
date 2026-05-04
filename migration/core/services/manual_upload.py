"""Manual Tally XML upload pipeline.

Used by `migration.core.api.upload_tally_xml` to land a user-supplied
Tally Prime XML export (Gateway -> Export -> Masters) into the existing
Integration* silver doctypes. The upsert shape mirrors
`migration.core.api.ingest_source_objects` so that records produced via
the bridge and records produced via manual upload share one schema.

No new doctypes are introduced. All writes go through the standard
controller path (no `ignore_permissions`) so that the caller's
permissions on the Integration doctypes are enforced by Frappe.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

import frappe
from frappe.utils import now_datetime

from migration.core.parsers.tally import parse_list_of_accounts

INTEGRATION_CONNECTION_DOCTYPE = "Integration Source Connection"
INTEGRATION_SOURCE_OBJECT_DOCTYPE = "Integration Source Object"
INTEGRATION_NORMALIZED_RECORD_DOCTYPE = "Integration Normalized Source Record"

# The connection doctype's `source_type` Select field allows:
#   "Tally", "SAP", "CSV / Excel", "ERPNext", "Other".
# A manual XML export from Tally is still a Tally source, so we reuse
# "Tally" rather than inventing a new option. We tag the connection
# label so it's distinguishable from bridge-paired Tally connections.
MANUAL_SOURCE_TYPE = "Tally"
MANUAL_CONNECTION_LABEL = "Tally manual XML upload"

_TALLY_ENVELOPE_MARKERS = ("<TALLYMESSAGE", "<ENVELOPE", "<TALLYREPORT")


def _json_dumps(value) -> str:
	return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _hash_text(value) -> str:
	if value is None:
		value = ""
	if not isinstance(value, str):
		value = _json_dumps(value)
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decode_xml_bytes(xml_bytes: bytes) -> str:
	"""Decode raw XML bytes, honoring BOM-declared encoding.

	Tally Prime exports as UTF-16 LE by default (BOM `\\xff\\xfe`); other
	tools may emit UTF-8 with or without a BOM. Decoding everything as
	UTF-8 with `errors='replace'` turns UTF-16 into a stream of U+FFFD,
	which then fails every downstream marker / parser check. Detect the
	BOM and pick the right codec.
	"""
	if xml_bytes.startswith(b"\xff\xfe"):
		return xml_bytes.decode("utf-16-le", errors="replace").lstrip("\ufeff")
	if xml_bytes.startswith(b"\xfe\xff"):
		return xml_bytes.decode("utf-16-be", errors="replace").lstrip("\ufeff")
	if xml_bytes.startswith(b"\xef\xbb\xbf"):
		return xml_bytes.decode("utf-8-sig", errors="replace")
	return xml_bytes.decode("utf-8", errors="replace")


def validate_tally_envelope(xml_bytes: bytes) -> str:
	"""Decode + sanity-check a Tally XML upload.

	Raises `frappe.ValidationError` (via `frappe.throw`) if the payload is
	not well-formed XML or doesn't look like a Tally envelope.
	Returns the decoded XML string on success.
	"""
	if not xml_bytes:
		frappe.throw("Empty XML upload")

	if isinstance(xml_bytes, str):
		xml_text = xml_bytes
	else:
		xml_text = _decode_xml_bytes(xml_bytes)

	upper = xml_text.upper()
	if not any(marker in upper for marker in _TALLY_ENVELOPE_MARKERS):
		frappe.throw(
			"Uploaded file does not look like a Tally export "
			"(missing <TALLYMESSAGE> / <ENVELOPE> root)."
		)

	# Well-formedness check using the same cleaner the parser uses.
	from migration.core.parsers.tally import _clean_xml

	try:
		ET.fromstring(_clean_xml(xml_text))
	except ET.ParseError as exc:
		frappe.throw(f"Tally XML is not well-formed: {exc}")

	return xml_text


def get_or_create_manual_connection(owner_user: str) -> str:
	"""Find or create the Tally manual-upload connection for this user."""
	existing = frappe.get_all(
		INTEGRATION_CONNECTION_DOCTYPE,
		filters={
			"owner_user": owner_user,
			"source_type": MANUAL_SOURCE_TYPE,
			"connection_label": MANUAL_CONNECTION_LABEL,
		},
		fields=["name"],
		order_by="modified desc",
		limit_page_length=1,
	)
	if existing:
		return existing[0].name

	doc = frappe.get_doc({
		"doctype": INTEGRATION_CONNECTION_DOCTYPE,
		"connection_label": MANUAL_CONNECTION_LABEL,
		"source_type": MANUAL_SOURCE_TYPE,
		"status": "Active",
		"owner_user": owner_user,
	})
	doc.insert()
	return doc.name


def _normalized_payload(object_type: str, source_id: str, record: dict) -> dict:
	return {
		"source_type": MANUAL_SOURCE_TYPE,
		"object_type": object_type,
		"source_id": source_id,
		"source_guid": record.get("guid") or "",
		"name": record.get("name") or "",
		"record": record,
	}


def _upsert_one(
	connection_id: str,
	object_type: str,
	record: dict,
	now,
) -> tuple[bool, str | None]:
	"""Upsert a single source-object + normalized-record pair.

	Returns `(success, error_message)`.
	"""
	source_name = (record.get("name") or "").strip()
	source_guid = (record.get("guid") or "").strip()
	source_id = source_guid or source_name
	if not source_id:
		return False, f"{object_type} record missing both name and guid"

	normalized = _normalized_payload(object_type, source_id, record)
	normalized_text = _json_dumps(normalized)
	normalized_hash = _hash_text(normalized_text)

	# 1. Source Object — same upsert key as ingest_source_objects.
	so_filters = {
		"connection": connection_id,
		"object_type": object_type,
		"source_id": source_id,
	}
	existing_so = frappe.get_all(
		INTEGRATION_SOURCE_OBJECT_DOCTYPE,
		filters=so_filters,
		fields=["name"],
		limit_page_length=1,
	)
	if existing_so:
		so_doc = frappe.get_doc(INTEGRATION_SOURCE_OBJECT_DOCTYPE, existing_so[0].name)
	else:
		so_doc = frappe.new_doc(INTEGRATION_SOURCE_OBJECT_DOCTYPE)
		so_doc.connection = connection_id
		so_doc.object_type = object_type
		so_doc.source_id = source_id
		so_doc.first_seen_at = now

	so_doc.source_guid = source_guid
	so_doc.source_name = source_name
	so_doc.raw_hash = normalized_hash
	so_doc.normalized_hash = normalized_hash
	so_doc.sync_status = "Seen"
	so_doc.last_seen_at = now
	if so_doc.is_new():
		so_doc.insert()
	else:
		so_doc.save()

	# 2. Normalized Source Record — same upsert key.
	nsr_filters = {
		"connection": connection_id,
		"source_object": so_doc.name,
		"record_type": object_type,
		"source_id": source_id,
	}
	existing_nsr = frappe.get_all(
		INTEGRATION_NORMALIZED_RECORD_DOCTYPE,
		filters=nsr_filters,
		fields=["name"],
		limit_page_length=1,
	)
	if existing_nsr:
		nsr_doc = frappe.get_doc(INTEGRATION_NORMALIZED_RECORD_DOCTYPE, existing_nsr[0].name)
	else:
		nsr_doc = frappe.new_doc(INTEGRATION_NORMALIZED_RECORD_DOCTYPE)
		nsr_doc.update(nsr_filters)
		nsr_doc.first_seen_at = now

	nsr_doc.record_name = source_name or normalized.get("name") or ""
	nsr_doc.sync_status = "Active"
	nsr_doc.normalized_hash = normalized_hash
	nsr_doc.extracted_at = now
	nsr_doc.last_seen_at = now
	nsr_doc.normalized_json = normalized_text
	if nsr_doc.is_new():
		nsr_doc.insert()
	else:
		nsr_doc.save()

	return True, None


_OBJECT_TYPE_BY_KEY = {
	"currencies": "currency",
	"groups": "group",
	"ledgers": "ledger",
	# Phase 1 export-of-masters does not include vouchers/voucher_types,
	# but we keep the keys so the response shape is stable.
	"voucher_types": "voucher_type",
	"vouchers": "voucher",
}


def land_parsed_payload(connection_id: str, parsed: dict) -> dict:
	"""Upsert the output of `parse_list_of_accounts` into the silver tables.

	Returns `{counts: {...}, errors: [...]}`. Per-record failures are
	captured in `errors` instead of aborting the whole upload.
	"""
	now = now_datetime()
	counts = {key: 0 for key in _OBJECT_TYPE_BY_KEY}
	errors: list[dict] = []

	for key, object_type in _OBJECT_TYPE_BY_KEY.items():
		records = parsed.get(key) or []
		if not isinstance(records, list):
			continue
		for record in records:
			if not isinstance(record, dict):
				continue
			try:
				ok, err = _upsert_one(connection_id, object_type, record, now)
			except Exception as exc:
				ok = False
				err = f"{type(exc).__name__}: {exc}"
			if ok:
				counts[key] += 1
			else:
				errors.append({
					"object_type": object_type,
					"source_name": record.get("name"),
					"error": err,
				})

	frappe.db.set_value(
		INTEGRATION_CONNECTION_DOCTYPE,
		connection_id,
		"last_sync_at",
		now,
		update_modified=True,
	)
	return {"counts": counts, "errors": errors}


def process_upload(owner_user: str, xml_bytes: bytes) -> dict:
	"""Top-level entry point used by the API endpoint.

	1. Validates the XML envelope.
	2. Resolves (or creates) the manual-upload connection for the user.
	3. Parses with the existing `parse_list_of_accounts`.
	4. Lands every parsed record into Integration Source Object +
	   Integration Normalized Source Record.
	"""
	xml_text = validate_tally_envelope(xml_bytes)
	connection_id = get_or_create_manual_connection(owner_user)
	parsed = parse_list_of_accounts(xml_text)
	result = land_parsed_payload(connection_id, parsed)
	frappe.db.commit()
	return {
		"connection_id": connection_id,
		"counts": result["counts"],
		"errors": result["errors"],
	}

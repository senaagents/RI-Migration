"""Whitelisted API endpoints for the Migration agent-app.

execute_migration runs as a background job (enqueue) so the HTTP request
returns immediately. The frontend polls get_migration_status for progress.
"""

import json
import logging
import os
import secrets
import hashlib

import frappe
import frappe.sessions
from frappe.utils import add_to_date, now_datetime
from frappe.utils.background_jobs import enqueue, is_job_enqueued

logger = logging.getLogger(__name__)

_CACHE_KEY = "migration_progress"

INTEGRATION_CONNECTION_DOCTYPE = "Integration Source Connection"
INTEGRATION_SOURCE_TYPE_DOCTYPE = "Integration Source Type"
INTEGRATION_CHECKPOINT_DOCTYPE = "Integration Sync Checkpoint"
INTEGRATION_SOURCE_OBJECT_DOCTYPE = "Integration Source Object"
INTEGRATION_RAW_PAYLOAD_DOCTYPE = "Integration Raw Payload"
INTEGRATION_NORMALIZED_RECORD_DOCTYPE = "Integration Normalized Source Record"

_TARGET_SCHEMA_SEEDS = {
	"sena_erp": [
		"Account",
		"Customer",
		"Supplier",
		"Item Group",
		"Item",
		"Warehouse",
		"Customer Group",
		"Supplier Group",
		"Cost Center",
		"Address",
		"Contact",
		"Sales Invoice",
		"Purchase Invoice",
		"Journal Entry",
	],
}

_TARGET_SCHEMA_SAFE_FIELD_TYPES = {
	"Autocomplete",
	"Check",
	"Data",
	"Currency",
	"Date",
	"Datetime",
	"Duration",
	"Dynamic Link",
	"Email",
	"Float",
	"Int",
	"Link",
	"Long Text",
	"Markdown Editor",
	"Percent",
	"Phone",
	"Color",
	"Read Only",
	"Select",
	"Small Text",
	"Text",
	"Text Editor",
	"Time",
}

_TARGET_SCHEMA_EXCLUDED_FIELD_TYPES = {
	"Attach",
	"Attach Image",
	"Barcode",
	"Button",
	"Column Break",
	"Code",
	"Fold",
	"Geolocation",
	"HTML",
	"Image",
	"JSON",
	"Section Break",
	"Signature",
	"Table MultiSelect",
	"Tab Break",
}

_TARGET_SCHEMA_TABLE_FIELD_TYPES = {"Table"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_progress(job_id, status, step, progress, steps_done=None, errors=None, summary=None):
	"""Write migration progress to Redis cache + push realtime event."""
	data = {
		"status": status,
		"current_step": step,
		"progress": progress,
		"steps": steps_done or [],
		"errors": errors or [],
		"summary": summary,
	}
	frappe.cache.hset(_CACHE_KEY, job_id, json.dumps(data))
	frappe.publish_realtime("migration_progress", {"job_id": job_id, **data})


def _parse_json(value, default=None):
	if value in (None, ""):
		return default
	if isinstance(value, (dict, list)):
		return value
	return json.loads(value)


def _json_dumps(value):
	return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _hash_text(value):
	if value is None:
		value = ""
	if not isinstance(value, str):
		value = _json_dumps(value)
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _get_password(doc, fieldname):
	try:
		return doc.get_password(fieldname) or ""
	except Exception:
		return doc.get(fieldname) or ""


def _verify_bridge(connection_id, bridge_token):
	if not connection_id or not bridge_token:
		frappe.throw("connection_id and bridge_token are required")
	doc = frappe.get_doc(INTEGRATION_CONNECTION_DOCTYPE, connection_id)
	stored = _get_password(doc, "bridge_token")
	if not stored or not secrets.compare_digest(stored, bridge_token):
		frappe.throw("Invalid bridge token", frappe.PermissionError)
	return doc


def _require_integration_user():
	if frappe.session.user == "Guest":
		frappe.throw("Login required", frappe.PermissionError)


def _can_manage_integration_connection(doc):
	if "System Manager" in frappe.get_roles(frappe.session.user):
		return True
	return not doc.owner_user or doc.owner_user == frappe.session.user


def _assert_can_manage_integration_connection(doc):
	_require_integration_user()
	if not _can_manage_integration_connection(doc):
		frappe.throw("Not permitted", frappe.PermissionError)


def _delete_integration_connection_snapshot(connection_id):
	"""Delete an Integration connection and all discovery rows owned by it."""
	counts = {
		"raw_payloads": frappe.db.count(INTEGRATION_RAW_PAYLOAD_DOCTYPE, {"connection": connection_id}),
		"normalized_records": frappe.db.count(INTEGRATION_NORMALIZED_RECORD_DOCTYPE, {"connection": connection_id}),
		"source_objects": frappe.db.count(INTEGRATION_SOURCE_OBJECT_DOCTYPE, {"connection": connection_id}),
		"checkpoints": frappe.db.count(INTEGRATION_CHECKPOINT_DOCTYPE, {"connection": connection_id}),
	}
	frappe.db.delete(INTEGRATION_RAW_PAYLOAD_DOCTYPE, {"connection": connection_id})
	frappe.db.delete(INTEGRATION_NORMALIZED_RECORD_DOCTYPE, {"connection": connection_id})
	frappe.db.delete(INTEGRATION_SOURCE_OBJECT_DOCTYPE, {"connection": connection_id})
	frappe.db.delete(INTEGRATION_CHECKPOINT_DOCTYPE, {"connection": connection_id})
	frappe.db.delete(INTEGRATION_CONNECTION_DOCTYPE, {"name": connection_id})
	return counts


def _new_pairing_code():
	for _ in range(10):
		code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
		if not frappe.get_all(INTEGRATION_CONNECTION_DOCTYPE, filters={"pairing_code": code, "status": "Pairing"}, limit=1):
			return code
	frappe.throw("Could not allocate a pairing code. Please try again.")


def _normalized_record_from_item(item, object_type, source_id):
	normalized = item.get("normalized_json")
	if normalized in (None, ""):
		normalized = {
			"source_type": "Tally",
			"object_type": object_type,
			"source_id": source_id,
			"source_guid": item.get("source_guid") or "",
			"name": item.get("source_name") or "",
			"alter_id": item.get("source_alter_id") or "",
		}
	if isinstance(normalized, str):
		return _parse_json(normalized, default={}) or {}
	return normalized or {}


def _is_blank_source_record(record_type, source_id, record_name=None, normalized=None):
	"""Reject placeholder rows emitted by partial source parses."""
	record_type = str(record_type or "").strip()
	source_id = str(source_id or "").strip()
	record_name = str(record_name or "").strip()
	normalized = normalized or {}
	if not record_type or not source_id:
		return True
	if source_id == f"{record_type}:" and not record_name:
		source_guid = str(normalized.get("source_guid") or "").strip()
		normalized_name = str(normalized.get("name") or "").strip()
		return not source_guid and not normalized_name
	return False


def _get_readable_integration_connection(connection_id):
	_require_integration_user()
	if not connection_id:
		frappe.throw("connection_id is required")
	doc = frappe.get_doc(INTEGRATION_CONNECTION_DOCTYPE, connection_id)
	if not _can_manage_integration_connection(doc):
		frappe.throw("Not permitted", frappe.PermissionError)
	return doc


def _source_key_from_type(source_type):
	return str(source_type or "").strip().lower().replace(" / ", "_").replace(" ", "_")


def _source_type_from_key(source_key):
	key = str(source_key or "").strip().lower()
	if key == "tally":
		return "Tally"
	if key == "sap":
		return "SAP"
	if key in ("excel", "csv_excel", "csv/excel"):
		return "CSV / Excel"
	return source_key


def _safe_json_object(value):
	parsed = _parse_json(value, default={}) or {}
	return parsed if isinstance(parsed, dict) else {}


def _infer_scalar_type(value):
	if isinstance(value, bool):
		return "boolean"
	if isinstance(value, int) or isinstance(value, float):
		return "number"
	if value in (None, ""):
		return "empty"
	return "text"


def _merge_schema_fields(schema_fields, record):
	for key, value in record.items():
		if isinstance(value, (dict, list)):
			field_type = "json"
		else:
			field_type = _infer_scalar_type(value)
		existing = schema_fields.setdefault(key, {"field": key, "type": field_type, "sample_values": []})
		if existing["type"] == "empty" and field_type != "empty":
			existing["type"] = field_type
		if value not in (None, "") and len(existing["sample_values"]) < 3 and value not in existing["sample_values"]:
			existing["sample_values"].append(value)


def _record_matches_filters(record, filters):
	for condition in filters or []:
		field = condition.get("field")
		op = (condition.get("op") or "=").lower()
		expected = condition.get("value")
		actual = record.get(field)
		if op in ("=", "==", "eq") and actual != expected:
			return False
		if op in ("!=", "ne") and actual == expected:
			return False
		if op == "contains" and str(expected).casefold() not in str(actual or "").casefold():
			return False
		if op == "in" and actual not in (expected or []):
			return False
	return True


def _normalize_target_key(target_key):
	return str(target_key or "").strip().casefold()


def _resolve_declared_migration_target(target_key):
	installed_apps = set(frappe.get_installed_apps())
	normalized_target_key = _normalize_target_key(target_key)
	for target in _get_declared_migration_targets(installed_apps):
		if _normalize_target_key(target.get("target_key")) != normalized_target_key:
			continue
		required_app = target.get("required_app")
		if required_app and required_app not in installed_apps:
			frappe.throw(f"Required app {required_app} is not installed")
		return target, required_app
	frappe.throw(f"Migration target {target_key} is not declared by any installed app")


def _normalize_select_options(options):
	if options in (None, ""):
		return None
	if isinstance(options, (list, tuple)):
		return list(options)
	return options


def _is_schema_field_type_safe(fieldtype):
	return fieldtype in _TARGET_SCHEMA_SAFE_FIELD_TYPES or fieldtype in _TARGET_SCHEMA_TABLE_FIELD_TYPES


def _field_schema_state(fieldtype, read_only=False, hidden=False):
	if fieldtype in _TARGET_SCHEMA_TABLE_FIELD_TYPES:
		return "table"
	if fieldtype not in _TARGET_SCHEMA_SAFE_FIELD_TYPES:
		return "excluded"
	if read_only or hidden or fieldtype == "Read Only":
		return "read_only"
	return "writable"


def _serialize_schema_field(df):
	fieldtype = str(getattr(df, "fieldtype", "") or "")
	read_only = bool(getattr(df, "read_only", 0))
	hidden = bool(getattr(df, "hidden", 0))
	state = _field_schema_state(fieldtype, read_only=read_only, hidden=hidden)
	field_schema = {
		"fieldname": getattr(df, "fieldname", ""),
		"label": getattr(df, "label", "") or getattr(df, "fieldname", ""),
		"fieldtype": fieldtype,
		"state": state,
		"readable": state != "excluded",
		"writable": state in ("writable", "table"),
		"read_only": read_only,
		"hidden": hidden,
		"reqd": bool(getattr(df, "reqd", 0)),
		"in_list_view": bool(getattr(df, "in_list_view", 0)),
		"unique": bool(getattr(df, "unique", 0)),
		"allow_on_submit": bool(getattr(df, "allow_on_submit", 0)),
		"translatable": bool(getattr(df, "translatable", 0)),
		"depends_on": getattr(df, "depends_on", None),
		"mandatory_depends_on": getattr(df, "mandatory_depends_on", None),
		"description": getattr(df, "description", None),
		"default": getattr(df, "default", None),
		"permlevel": int(getattr(df, "permlevel", 0) or 0),
	}
	options = _normalize_select_options(getattr(df, "options", None))
	if options not in (None, ""):
		field_schema["options"] = options
	if getattr(df, "length", None) not in (None, ""):
		field_schema["length"] = getattr(df, "length")
	if getattr(df, "precision", None) not in (None, ""):
		field_schema["precision"] = getattr(df, "precision")
	if fieldtype == "Table" and options:
		field_schema["child_doctype"] = options
	if fieldtype == "Link" and options:
		field_schema["link_to"] = options
	return field_schema


def _summarize_meta(meta):
	return {
		"doctype": meta.name,
		"module": getattr(meta, "module", None),
		"issingle": bool(getattr(meta, "issingle", 0)),
		"istable": bool(getattr(meta, "istable", 0)),
		"is_tree": bool(getattr(meta, "is_tree", 0)),
		"custom": bool(getattr(meta, "custom", 0)),
		"allow_import": bool(getattr(meta, "allow_import", 0)),
	}


def _discover_doctype_schema(doctype, visited=None, depth=0, max_depth=2):
	visited = visited or set()
	normalized = _normalize_target_key(doctype)
	if normalized in visited:
		return None
	visited = set(visited)
	visited.add(normalized)

	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return None

	schema = _summarize_meta(meta)
	fields = []
	skipped_fields = []
	child_tables = []
	for df in getattr(meta, "fields", []) or []:
		fieldtype = str(getattr(df, "fieldtype", "") or "")
		if fieldtype in _TARGET_SCHEMA_EXCLUDED_FIELD_TYPES:
			skipped_fields.append({
				"fieldname": getattr(df, "fieldname", ""),
				"fieldtype": fieldtype,
				"reason": "excluded_fieldtype",
			})
			continue
		if fieldtype == "Table":
			table_field = _serialize_schema_field(df)
			fields.append(table_field)
			if depth < max_depth:
				child_schema = _discover_doctype_schema(table_field.get("child_doctype"), visited=visited, depth=depth + 1, max_depth=max_depth)
				if child_schema:
					child_tables.append({
						"fieldname": table_field.get("fieldname"),
						"label": table_field.get("label"),
						"doctype": table_field.get("child_doctype"),
						"schema": child_schema,
					})
			continue
		if not _is_schema_field_type_safe(fieldtype):
			skipped_fields.append({
				"fieldname": getattr(df, "fieldname", ""),
				"fieldtype": fieldtype,
				"reason": "unsafe_fieldtype",
			})
			continue
		fields.append(_serialize_schema_field(df))

	schema.update({
		"fields": fields,
		"child_tables": child_tables,
		"skipped_fields": skipped_fields,
		"field_count": len(fields),
		"writable_field_count": sum(1 for field in fields if field.get("writable")),
		"readable_field_count": sum(1 for field in fields if field.get("readable")),
		"skipped_field_count": len(skipped_fields),
	})
	return schema


def _discover_target_schema_doctypes(target):
	target_key = _normalize_target_key(target.get("target_key"))
	configured_doctypes = target.get("schema_doctypes") or target.get("schema_doctype_names") or []
	if isinstance(configured_doctypes, str):
		configured_doctypes = [configured_doctypes]
	elif not isinstance(configured_doctypes, (list, tuple)):
		configured_doctypes = []

	seed_doctypes = list(configured_doctypes)
	if not seed_doctypes:
		seed_doctypes = list(_TARGET_SCHEMA_SEEDS.get(target_key, []))

	seen = set()
	doctypes = []
	for doctype in seed_doctypes:
		normalized = _normalize_target_key(doctype)
		if not doctype or normalized in seen:
			continue
		schema = _discover_doctype_schema(doctype, visited=seen, depth=0)
		if not schema:
			continue
		seen.add(normalized)
		doctypes.append(schema)
	return doctypes


# ---------------------------------------------------------------------------
# Quick endpoints (synchronous, fast)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_target_companies():
	"""Return list of existing companies on this site."""
	return frappe.get_all("Company", fields=["name", "abbr", "default_currency", "country"])


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_csrf_token():
	"""Return a CSRF token for standalone dev UIs that do not load Frappe JS."""
	return frappe.sessions.get_csrf_token()


@frappe.whitelist(allow_guest=True)
def get_integration_source_types():
	"""Return installed source types that the Migration/Integrations UI can render."""
	from migration.setup import ensure_integration_source_types

	ensure_integration_source_types()
	rows = frappe.get_all(
		INTEGRATION_SOURCE_TYPE_DOCTYPE,
		filters={"enabled": 1},
		fields=[
			"name", "source_key", "title", "subtitle", "description",
			"implementation_status", "sort_order", "icon_label",
			"icon_bg_class", "icon_text_class", "capabilities_json", "methods_json",
		],
		order_by="sort_order asc, title asc",
		limit_page_length=100,
	)
	for row in rows:
		row["capabilities"] = _parse_json(row.pop("capabilities_json", None), default={}) or {}
		row["methods"] = _parse_json(row.pop("methods_json", None), default=[]) or []
	return rows


# Backward-compatible API aliases for bridge/UI builds created before the
# Integration naming cleanup. Keep these until all downloaded bridge packages
# have moved to the integration_* endpoint names.
get_tyd_source_types = get_integration_source_types


@frappe.whitelist(allow_guest=True)
def get_migration_target_types():
	"""Return installed AgentApps that declare migration target capability."""
	installed_apps = set(frappe.get_installed_apps())
	runtime_agents_by_name = {}
	if frappe.db.table_exists("Runtime Agent"):
		for agent in frappe.get_all(
			"Runtime Agent",
			filters={"is_app": 1, "is_enabled": 1},
			fields=["name", "agent_name", "source_registry_item", "sena_accessible", "ui"],
			limit_page_length=500,
		):
			runtime_agents_by_name[(agent.agent_name or "").casefold()] = agent

	targets = []
	for target in _get_declared_migration_targets(installed_apps):
		required_app = target.get("required_app")
		if required_app and required_app not in installed_apps:
			continue

		agent_name = target.get("agent_name")
		runtime_agent = runtime_agents_by_name.get(str(agent_name or "").casefold()) if agent_name else None
		source_registry_item = target.get("source_registry_item")
		if runtime_agent and runtime_agent.get("source_registry_item"):
			source_registry_item = runtime_agent.source_registry_item

		targets.append({
			"target_key": target.get("target_key"),
			"title": target.get("title"),
			"subtitle": target.get("subtitle"),
			"agent_name": agent_name,
			"runtime_agent": runtime_agent.name if runtime_agent else None,
			"source_registry_item": source_registry_item,
			"required_app": required_app,
			"implementation_status": target.get("implementation_status") or "Ready",
			"sort_order": int(target.get("sort_order") or 100),
			"icon_label": target.get("icon_label"),
			"icon_bg_class": target.get("icon_bg_class") or "bg-gray-100 dark:bg-gray-800",
			"icon_text_class": target.get("icon_text_class") or "text-gray-500",
			"target_kind": target.get("target_kind") or "agent_app",
			"capabilities": target.get("capabilities") or {},
		})

	targets = [target for target in targets if target.get("target_key") and target.get("title")]
	targets.sort(key=lambda item: (item["sort_order"], item["title"]))
	return targets


@frappe.whitelist(allow_guest=True)
def get_migration_target_schema(target_key):
	"""Return conservative schema discovery for a declared migration target."""
	if not target_key:
		frappe.throw("target_key is required")

	target, required_app = _resolve_declared_migration_target(target_key)
	doctypes = _discover_target_schema_doctypes(target)
	total_fields = sum(doctype.get("field_count", 0) for doctype in doctypes)
	total_writable_fields = sum(doctype.get("writable_field_count", 0) for doctype in doctypes)
	total_readable_fields = sum(doctype.get("readable_field_count", 0) for doctype in doctypes)
	total_skipped_fields = sum(doctype.get("skipped_field_count", 0) for doctype in doctypes)

	return {
		"target": {
			"target_key": target.get("target_key"),
			"title": target.get("title"),
			"subtitle": target.get("subtitle"),
			"required_app": required_app,
			"target_kind": target.get("target_kind") or "agent_app",
			"implementation_status": target.get("implementation_status") or "Ready",
			"capabilities": target.get("capabilities") or {},
		},
		"schema": {
			"doctype_count": len(doctypes),
			"field_count": total_fields,
			"readable_field_count": total_readable_fields,
			"writable_field_count": total_writable_fields,
			"skipped_field_count": total_skipped_fields,
			"doctypes": doctypes,
		},
	}


def _get_declared_migration_targets(installed_apps):
	targets = []
	for app_name in installed_apps:
		try:
			hooks_module = frappe.get_module(f"{app_name}.hooks")
		except Exception:
			continue
		for target in getattr(hooks_module, "migration_targets", None) or []:
			if isinstance(target, dict):
				targets.append(dict(target))
	return targets


# ---------------------------------------------------------------------------
# Integration source control-plane APIs
# ---------------------------------------------------------------------------

@frappe.whitelist()
def create_integration_tally_pairing(connection_label=None):
	"""Create a bridge pairing record for a Tally source connection.

	The desktop bridge starts in an untrusted state. A logged-in user creates
	this pairing from the UI, then the bridge claims it with the displayed code.
	"""
	_require_integration_user()

	pairing_code = _new_pairing_code()
	bridge_token = secrets.token_urlsafe(32)
	label = connection_label or "Tally connection"

	doc = frappe.get_doc({
		"doctype": INTEGRATION_CONNECTION_DOCTYPE,
		"connection_label": label,
		"source_type": "Tally",
		"status": "Pairing",
		"owner_user": frappe.session.user,
		"pairing_code": pairing_code,
		"pairing_expires_at": add_to_date(now_datetime(), minutes=30),
		"bridge_token": bridge_token,
		"tally_port": 9000,
	})
	doc.insert(ignore_permissions=True)
	_cleanup_integration_connections(source_type="Tally", keep_latest=3, owner_user=frappe.session.user)
	frappe.db.commit()

	return {
		"connection_id": doc.name,
		"pairing_code": pairing_code,
		"expires_at": str(doc.pairing_expires_at),
	}


create_tyd_tally_pairing = create_integration_tally_pairing


@frappe.whitelist()
def list_integration_connections():
	"""List Integration source connections for the current user."""
	_require_integration_user()
	_cleanup_integration_connections(source_type="Tally", keep_latest=3, owner_user=frappe.session.user)

	return frappe.get_all(
		INTEGRATION_CONNECTION_DOCTYPE,
		filters={"owner_user": frappe.session.user},
		fields=[
			"name", "connection_label", "source_type", "status", "bridge_id",
			"tally_company_name", "tally_version", "last_seen_at", "last_sync_at",
			"last_error", "modified",
		],
		order_by="modified desc",
		limit_page_length=100,
	)


list_tyd_connections = list_integration_connections


@frappe.whitelist()
def delete_integration_connection(connection_id=None):
	"""Delete one Integration source connection and its discovery snapshot."""
	if not connection_id:
		frappe.throw("connection_id is required")
	doc = frappe.get_doc(INTEGRATION_CONNECTION_DOCTYPE, connection_id)
	_assert_can_manage_integration_connection(doc)
	counts = _delete_integration_connection_snapshot(connection_id)
	frappe.db.commit()
	return {"deleted": [connection_id], "counts": {connection_id: counts}}


delete_tyd_connection = delete_integration_connection


@frappe.whitelist()
def cleanup_integration_connections(source_type="Tally", keep_latest=3):
	"""Delete older Integration source connections after keeping the newest N."""
	_require_integration_user()
	return _cleanup_integration_connections(source_type=source_type, keep_latest=keep_latest, owner_user=frappe.session.user)


cleanup_tyd_connections = cleanup_integration_connections


@frappe.whitelist()
def discover_sap_hana_source(address=None, port=30015, user=None, password=None, schema=None, sample_limit=0):
	"""Run read-only SAP B1 on HANA discovery for a source database.

	Credentials can be passed explicitly by trusted callers or supplied through
	site/common config as sap_hana_address, sap_hana_port, sap_hana_user,
	sap_hana_password, and sap_hana_schema.
	"""
	_require_integration_user()
	from migration.core.connectors.sap_hana import SAPHanaClient

	address = address or frappe.conf.get("sap_hana_address") or os.environ.get("SAP_HANA_ADDRESS")
	port = int(port or frappe.conf.get("sap_hana_port") or os.environ.get("SAP_HANA_PORT") or 30015)
	user = user or frappe.conf.get("sap_hana_user") or os.environ.get("SAP_HANA_USER")
	password = password or frappe.conf.get("sap_hana_password") or os.environ.get("SAP_HANA_PASSWORD")
	schema = schema or frappe.conf.get("sap_hana_schema") or os.environ.get("SAP_HANA_SCHEMA")
	sample_limit = int(sample_limit or 0)

	missing = [
		label for label, value in (
			("address", address),
			("user", user),
			("password", password),
			("schema", schema),
		)
		if not value
	]
	if missing:
		frappe.throw(f"Missing SAP HANA connection values: {', '.join(missing)}")

	client = SAPHanaClient(
		address=address,
		port=port,
		user=user,
		password=password,
		schema=schema,
	)
	try:
		result = client.discover_sap_b1(schema=schema, sample_limit=sample_limit)
	finally:
		client.close()

	return {
		"source_type": result["source_type"],
		"schema": result["schema"],
		"generated_at": result["generated_at"],
		"connection": result["connection"],
		"tables": result["tables"],
		"modules": result["modules"],
		"samples": result["samples"] if sample_limit > 0 else {},
	}


@frappe.whitelist()
def create_sap_hana_discovery_snapshot(
	connection_label=None,
	address=None,
	port=30015,
	user=None,
	password=None,
	schema=None,
	keep_latest=3,
):
	"""Persist a read-only SAP HANA discovery snapshot as Integration records."""
	_require_integration_user()
	result = discover_sap_hana_source(
		address=address,
		port=port,
		user=user,
		password=password,
		schema=schema,
		sample_limit=0,
	)
	label = connection_label or f"SAP HANA {result['schema']}"
	capabilities = {
		"source_type": result["source_type"],
		"schema": result["schema"],
		"generated_at": result["generated_at"],
		"modules": result["modules"],
		"table_count": len(result["tables"]),
	}
	doc = frappe.get_doc({
		"doctype": INTEGRATION_CONNECTION_DOCTYPE,
		"connection_label": label,
		"source_type": "SAP",
		"status": "Active" if result["connection"].get("connected") else "Error",
		"owner_user": frappe.session.user,
		"capabilities_json": _json_dumps(capabilities),
		"last_seen_at": now_datetime(),
		"last_sync_at": now_datetime(),
		"last_error": result["connection"].get("error"),
		"notes": f"Read-only SAP B1 HANA discovery for schema {result['schema']}. Credentials are not stored.",
	})
	doc.insert(ignore_permissions=True)

	created = 0
	for table_name, table_info in sorted(result["tables"].items()):
		if not table_info.get("exists"):
			continue
		raw = {
			"table": table_name,
			"description": table_info.get("description"),
			"row_count": table_info.get("row_count"),
		}
		frappe.get_doc({
			"doctype": INTEGRATION_SOURCE_OBJECT_DOCTYPE,
			"connection": doc.name,
			"object_type": "SAP Table",
			"source_id": table_name,
			"source_name": table_info.get("description") or table_name,
			"sync_status": "Seen",
			"raw_hash": _hash_text(raw),
			"normalized_hash": _hash_text(raw),
			"first_seen_at": now_datetime(),
			"last_seen_at": now_datetime(),
		}).insert(ignore_permissions=True)
		created += 1

	_cleanup_integration_connections(source_type="SAP", keep_latest=keep_latest, owner_user=frappe.session.user)
	frappe.db.commit()
	return {
		"connection_id": doc.name,
		"connection_label": doc.connection_label,
		"schema": result["schema"],
		"connected": result["connection"].get("connected"),
		"source_objects_created": created,
		"modules": result["modules"],
		"tables": result["tables"],
	}


SAP_MASTER_TABLES = {
	"SAP Branch": {
		"table": "OBPL",
		"id": "BPLId",
		"name": "BPLName",
		"columns": ["BPLId", "BPLName", "MainBPL", "DflWhs", "FederalTaxID", "State", "Disabled"],
	},
	"SAP Account": {
		"table": "OACT",
		"id": "AcctCode",
		"name": "AcctName",
		"columns": ["AcctCode", "AcctName", "FatherNum", "GroupMask", "Postable", "ActType", "ActCurr", "CurrTotal"],
	},
	"SAP BP Group": {
		"table": "OCRG",
		"id": "GroupCode",
		"name": "GroupName",
		"columns": ["GroupCode", "GroupName", "GroupType"],
	},
	"SAP Business Partner": {
		"table": "OCRD",
		"id": "CardCode",
		"name": "CardName",
		"columns": [
			"CardCode", "CardName", "CardType", "GroupCode", "Phone1", "Cellular",
			"E_Mail", "City", "State1", "Country", "Currency", "Balance",
			"CreditLine", "LicTradNum", "validFor", "frozenFor", "CreateDate", "UpdateDate",
		],
	},
	"SAP BP Address": {
		"table": "CRD1",
		"id": ["CardCode", "Address", "AdresType"],
		"name": "Address",
		"columns": [
			"CardCode", "Address", "AdresType", "Street", "Block", "ZipCode",
			"City", "County", "Country", "State", "GSTRegnNo", "GSTType",
		],
	},
	"SAP Item Group": {
		"table": "OITB",
		"id": "ItmsGrpCod",
		"name": "ItmsGrpNam",
		"columns": ["ItmsGrpCod", "ItmsGrpNam"],
	},
	"SAP UOM": {
		"table": "OUOM",
		"id": "UomEntry",
		"name": "UomName",
		"columns": ["UomEntry", "UomCode", "UomName"],
	},
	"SAP Warehouse": {
		"table": "OWHS",
		"id": "WhsCode",
		"name": "WhsName",
		"columns": ["WhsCode", "WhsName", "Location", "DropShip", "Nettable", "Street", "City", "Country", "State", "ZipCode", "Inactive"],
	},
	"SAP Item": {
		"table": "OITM",
		"id": "ItemCode",
		"name": "ItemName",
		"columns": [
			"ItemCode", "ItemName", "FrgnName", "ItmsGrpCod", "ItemType",
			"InvntItem", "SellItem", "PrchseItem", "ManBtchNum", "ManSerNum",
			"OnHand", "IsCommited", "OnOrder", "BuyUnitMsr", "SalUnitMsr",
			"InvntryUom", "AvgPrice", "DfltWH", "GSTRelevnt", "CreateDate", "UpdateDate",
		],
	},
}


SAP_MASTER_TARGETS = {
	"SAP Branch": {"label": "Branches", "doctypes": ["Branch"]},
	"SAP Account": {"label": "Chart of Accounts", "doctypes": ["Account"]},
	"SAP BP Group": {"label": "Business Partner Groups", "doctypes": ["Customer Group", "Supplier Group"]},
	"SAP Business Partner": {"label": "Business Partners", "doctypes": ["Customer", "Supplier"]},
	"SAP BP Address": {"label": "Business Partner Addresses", "doctypes": ["Address"]},
	"SAP Item Group": {"label": "Item Groups", "doctypes": ["Item Group"]},
	"SAP UOM": {"label": "Units of Measure", "doctypes": ["UOM"]},
	"SAP Warehouse": {"label": "Warehouses", "doctypes": ["Warehouse"]},
	"SAP Item": {"label": "Items", "doctypes": ["Item"]},
}


def _target_table_exists(doctype):
	return frappe.db.table_exists(doctype)


def _target_exists(doctype, filters=None, name=None):
	if not _target_table_exists(doctype):
		return False
	try:
		if name and frappe.db.exists(doctype, name):
			return True
		return bool(filters and frappe.db.exists(doctype, filters))
	except Exception:
		return False


def _sap_clean_target_name(*values):
	for value in values:
		text = str(value or "").strip()
		if text:
			return text
	return ""


def _sap_plan_candidates(record_type, normalized, company=None):
	record = normalized.get("record") or {}
	company_abbr = ""
	if company:
		try:
			company_abbr = frappe.db.get_value("Company", company, "abbr") or ""
		except Exception:
			company_abbr = ""

	if record_type == "SAP Branch":
		name = _sap_clean_target_name(record.get("BPLName"), normalized.get("source_id"))
		return [{
			"doctype": "Branch",
			"target_name": name,
			"exists": _target_exists("Branch", name=name, filters={"branch": name}),
		}]

	if record_type == "SAP Account":
		account_code = _sap_clean_target_name(record.get("AcctCode"), normalized.get("source_id"))
		account_name = _sap_clean_target_name(record.get("AcctName"), account_code)
		target_name = f"{account_name} - {company_abbr}" if company_abbr else account_name
		return [{
			"doctype": "Account",
			"target_name": target_name,
			"exists": _target_exists("Account", name=target_name, filters={"account_number": account_code}),
		}]

	if record_type == "SAP BP Group":
		group_type = str(record.get("GroupType") or "").upper()
		target_doctype = "Supplier Group" if group_type == "S" else "Customer Group"
		name = _sap_clean_target_name(record.get("GroupName"), normalized.get("source_id"))
		return [{
			"doctype": target_doctype,
			"target_name": name,
			"exists": _target_exists(target_doctype, name=name),
		}]

	if record_type == "SAP Business Partner":
		card_type = str(record.get("CardType") or "").upper()
		if card_type == "S":
			target_doctype = "Supplier"
		elif card_type == "C":
			target_doctype = "Customer"
		else:
			return []
		name = _sap_clean_target_name(record.get("CardName"), record.get("CardCode"), normalized.get("source_id"))
		return [{
			"doctype": target_doctype,
			"target_name": name,
			"exists": _target_exists(target_doctype, name=name),
		}]

	if record_type == "SAP BP Address":
		name = _sap_clean_target_name(
			" ".join(str(record.get(key) or "").strip() for key in ("CardCode", "Address", "AdresType")).strip(),
			normalized.get("source_id"),
		)
		return [{
			"doctype": "Address",
			"target_name": name,
			"exists": _target_exists("Address", name=name),
		}]

	if record_type == "SAP Item Group":
		name = _sap_clean_target_name(record.get("ItmsGrpNam"), normalized.get("source_id"))
		return [{
			"doctype": "Item Group",
			"target_name": name,
			"exists": _target_exists("Item Group", name=name),
		}]

	if record_type == "SAP UOM":
		name = _sap_clean_target_name(record.get("UomCode"), record.get("UomName"), normalized.get("source_id"))
		return [{
			"doctype": "UOM",
			"target_name": name,
			"exists": _target_exists("UOM", name=name, filters={"uom_name": name}),
		}]

	if record_type == "SAP Warehouse":
		warehouse_name = _sap_clean_target_name(record.get("WhsName"), record.get("WhsCode"), normalized.get("source_id"))
		target_name = f"{warehouse_name} - {company_abbr}" if company_abbr else warehouse_name
		return [{
			"doctype": "Warehouse",
			"target_name": target_name,
			"exists": _target_exists("Warehouse", name=target_name, filters={"warehouse_name": warehouse_name}),
		}]

	if record_type == "SAP Item":
		item_code = _sap_clean_target_name(record.get("ItemCode"), normalized.get("source_id"))
		return [{
			"doctype": "Item",
			"target_name": item_code,
			"exists": _target_exists("Item", name=item_code, filters={"item_code": item_code}),
		}]

	return []


def _sap_parse_selected_record_types(selected_record_types_json=None):
	selected = _parse_json(selected_record_types_json, default=None)
	if not selected:
		return list(SAP_MASTER_TABLES)
	if not isinstance(selected, list):
		frappe.throw("selected_record_types_json must be a JSON array")
	return [record_type for record_type in selected if record_type in SAP_MASTER_TABLES]


@frappe.whitelist()
def plan_sap_hana_master_migration(connection_id=None, company=None, selected_record_types_json=None, sample_limit=5):
	"""Build a non-writing SAP B1 master-data migration plan for Sena ERP."""
	connection = _get_readable_integration_connection(connection_id)
	if connection.source_type != "SAP":
		frappe.throw("connection_id must refer to a SAP connection")

	record_types = _sap_parse_selected_record_types(selected_record_types_json)
	sample_limit = max(1, min(int(sample_limit or 5), 20))
	rows = frappe.get_all(
		INTEGRATION_NORMALIZED_RECORD_DOCTYPE,
		filters={
			"connection": connection.name,
			"record_type": ["in", record_types],
			"sync_status": ["!=", "Deleted"],
		},
		fields=["record_type", "source_id", "record_name", "normalized_json"],
		order_by="record_type asc, source_id asc",
		limit_page_length=0,
	)

	by_type = {}
	target_doctypes = {}
	for row in rows:
		normalized = _safe_json_object(row.normalized_json)
		if _is_blank_source_record(row.record_type, row.source_id, row.record_name, normalized):
			continue
		summary = by_type.setdefault(row.record_type, {
			"record_type": row.record_type,
			"label": SAP_MASTER_TARGETS.get(row.record_type, {}).get("label", row.record_type),
			"source_count": 0,
			"target_doctypes": SAP_MASTER_TARGETS.get(row.record_type, {}).get("doctypes", []),
			"planned_count": 0,
			"existing_count": 0,
			"create_count": 0,
			"skipped_count": 0,
			"samples": [],
		})
		summary["source_count"] += 1
		candidates = _sap_plan_candidates(row.record_type, normalized, company=company)
		if not candidates:
			summary["skipped_count"] += 1
			continue
		for candidate in candidates:
			doctype = candidate["doctype"]
			target_doctypes.setdefault(doctype, {"doctype": doctype, "planned_count": 0, "existing_count": 0, "create_count": 0})
			target_doctypes[doctype]["planned_count"] += 1
			summary["planned_count"] += 1
			if candidate["exists"]:
				target_doctypes[doctype]["existing_count"] += 1
				summary["existing_count"] += 1
			else:
				target_doctypes[doctype]["create_count"] += 1
				summary["create_count"] += 1
		if len(summary["samples"]) < sample_limit:
			summary["samples"].append({
				"source_id": row.source_id,
				"source_name": row.record_name,
				"targets": candidates,
			})

	for record_type in record_types:
		by_type.setdefault(record_type, {
			"record_type": record_type,
			"label": SAP_MASTER_TARGETS.get(record_type, {}).get("label", record_type),
			"source_count": 0,
			"target_doctypes": SAP_MASTER_TARGETS.get(record_type, {}).get("doctypes", []),
			"planned_count": 0,
			"existing_count": 0,
			"create_count": 0,
			"skipped_count": 0,
			"samples": [],
		})

	summaries = [by_type[record_type] for record_type in record_types]
	return {
		"connection_id": connection.name,
		"connection_label": connection.connection_label,
		"company": company,
		"dry_run": True,
		"record_types": summaries,
		"target_doctypes": sorted(target_doctypes.values(), key=lambda item: item["doctype"]),
		"totals": {
			"source_count": sum(item["source_count"] for item in summaries),
			"planned_count": sum(item["planned_count"] for item in summaries),
			"existing_count": sum(item["existing_count"] for item in summaries),
			"create_count": sum(item["create_count"] for item in summaries),
			"skipped_count": sum(item["skipped_count"] for item in summaries),
		},
	}


def _sap_existing_columns(client, schema, table):
	return {row["COLUMN_NAME"] for row in client.describe_table(schema, table)}


def _sap_record_id(row, id_columns):
	if isinstance(id_columns, str):
		return str(row.get(id_columns) or "")
	return ":".join(str(row.get(column) or "") for column in id_columns)


def _sap_fetch_records(client, schema, definition, limit=0):
	from migration.core.connectors.sap_hana import _quote_identifier

	table = definition["table"]
	existing = _sap_existing_columns(client, schema, table)
	columns = [column for column in definition["columns"] if column in existing]
	for required in definition["id"] if isinstance(definition["id"], list) else [definition["id"]]:
		if required in existing and required not in columns:
			columns.insert(0, required)
	name_column = definition.get("name")
	if name_column in existing and name_column not in columns:
		columns.append(name_column)
	if not columns:
		return []
	limit_clause = f" LIMIT {max(1, int(limit))}" if int(limit or 0) > 0 else ""
	sql = (
		f"SELECT {', '.join(_quote_identifier(column) for column in columns)} "
		f"FROM {_quote_identifier(schema)}.{_quote_identifier(table)}{limit_clause}"
	)
	return client.query(sql)


def _get_or_create_sap_table_source_object(connection_id, table_name, description=None):
	existing = frappe.get_all(
		INTEGRATION_SOURCE_OBJECT_DOCTYPE,
		filters={"connection": connection_id, "object_type": "SAP Table", "source_id": table_name},
		fields=["name"],
		limit_page_length=1,
	)
	if existing:
		return existing[0].name
	doc = frappe.get_doc({
		"doctype": INTEGRATION_SOURCE_OBJECT_DOCTYPE,
		"connection": connection_id,
		"object_type": "SAP Table",
		"source_id": table_name,
		"source_name": description or table_name,
		"sync_status": "Seen",
		"first_seen_at": now_datetime(),
		"last_seen_at": now_datetime(),
	})
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def extract_sap_hana_master_data(connection_id=None, address=None, port=30015, user=None, password=None, schema=None, limit_per_type=0):
	"""Extract SAP B1 master data into normalized Integration records."""
	_require_integration_user()
	from migration.core.connectors.sap_hana import SAPHanaClient

	if connection_id:
		connection = _get_readable_integration_connection(connection_id)
		if connection.source_type != "SAP":
			frappe.throw("connection_id must refer to a SAP connection")
	else:
		snapshot = create_sap_hana_discovery_snapshot(
			connection_label="SAP HANA Master Data",
			address=address,
			port=port,
			user=user,
			password=password,
			schema=schema,
			keep_latest=3,
		)
		connection_id = snapshot["connection_id"]
		connection = frappe.get_doc(INTEGRATION_CONNECTION_DOCTYPE, connection_id)

	capabilities = _safe_json_object(connection.capabilities_json)
	schema = schema or capabilities.get("schema") or frappe.conf.get("sap_hana_schema") or os.environ.get("SAP_HANA_SCHEMA")
	address = address or frappe.conf.get("sap_hana_address") or os.environ.get("SAP_HANA_ADDRESS")
	port = int(port or frappe.conf.get("sap_hana_port") or os.environ.get("SAP_HANA_PORT") or 30015)
	user = user or frappe.conf.get("sap_hana_user") or os.environ.get("SAP_HANA_USER")
	password = password or frappe.conf.get("sap_hana_password") or os.environ.get("SAP_HANA_PASSWORD")

	missing = [
		label for label, value in (
			("address", address),
			("user", user),
			("password", password),
			("schema", schema),
		)
		if not value
	]
	if missing:
		frappe.throw(f"Missing SAP HANA connection values: {', '.join(missing)}")

	record_types = list(SAP_MASTER_TABLES)
	for record_type in record_types:
		frappe.db.delete(INTEGRATION_NORMALIZED_RECORD_DOCTYPE, {"connection": connection_id, "record_type": record_type})

	client = SAPHanaClient(address=address, port=port, user=user, password=password, schema=schema)
	inserted = {}
	now = now_datetime()
	try:
		for record_type, definition in SAP_MASTER_TABLES.items():
			source_object = _get_or_create_sap_table_source_object(connection_id, definition["table"], record_type)
			rows = _sap_fetch_records(client, schema, definition, limit=limit_per_type)
			for row in rows:
				source_id = _sap_record_id(row, definition["id"])
				if not source_id:
					continue
				normalized = {
					"source_type": "SAP",
					"source_system": "SAP Business One on HANA",
					"schema": schema,
					"table": definition["table"],
					"record_type": record_type,
					"source_id": source_id,
					"record": row,
				}
				name_column = definition.get("name")
				record_name = str(row.get(name_column) or source_id)
				normalized_hash = _hash_text(normalized)
				frappe.get_doc({
					"doctype": INTEGRATION_NORMALIZED_RECORD_DOCTYPE,
					"connection": connection_id,
					"source_object": source_object,
					"record_type": record_type,
					"source_id": source_id,
					"record_name": record_name,
					"sync_status": "Active",
					"normalized_hash": normalized_hash,
					"extracted_at": now,
					"first_seen_at": now,
					"last_seen_at": now,
					"normalized_json": _json_dumps(normalized),
				}).insert(ignore_permissions=True)
			inserted[record_type] = len(rows)
		connection.status = "Active"
		connection.last_seen_at = now
		connection.last_sync_at = now
		connection.last_error = None
		connection.save(ignore_permissions=True)
		frappe.db.commit()
	finally:
		client.close()

	return {
		"connection_id": connection_id,
		"schema": schema,
		"inserted": inserted,
		"total_inserted": sum(inserted.values()),
	}


def _cleanup_integration_connections(source_type="Tally", keep_latest=3, owner_user=None, dry_run=False):
	keep_latest = max(int(keep_latest or 0), 0)
	filters = {"source_type": source_type or "Tally"}
	if owner_user:
		filters["owner_user"] = owner_user

	rows = frappe.get_all(
		INTEGRATION_CONNECTION_DOCTYPE,
		filters=filters,
		fields=["name", "modified"],
		order_by="modified desc",
		limit_page_length=500,
	)
	to_keep = [row.name for row in rows[:keep_latest]]
	to_delete = [row.name for row in rows[keep_latest:]]
	counts = {}
	if not dry_run:
		for connection_id in to_delete:
			counts[connection_id] = _delete_integration_connection_snapshot(connection_id)
		frappe.db.commit()
	return {
		"source_type": source_type or "Tally",
		"kept": to_keep,
		"deleted": to_delete,
		"counts": counts,
	}


@frappe.whitelist()
def get_integration_connection_status(connection_id=None):
	"""Return a connection with checkpoint and object-count summaries."""
	if not connection_id:
		frappe.throw("connection_id is required")

	doc = frappe.get_doc(INTEGRATION_CONNECTION_DOCTYPE, connection_id)
	if doc.owner_user and doc.owner_user != frappe.session.user and "System Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw("Not permitted", frappe.PermissionError)

	checkpoints = frappe.get_all(
		INTEGRATION_CHECKPOINT_DOCTYPE,
		filters={"connection": connection_id},
		fields=[
			"name", "stream_name", "object_type", "partition_key", "status",
			"last_success_at", "last_attempt_at", "error_count", "last_error",
		],
		order_by="stream_name asc",
		limit_page_length=200,
	)
	count_rows = frappe.db.sql(
		"""
		select object_type, sync_status, count(*) as count
		from `tabIntegration Source Object`
		where connection = %s
		group by object_type, sync_status
		""",
		(connection_id,),
		as_dict=True,
	)
	normalized_count_rows = frappe.db.sql(
		"""
		select record_type, sync_status, count(*) as count
		from `tabIntegration Normalized Source Record`
		where connection = %s
		group by record_type, sync_status
		""",
		(connection_id,),
		as_dict=True,
	)
	return {
		"connection": doc.as_dict(),
		"checkpoints": checkpoints,
		"object_counts": count_rows,
		"normalized_counts": normalized_count_rows,
	}


get_tyd_connection_status = get_integration_connection_status


@frappe.whitelist()
def list_integration_sources(source_key=None):
	"""Return external-source connections that Talk To Your Data can query."""
	_require_integration_user()
	filters = {"owner_user": frappe.session.user}
	if source_key:
		filters["source_type"] = _source_type_from_key(source_key)

	rows = frappe.get_all(
		INTEGRATION_CONNECTION_DOCTYPE,
		filters=filters,
		fields=[
			"name", "connection_label", "source_type", "status", "tally_company_name",
			"last_seen_at", "last_sync_at", "last_error", "modified",
		],
		order_by="modified desc",
		limit_page_length=100,
	)
	for row in rows:
		row["source_key"] = _source_key_from_type(row.source_type)
		row["adapter"] = "integration_record"
		row["source_kind"] = "external_integration"
		row["title"] = row.connection_label or row.tally_company_name or row.name
		row["freshness"] = {
			"last_seen_at": row.last_seen_at,
			"last_sync_at": row.last_sync_at,
			"status": row.status,
			"last_error": row.last_error,
		}
		row["record_type_counts"] = frappe.db.sql(
			"""
			select record_type, count(*) as count
			from `tabIntegration Normalized Source Record`
			where connection = %s
			group by record_type
			order by count desc
			""",
			(row.name,),
			as_dict=True,
		)
	return rows


@frappe.whitelist()
def get_integration_source_schema(connection_id=None, source_key=None, sample_limit=5):
	"""Expose a governed schema summary over Integration normalized records."""
	_require_integration_user()
	if not connection_id:
		rows = list_integration_sources(source_key=source_key)
		connection_id = rows[0].name if rows else None
	if not connection_id:
		return {"connection": None, "record_types": []}

	doc = _get_readable_integration_connection(connection_id)
	sample_limit = max(1, min(int(sample_limit or 5), 20))
	counts = frappe.db.sql(
		"""
		select record_type, count(*) as count, max(last_seen_at) as last_seen_at
		from `tabIntegration Normalized Source Record`
		where connection = %s
			and not (coalesce(record_name, '') = '' and source_id = concat(record_type, ':'))
		group by record_type
		order by record_type asc
		""",
		(connection_id,),
		as_dict=True,
	)
	record_types = []
	for count in counts:
		samples = frappe.get_all(
			INTEGRATION_NORMALIZED_RECORD_DOCTYPE,
			filters={"connection": connection_id, "record_type": count.record_type, "sync_status": ["!=", "Deleted"]},
			fields=["name", "source_id", "record_name", "normalized_json", "last_seen_at"],
			order_by="last_seen_at desc",
			limit_page_length=sample_limit,
		)
		schema_fields = {}
		sample_rows = []
		for sample in samples:
			normalized = _safe_json_object(sample.normalized_json)
			if _is_blank_source_record(count.record_type, sample.source_id, sample.record_name, normalized):
				continue
			_merge_schema_fields(schema_fields, normalized)
			sample_rows.append({
				"name": sample.name,
				"source_id": sample.source_id,
				"record_name": sample.record_name,
				"last_seen_at": sample.last_seen_at,
				"record": normalized,
			})
		record_types.append({
			"record_type": count.record_type,
			"count": count.count,
			"last_seen_at": count.last_seen_at,
			"fields": sorted(schema_fields.values(), key=lambda field: field["field"]),
			"samples": sample_rows,
		})

	return {
		"connection": {
			"name": doc.name,
			"connection_label": doc.connection_label,
			"source_type": doc.source_type,
			"source_key": _source_key_from_type(doc.source_type),
			"status": doc.status,
			"last_seen_at": doc.last_seen_at,
			"last_sync_at": doc.last_sync_at,
		},
		"adapter": "integration_record",
		"record_types": record_types,
	}


@frappe.whitelist()
def query_integration_records(connection_id=None, record_type=None, filters_json=None, fields_json=None, limit=50):
	"""Query normalized Integration records without exposing arbitrary SQL."""
	_get_readable_integration_connection(connection_id)
	limit = max(1, min(int(limit or 50), 200))
	filters = _parse_json(filters_json, default=[]) or []
	fields = _parse_json(fields_json, default=[]) or []
	if not isinstance(filters, list):
		frappe.throw("filters_json must be a JSON array")
	if not isinstance(fields, list):
		frappe.throw("fields_json must be a JSON array")

	db_filters = {"connection": connection_id, "sync_status": ["!=", "Deleted"]}
	if record_type:
		db_filters["record_type"] = record_type
	rows = frappe.get_all(
		INTEGRATION_NORMALIZED_RECORD_DOCTYPE,
		filters=db_filters,
		fields=["name", "record_type", "source_id", "record_name", "normalized_json", "last_seen_at"],
		order_by="last_seen_at desc",
		limit_page_length=min(limit * 5, 500),
	)
	results = []
	for row in rows:
		record = _safe_json_object(row.normalized_json)
		if _is_blank_source_record(row.record_type, row.source_id, row.record_name, record):
			continue
		if not _record_matches_filters(record, filters):
			continue
		if fields:
			record = {field: record.get(field) for field in fields}
		results.append({
			"name": row.name,
			"record_type": row.record_type,
			"source_id": row.source_id,
			"record_name": row.record_name,
			"last_seen_at": row.last_seen_at,
			"record": record,
		})
		if len(results) >= limit:
			break
	return {"rows": results, "count": len(results), "adapter": "integration_record"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def claim_integration_bridge_pairing(pairing_code=None, bridge_id=None, host="localhost", port=9000, capabilities_json=None):
	"""Claim a pending pairing from the local bridge.

	The bridge receives a durable bridge_token only after proving it has the
	user-visible pairing code.
	"""
	if not pairing_code:
		frappe.throw("pairing_code is required")

	rows = frappe.get_all(
		INTEGRATION_CONNECTION_DOCTYPE,
		filters={"pairing_code": pairing_code, "status": "Pairing"},
		fields=["name", "pairing_expires_at"],
		limit=1,
	)
	if not rows:
		frappe.throw("Pairing code not found")
	if rows[0].pairing_expires_at and rows[0].pairing_expires_at < now_datetime():
		frappe.throw("Pairing code expired")

	doc = frappe.get_doc(INTEGRATION_CONNECTION_DOCTYPE, rows[0].name)
	token = _get_password(doc, "bridge_token") or secrets.token_urlsafe(32)
	capabilities = _parse_json(capabilities_json, default={}) or {}
	doc.status = "Active"
	doc.bridge_id = bridge_id or doc.bridge_id or f"bridge-{frappe.generate_hash(length=8)}"
	doc.tally_host = host or "localhost"
	doc.tally_port = int(port or 9000)
	doc.capabilities_json = _json_dumps(capabilities)
	doc.last_seen_at = now_datetime()
	doc.bridge_token = token
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"connection_id": doc.name,
		"bridge_token": token,
		"status": doc.status,
	}


claim_tyd_bridge_pairing = claim_integration_bridge_pairing


@frappe.whitelist(allow_guest=True, methods=["POST"])
def bridge_heartbeat(connection_id=None, bridge_token=None, status="Active", tally_company_name=None, tally_version=None, capabilities_json=None, last_error=None):
	"""Heartbeat/update endpoint for the local bridge."""
	doc = _verify_bridge(connection_id, bridge_token)
	capabilities = _parse_json(capabilities_json, default=None)

	doc.status = status or "Active"
	if tally_company_name is not None:
		doc.tally_company_name = tally_company_name
	if tally_version is not None:
		doc.tally_version = tally_version
	if capabilities is not None:
		doc.capabilities_json = _json_dumps(capabilities)
	if last_error is not None:
		doc.last_error = last_error
	doc.last_seen_at = now_datetime()
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	return {"ok": True, "connection_id": doc.name, "status": doc.status}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def upsert_sync_checkpoint(connection_id=None, bridge_token=None, stream_name=None, object_type=None, partition_key="", status="Running", cursor=None, error=None):
	"""Create/update the durable cursor for one source stream."""
	_verify_bridge(connection_id, bridge_token)
	if not stream_name or not object_type:
		frappe.throw("stream_name and object_type are required")

	filters = {
		"connection": connection_id,
		"stream_name": stream_name,
		"object_type": object_type,
		"partition_key": partition_key or "",
	}
	existing = frappe.get_all(INTEGRATION_CHECKPOINT_DOCTYPE, filters=filters, fields=["name"], limit=1)
	doc = frappe.get_doc(INTEGRATION_CHECKPOINT_DOCTYPE, existing[0].name) if existing else frappe.new_doc(INTEGRATION_CHECKPOINT_DOCTYPE)
	doc.update(filters)
	doc.status = status or "Running"
	doc.last_attempt_cursor = cursor or ""
	doc.last_attempt_at = now_datetime()
	if doc.status == "Success":
		doc.last_success_cursor = cursor or ""
		doc.last_success_at = now_datetime()
		doc.last_error = ""
	elif error:
		doc.error_count = int(doc.error_count or 0) + 1
		doc.last_error = str(error)
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "checkpoint": doc.name}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def ingest_source_objects(connection_id=None, bridge_token=None, objects_json=None):
	"""Upsert source-object metadata and optional raw payloads from the bridge."""
	_verify_bridge(connection_id, bridge_token)
	objects = _parse_json(objects_json, default=[]) or []
	if not isinstance(objects, list):
		frappe.throw("objects_json must be a JSON array")

	now = now_datetime()
	created = updated = payloads = normalized_records = 0
	for item in objects:
		object_type = item.get("object_type")
		source_id = item.get("source_id") or item.get("source_guid") or item.get("source_name")
		if not object_type or not source_id:
			continue
		normalized = _normalized_record_from_item(item, object_type, source_id)
		if _is_blank_source_record(object_type, source_id, item.get("source_name"), normalized):
			continue
		normalized_hash = item.get("normalized_hash") or _hash_text(normalized)
		filters = {"connection": connection_id, "object_type": object_type, "source_id": source_id}
		existing = frappe.get_all(INTEGRATION_SOURCE_OBJECT_DOCTYPE, filters=filters, fields=["name"], limit=1)
		doc = frappe.get_doc(INTEGRATION_SOURCE_OBJECT_DOCTYPE, existing[0].name) if existing else frappe.new_doc(INTEGRATION_SOURCE_OBJECT_DOCTYPE)
		if doc.is_new():
			doc.connection = connection_id
			doc.object_type = object_type
			doc.source_id = source_id
			doc.first_seen_at = now
			created += 1
		else:
			updated += 1
		doc.source_guid = item.get("source_guid") or ""
		doc.source_name = item.get("source_name") or ""
		doc.source_alter_id = item.get("source_alter_id") or ""
		doc.raw_hash = item.get("raw_hash") or _hash_text(item.get("raw_payload", ""))
		doc.normalized_hash = normalized_hash
		doc.sync_status = item.get("sync_status") or "Seen"
		doc.last_seen_at = now
		if doc.is_new():
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)

		normalized_text = _json_dumps(normalized)
		normalized_filters = {
			"connection": connection_id,
			"source_object": doc.name,
			"record_type": object_type,
			"source_id": source_id,
		}
		existing_normalized = frappe.get_all(
			INTEGRATION_NORMALIZED_RECORD_DOCTYPE,
			filters=normalized_filters,
			fields=["name"],
			limit=1,
		)
		normalized_doc = (
			frappe.get_doc(INTEGRATION_NORMALIZED_RECORD_DOCTYPE, existing_normalized[0].name)
			if existing_normalized else frappe.new_doc(INTEGRATION_NORMALIZED_RECORD_DOCTYPE)
		)
		if normalized_doc.is_new():
			normalized_doc.update(normalized_filters)
			normalized_doc.first_seen_at = now
		normalized_doc.record_name = item.get("source_name") or normalized.get("name") or ""
		normalized_doc.sync_status = "Active" if not item.get("deleted_at") else "Deleted"
		normalized_doc.normalized_hash = normalized_hash
		normalized_doc.extracted_at = now
		normalized_doc.last_seen_at = now
		normalized_doc.normalized_json = normalized_text
		if normalized_doc.is_new():
			normalized_doc.insert(ignore_permissions=True)
		else:
			normalized_doc.save(ignore_permissions=True)
		normalized_records += 1

		raw_payload = item.get("raw_payload")
		payload_text = raw_payload if isinstance(raw_payload, str) else _json_dumps(raw_payload)
		if payload_text and payload_text.strip():
			payload_doc = frappe.get_doc({
				"doctype": INTEGRATION_RAW_PAYLOAD_DOCTYPE,
				"connection": connection_id,
				"source_object": doc.name,
				"object_type": object_type,
				"source_id": source_id,
				"payload_format": item.get("payload_format") or "xml",
				"payload_hash": doc.raw_hash,
				"extracted_at": now,
				"payload": payload_text,
			})
			payload_doc.insert(ignore_permissions=True)
			payloads += 1

	frappe.db.set_value(INTEGRATION_CONNECTION_DOCTYPE, connection_id, "last_sync_at", now, update_modified=True)
	frappe.db.commit()
	return {"ok": True, "created": created, "updated": updated, "payloads": payloads, "normalized_records": normalized_records}


@frappe.whitelist()
def test_tally_connection(host="localhost", port=9000):
	"""Test connectivity to TallyPrime."""
	from migration.core.connectors.tally import TallyClient

	client = TallyClient(host=host, port=int(port))
	return client.test_connection()


# ---------------------------------------------------------------------------
# Data fetch (synchronous — ~30s for 66MB, on the edge of timeout)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def fetch_tally_data(host="localhost", port=9000):
	"""Fetch and parse all master data from TallyPrime.

	Returns summary counts and the parsed data for preview.
	"""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import (
		parse_list_of_accounts,
		parse_trial_balance,
		parse_stock_summary,
	)
	from migration.core.transformers.tally_to_erpnext import classify_ledger

	client = TallyClient(host=host, port=int(port))

	accounts_xml = client.get_list_of_accounts()
	data = parse_list_of_accounts(accounts_xml)

	groups_by_name = {g["name"]: g for g in data["groups"]}
	customers = [l for l in data["ledgers"] if classify_ledger(l, groups_by_name) == "customer"]
	suppliers = [l for l in data["ledgers"] if classify_ledger(l, groups_by_name) == "supplier"]
	accounts = [l for l in data["ledgers"] if classify_ledger(l, groups_by_name) == "account"]

	tb_xml = client.get_trial_balance()
	trial_balance = parse_trial_balance(tb_xml)

	stock_xml = client.get_stock_summary()
	stock_items = parse_stock_summary(stock_xml)

	return {
		"company": data.get("company", ""),
		"groups_count": len(data["groups"]),
		"ledgers_count": len(data["ledgers"]),
		"accounts_count": len(accounts),
		"customers_count": len(customers),
		"suppliers_count": len(suppliers),
		"currencies_count": len(data["currencies"]),
		"trial_balance_entries": len(trial_balance),
		"stock_items_count": len(stock_items),
		"sample_customers": [{"name": c["name"], "gstin": c.get("gstin", ""), "state": c.get("state", "")} for c in customers[:10]],
		"sample_suppliers": [{"name": s["name"], "parent": s.get("parent", "")} for s in suppliers[:10]],
		"sample_accounts": [{"name": a["name"], "parent": a.get("parent", "")} for a in accounts[:10]],
		"sample_stock": stock_items[:10],
	}


@frappe.whitelist()
def preview_migration(host="localhost", port=9000, company_name="", company_abbr=""):
	"""Preview what the migration will create without making changes."""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import parse_list_of_accounts
	from migration.core.transformers.tally_to_erpnext import transform_all

	if not company_name or not company_abbr:
		frappe.throw("company_name and company_abbr are required")

	client = TallyClient(host=host, port=int(port))
	accounts_xml = client.get_list_of_accounts()
	tally_data = parse_list_of_accounts(accounts_xml)

	transformed = transform_all(tally_data, company_name, company_abbr)

	return {
		"accounts_count": len(transformed["accounts"]),
		"customers_count": len(transformed["customers"]),
		"suppliers_count": len(transformed["suppliers"]),
		"item_groups_count": len(transformed["item_groups"]),
		"items_count": len(transformed["items"]),
		"warehouses_count": len(transformed["warehouses"]),
		"sample_accounts": transformed["accounts"][:20],
		"sample_customers": transformed["customers"][:10],
		"sample_suppliers": transformed["suppliers"][:10],
	}


def _fetch_tally_master_data(host, port):
	"""Fetch Tally master data collections used by the master-data migration."""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import parse_collection, parse_list_of_accounts

	client = TallyClient(host=host, port=int(port))
	accounts_xml = client.get_list_of_accounts()
	tally_data = parse_list_of_accounts(accounts_xml)
	warnings = []

	collections = [
		("stock_groups", "Stock Group", "STOCKGROUP"),
		("stock_items", "Stock Item", "STOCKITEM"),
		("godowns", "Godown", "GODOWN"),
	]
	for key, collection_type, tag_name in collections:
		try:
			xml = client.get_collection(collection_type)
			tally_data[key] = parse_collection(xml, tag_name)
		except Exception as exc:
			tally_data[key] = []
			warnings.append(f"{collection_type}: {exc}")

	return tally_data, warnings


def _master_doc_status(transformed, company_name):
	"""Return existing/missing counts for transformed master docs."""
	def summarize(key, doctype, label_fn, exists_fn):
		rows = transformed.get(key, [])
		existing = []
		missing = []
		for row in rows:
			label = label_fn(row)
			if not label:
				continue
			if exists_fn(row, label):
				existing.append(label)
			else:
				missing.append(label)
		return {
			"doctype": doctype,
			"total": len(rows),
			"existing_count": len(existing),
			"missing_count": len(missing),
			"sample_existing": existing[:10],
			"sample_missing": missing[:10],
		}

	return {
		"accounts": summarize(
			"accounts",
			"Account",
			lambda row: row.get("account_name"),
			lambda row, label: frappe.db.exists("Account", {"account_name": label, "company": company_name}),
		),
		"customers": summarize(
			"customers",
			"Customer",
			lambda row: row.get("customer_name"),
			lambda row, label: frappe.db.exists("Customer", {"customer_name": label}),
		),
		"suppliers": summarize(
			"suppliers",
			"Supplier",
			lambda row: row.get("supplier_name"),
			lambda row, label: frappe.db.exists("Supplier", {"supplier_name": label}),
		),
		"item_groups": summarize(
			"item_groups",
			"Item Group",
			lambda row: row.get("item_group_name"),
			lambda row, label: frappe.db.exists("Item Group", label),
		),
		"items": summarize(
			"items",
			"Item",
			lambda row: row.get("item_code"),
			lambda row, label: frappe.db.exists("Item", label),
		),
		"warehouses": summarize(
			"warehouses",
			"Warehouse",
			lambda row: row.get("warehouse_name"),
			lambda row, label: frappe.db.exists("Warehouse", {"warehouse_name": label, "company": company_name}),
		),
	}


def _compact_import_summary(summary, sample_size=20):
	"""Keep progress payloads small while preserving enough detail for review."""
	details = summary.get("details") or {}
	compact = {
		"created": summary.get("created", 0),
		"skipped": summary.get("skipped", 0),
		"errors": summary.get("errors", 0),
		"details": {},
	}
	for key in ("created", "skipped", "errors"):
		rows = details.get(key) or []
		compact["details"][key] = rows[:sample_size]
		compact[f"{key}_sample_count"] = min(len(rows), sample_size)
	return compact


@frappe.whitelist()
def preview_master_data_migration(host="localhost", port=9000, company_name="", company_abbr=""):
	"""Preview Tally master data, including stock masters, without writing ERPNext docs."""
	from migration.core.transformers.tally_to_erpnext import transform_all

	if not company_name or not company_abbr:
		frappe.throw("company_name and company_abbr are required")

	tally_data, warnings = _fetch_tally_master_data(host, int(port))
	transformed = transform_all(tally_data, company_name, company_abbr)

	return {
		"source_company": tally_data.get("company", ""),
		"warnings": warnings,
		"source_counts": {
			"groups": len(tally_data.get("groups", [])),
			"ledgers": len(tally_data.get("ledgers", [])),
			"stock_groups": len(tally_data.get("stock_groups", [])),
			"stock_items": len(tally_data.get("stock_items", [])),
			"godowns": len(tally_data.get("godowns", [])),
		},
		"target_counts": {
			"accounts": len(transformed.get("accounts", [])),
			"customers": len(transformed.get("customers", [])),
			"suppliers": len(transformed.get("suppliers", [])),
			"item_groups": len(transformed.get("item_groups", [])),
			"items": len(transformed.get("items", [])),
			"warehouses": len(transformed.get("warehouses", [])),
		},
		"status": _master_doc_status(transformed, company_name),
	}


@frappe.whitelist()
def execute_master_data_migration(host="localhost", port=9000, company_name="", company_abbr="", dry_run=True):
	"""Start a master-data-only migration job.

	This intentionally excludes opening balances, opening stock, and vouchers.
	Use it as the first safe migration phase.
	"""
	frappe.only_for("System Manager")

	if not company_name or not company_abbr:
		frappe.throw("company_name and company_abbr are required")

	if isinstance(dry_run, str):
		dry_run = dry_run.lower() in ("true", "1", "yes")

	job_id = f"migration_master_{frappe.generate_hash(length=8)}"
	_set_progress(job_id, "queued", "Queued master-data migration", 0)

	enqueue(
		"migration.core.api._run_master_data_migration_job",
		host=host,
		port=int(port),
		company_name=company_name,
		company_abbr=company_abbr,
		dry_run=dry_run,
		progress_id=job_id,
		queue="long",
		timeout=900,
		enqueue_after_commit=False,
	)

	return {"job_id": job_id, "status": "queued", "dry_run": dry_run}


def _run_master_data_migration_job(host, port, company_name, company_abbr, dry_run=True, progress_id=""):
	"""Run master-data-only Tally -> ERPNext migration."""
	from migration.core.transformers.tally_to_erpnext import transform_all
	from migration.core.importers.erpnext import ERPNextImporter

	job_id = progress_id
	steps_done = []
	errors = []

	def progress(step, pct):
		_set_progress(job_id, "running", step, pct, steps_done, errors)

	try:
		progress("Fetching Tally master data...", 10)
		tally_data, warnings = _fetch_tally_master_data(host, int(port))
		errors.extend(warnings)
		steps_done.append({
			"name": "Fetch Tally masters",
			"status": "done",
			"detail": f"{len(tally_data.get('groups', []))} groups, {len(tally_data.get('ledgers', []))} ledgers, {len(tally_data.get('stock_items', []))} stock items",
		})

		progress("Mapping master data to ERPNext...", 35)
		transformed = transform_all(tally_data, company_name, company_abbr)
		steps_done.append({
			"name": "Transform masters",
			"status": "done",
			"detail": f"{len(transformed.get('accounts', []))} accounts, {len(transformed.get('customers', []))} customers, {len(transformed.get('suppliers', []))} suppliers, {len(transformed.get('items', []))} items",
		})

		importer = ERPNextImporter(company_name, dry_run=dry_run)

		progress("Importing Chart of Accounts...", 45)
		importer._import_chart_of_accounts(transformed.get("accounts", []))
		steps_done.append({"name": "Chart of Accounts", "status": "done"})

		progress("Importing party groups...", 55)
		importer._import_customer_groups(transformed.get("customers", []))
		importer._import_supplier_groups(transformed.get("suppliers", []))
		steps_done.append({"name": "Party Groups", "status": "done"})

		progress("Importing customers...", 65)
		importer._import_customers(transformed.get("customers", []))
		steps_done.append({"name": "Customers", "status": "done"})

		progress("Importing suppliers...", 75)
		importer._import_suppliers(transformed.get("suppliers", []))
		steps_done.append({"name": "Suppliers", "status": "done"})

		progress("Importing item groups...", 82)
		importer._import_item_groups(transformed.get("item_groups", []))
		steps_done.append({"name": "Item Groups", "status": "done"})

		progress("Importing items...", 90)
		importer._import_items(transformed.get("items", []))
		steps_done.append({"name": "Items", "status": "done"})

		progress("Importing warehouses...", 96)
		importer._import_warehouses(transformed.get("warehouses", []))
		steps_done.append({"name": "Warehouses", "status": "done"})

		if not dry_run:
			frappe.db.commit()

		summary = _compact_import_summary(importer.get_summary())
		summary["master_status"] = _master_doc_status(transformed, company_name)

		_set_progress(
			job_id,
			"done",
			"Master-data migration complete",
			100,
			steps_done,
			errors,
			summary,
		)
	except Exception as exc:
		logger.exception("Master-data migration job %s failed", job_id)
		errors.append(str(exc))
		_set_progress(job_id, "error", f"Failed: {exc}", 0, steps_done, errors)


# ---------------------------------------------------------------------------
# Migration execution (background job)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def execute_migration(host="localhost", port=9000, company_name="", company_abbr="", dry_run=False):
	"""Start migration as a background job. Returns immediately with a job_id.

	Poll get_migration_status(job_id) for progress.
	"""
	frappe.only_for("System Manager")

	if not company_name or not company_abbr:
		frappe.throw("company_name and company_abbr are required")

	if isinstance(dry_run, str):
		dry_run = dry_run.lower() in ("true", "1", "yes")

	job_id = f"migration_{frappe.generate_hash(length=8)}"

	# Check no other migration is running
	existing = frappe.cache.hgetall(_CACHE_KEY)
	for existing_id, raw in (existing or {}).items():
		try:
			state = json.loads(raw) if isinstance(raw, str) else raw
		except (json.JSONDecodeError, TypeError):
			continue
		if state.get("status") == "running":
			return {"error": "Another migration is already running", "existing_job_id": existing_id}

	# Seed initial progress
	_set_progress(job_id, "queued", "Queued for execution", 0)

	enqueue(
		"migration.core.api._run_migration_job",
		host=host,
		port=int(port),
		company_name=company_name,
		company_abbr=company_abbr,
		dry_run=dry_run,
		progress_id=job_id,
		queue="long",
		timeout=600,
		enqueue_after_commit=True,
	)

	return {"job_id": job_id, "status": "queued"}


@frappe.whitelist()
def get_migration_status(job_id=None):
	"""Poll for migration progress."""
	if not job_id:
		frappe.throw("job_id is required")

	raw = frappe.cache.hget(_CACHE_KEY, job_id)
	if not raw:
		return {"status": "not_found"}
	try:
		return json.loads(raw) if isinstance(raw, str) else raw
	except (json.JSONDecodeError, TypeError):
		return {"status": "not_found"}


# ---------------------------------------------------------------------------
# Background job (NOT whitelisted — called via enqueue only)
# ---------------------------------------------------------------------------

def _run_migration_job(host, port, company_name, company_abbr, dry_run=False, progress_id=""):
	"""The actual migration logic. Runs in a background RQ worker."""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import (
		parse_list_of_accounts,
		parse_trial_balance,
		parse_stock_summary,
		parse_collection,
	)
	from migration.core.transformers.tally_to_erpnext import transform_all
	from migration.core.importers.erpnext import ERPNextImporter

	job_id = progress_id
	steps_done = []
	errors = []

	def progress(step, pct):
		_set_progress(job_id, "running", step, pct, steps_done, errors)

	try:
		# Step 1: Connect and fetch master data
		progress("Connecting to Tally...", 5)
		client = TallyClient(host=host, port=int(port))

		progress("Fetching master data from Tally (this may take a minute)...", 10)
		accounts_xml = client.get_list_of_accounts()
		steps_done.append({"name": "Fetch master data", "status": "done"})

		# Step 2: Parse
		progress("Parsing Tally data...", 25)
		tally_data = parse_list_of_accounts(accounts_xml)
		steps_done.append({
			"name": "Parse data",
			"status": "done",
			"detail": f"{len(tally_data['groups'])} groups, {len(tally_data['ledgers'])} ledgers",
		})

		# Step 2b: Fetch stock collections (Stock Groups, Stock Items, Godowns)
		progress("Fetching stock groups from Tally...", 28)
		try:
			sg_xml = client.get_collection("Stock Group")
			tally_data["stock_groups"] = parse_collection(sg_xml, "STOCKGROUP")
		except Exception as exc:
			errors.append(f"Stock groups fetch: {exc}")
			tally_data["stock_groups"] = []

		progress("Fetching stock items from Tally...", 30)
		try:
			si_xml = client.get_collection("Stock Item")
			tally_data["stock_items"] = parse_collection(si_xml, "STOCKITEM")
		except Exception as exc:
			errors.append(f"Stock items fetch: {exc}")
			tally_data["stock_items"] = []

		progress("Fetching godowns from Tally...", 32)
		try:
			gd_xml = client.get_collection("Godown")
			tally_data["godowns"] = parse_collection(gd_xml, "GODOWN")
		except Exception as exc:
			errors.append(f"Godowns fetch: {exc}")
			tally_data["godowns"] = []

		steps_done.append({
			"name": "Fetch stock data",
			"status": "done",
			"detail": f"{len(tally_data.get('stock_groups', []))} stock groups, {len(tally_data.get('stock_items', []))} stock items, {len(tally_data.get('godowns', []))} godowns",
		})

		# Step 3: Transform
		progress("Mapping to ERPNext format...", 35)
		transformed = transform_all(tally_data, company_name, company_abbr)
		steps_done.append({
			"name": "Transform",
			"status": "done",
			"detail": f"{len(transformed['accounts'])} accounts, {len(transformed['customers'])} customers, {len(transformed['suppliers'])} suppliers",
		})

		# Step 4: Import
		importer = ERPNextImporter(company_name, dry_run=dry_run)

		progress("Importing Chart of Accounts...", 45)
		importer._import_chart_of_accounts(transformed.get("accounts", []))
		steps_done.append({
			"name": "Chart of Accounts",
			"status": "done",
			"detail": f"{len(transformed.get('accounts', []))} accounts",
		})

		progress("Creating Customer Groups...", 55)
		importer._import_customer_groups(transformed.get("customers", []))
		importer._import_supplier_groups(transformed.get("suppliers", []))
		steps_done.append({"name": "Party Groups", "status": "done"})

		progress("Importing Customers...", 60)
		importer._import_customers(transformed.get("customers", []))
		steps_done.append({
			"name": "Customers",
			"status": "done",
			"detail": f"{len(transformed.get('customers', []))} customers",
		})

		progress("Importing Suppliers...", 70)
		importer._import_suppliers(transformed.get("suppliers", []))
		steps_done.append({
			"name": "Suppliers",
			"status": "done",
			"detail": f"{len(transformed.get('suppliers', []))} suppliers",
		})

		if transformed.get("item_groups"):
			progress("Importing Item Groups...", 75)
			importer._import_item_groups(transformed["item_groups"])
			steps_done.append({"name": "Item Groups", "status": "done"})

		if transformed.get("items"):
			progress("Importing Items...", 78)
			importer._import_items(transformed["items"])
			steps_done.append({"name": "Items", "status": "done"})

		if transformed.get("warehouses"):
			progress("Importing Warehouses...", 80)
			importer._import_warehouses(transformed["warehouses"])
			steps_done.append({"name": "Warehouses", "status": "done"})

		# Step 5: Opening balances
		progress("Fetching trial balance...", 82)
		try:
			tb_xml = client.get_trial_balance()
			trial_balance = parse_trial_balance(tb_xml)
			progress("Importing opening balances...", 88)
			importer.import_opening_balances(trial_balance)
			steps_done.append({
				"name": "Opening Balances",
				"status": "done",
				"detail": f"{len(trial_balance)} entries",
			})
		except Exception as exc:
			errors.append(f"Opening balances: {exc}")
			steps_done.append({"name": "Opening Balances", "status": "error", "detail": str(exc)})

		# Step 6: Opening stock
		progress("Fetching stock summary...", 92)
		try:
			stock_xml = client.get_stock_summary()
			stock_data = parse_stock_summary(stock_xml)
			progress("Importing opening stock...", 96)
			importer.import_opening_stock(stock_data)
			steps_done.append({
				"name": "Opening Stock",
				"status": "done",
				"detail": f"{len(stock_data)} items",
			})
		except Exception as exc:
			errors.append(f"Opening stock: {exc}")
			steps_done.append({"name": "Opening Stock", "status": "error", "detail": str(exc)})

		# Done
		if not dry_run:
			frappe.db.commit()

		summary = importer.get_summary()
		errors.extend([e.get("error", str(e)) for e in summary.get("details", {}).get("errors", [])])

		_set_progress(
			job_id, "done", "Migration complete!", 100,
			steps_done, errors, summary,
		)
		logger.info("Migration job %s completed: %s", job_id, summary)

	except Exception as exc:
		logger.exception("Migration job %s failed", job_id)
		errors.append(str(exc))
		_set_progress(job_id, "error", f"Failed: {exc}", 0, steps_done, errors)


# ---------------------------------------------------------------------------
# File-based migration (also background job)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def execute_migration_from_file(file_path="", company_name="", company_abbr="", dry_run=False):
	"""Start migration from a saved XML file as a background job."""
	frappe.only_for("System Manager")

	if not file_path or not company_name or not company_abbr:
		frappe.throw("file_path, company_name, and company_abbr are required")

	if isinstance(dry_run, str):
		dry_run = dry_run.lower() in ("true", "1", "yes")

	job_id = f"migration_file_{frappe.generate_hash(length=8)}"
	_set_progress(job_id, "queued", "Queued for execution", 0)

	enqueue(
		"migration.core.api._run_file_migration_job",
		file_path=file_path,
		company_name=company_name,
		company_abbr=company_abbr,
		dry_run=dry_run,
		progress_id=job_id,
		queue="long",
		timeout=600,
		enqueue_after_commit=True,
	)

	return {"job_id": job_id, "status": "queued"}


def _run_file_migration_job(file_path, company_name, company_abbr, dry_run=False, progress_id=""):
	"""File-based migration job. Runs in background worker."""
	from migration.core.parsers.tally import parse_list_of_accounts
	from migration.core.transformers.tally_to_erpnext import transform_all
	from migration.core.importers.erpnext import ERPNextImporter

	job_id = progress_id
	steps_done = []
	errors = []

	def progress(step, pct):
		_set_progress(job_id, "running", step, pct, steps_done, errors)

	try:
		progress("Reading XML file...", 10)
		with open(file_path, "r", encoding="utf-8", errors="replace") as f:
			xml_string = f.read()

		progress("Parsing data...", 30)
		tally_data = parse_list_of_accounts(xml_string)
		steps_done.append({"name": "Parse", "status": "done"})

		progress("Transforming...", 40)
		transformed = transform_all(tally_data, company_name, company_abbr)
		steps_done.append({"name": "Transform", "status": "done"})

		progress("Importing...", 50)
		importer = ERPNextImporter(company_name, dry_run=dry_run)
		importer.import_all(transformed)

		if not dry_run:
			frappe.db.commit()

		summary = importer.get_summary()
		_set_progress(job_id, "done", "Complete!", 100, steps_done, errors, summary)

	except Exception as exc:
		logger.exception("File migration job %s failed", job_id)
		errors.append(str(exc))
		_set_progress(job_id, "error", f"Failed: {exc}", 0, steps_done, errors)


# ---------------------------------------------------------------------------
# Chart-of-Accounts only (structure, no opening balances)
# ---------------------------------------------------------------------------

def import_chart_of_accounts_from_file(file_path="", company_name="", company_abbr="", dry_run=False):
	"""Load ONLY the Chart of Accounts (groups + ledger accounts) from a saved
	Tally List of Accounts XML. Skips customers/suppliers/items/warehouses and
	posts no opening balances.

	Call via:
	  bench --site <site> execute \
	    migration.core.api.import_chart_of_accounts_from_file \
	    --kwargs '{"file_path": "/path/to/list-of-accounts.xml", \
	              "company_name": "Avinash Industries", "company_abbr": "AI"}'
	"""
	from migration.core.parsers.tally import parse_list_of_accounts
	from migration.core.transformers.tally_to_erpnext import transform_accounts
	from migration.core.importers.erpnext import ERPNextImporter

	if not file_path or not company_name or not company_abbr:
		frappe.throw("file_path, company_name, and company_abbr are required")
	if isinstance(dry_run, str):
		dry_run = dry_run.lower() in ("true", "1", "yes")

	with open(file_path, "r", encoding="utf-8", errors="replace") as f:
		xml_string = f.read()

	parsed = parse_list_of_accounts(xml_string)
	groups = parsed.get("groups", [])
	ledgers = parsed.get("ledgers", [])

	# Custodian filter: drop blank/whitespace names
	groups = [g for g in groups if (g.get("name") or "").strip()]
	ledgers = [l for l in ledgers if (l.get("name") or "").strip()]

	accounts = transform_accounts(groups, ledgers, company_name, company_abbr)

	importer = ERPNextImporter(company_name, dry_run=dry_run)
	importer._import_chart_of_accounts(accounts)
	if not dry_run:
		frappe.db.commit()

	return {
		"parsed": {"groups": len(groups), "ledgers": len(ledgers)},
		"transformed_accounts": len(accounts),
		"summary": importer.results,
	}


# ---------------------------------------------------------------------------
# Stock-only migration (for incremental runs)
# ---------------------------------------------------------------------------

def import_stock_data(host="localhost", port=9000, company_name="", company_abbr=""):
	"""Fetch stock groups, stock items, and godowns from Tally and import into ERPNext.

	Designed for incremental use — skips duplicates, only creates new records.
	Call via: bench --site <site> execute migration.core.api.import_stock_data --kwargs '{...}'
	"""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import parse_collection
	from migration.core.transformers.tally_to_erpnext import (
		transform_item_groups,
		transform_items,
		transform_warehouses,
	)
	from migration.core.importers.erpnext import ERPNextImporter

	if not company_name or not company_abbr:
		print("ERROR: company_name and company_abbr are required")
		return

	client = TallyClient(host=host, port=int(port))

	print("Fetching stock groups...")
	sg_xml = client.get_collection("Stock Group")
	stock_groups = parse_collection(sg_xml, "STOCKGROUP")
	print(f"  Found {len(stock_groups)} stock groups")

	print("Fetching stock items...")
	si_xml = client.get_collection("Stock Item")
	stock_items = parse_collection(si_xml, "STOCKITEM")
	print(f"  Found {len(stock_items)} stock items")

	print("Fetching godowns...")
	gd_xml = client.get_collection("Godown")
	godowns = parse_collection(gd_xml, "GODOWN")
	print(f"  Found {len(godowns)} godowns")

	print("Transforming...")
	item_groups = transform_item_groups(stock_groups)
	items = transform_items(stock_items)
	warehouses = transform_warehouses(godowns, company_name)

	print(f"  {len(item_groups)} item groups, {len(items)} items, {len(warehouses)} warehouses")

	print("Importing...")
	importer = ERPNextImporter(company_name)
	importer._import_item_groups(item_groups)
	importer._import_items(items)
	importer._import_warehouses(warehouses)
	frappe.db.commit()

	summary = importer.get_summary()
	print(f"Done: {summary['created']} created, {summary['skipped']} skipped, {summary['errors']} errors")
	return summary


# ---------------------------------------------------------------------------
# Cost centre migration (incremental)
# ---------------------------------------------------------------------------

def import_cost_centres(host="localhost", port=9000, company_name="", company_abbr=""):
	"""Fetch cost centres from Tally and import into ERPNext.

	Skips payroll/employee entries. Creates non-duplicate Cost Centers.
	Call via: bench --site <site> execute migration.core.api.import_cost_centres --kwargs '{...}'
	"""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import parse_collection
	from migration.core.transformers.tally_to_erpnext import transform_cost_centres
	from migration.core.importers.erpnext import ERPNextImporter

	if not company_name or not company_abbr:
		print("ERROR: company_name and company_abbr are required")
		return

	client = TallyClient(host=host, port=int(port))

	print("Fetching cost centres from Tally...")
	cc_xml = client.get_collection("Cost Centre")
	raw_ccs = parse_collection(cc_xml, "COSTCENTRE")
	print(f"  Found {len(raw_ccs)} total cost centres")

	payroll = [c for c in raw_ccs if c.get("for_payroll") or c.get("is_employee_group")]
	business = [c for c in raw_ccs if not c.get("for_payroll") and not c.get("is_employee_group")]
	print(f"  Payroll/employee (skipped): {len(payroll)}")
	print(f"  Business (to import): {len(business)}")

	print("Transforming...")
	transformed = transform_cost_centres(raw_ccs, company_name, company_abbr)
	print(f"  {len(transformed)} cost centres after transform")

	print("Importing...")
	importer = ERPNextImporter(company_name)
	importer._import_cost_centres(transformed)
	frappe.db.commit()

	summary = importer.get_summary()
	print(f"Done: {summary['created']} created, {summary['skipped']} skipped, {summary['errors']} errors")
	if summary.get("details", {}).get("errors"):
		for e in summary["details"]["errors"][:10]:
			print(f"  Error: {e}")
	return summary


# ---------------------------------------------------------------------------
# Opening balances + opening stock (incremental)
# ---------------------------------------------------------------------------

def import_opening_data(host="localhost", port=9000, company_name=""):
	"""Fetch trial balance and stock summary from Tally and create opening entries.

	Call via: bench --site <site> execute migration.core.api.import_opening_data --kwargs '{...}'
	"""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import (
		parse_trial_balance,
		parse_stock_summary,
	)
	from migration.core.importers.erpnext import ERPNextImporter

	if not company_name:
		print("ERROR: company_name is required")
		return

	client = TallyClient(host=host, port=int(port))
	importer = ERPNextImporter(company_name)

	# Opening balances
	print("Fetching trial balance from Tally...")
	tb_xml = client.get_trial_balance()
	trial_balance = parse_trial_balance(tb_xml)
	print(f"  Found {len(trial_balance)} trial balance entries")

	print("Importing opening balances...")
	importer.import_opening_balances(trial_balance)

	# Opening stock
	print("Fetching stock summary from Tally...")
	stock_xml = client.get_stock_summary()
	stock_data = parse_stock_summary(stock_xml)
	print(f"  Found {len(stock_data)} stock items with balances")

	print("Importing opening stock...")
	importer.import_opening_stock(stock_data)

	frappe.db.commit()

	summary = importer.get_summary()
	print(f"Done: {summary['created']} created, {summary['skipped']} skipped, {summary['errors']} errors")
	if summary.get("details", {}).get("errors"):
		for e in summary["details"]["errors"][:10]:
			print(f"  Error: {e}")
	return summary


# ---------------------------------------------------------------------------
# Voucher / transaction migration (background job)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def execute_voucher_migration(
	host="localhost", port=9000, company_name="", company_abbr="",
	from_date=None, to_date=None, dry_run=False,
):
	"""Fetch vouchers from Tally Day Book and import as ERPNext transactions.

	Runs as a background job. Poll get_migration_status(job_id) for progress.
	Requires master data (accounts, customers, suppliers, items) to already exist.
	"""
	frappe.only_for("System Manager")

	if not company_name or not company_abbr:
		frappe.throw("company_name and company_abbr are required")

	if isinstance(dry_run, str):
		dry_run = dry_run.lower() in ("true", "1", "yes")

	job_id = f"voucher_migration_{frappe.generate_hash(length=8)}"

	# Check no other migration is running
	existing = frappe.cache.hgetall(_CACHE_KEY)
	for existing_id, raw in (existing or {}).items():
		try:
			state = json.loads(raw) if isinstance(raw, str) else raw
		except (json.JSONDecodeError, TypeError):
			continue
		if state.get("status") == "running":
			return {"error": "Another migration is already running", "existing_job_id": existing_id}

	_set_progress(job_id, "queued", "Queued for voucher migration", 0)

	enqueue(
		"migration.core.api._run_voucher_migration_job",
		host=host,
		port=int(port),
		company_name=company_name,
		company_abbr=company_abbr,
		from_date=from_date,
		to_date=to_date,
		dry_run=dry_run,
		progress_id=job_id,
		queue="long",
		timeout=1800,
		enqueue_after_commit=True,
	)

	return {"job_id": job_id, "status": "queued"}


def _run_voucher_migration_job(
	host, port, company_name, company_abbr,
	from_date=None, to_date=None, dry_run=False, progress_id="",
):
	"""Background job: fetch Day Book from Tally, parse, transform, import."""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import parse_day_book
	from migration.core.transformers.tally_to_erpnext import transform_vouchers
	from migration.core.importers.erpnext import ERPNextImporter

	job_id = progress_id
	steps_done = []
	errors = []

	def progress(step, pct):
		_set_progress(job_id, "running", step, pct, steps_done, errors)

	try:
		# Step 1: Fetch Day Book
		progress("Connecting to Tally...", 5)
		client = TallyClient(host=host, port=int(port))

		progress("Fetching Day Book from Tally (this may take several minutes)...", 10)
		day_book_xml = client.get_day_book(from_date=from_date, to_date=to_date)
		steps_done.append({"name": "Fetch Day Book", "status": "done"})

		# Step 2: Fetch voucher type hierarchy (needed for type resolution)
		progress("Fetching voucher type definitions...", 20)
		try:
			vt_xml = client.get_collection("Voucher Type", ["Name", "Parent"])
			from migration.core.parsers.tally import parse_collection
			voucher_types = parse_collection(vt_xml, "VOUCHERTYPE")
		except Exception as exc:
			logger.warning("Could not fetch voucher types: %s — using inline type detection", exc)
			voucher_types = []
		steps_done.append({"name": "Fetch voucher types", "status": "done"})

		# Step 3: Parse
		progress("Parsing vouchers...", 30)
		vouchers = parse_day_book(day_book_xml)
		steps_done.append({
			"name": "Parse vouchers",
			"status": "done",
			"detail": f"{len(vouchers)} vouchers found",
		})

		# Step 4: Transform
		progress("Transforming vouchers to ERPNext format...", 40)
		transformed = transform_vouchers(vouchers, voucher_types, company_name, company_abbr)
		detail_parts = []
		for key in ("sales_invoices", "purchase_invoices", "payment_entries", "journal_entries"):
			count = len(transformed.get(key, []))
			if count:
				detail_parts.append(f"{count} {key.replace('_', ' ')}")
		steps_done.append({
			"name": "Transform vouchers",
			"status": "done",
			"detail": ", ".join(detail_parts) or "0 vouchers",
		})

		# Step 5: Import
		importer = ERPNextImporter(company_name, dry_run=dry_run)

		si_count = len(transformed.get("sales_invoices", []))
		if si_count:
			progress(f"Importing {si_count} Sales Invoices...", 50)
			importer._import_sales_invoices(transformed["sales_invoices"])
			steps_done.append({"name": "Sales Invoices", "status": "done", "detail": f"{si_count}"})

		pi_count = len(transformed.get("purchase_invoices", []))
		if pi_count:
			progress(f"Importing {pi_count} Purchase Invoices...", 65)
			importer._import_purchase_invoices(transformed["purchase_invoices"])
			steps_done.append({"name": "Purchase Invoices", "status": "done", "detail": f"{pi_count}"})

		pe_count = len(transformed.get("payment_entries", []))
		if pe_count:
			progress(f"Importing {pe_count} Payment Entries...", 80)
			importer._import_payment_entries(transformed["payment_entries"])
			steps_done.append({"name": "Payment Entries", "status": "done", "detail": f"{pe_count}"})

		je_count = len(transformed.get("journal_entries", []))
		if je_count:
			progress(f"Importing {je_count} Journal Entries...", 90)
			importer._import_journal_entries(transformed["journal_entries"])
			steps_done.append({"name": "Journal Entries", "status": "done", "detail": f"{je_count}"})

		if not dry_run:
			frappe.db.commit()

		summary = importer.get_summary()
		errors.extend([e.get("error", str(e)) for e in summary.get("details", {}).get("errors", [])])

		_set_progress(
			job_id, "done", "Voucher migration complete!", 100,
			steps_done, errors, summary,
		)
		logger.info("Voucher migration job %s completed: %s", job_id, summary)

	except Exception as exc:
		logger.exception("Voucher migration job %s failed", job_id)
		errors.append(str(exc))
		_set_progress(job_id, "error", f"Failed: {exc}", 0, steps_done, errors)


@frappe.whitelist()
def execute_voucher_migration_from_file(
	file_path="", company_name="", company_abbr="", dry_run=False,
):
	"""Import vouchers from a saved Day Book XML file."""
	frappe.only_for("System Manager")

	if not file_path or not company_name or not company_abbr:
		frappe.throw("file_path, company_name, and company_abbr are required")

	if isinstance(dry_run, str):
		dry_run = dry_run.lower() in ("true", "1", "yes")

	job_id = f"voucher_file_{frappe.generate_hash(length=8)}"
	_set_progress(job_id, "queued", "Queued for voucher file migration", 0)

	enqueue(
		"migration.core.api._run_voucher_file_migration_job",
		file_path=file_path,
		company_name=company_name,
		company_abbr=company_abbr,
		dry_run=dry_run,
		progress_id=job_id,
		queue="long",
		timeout=1800,
		enqueue_after_commit=True,
	)

	return {"job_id": job_id, "status": "queued"}


def _run_voucher_file_migration_job(file_path, company_name, company_abbr, dry_run=False, progress_id=""):
	"""File-based voucher migration job."""
	from migration.core.parsers.tally import parse_day_book
	from migration.core.transformers.tally_to_erpnext import transform_vouchers
	from migration.core.importers.erpnext import ERPNextImporter

	job_id = progress_id
	steps_done = []
	errors = []

	def progress(step, pct):
		_set_progress(job_id, "running", step, pct, steps_done, errors)

	try:
		progress("Reading Day Book XML file...", 10)
		with open(file_path, "r", encoding="utf-8", errors="replace") as f:
			xml_string = f.read()

		progress("Parsing vouchers...", 30)
		vouchers = parse_day_book(xml_string)
		steps_done.append({"name": "Parse", "status": "done", "detail": f"{len(vouchers)} vouchers"})

		progress("Transforming...", 45)
		transformed = transform_vouchers(vouchers, [], company_name, company_abbr)
		steps_done.append({"name": "Transform", "status": "done"})

		progress("Importing vouchers...", 55)
		importer = ERPNextImporter(company_name, dry_run=dry_run)
		importer.import_vouchers(transformed)

		if not dry_run:
			frappe.db.commit()

		summary = importer.get_summary()
		_set_progress(job_id, "done", "Complete!", 100, steps_done, errors, summary)

	except Exception as exc:
		logger.exception("Voucher file migration job %s failed", job_id)
		errors.append(str(exc))
		_set_progress(job_id, "error", f"Failed: {exc}", 0, steps_done, errors)


# ---------------------------------------------------------------------------
# Opening stock from item-level balances (incremental)
# ---------------------------------------------------------------------------

def import_opening_stock_from_tally(host="localhost", port=9000, company_name=""):
	"""Fetch item-level closing balances from Tally and create Stock Reconciliation.

	Uses the TDL Collection API to get CLOSINGBALANCE per Stock Item,
	instead of the Stock Summary report (which gives group-level data).

	Call via: bench --site <site> execute migration.core.api.import_opening_stock_from_tally --kwargs '{...}'
	"""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import parse_stock_item_balances
	from migration.core.importers.erpnext import ERPNextImporter

	if not company_name:
		print("ERROR: company_name is required")
		return

	client = TallyClient(host=host, port=int(port))

	print("Fetching item-level closing balances from Tally...")
	xml = client.get_stock_item_balances()
	stock_data = parse_stock_item_balances(xml)
	print(f"  Found {len(stock_data)} items with closing stock (qty > 0)")

	if not stock_data:
		print("No items with positive closing stock found.")
		return

	importer = ERPNextImporter(company_name)
	print("Creating Stock Reconciliation...")
	importer.import_opening_stock(stock_data)
	frappe.db.commit()

	summary = importer.get_summary()
	print(f"Done: {summary['created']} created, {summary['skipped']} skipped, {summary['errors']} errors")
	if summary.get("details", {}).get("errors"):
		for e in summary["details"]["errors"][:10]:
			print(f"  Error: {e}")
	return summary


# ---------------------------------------------------------------------------
# Opening receivable/payable invoices (incremental)
# ---------------------------------------------------------------------------

def import_opening_invoices_from_tally(host="localhost", port=9000, company_name="", company_abbr=""):
	"""Fetch ledger opening balances from Tally and create opening Sales/Purchase Invoices.

	Call via: bench --site <site> execute migration.core.api.import_opening_invoices_from_tally --kwargs '{...}'
	"""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import parse_list_of_accounts
	from migration.core.transformers.tally_to_erpnext import classify_ledger
	from migration.core.importers.erpnext import ERPNextImporter

	if not company_name or not company_abbr:
		print("ERROR: company_name and company_abbr are required")
		return

	client = TallyClient(host=host, port=int(port))

	print("Fetching master data from Tally...")
	accounts_xml = client.get_list_of_accounts()
	tally_data = parse_list_of_accounts(accounts_xml)
	print(f"  Parsed {len(tally_data['ledgers'])} ledgers, {len(tally_data['groups'])} groups")

	groups_by_name = {g["name"]: g for g in tally_data["groups"]}

	customer_balances = []
	supplier_balances = []

	for led in tally_data["ledgers"]:
		classification = classify_ledger(led, groups_by_name)
		opening = led.get("opening_balance", 0)
		if abs(opening) < 0.01:
			continue

		party_name = led.get("mailing_name") or led["name"]

		if classification == "customer":
			customer_balances.append({"party": party_name, "amount": opening})
		elif classification == "supplier":
			supplier_balances.append({"party": party_name, "amount": abs(opening)})

	print(f"  Found {len(customer_balances)} customers with opening balances")
	print(f"  Found {len(supplier_balances)} suppliers with opening balances")
	total_receivable = sum(abs(c["amount"]) for c in customer_balances)
	total_payable = sum(abs(s["amount"]) for s in supplier_balances)
	print(f"  Total receivable: {total_receivable:,.2f}")
	print(f"  Total payable: {total_payable:,.2f}")

	if not customer_balances and not supplier_balances:
		print("No opening balances to import.")
		return

	importer = ERPNextImporter(company_name)
	print("Creating opening invoices...")
	importer.import_opening_invoices(customer_balances, supplier_balances)

	summary = importer.get_summary()
	print(f"Done: {summary['created']} created, {summary['skipped']} skipped, {summary['errors']} errors")
	if summary.get("details", {}).get("errors"):
		for e in summary["details"]["errors"][:20]:
			print(f"  Error: {e}")
	return summary


# ---------------------------------------------------------------------------
# Ledger-level opening balances (replaces trial-balance approach)
# ---------------------------------------------------------------------------

def import_ledger_opening_balances(host="localhost", port=9000, company_name="", company_abbr=""):
	"""Fetch individual ledger opening balances from Tally and create a Journal Entry.

	Uses leaf-level ledger data (not group-level Trial Balance), giving accurate
	per-account opening balances. Skips customer/supplier ledgers (handled by opening invoices).

	Call via: bench --site <site> execute migration.core.api.import_ledger_opening_balances --kwargs '{...}'
	"""
	from migration.core.connectors.tally import TallyClient
	from migration.core.parsers.tally import parse_list_of_accounts
	from migration.core.importers.erpnext import ERPNextImporter

	if not company_name or not company_abbr:
		print("ERROR: company_name and company_abbr are required")
		return

	client = TallyClient(host=host, port=int(port))

	print("Fetching master data from Tally...")
	accounts_xml = client.get_list_of_accounts()
	tally_data = parse_list_of_accounts(accounts_xml)

	ledgers = tally_data.get("ledgers", [])
	groups = tally_data.get("groups", [])
	print(f"  Parsed {len(ledgers)} ledgers, {len(groups)} groups")

	# Count ledgers with opening balances
	with_balance = [l for l in ledgers if abs(l.get("opening_balance", 0)) > 0.01]
	total_debit = sum(abs(l["opening_balance"]) for l in with_balance if l["opening_balance"] < 0)
	total_credit = sum(l["opening_balance"] for l in with_balance if l["opening_balance"] > 0)
	print(f"  {len(with_balance)} ledgers with opening balances")
	print(f"  Total debit (assets/expenses): {total_debit:,.2f}")
	print(f"  Total credit (liabilities/income): {total_credit:,.2f}")

	importer = ERPNextImporter(company_name)
	print("Creating ledger-level opening JE...")
	importer.import_opening_balances_from_ledgers(ledgers, groups, company_abbr)

	summary = importer.get_summary()
	print(f"Done: {summary['created']} created, {summary['skipped']} skipped, {summary['errors']} errors")
	if summary.get("details", {}).get("created"):
		for c in summary["details"]["created"]:
			print(f"  Created: {c}")
	if summary.get("details", {}).get("errors"):
		for e in summary["details"]["errors"][:10]:
			print(f"  Error: {e}")
	return summary

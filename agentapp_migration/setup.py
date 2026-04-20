import json

import frappe


SOURCE_TYPES = [
	{
		"source_key": "tally",
		"title": "Tally",
		"subtitle": "TallyPrime / ERP 9",
		"enabled": 1,
		"implementation_status": "Ready",
		"sort_order": 10,
		"description": "Read-only Tally bridge, XML export, live server, and Excel fallback flows.",
		"icon_label": "Ta",
		"icon_bg_class": "bg-blue-50 dark:bg-blue-950",
		"icon_text_class": "text-blue-500",
		"capabilities_json": {
			"snapshot": True,
			"incremental_sync": True,
			"live_query": True,
			"file_upload": True,
			"writeback": False,
		},
		"methods_json": [
			{"key": "bridge", "title": "Sena Tally Bridge", "status": "Ready"},
			{"key": "xml", "title": "Upload Tally Export", "status": "Fallback"},
			{"key": "live", "title": "Live Server Connection", "status": "Ready"},
			{"key": "excel", "title": "Upload Excel Exports", "status": "Fallback"},
		],
	},
	{
		"source_key": "sap",
		"title": "SAP",
		"subtitle": "SAP B1 / S/4HANA",
		"enabled": 1,
		"implementation_status": "Design",
		"sort_order": 20,
		"description": "SAP source connection starting with SAP Business One on HANA.",
		"icon_label": "SAP",
		"icon_bg_class": "bg-orange-50 dark:bg-orange-950",
		"icon_text_class": "text-orange-500",
		"capabilities_json": {
			"snapshot": True,
			"incremental_sync": True,
			"live_query": True,
			"file_upload": False,
			"writeback": False,
		},
		"methods_json": [
			{"key": "hana", "title": "SAP B1 HANA Read-Only", "status": "Design"},
			{"key": "api", "title": "SAP API Connector", "status": "Future"},
		],
	},
	{
		"source_key": "excel",
		"title": "CSV / Excel",
		"subtitle": "Import from files",
		"enabled": 1,
		"implementation_status": "Design",
		"sort_order": 30,
		"description": "Spreadsheet source connection with sheet discovery and column mapping.",
		"icon_label": "XL",
		"icon_bg_class": "bg-purple-50 dark:bg-purple-950",
		"icon_text_class": "text-purple-500",
		"capabilities_json": {
			"snapshot": True,
			"incremental_sync": False,
			"live_query": False,
			"file_upload": True,
			"writeback": False,
		},
		"methods_json": [
			{"key": "workbook", "title": "Workbook Upload", "status": "Design"},
			{"key": "csv", "title": "CSV Upload", "status": "Design"},
		],
	},
]


def _as_json(value):
	return json.dumps(value, ensure_ascii=False, sort_keys=True)


def ensure_integration_source_types():
	if not frappe.db.table_exists("Integration Source Type"):
		return

	for source_type in SOURCE_TYPES:
		docname = source_type["source_key"]
		values = {
			**source_type,
			"capabilities_json": _as_json(source_type.get("capabilities_json")),
			"methods_json": _as_json(source_type.get("methods_json")),
		}
		if frappe.db.exists("Integration Source Type", docname):
			doc = frappe.get_doc("Integration Source Type", docname)
			doc.update(values)
			doc.save(ignore_permissions=True)
		else:
			frappe.get_doc({"doctype": "Integration Source Type", **values}).insert(ignore_permissions=True)

	frappe.db.commit()


def after_install():
	ensure_integration_source_types()

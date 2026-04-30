"""Export/import company data for pushing to production.

Export reads from local ERPNext, writes JSON files.
Import reads JSON files, creates docs on the target site.
"""

import json
import logging
import os

import frappe
from frappe.utils.nestedset import rebuild_tree

logger = logging.getLogger(__name__)

_DEFAULT_EXPORT_DIR = "/tmp/avinash_export"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_company_data(company_name="Avinash Industries", company_abbr="AI", export_dir=_DEFAULT_EXPORT_DIR):
	"""Export all company data as JSON files for prod import.

	Call via: bench --site dev.localhost execute migration.agentapp_migration.prod_sync.export_company_data
	"""
	os.makedirs(export_dir, exist_ok=True)

	# Export in dependency order. Each entry: (doctype, filters, options)
	exports = [
		("UOM", {}, {}),
		("Customer Group", {}, {}),
		("Supplier Group", {}, {}),
		("Item Group", {}, {}),
		("Account", {"company": company_name}, {"order_by": "lft asc"}),
		("Cost Center", {"company": company_name}, {"order_by": "lft asc"}),
		("Warehouse", {"company": company_name}, {"order_by": "lft asc"}),
		("Customer", {}, {}),
		("Supplier", {}, {}),
		("Item", {}, {}),
		("Address", {}, {"filter_by_company": True}),
		("Stock Reconciliation", {"company": company_name, "docstatus": 1}, {}),
		("Journal Entry", {"company": company_name, "docstatus": 1}, {}),
		("Sales Invoice", {"company": company_name, "docstatus": 1}, {}),
		("Purchase Invoice", {"company": company_name, "docstatus": 1}, {}),
		("Payment Entry", {"company": company_name, "docstatus": 1}, {}),
	]

	summary = {}

	for doctype, filters, options in exports:
		docs = _export_doctype(doctype, filters, options, company_name)
		filename = doctype.lower().replace(" ", "_") + ".json"
		filepath = os.path.join(export_dir, filename)
		with open(filepath, "w") as f:
			json.dump(docs, f, default=str, indent=1)
		summary[doctype] = len(docs)
		print(f"  {doctype}: {len(docs)} records -> {filename}")

	# Write manifest
	with open(os.path.join(export_dir, "manifest.json"), "w") as f:
		json.dump({"company": company_name, "abbr": company_abbr, "counts": summary}, f, indent=2)

	print(f"\nExport complete: {sum(summary.values())} total records in {export_dir}/")
	return summary


def _export_doctype(doctype, filters, options, company_name):
	"""Export all docs of a doctype as list of dicts."""
	names = frappe.get_all(
		doctype,
		filters=filters,
		pluck="name",
		order_by=options.get("order_by", "creation asc"),
		limit_page_length=0,
	)

	# For Address, filter to only those linked to customers/suppliers in this company
	if options.get("filter_by_company"):
		names = _filter_addresses_by_company(company_name)

	docs = []
	for name in names:
		doc = frappe.get_doc(doctype, name)
		doc_dict = doc.as_dict()
		# Strip Frappe internal fields that shouldn't be transferred
		_clean_doc(doc_dict)
		docs.append(doc_dict)

	return docs


def _filter_addresses_by_company(company_name):
	"""Get addresses linked to customers or suppliers (which are global but ours)."""
	return frappe.db.sql_list("""
		SELECT DISTINCT a.name
		FROM tabAddress a
		JOIN `tabDynamic Link` dl ON dl.parent = a.name AND dl.parenttype = 'Address'
		WHERE dl.link_doctype IN ('Customer', 'Supplier')
	""")


def _clean_doc(doc_dict):
	"""Remove Frappe metadata fields that cause issues on import."""
	remove_keys = [
		"modified", "modified_by", "creation", "owner",
		"_user_tags", "_comments", "_assign", "_liked_by",
		"__unsaved", "__islocal", "__onload", "__run_link_triggers",
		# Nested set fields — rebuilt after import
		"lft", "rgt", "old_parent",
	]
	for key in remove_keys:
		doc_dict.pop(key, None)

	# Clean child table rows too
	for key, val in doc_dict.items():
		if isinstance(val, list):
			for row in val:
				if isinstance(row, dict):
					for rk in remove_keys:
						row.pop(rk, None)
					# Remove child row name (auto-generated on insert)
					row.pop("name", None)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

_IMPORT_ORDER = [
	"uom",
	"customer_group",
	"supplier_group",
	"item_group",
	"account",
	"cost_center",
	"warehouse",
	"customer",
	"supplier",
	"item",
	"address",
	"stock_reconciliation",
	"journal_entry",
	"sales_invoice",
	"purchase_invoice",
	"payment_entry",
]

# Doctypes that need nested set rebuild after import
_NESTED_SET_DOCTYPES = {"Account", "Cost Center", "Warehouse", "Item Group", "Customer Group", "Supplier Group"}

# Doctypes where we should submit after insert
_SUBMITTABLE_DOCTYPES = {"Stock Reconciliation", "Journal Entry", "Sales Invoice", "Purchase Invoice", "Payment Entry"}


def import_company_data(import_dir=_DEFAULT_EXPORT_DIR):
	"""Import company data from JSON files. Run on the PROD site.

	Call via: bench --site <prod_site> execute migration.agentapp_migration.prod_sync.import_company_data
	"""
	results = {}
	trees_to_rebuild = set()

	for file_key in _IMPORT_ORDER:
		filepath = os.path.join(import_dir, f"{file_key}.json")
		if not os.path.exists(filepath):
			print(f"  {file_key}: SKIPPED (file not found)")
			continue

		with open(filepath) as f:
			docs = json.load(f)

		doctype = docs[0]["doctype"] if docs else file_key.replace("_", " ").title()
		created = 0
		skipped = 0
		errors = 0
		error_details = []

		for doc_dict in docs:
			dt = doc_dict.get("doctype", doctype)
			doc_name = doc_dict.get("name", "")

			# Skip if already exists
			if doc_name and frappe.db.exists(dt, doc_name):
				skipped += 1
				continue

			try:
				result = _import_single_doc(doc_dict)
				if result == "created":
					created += 1
				elif result == "skipped":
					skipped += 1
			except Exception as exc:
				errors += 1
				error_details.append(f"{doc_name}: {str(exc)[:100]}")
				if errors <= 3:
					logger.warning("Import error for %s %s: %s", dt, doc_name, exc)

		frappe.db.commit()

		# Track nested set doctypes for rebuild
		if doctype in _NESTED_SET_DOCTYPES and created > 0:
			trees_to_rebuild.add(doctype)

		results[file_key] = {"created": created, "skipped": skipped, "errors": errors}
		print(f"  {file_key}: created={created}, skipped={skipped}, errors={errors}")
		if error_details:
			for e in error_details[:5]:
				print(f"    ERROR: {e}")

	# Rebuild nested set trees
	for dt in trees_to_rebuild:
		try:
			rebuild_tree(dt)
			print(f"  Rebuilt {dt} tree")
		except Exception as exc:
			print(f"  Failed to rebuild {dt} tree: {exc}")

	frappe.db.commit()
	print(f"\nImport complete.")
	return results


def _import_single_doc(doc_dict):
	"""Import a single document. Returns 'created' or 'skipped'."""
	doctype = doc_dict["doctype"]
	is_submittable = doctype in _SUBMITTABLE_DOCTYPES
	target_docstatus = doc_dict.get("docstatus", 0)

	# For submittable docs, insert as draft first
	if is_submittable:
		doc_dict["docstatus"] = 0

	doc = frappe.get_doc(doc_dict)
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_links = True
	doc.flags.ignore_validate = True

	try:
		doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
	except frappe.DuplicateEntryError:
		return "skipped"

	# Submit if the source doc was submitted
	if is_submittable and target_docstatus == 1:
		try:
			doc.flags.ignore_permissions = True
			doc.flags.ignore_validate = True
			doc.submit()
		except Exception as exc:
			logger.warning("Insert OK but submit failed for %s %s: %s", doctype, doc.name, exc)

	return "created"

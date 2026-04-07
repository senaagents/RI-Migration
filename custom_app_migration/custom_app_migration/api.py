"""Whitelisted API endpoints for the Migration agent-app.

execute_migration runs as a background job (enqueue) so the HTTP request
returns immediately. The frontend polls get_migration_status for progress.
"""

import json
import logging

import frappe
from frappe.utils.background_jobs import enqueue, is_job_enqueued

logger = logging.getLogger(__name__)

_CACHE_KEY = "migration_progress"


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


# ---------------------------------------------------------------------------
# Quick endpoints (synchronous, fast)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_target_companies():
	"""Return list of existing companies on this site."""
	return frappe.get_all("Company", fields=["name", "abbr", "default_currency", "country"])


@frappe.whitelist()
def test_tally_connection(host="localhost", port=9000):
	"""Test connectivity to TallyPrime."""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient

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
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import (
		parse_list_of_accounts,
		parse_trial_balance,
		parse_stock_summary,
	)
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import classify_ledger

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
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import parse_list_of_accounts
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import transform_all

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
		"custom_app_migration.custom_app_migration.api._run_migration_job",
		host=host,
		port=int(port),
		company_name=company_name,
		company_abbr=company_abbr,
		dry_run=dry_run,
		job_id=job_id,
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

def _run_migration_job(host, port, company_name, company_abbr, dry_run=False, job_id=""):
	"""The actual migration logic. Runs in a background RQ worker."""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import (
		parse_list_of_accounts,
		parse_trial_balance,
		parse_stock_summary,
	)
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import transform_all
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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
		"custom_app_migration.custom_app_migration.api._run_file_migration_job",
		file_path=file_path,
		company_name=company_name,
		company_abbr=company_abbr,
		dry_run=dry_run,
		job_id=job_id,
		queue="long",
		timeout=600,
		enqueue_after_commit=True,
	)

	return {"job_id": job_id, "status": "queued"}


def _run_file_migration_job(file_path, company_name, company_abbr, dry_run=False, job_id=""):
	"""File-based migration job. Runs in background worker."""
	from custom_app_migration.custom_app_migration.parsers.tally import parse_list_of_accounts
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import transform_all
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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

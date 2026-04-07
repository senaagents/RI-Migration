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
		parse_collection,
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


# ---------------------------------------------------------------------------
# Stock-only migration (for incremental runs)
# ---------------------------------------------------------------------------

def import_stock_data(host="localhost", port=9000, company_name="", company_abbr=""):
	"""Fetch stock groups, stock items, and godowns from Tally and import into ERPNext.

	Designed for incremental use — skips duplicates, only creates new records.
	Call via: bench --site <site> execute custom_app_migration.custom_app_migration.api.import_stock_data --kwargs '{...}'
	"""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import parse_collection
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import (
		transform_item_groups,
		transform_items,
		transform_warehouses,
	)
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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
	Call via: bench --site <site> execute custom_app_migration.custom_app_migration.api.import_cost_centres --kwargs '{...}'
	"""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import parse_collection
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import transform_cost_centres
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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

	Call via: bench --site <site> execute custom_app_migration.custom_app_migration.api.import_opening_data --kwargs '{...}'
	"""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import (
		parse_trial_balance,
		parse_stock_summary,
	)
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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
		"custom_app_migration.custom_app_migration.api._run_voucher_migration_job",
		host=host,
		port=int(port),
		company_name=company_name,
		company_abbr=company_abbr,
		from_date=from_date,
		to_date=to_date,
		dry_run=dry_run,
		job_id=job_id,
		queue="long",
		timeout=1800,
		enqueue_after_commit=True,
	)

	return {"job_id": job_id, "status": "queued"}


def _run_voucher_migration_job(
	host, port, company_name, company_abbr,
	from_date=None, to_date=None, dry_run=False, job_id="",
):
	"""Background job: fetch Day Book from Tally, parse, transform, import."""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import parse_day_book
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import transform_vouchers
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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
			from custom_app_migration.custom_app_migration.parsers.tally import parse_collection
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
		"custom_app_migration.custom_app_migration.api._run_voucher_file_migration_job",
		file_path=file_path,
		company_name=company_name,
		company_abbr=company_abbr,
		dry_run=dry_run,
		job_id=job_id,
		queue="long",
		timeout=1800,
		enqueue_after_commit=True,
	)

	return {"job_id": job_id, "status": "queued"}


def _run_voucher_file_migration_job(file_path, company_name, company_abbr, dry_run=False, job_id=""):
	"""File-based voucher migration job."""
	from custom_app_migration.custom_app_migration.parsers.tally import parse_day_book
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import transform_vouchers
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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

	Call via: bench --site <site> execute custom_app_migration.custom_app_migration.api.import_opening_stock_from_tally --kwargs '{...}'
	"""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import parse_stock_item_balances
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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

	Call via: bench --site <site> execute custom_app_migration.custom_app_migration.api.import_opening_invoices_from_tally --kwargs '{...}'
	"""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import parse_list_of_accounts
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import classify_ledger
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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

	Call via: bench --site <site> execute custom_app_migration.custom_app_migration.api.import_ledger_opening_balances --kwargs '{...}'
	"""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import parse_list_of_accounts
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

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

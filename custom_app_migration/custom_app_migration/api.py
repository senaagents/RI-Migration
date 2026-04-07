"""Whitelisted API endpoints for the Migration agent-app."""

import frappe


@frappe.whitelist()
def get_target_companies():
	"""Return list of existing companies on this site."""
	return frappe.get_all("Company", fields=["name", "abbr", "default_currency", "country"])


@frappe.whitelist()
def test_tally_connection(host="localhost", port=9000):
	"""Test connectivity to TallyPrime."""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient

	client = TallyClient(host=host, port=int(port))
	result = client.test_connection()
	return result


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

	# Fetch and parse list of accounts
	accounts_xml = client.get_list_of_accounts()
	data = parse_list_of_accounts(accounts_xml)

	# Classify ledgers
	groups_by_name = {g["name"]: g for g in data["groups"]}
	customers = [l for l in data["ledgers"] if classify_ledger(l, groups_by_name) == "customer"]
	suppliers = [l for l in data["ledgers"] if classify_ledger(l, groups_by_name) == "supplier"]
	accounts = [l for l in data["ledgers"] if classify_ledger(l, groups_by_name) == "account"]

	# Fetch trial balance
	tb_xml = client.get_trial_balance()
	trial_balance = parse_trial_balance(tb_xml)

	# Fetch stock summary
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
	"""Preview what the migration will create without making changes.

	Returns transformed ERPNext doc dicts for review.
	"""
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


@frappe.whitelist()
def execute_migration(host="localhost", port=9000, company_name="", company_abbr="", dry_run=False):
	"""Execute the full migration from Tally to ERPNext.

	Args:
		host: TallyPrime server host.
		port: TallyPrime server port.
		company_name: ERPNext company name.
		company_abbr: ERPNext company abbreviation.
		dry_run: If true, validate without creating documents.
	"""
	frappe.only_for("System Manager")

	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import (
		parse_list_of_accounts,
		parse_trial_balance,
		parse_stock_summary,
	)
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import transform_all
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

	if not company_name or not company_abbr:
		frappe.throw("company_name and company_abbr are required")

	if isinstance(dry_run, str):
		dry_run = dry_run.lower() in ("true", "1", "yes")

	client = TallyClient(host=host, port=int(port))

	# 1. Fetch data
	accounts_xml = client.get_list_of_accounts()
	tally_data = parse_list_of_accounts(accounts_xml)

	# 2. Transform
	transformed = transform_all(tally_data, company_name, company_abbr)

	# 3. Import
	importer = ERPNextImporter(company_name, dry_run=dry_run)
	importer.import_all(transformed)

	# 4. Opening balances
	tb_xml = client.get_trial_balance()
	trial_balance = parse_trial_balance(tb_xml)
	importer.import_opening_balances(trial_balance)

	# 5. Opening stock
	stock_xml = client.get_stock_summary()
	stock_data = parse_stock_summary(stock_xml)
	importer.import_opening_stock(stock_data)

	if not dry_run:
		frappe.db.commit()

	return importer.get_summary()


@frappe.whitelist()
def execute_migration_from_file(file_path="", company_name="", company_abbr="", dry_run=False):
	"""Execute migration from a saved XML file (list-of-accounts.xml).

	For offline processing when Tally is not reachable.
	"""
	frappe.only_for("System Manager")

	from custom_app_migration.custom_app_migration.parsers.tally import parse_list_of_accounts
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import transform_all
	from custom_app_migration.custom_app_migration.importers.erpnext import ERPNextImporter

	if not file_path or not company_name or not company_abbr:
		frappe.throw("file_path, company_name, and company_abbr are required")

	if isinstance(dry_run, str):
		dry_run = dry_run.lower() in ("true", "1", "yes")

	with open(file_path, "r", encoding="utf-8", errors="replace") as f:
		xml_string = f.read()

	tally_data = parse_list_of_accounts(xml_string)
	transformed = transform_all(tally_data, company_name, company_abbr)

	importer = ERPNextImporter(company_name, dry_run=dry_run)
	importer.import_all(transformed)

	if not dry_run:
		frappe.db.commit()

	return importer.get_summary()

"""Audit migration: compare Tally source data with ERPNext imported data."""

import json
import logging

import frappe

logger = logging.getLogger(__name__)


@frappe.whitelist()
def audit_migration(host="10.211.55.3", port=9000, company_name="Avinash Industries", company_abbr="AI"):
	"""Compare Tally data with ERPNext data and report discrepancies."""
	from custom_app_migration.custom_app_migration.connectors.tally import TallyClient
	from custom_app_migration.custom_app_migration.parsers.tally import (
		parse_list_of_accounts,
		parse_collection,
		parse_trial_balance,
		parse_stock_summary,
	)
	from custom_app_migration.custom_app_migration.transformers.tally_to_erpnext import classify_ledger

	client = TallyClient(host, int(port))

	# ── Fetch from Tally ─────────────────────────────────────────────
	print("Fetching master data from Tally...")
	accounts_xml = client.get_list_of_accounts()
	tally_data = parse_list_of_accounts(accounts_xml)
	groups_by_name = {g["name"]: g for g in tally_data.get("groups", [])}

	tally_customers = [l for l in tally_data["ledgers"] if classify_ledger(l, groups_by_name) == "customer"]
	tally_suppliers = [l for l in tally_data["ledgers"] if classify_ledger(l, groups_by_name) == "supplier"]
	tally_accounts = [l for l in tally_data["ledgers"] if classify_ledger(l, groups_by_name) == "account"]

	print("Fetching stock data from Tally...")
	sg_xml = client.get_collection("Stock Group")
	stock_groups = parse_collection(sg_xml, "STOCKGROUP")
	si_xml = client.get_collection("Stock Item")
	stock_items = parse_collection(si_xml, "STOCKITEM")
	gd_xml = client.get_collection("Godown")
	godowns = parse_collection(gd_xml, "GODOWN")

	print("Fetching trial balance from Tally...")
	tb_xml = client.get_trial_balance()
	trial_balance = parse_trial_balance(tb_xml)

	print("Fetching stock summary from Tally...")
	stk_xml = client.get_stock_summary()
	stock_summary = parse_stock_summary(stk_xml)

	# ── Count from ERPNext ───────────────────────────────────────────
	erp_accounts = frappe.db.count("Account", {"company": company_name})
	erp_customers = frappe.db.count("Customer")
	erp_suppliers = frappe.db.count("Supplier")
	erp_items = frappe.db.count("Item")
	erp_item_groups = frappe.db.count("Item Group")
	erp_warehouses = frappe.db.count("Warehouse", {"company": company_name})
	erp_addresses = frappe.db.count("Address")
	erp_customer_groups = frappe.db.count("Customer Group")
	erp_supplier_groups = frappe.db.count("Supplier Group")

	# ── Part 1: Counts comparison ────────────────────────────────────
	print("\n" + "=" * 70)
	print("PART 1: COUNT COMPARISON")
	print("=" * 70)
	print(f"{'Entity':<30} {'Tally':>10} {'ERPNext':>10} {'Delta':>10}")
	print("-" * 70)

	tally_group_count = len(tally_data["groups"])
	tally_account_count = len(tally_accounts)
	total_tally_accounts = tally_group_count + tally_account_count

	comparisons = [
		("Account Groups", tally_group_count, "n/a (included in accounts)"),
		("Account Ledgers", tally_account_count, "n/a"),
		("Total Accounts (grp+leaf)", total_tally_accounts, erp_accounts),
		("Customers", len(tally_customers), erp_customers),
		("Suppliers", len(tally_suppliers), erp_suppliers),
		("Stock Groups / Item Groups", len(stock_groups), erp_item_groups),
		("Stock Items / Items", len(stock_items), erp_items),
		("Godowns / Warehouses", len(godowns), erp_warehouses),
		("Addresses", "n/a", erp_addresses),
		("Customer Groups", "n/a", erp_customer_groups),
		("Supplier Groups", "n/a", erp_supplier_groups),
	]

	for label, tally_val, erp_val in comparisons:
		if isinstance(tally_val, str) or isinstance(erp_val, str):
			print(f"{label:<30} {str(tally_val):>10} {str(erp_val):>10}")
		else:
			delta = erp_val - tally_val
			flag = " ✗" if delta != 0 else ""
			print(f"{label:<30} {tally_val:>10} {erp_val:>10} {delta:>+10}{flag}")

	# ── Part 2: Data quality checks ──────────────────────────────────
	print("\n" + "=" * 70)
	print("PART 2: DATA QUALITY CHECKS")
	print("=" * 70)

	# 2a. Orphaned accounts (leaf with no parent)
	orphaned_accounts = frappe.db.sql(
		"SELECT name FROM tabAccount WHERE company=%s AND (parent_account IS NULL OR parent_account='') AND is_group=0",
		company_name, as_dict=1,
	)
	print(f"\n[2a] Leaf accounts with no parent: {len(orphaned_accounts)}")
	for a in orphaned_accounts[:10]:
		print(f"  - {a['name']}")

	# 2b. Items with missing UOM
	bad_uom_items = frappe.db.sql(
		"SELECT name FROM tabItem WHERE stock_uom IS NULL OR stock_uom = ''",
		as_dict=1,
	)
	print(f"\n[2b] Items with missing stock_uom: {len(bad_uom_items)}")
	for i in bad_uom_items[:10]:
		print(f"  - {i['name']}")

	# 2c. Items with no Item Group
	no_group_items = frappe.db.sql(
		"SELECT name FROM tabItem WHERE item_group IS NULL OR item_group = ''",
		as_dict=1,
	)
	print(f"\n[2c] Items with missing item_group: {len(no_group_items)}")

	# 2d. Customers with no linked address
	customers_with_addr = frappe.db.sql("""
		SELECT COUNT(DISTINCT dl.link_name) FROM `tabDynamic Link` dl
		WHERE dl.link_doctype='Customer' AND dl.parenttype='Address'
	""")[0][0]
	print(f"\n[2d] Customers with address: {customers_with_addr} / {erp_customers}")
	print(f"     Customers WITHOUT address: {erp_customers - customers_with_addr}")

	# 2d2. Suppliers with no linked address
	suppliers_with_addr = frappe.db.sql("""
		SELECT COUNT(DISTINCT dl.link_name) FROM `tabDynamic Link` dl
		WHERE dl.link_doctype='Supplier' AND dl.parenttype='Address'
	""")[0][0]
	print(f"     Suppliers with address: {suppliers_with_addr} / {erp_suppliers}")
	print(f"     Suppliers WITHOUT address: {erp_suppliers - suppliers_with_addr}")

	# 2e. Orphaned warehouses
	orphaned_wh = frappe.db.sql(
		"SELECT name FROM tabWarehouse WHERE company=%s AND (parent_warehouse IS NULL OR parent_warehouse='') AND is_group=0",
		company_name, as_dict=1,
	)
	print(f"\n[2e] Leaf warehouses with no parent: {len(orphaned_wh)}")
	for w in orphaned_wh[:10]:
		print(f"  - {w['name']}")

	# 2f. Opening balance totals — JE vs Tally trial balance
	je_totals = frappe.db.sql("""
		SELECT
			SUM(jea.debit_in_account_currency) as total_debit,
			SUM(jea.credit_in_account_currency) as total_credit,
			COUNT(*) as row_count
		FROM `tabJournal Entry Account` jea
		JOIN `tabJournal Entry` je ON jea.parent = je.name
		WHERE je.company=%s AND je.is_opening='Yes' AND je.docstatus=1
	""", company_name, as_dict=1)
	je_tot = je_totals[0] if je_totals else {}

	tally_tb_debit = sum(abs(e.get("debit", 0)) for e in trial_balance)
	tally_tb_credit = sum(e.get("credit", 0) for e in trial_balance)

	print(f"\n[2f] Opening Balance (Journal Entry vs Tally Trial Balance):")
	print(f"     JE rows: {je_tot.get('row_count', 0)}")
	print(f"     JE total debit:  {je_tot.get('total_debit', 0):>15.2f}")
	print(f"     JE total credit: {je_tot.get('total_credit', 0):>15.2f}")
	print(f"     Tally TB total debit (abs):  {tally_tb_debit:>15.2f}")
	print(f"     Tally TB total credit:       {tally_tb_credit:>15.2f}")
	print(f"     Tally TB entries: {len(trial_balance)}")

	# 2g. Stock Reconciliation totals
	sr_total = frappe.db.sql("""
		SELECT SUM(sri.amount) as total_value, COUNT(*) as item_count
		FROM `tabStock Reconciliation Item` sri
		JOIN `tabStock Reconciliation` sr ON sri.parent = sr.name
		WHERE sr.company=%s AND sr.docstatus=1 AND sr.purpose='Opening Stock'
	""", company_name, as_dict=1)
	sr_tot = sr_total[0] if sr_total else {}

	tally_stock_value = sum(s.get("value", 0) for s in stock_summary if s.get("qty", 0) > 0)

	print(f"\n[2g] Opening Stock (Stock Reconciliation vs Tally Stock Summary):")
	print(f"     SR items: {sr_tot.get('item_count', 0)}")
	print(f"     SR total value:         {sr_tot.get('total_value', 0):>15.2f}")
	print(f"     Tally stock total value: {tally_stock_value:>15.2f}")
	tally_stock_items_with_qty = [s for s in stock_summary if s.get("qty", 0) > 0]
	print(f"     Tally items with qty>0: {len(tally_stock_items_with_qty)}")

	# 2h. Items in Tally stock summary NOT in Stock Reconciliation
	sr_items = set(frappe.db.sql_list("""
		SELECT sri.item_code FROM `tabStock Reconciliation Item` sri
		JOIN `tabStock Reconciliation` sr ON sri.parent = sr.name
		WHERE sr.company=%s AND sr.docstatus=1 AND sr.purpose='Opening Stock'
	""", company_name))
	tally_stock_names = set(s["item"] for s in tally_stock_items_with_qty)
	missing_in_sr = tally_stock_names - sr_items
	print(f"\n[2h] Items in Tally stock but NOT in Stock Reconciliation: {len(missing_in_sr)}")
	for name in sorted(missing_in_sr)[:20]:
		# Check if item exists in ERPNext at all
		exists = frappe.db.exists("Item", name)
		print(f"  - {name}  (Item exists: {bool(exists)})")

	# ── Part 3: Name matching ────────────────────────────────────────
	print("\n" + "=" * 70)
	print("PART 3: NAME MATCHING ISSUES")
	print("=" * 70)

	# 3a. Customer name mismatches
	erp_customer_names = set(frappe.db.sql_list("SELECT customer_name FROM tabCustomer"))
	tally_customer_names = set(c.get("mailing_name") or c["name"] for c in tally_customers)
	tally_only_customers = tally_customer_names - erp_customer_names
	erp_only_customers = erp_customer_names - tally_customer_names
	print(f"\n[3a] Customers in Tally but NOT in ERPNext: {len(tally_only_customers)}")
	for n in sorted(tally_only_customers)[:15]:
		print(f"  - {n}")
	if erp_only_customers:
		print(f"     Customers in ERPNext but NOT in Tally: {len(erp_only_customers)}")

	# 3b. Supplier name mismatches
	erp_supplier_names = set(frappe.db.sql_list("SELECT supplier_name FROM tabSupplier"))
	tally_supplier_names = set(s.get("mailing_name") or s["name"] for s in tally_suppliers)
	tally_only_suppliers = tally_supplier_names - erp_supplier_names
	print(f"\n[3b] Suppliers in Tally but NOT in ERPNext: {len(tally_only_suppliers)}")
	for n in sorted(tally_only_suppliers)[:15]:
		print(f"  - {n}")

	# 3c. Account name mismatches (tally ledger name should exist as account_name in ERPNext)
	erp_account_names = set(frappe.db.sql_list(
		"SELECT account_name FROM tabAccount WHERE company=%s", company_name
	))
	tally_account_names = set(a["name"] for a in tally_accounts)
	tally_group_names = set(g["name"] for g in tally_data["groups"])
	all_tally_account_names = tally_account_names | tally_group_names
	tally_only_accounts = all_tally_account_names - erp_account_names
	print(f"\n[3c] Tally accounts/groups NOT in ERPNext: {len(tally_only_accounts)}")
	for n in sorted(tally_only_accounts)[:15]:
		print(f"  - {n}")

	# 3d. Item name mismatches
	erp_item_codes = set(frappe.db.sql_list("SELECT item_code FROM tabItem"))
	tally_item_names = set(si["name"] for si in stock_items)
	tally_only_items = tally_item_names - erp_item_codes
	erp_only_items = erp_item_codes - tally_item_names
	print(f"\n[3d] Items in Tally but NOT in ERPNext: {len(tally_only_items)}")
	for n in sorted(tally_only_items)[:15]:
		print(f"  - {n}")
	if erp_only_items:
		print(f"     Items in ERPNext but NOT in Tally: {len(erp_only_items)}")

	# 3e. Warehouse / Godown name mismatches
	erp_wh_names = set(frappe.db.sql_list(
		"SELECT warehouse_name FROM tabWarehouse WHERE company=%s", company_name
	))
	tally_godown_names = set(g["name"] for g in godowns)
	tally_only_wh = tally_godown_names - erp_wh_names
	print(f"\n[3e] Godowns in Tally but NOT in ERPNext warehouses: {len(tally_only_wh)}")
	for n in sorted(tally_only_wh)[:10]:
		print(f"  - {n}")

	print("\n" + "=" * 70)
	print("AUDIT COMPLETE")
	print("=" * 70)

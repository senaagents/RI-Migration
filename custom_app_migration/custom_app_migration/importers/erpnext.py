"""ERPNext document importer.

Creates ERPNext documents from transformed Tally data in dependency order.
Each method handles duplicate detection and error logging.
"""

import logging

import frappe
from frappe.utils.nestedset import rebuild_tree

logger = logging.getLogger(__name__)


class ERPNextImporter:
	def __init__(self, company_name, dry_run=False):
		self.company = company_name
		self.dry_run = dry_run
		self.results = {
			"created": [],
			"skipped": [],
			"errors": [],
		}

	def import_all(self, data):
		"""Import all data in dependency order.

		Args:
			data: dict from transform_all() with keys:
			      accounts, customers, suppliers, item_groups, items, warehouses
		"""
		self._import_chart_of_accounts(data.get("accounts", []))
		self._import_customer_groups(data.get("customers", []))
		self._import_supplier_groups(data.get("suppliers", []))
		self._import_item_groups(data.get("item_groups", []))
		self._import_items(data.get("items", []))
		self._import_warehouses(data.get("warehouses", []))
		self._import_customers(data.get("customers", []))
		self._import_suppliers(data.get("suppliers", []))
		return self.results

	def _insert_doc(self, doc_dict):
		"""Insert a single doc. Returns True if created, False if skipped/error."""
		doctype = doc_dict.get("doctype")
		# Remove internal _tally_* keys before inserting
		clean = {k: v for k, v in doc_dict.items() if not k.startswith("_tally_")}

		if self.dry_run:
			self.results["created"].append({"doctype": doctype, "name": clean.get("name", clean.get("account_name", clean.get("item_code", "?")))})
			return True

		try:
			doc = frappe.get_doc(clean)
			doc.flags.ignore_permissions = True
			doc.flags.ignore_mandatory = True
			doc.insert(ignore_permissions=True)
			self.results["created"].append({"doctype": doctype, "name": doc.name})
			return True
		except frappe.DuplicateEntryError:
			name = clean.get("name", clean.get("account_name", clean.get("item_code", "?")))
			self.results["skipped"].append({"doctype": doctype, "name": name, "reason": "duplicate"})
			return False
		except Exception as exc:
			name = clean.get("name", clean.get("account_name", clean.get("item_code", "?")))
			self.results["errors"].append({"doctype": doctype, "name": name, "error": str(exc)})
			logger.warning("Failed to insert %s %s: %s", doctype, name, exc)
			return False

	def _import_chart_of_accounts(self, accounts):
		"""Import accounts: groups first (sorted by depth), then leaves."""
		if not accounts:
			return

		# Separate groups and leaves
		groups = [a for a in accounts if a.get("is_group")]
		leaves = [a for a in accounts if not a.get("is_group")]

		# Sort groups by depth (root first): count segments in parent_account
		def depth(acc):
			parent = acc.get("parent_account", "")
			return parent.count(" - ") if parent else 0

		groups.sort(key=depth)

		# Insert root groups first (no parent)
		for acc in groups:
			if not acc.get("parent_account"):
				self._insert_doc(acc)

		# Then non-root groups
		for acc in groups:
			if acc.get("parent_account"):
				self._insert_doc(acc)

		# Then leaf accounts
		for acc in leaves:
			self._insert_doc(acc)

		# Rebuild nested set tree
		if not self.dry_run:
			try:
				rebuild_tree("Account")
			except Exception as exc:
				logger.warning("Failed to rebuild Account tree: %s", exc)

		frappe.db.commit()

	def _import_customer_groups(self, customers):
		"""Create Customer Group docs for unique groups referenced by customers."""
		seen = set()
		for c in customers:
			group = c.get("customer_group", "All Customer Groups")
			if group == "All Customer Groups" or group in seen:
				continue
			seen.add(group)
			if not frappe.db.exists("Customer Group", group):
				self._insert_doc({
					"doctype": "Customer Group",
					"customer_group_name": group,
					"parent_customer_group": "All Customer Groups",
					"is_group": 0,
				})
		if not self.dry_run:
			frappe.db.commit()

	def _import_supplier_groups(self, suppliers):
		"""Create Supplier Group docs for unique groups referenced by suppliers."""
		seen = set()
		for s in suppliers:
			group = s.get("supplier_group", "All Supplier Groups")
			if group == "All Supplier Groups" or group in seen:
				continue
			seen.add(group)
			if not frappe.db.exists("Supplier Group", group):
				self._insert_doc({
					"doctype": "Supplier Group",
					"supplier_group_name": group,
					"parent_supplier_group": "All Supplier Groups",
				})
		if not self.dry_run:
			frappe.db.commit()

	def _import_item_groups(self, item_groups):
		"""Import Item Groups. Parent groups before children."""
		# Sort: no parent first, then with parent
		no_parent = [ig for ig in item_groups if not ig.get("parent_item_group") or ig["parent_item_group"] == "All Item Groups"]
		with_parent = [ig for ig in item_groups if ig.get("parent_item_group") and ig["parent_item_group"] != "All Item Groups"]

		for ig in no_parent:
			if not frappe.db.exists("Item Group", ig["item_group_name"]):
				self._insert_doc(ig)
		for ig in with_parent:
			if not frappe.db.exists("Item Group", ig["item_group_name"]):
				self._insert_doc(ig)

		if not self.dry_run:
			frappe.db.commit()

	def _import_items(self, items):
		"""Import Items."""
		for item in items:
			if not frappe.db.exists("Item", item["item_code"]):
				self._insert_doc(item)

		if not self.dry_run:
			frappe.db.commit()

	def _import_warehouses(self, warehouses):
		"""Import Warehouses."""
		for wh in warehouses:
			if not frappe.db.exists("Warehouse", {"warehouse_name": wh["warehouse_name"], "company": self.company}):
				self._insert_doc(wh)

		if not self.dry_run:
			frappe.db.commit()

	def _import_customers(self, customers):
		"""Import Customers with optional Address creation."""
		for c in customers:
			customer_name = c.get("customer_name", "")
			if frappe.db.exists("Customer", {"customer_name": customer_name}):
				self.results["skipped"].append({"doctype": "Customer", "name": customer_name, "reason": "duplicate"})
				continue

			self._insert_doc(c)

			# Create Address if we have address data
			address = c.get("_tally_address", "")
			if address and not self.dry_run:
				self._create_address(
					link_doctype="Customer",
					link_name=customer_name,
					address_line=address,
					state=c.get("_tally_state", ""),
					pincode=c.get("_tally_pincode", ""),
					email=c.get("_tally_email", ""),
					phone=c.get("_tally_phone", ""),
				)

		if not self.dry_run:
			frappe.db.commit()

	def _import_suppliers(self, suppliers):
		"""Import Suppliers with optional Address creation."""
		for s in suppliers:
			supplier_name = s.get("supplier_name", "")
			if frappe.db.exists("Supplier", {"supplier_name": supplier_name}):
				self.results["skipped"].append({"doctype": "Supplier", "name": supplier_name, "reason": "duplicate"})
				continue

			self._insert_doc(s)

			address = s.get("_tally_address", "")
			if address and not self.dry_run:
				self._create_address(
					link_doctype="Supplier",
					link_name=supplier_name,
					address_line=address,
					state=s.get("_tally_state", ""),
					pincode=s.get("_tally_pincode", ""),
					email=s.get("_tally_email", ""),
					phone=s.get("_tally_phone", ""),
				)

		if not self.dry_run:
			frappe.db.commit()

	def _create_address(self, link_doctype, link_name, address_line, state="", pincode="", email="", phone=""):
		"""Create an Address doc linked to a Customer or Supplier."""
		lines = address_line.split("\n") if address_line else []
		try:
			addr = frappe.get_doc({
				"doctype": "Address",
				"address_title": link_name,
				"address_type": "Billing",
				"address_line1": lines[0] if lines else "",
				"address_line2": lines[1] if len(lines) > 1 else "",
				"city": lines[-1] if len(lines) > 2 else (state or ""),
				"state": state,
				"pincode": pincode,
				"country": "India",
				"email_id": email,
				"phone": phone,
				"links": [{"link_doctype": link_doctype, "link_name": link_name}],
			})
			addr.flags.ignore_permissions = True
			addr.flags.ignore_mandatory = True
			addr.insert(ignore_permissions=True)
		except Exception as exc:
			logger.warning("Failed to create address for %s %s: %s", link_doctype, link_name, exc)

	def import_opening_balances(self, trial_balance, as_of_date=None):
		"""Create a Journal Entry for opening account balances.

		Args:
			trial_balance: list of {account, debit, credit} from parse_trial_balance.
			as_of_date: YYYY-MM-DD date string. Defaults to fiscal year start.
		"""
		if not trial_balance or self.dry_run:
			return

		if not as_of_date:
			fy = frappe.db.get_value("Fiscal Year", {"company": self.company}, "year_start_date")
			as_of_date = str(fy) if fy else "2026-04-01"

		rows = []
		for entry in trial_balance:
			debit = entry.get("debit", 0)
			credit = entry.get("credit", 0)
			net = credit - debit
			if abs(net) < 0.01:
				continue

			account_name = entry["account"]
			# Try to find the account in ERPNext
			account = frappe.db.get_value("Account", {"account_name": account_name, "company": self.company}, "name")
			if not account:
				continue

			row = {"account": account, "party_type": "", "party": ""}
			if net > 0:
				row["credit_in_account_currency"] = net
			else:
				row["debit_in_account_currency"] = abs(net)
			rows.append(row)

		if not rows:
			return

		try:
			je = frappe.get_doc({
				"doctype": "Journal Entry",
				"company": self.company,
				"posting_date": as_of_date,
				"voucher_type": "Opening Entry",
				"is_opening": "Yes",
				"accounts": rows,
			})
			je.flags.ignore_permissions = True
			je.insert(ignore_permissions=True)
			je.submit()
			self.results["created"].append({"doctype": "Journal Entry", "name": je.name})
		except Exception as exc:
			self.results["errors"].append({"doctype": "Journal Entry", "name": "Opening Entry", "error": str(exc)})
			logger.warning("Failed to create opening journal entry: %s", exc)

	def import_opening_stock(self, stock_data, warehouse=None, as_of_date=None):
		"""Create a Stock Reconciliation for opening inventory.

		Args:
			stock_data: list of {item, qty, rate, value} from parse_stock_summary.
			warehouse: Default warehouse name.
			as_of_date: YYYY-MM-DD date string.
		"""
		if not stock_data or self.dry_run:
			return

		if not as_of_date:
			fy = frappe.db.get_value("Fiscal Year", {"company": self.company}, "year_start_date")
			as_of_date = str(fy) if fy else "2026-04-01"

		if not warehouse:
			warehouse = frappe.db.get_value("Warehouse", {"company": self.company, "is_group": 0}, "name")

		items = []
		for entry in stock_data:
			item_code = entry.get("item", "")
			qty = entry.get("qty", 0)
			rate = entry.get("rate", 0)
			if qty <= 0 or not item_code:
				continue
			if not frappe.db.exists("Item", item_code):
				continue

			items.append({
				"item_code": item_code,
				"warehouse": warehouse,
				"qty": qty,
				"valuation_rate": rate,
			})

		if not items:
			return

		try:
			sr = frappe.get_doc({
				"doctype": "Stock Reconciliation",
				"company": self.company,
				"posting_date": as_of_date,
				"purpose": "Opening Stock",
				"items": items,
			})
			sr.flags.ignore_permissions = True
			sr.insert(ignore_permissions=True)
			sr.submit()
			self.results["created"].append({"doctype": "Stock Reconciliation", "name": sr.name})
		except Exception as exc:
			self.results["errors"].append({"doctype": "Stock Reconciliation", "name": "Opening Stock", "error": str(exc)})
			logger.warning("Failed to create opening stock reconciliation: %s", exc)

	def get_summary(self):
		return {
			"created": len(self.results["created"]),
			"skipped": len(self.results["skipped"]),
			"errors": len(self.results["errors"]),
			"details": self.results,
		}

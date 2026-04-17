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
		"""Import Item Groups in topological order (parents before children)."""
		if not item_groups:
			return

		# Build lookup and sort by hierarchy depth
		by_name = {ig["item_group_name"]: ig for ig in item_groups if ig.get("item_group_name")}

		def _depth(ig):
			d = 0
			parent = ig.get("parent_item_group", "All Item Groups")
			visited = set()
			while parent and parent != "All Item Groups" and parent not in visited:
				visited.add(parent)
				d += 1
				p = by_name.get(parent)
				parent = p.get("parent_item_group", "All Item Groups") if p else None
			return d

		sorted_groups = sorted(by_name.values(), key=_depth)

		for ig in sorted_groups:
			name = ig.get("item_group_name", "")
			if not name:
				continue
			# Ensure parent exists or fall back to "All Item Groups"
			parent = ig.get("parent_item_group", "All Item Groups")
			if parent and parent != "All Item Groups" and not frappe.db.exists("Item Group", parent):
				ig["parent_item_group"] = "All Item Groups"
			if not frappe.db.exists("Item Group", name):
				self._insert_doc(ig)

		if not self.dry_run:
			frappe.db.commit()

	def _import_items(self, items):
		"""Import Items. Falls back item_group to 'All Item Groups' if parent doesn't exist."""
		for item in items:
			if not item.get("item_code"):
				continue
			# Ensure item group exists, fall back to "All Item Groups"
			ig = item.get("item_group", "All Item Groups")
			if ig and ig != "All Item Groups" and not frappe.db.exists("Item Group", ig):
				item["item_group"] = "All Item Groups"
			if not frappe.db.exists("Item", item["item_code"]):
				self._insert_doc(item)

		if not self.dry_run:
			frappe.db.commit()

	def _import_cost_centres(self, cost_centres):
		"""Import Cost Centers. Skips duplicates."""
		if not cost_centres:
			return

		abbr = frappe.db.get_value("Company", self.company, "abbr") or self.company[:2].upper()

		for cc in cost_centres:
			name = f"{cc['cost_center_name']} - {abbr}"
			if frappe.db.exists("Cost Center", name):
				self.results["skipped"].append({"doctype": "Cost Center", "name": name, "reason": "duplicate"})
				continue
			self._insert_doc(cc)

		if not self.dry_run:
			try:
				from frappe.utils.nestedset import rebuild_tree
				rebuild_tree("Cost Center")
			except Exception as exc:
				logger.warning("Failed to rebuild Cost Center tree: %s", exc)
			frappe.db.commit()

	def _import_warehouses(self, warehouses):
		"""Import Warehouses in topological order (parents before children)."""
		if not warehouses:
			return

		# Sort: no parent first, then with parent
		no_parent = [wh for wh in warehouses if not wh.get("parent_warehouse")]
		with_parent = [wh for wh in warehouses if wh.get("parent_warehouse")]

		for wh in no_parent:
			if not frappe.db.exists("Warehouse", {"warehouse_name": wh["warehouse_name"], "company": self.company}):
				self._insert_doc(wh)

		for wh in with_parent:
			# If parent warehouse doesn't exist, clear the parent reference
			if wh.get("parent_warehouse") and not frappe.db.exists("Warehouse", wh["parent_warehouse"]):
				wh["parent_warehouse"] = ""
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

		Skips Receivable/Payable accounts (need party), group accounts,
		and special accounts like Profit & Loss and Opening Stock.
		"""
		if not trial_balance or self.dry_run:
			return

		if not as_of_date:
			fy = frappe.db.get_value("Fiscal Year", {"company": self.company}, "year_start_date")
			as_of_date = str(fy) if fy else "2026-04-01"

		# Account types that require party_type/party — skip these
		_SKIP_ACCOUNT_TYPES = {"Receivable", "Payable"}
		# Account names to skip entirely
		_SKIP_NAMES = {"profit & loss a/c", "opening stock", "stock-in-hand"}

		rows = []
		skipped_party = 0
		for entry in trial_balance:
			debit = entry.get("debit", 0)
			credit = entry.get("credit", 0)
			net = credit - debit
			if abs(net) < 0.01:
				continue

			account_name = entry["account"]
			if account_name.lower().strip() in _SKIP_NAMES:
				continue

			# Try to find the account in ERPNext
			account = frappe.db.get_value(
				"Account",
				{"account_name": account_name, "company": self.company},
				["name", "account_type", "is_group"],
				as_dict=True,
			)
			if not account:
				continue

			# Skip group accounts and receivable/payable accounts
			if account.get("is_group"):
				continue
			if account.get("account_type") in _SKIP_ACCOUNT_TYPES:
				skipped_party += 1
				continue

			row = {"account": account["name"], "party_type": "", "party": ""}
			if net > 0:
				row["credit_in_account_currency"] = net
			else:
				row["debit_in_account_currency"] = abs(net)
			rows.append(row)

		if skipped_party:
			logger.info("Skipped %d receivable/payable accounts (need opening invoices instead)", skipped_party)

		if not rows:
			return

		# Balance the entry — add a "Temporary Opening" row if totals don't match
		total_debit = sum(r.get("debit_in_account_currency", 0) for r in rows)
		total_credit = sum(r.get("credit_in_account_currency", 0) for r in rows)
		diff = round(total_credit - total_debit, 2)
		if abs(diff) > 0.01:
			# Find or use a temporary opening account
			temp_account = frappe.db.get_value(
				"Account",
				{"account_name": "Temporary Opening", "company": self.company},
				"name",
			)
			if not temp_account:
				# Try the standard naming convention
				temp_account = f"Temporary Opening - {frappe.db.get_value('Company', self.company, 'abbr')}"
				if not frappe.db.exists("Account", temp_account):
					temp_account = None

			if temp_account:
				balancing_row = {"account": temp_account, "party_type": "", "party": ""}
				if diff > 0:
					balancing_row["debit_in_account_currency"] = diff
				else:
					balancing_row["credit_in_account_currency"] = abs(diff)
				rows.append(balancing_row)
				logger.info("Added balancing row of %.2f to %s", diff, temp_account)
			else:
				logger.warning("No Temporary Opening account found — JE will be unbalanced by %.2f", diff)

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
			self.results["created"].append({"doctype": "Journal Entry", "name": je.name, "rows": len(rows)})
			logger.info("Created opening JE with %d rows (skipped %d party accounts)", len(rows), skipped_party)
		except Exception as exc:
			self.results["errors"].append({"doctype": "Journal Entry", "name": "Opening Entry", "error": str(exc)})
			logger.warning("Failed to create opening journal entry: %s", exc)

	def import_opening_balances_from_ledgers(self, ledgers, groups, company_abbr, as_of_date=None):
		"""Create a Journal Entry from individual ledger opening balances.

		Uses leaf-level ledger data (not group-level Trial Balance).
		Skips customer/supplier ledgers (handled by opening invoices).
		"""
		if not ledgers or self.dry_run:
			return

		from agentapp_migration.agentapp_migration.transformers.tally_to_erpnext import classify_ledger

		if not as_of_date:
			fy = frappe.db.get_value("Fiscal Year", {"company": self.company}, "year_start_date")
			as_of_date = str(fy) if fy else "2026-04-01"

		groups_by_name = {g["name"]: g for g in groups}
		_SKIP_ACCOUNT_TYPES = {"Receivable", "Payable", "Depreciation", "Accumulated Depreciation"}
		_SKIP_NAMES = {"profit & loss a/c", "opening stock", "stock-in-hand"}

		rows = []
		skipped_party = 0
		skipped_not_found = 0

		for led in ledgers:
			opening = led.get("opening_balance", 0)
			if abs(opening) < 0.01:
				continue

			ledger_name = led["name"]
			if ledger_name.lower().strip() in _SKIP_NAMES:
				continue

			# Skip customer/supplier ledgers — handled by opening invoices
			classification = classify_ledger(led, groups_by_name)
			if classification in ("customer", "supplier"):
				skipped_party += 1
				continue

			# Find the account in ERPNext
			account_full = f"{ledger_name} - {company_abbr}"
			account_meta = frappe.db.get_value(
				"Account", account_full,
				["name", "account_type", "is_group"],
				as_dict=True,
			)
			if not account_meta:
				# Try fuzzy match by account_name
				account_meta = frappe.db.get_value(
					"Account",
					{"account_name": ledger_name, "company": self.company},
					["name", "account_type", "is_group"],
					as_dict=True,
				)
			if not account_meta:
				skipped_not_found += 1
				continue

			if account_meta.get("is_group"):
				continue
			if account_meta.get("account_type") in _SKIP_ACCOUNT_TYPES:
				skipped_party += 1
				continue

			# Tally convention: negative opening_balance = debit balance (asset/expense)
			# positive opening_balance = credit balance (liability/income)
			row = {"account": account_meta["name"], "party_type": "", "party": ""}
			if opening > 0:
				row["credit_in_account_currency"] = opening
			else:
				row["debit_in_account_currency"] = abs(opening)
			rows.append(row)

		logger.info(
			"Opening balances from ledgers: %d rows, skipped %d party, %d not found",
			len(rows), skipped_party, skipped_not_found,
		)

		if not rows:
			return

		# Balance the entry with Temporary Opening
		total_debit = sum(r.get("debit_in_account_currency", 0) for r in rows)
		total_credit = sum(r.get("credit_in_account_currency", 0) for r in rows)
		diff = round(total_credit - total_debit, 2)

		temp_account = frappe.db.get_value(
			"Account", {"account_name": "Temporary Opening", "company": self.company}, "name",
		) or f"Temporary Opening - {company_abbr}"

		if abs(diff) > 0.01 and frappe.db.exists("Account", temp_account):
			balancing_row = {"account": temp_account, "party_type": "", "party": ""}
			if diff > 0:
				balancing_row["debit_in_account_currency"] = diff
			else:
				balancing_row["credit_in_account_currency"] = abs(diff)
			rows.append(balancing_row)
			logger.info("Added balancing row of %.2f to %s", diff, temp_account)

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
			self.results["created"].append({
				"doctype": "Journal Entry (Ledger Opening)",
				"name": je.name,
				"rows": len(rows),
				"total_debit": round(total_debit + max(0, diff), 2),
			})
			logger.info("Created ledger-level opening JE %s with %d rows, total %.2f",
						je.name, len(rows), total_debit + max(0, diff))
		except Exception as exc:
			self.results["errors"].append({
				"doctype": "Journal Entry (Ledger Opening)", "name": "Opening Entry", "error": str(exc),
			})
			logger.warning("Failed to create ledger opening journal entry: %s", exc)

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

			row = {
				"item_code": item_code,
				"warehouse": warehouse,
				"qty": qty,
				"valuation_rate": rate,
			}
			if rate == 0:
				row["allow_zero_valuation_rate"] = 1
			items.append(row)

		if not items:
			return

		# For Opening Stock, the expense_account must be a Balance Sheet account
		# (Asset/Liability root_type). Temporary Opening is the standard choice.
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		expense_account = frappe.db.get_value(
			"Account",
			{"account_name": "Temporary Opening", "company": self.company},
			"name",
		) or f"Temporary Opening - {abbr}"

		try:
			sr = frappe.get_doc({
				"doctype": "Stock Reconciliation",
				"company": self.company,
				"posting_date": as_of_date,
				"purpose": "Opening Stock",
				"expense_account": expense_account,
				"items": items,
			})
			sr.flags.ignore_permissions = True
			sr.insert(ignore_permissions=True)
			sr.submit()
			self.results["created"].append({"doctype": "Stock Reconciliation", "name": sr.name, "items": len(items)})
		except Exception as exc:
			self.results["errors"].append({"doctype": "Stock Reconciliation", "name": "Opening Stock", "error": str(exc)})
			logger.warning("Failed to create opening stock reconciliation: %s", exc)

	# ------------------------------------------------------------------
	# Opening receivable / payable invoices
	# ------------------------------------------------------------------

	def import_opening_invoices(self, customer_balances, supplier_balances, as_of_date=None):
		"""Create opening Sales/Purchase Invoices for party opening balances.

		Uses the same pattern as ERPNext's Opening Invoice Creation Tool:
		is_opening=Yes, Temporary Opening as income/expense account, update_stock=0.

		Args:
			customer_balances: list of {party, amount} where amount > 0 means receivable
			supplier_balances: list of {party, amount} where amount > 0 means payable
			as_of_date: posting date (defaults to FY start)
		"""
		if self.dry_run:
			return

		if not as_of_date:
			fy = frappe.db.get_value("Fiscal Year", {"company": self.company}, "year_start_date")
			as_of_date = str(fy) if fy else "2026-04-01"

		abbr = frappe.db.get_value("Company", self.company, "abbr")
		temp_account = frappe.db.get_value(
			"Account", {"account_type": "Temporary", "company": self.company}, "name"
		) or f"Temporary Opening - {abbr}"
		cost_center = frappe.db.get_value("Company", self.company, "cost_center") or f"Main - {abbr}"

		si_count = 0
		pi_count = 0

		# Sales Invoices for customer receivables
		for entry in customer_balances:
			party = entry.get("party", "")
			amount = abs(entry.get("amount", 0))
			if amount < 0.01 or not party:
				continue

			# Duplicate detection: check if opening SI already exists for this customer
			existing = frappe.db.get_value(
				"Sales Invoice",
				{"customer": party, "company": self.company, "is_opening": "Yes", "docstatus": ["!=", 2]},
				"name",
			)
			if existing:
				self.results["skipped"].append({
					"doctype": "Sales Invoice", "name": existing,
					"reason": f"Opening invoice already exists for {party}",
				})
				continue

			try:
				doc = frappe.get_doc({
					"doctype": "Sales Invoice",
					"company": self.company,
					"customer": party,
					"posting_date": as_of_date,
					"due_date": as_of_date,
					"set_posting_time": 1,
					"is_opening": "Yes",
					"update_stock": 0,
					"disable_rounded_total": 1,
					"items": [{
						"item_name": "Opening Balance",
						"description": "Opening Balance",
						"qty": 1,
						"rate": amount,
						"uom": "Nos",
						"income_account": temp_account,
						"cost_center": cost_center,
					}],
				})
				doc.flags.ignore_permissions = True
				doc.flags.ignore_mandatory = True
				doc.insert(ignore_permissions=True)
				doc.submit()
				si_count += 1
				self.results["created"].append({"doctype": "Sales Invoice (Opening)", "name": doc.name})
			except Exception as exc:
				self.results["errors"].append({
					"doctype": "Sales Invoice (Opening)", "name": party, "error": str(exc),
				})
				logger.warning("Failed to create opening SI for %s: %s", party, exc)

		if si_count:
			frappe.db.commit()
			logger.info("Created %d opening Sales Invoices", si_count)

		# Purchase Invoices for supplier payables
		for entry in supplier_balances:
			party = entry.get("party", "")
			amount = abs(entry.get("amount", 0))
			if amount < 0.01 or not party:
				continue

			# Duplicate detection
			existing = frappe.db.get_value(
				"Purchase Invoice",
				{"supplier": party, "company": self.company, "is_opening": "Yes", "docstatus": ["!=", 2]},
				"name",
			)
			if existing:
				self.results["skipped"].append({
					"doctype": "Purchase Invoice", "name": existing,
					"reason": f"Opening invoice already exists for {party}",
				})
				continue

			try:
				doc = frappe.get_doc({
					"doctype": "Purchase Invoice",
					"company": self.company,
					"supplier": party,
					"posting_date": as_of_date,
					"due_date": as_of_date,
					"set_posting_time": 1,
					"is_opening": "Yes",
					"update_stock": 0,
					"disable_rounded_total": 1,
					"items": [{
						"item_name": "Opening Balance",
						"description": "Opening Balance",
						"qty": 1,
						"rate": amount,
						"uom": "Nos",
						"expense_account": temp_account,
						"cost_center": cost_center,
					}],
				})
				doc.flags.ignore_permissions = True
				doc.flags.ignore_mandatory = True
				doc.insert(ignore_permissions=True)
				doc.submit()
				pi_count += 1
				self.results["created"].append({"doctype": "Purchase Invoice (Opening)", "name": doc.name})
			except Exception as exc:
				self.results["errors"].append({
					"doctype": "Purchase Invoice (Opening)", "name": party, "error": str(exc),
				})
				logger.warning("Failed to create opening PI for %s: %s", party, exc)

		if pi_count:
			frappe.db.commit()
			logger.info("Created %d opening Purchase Invoices", pi_count)

	# ------------------------------------------------------------------
	# Voucher / transaction imports
	# ------------------------------------------------------------------

	def import_vouchers(self, voucher_data):
		"""Import all voucher types in dependency order.

		Invoices first (so payment references resolve), then payments, then journals.

		Args:
			voucher_data: dict with keys: sales_invoices, purchase_invoices,
			              payment_entries, journal_entries
		"""
		self._import_sales_invoices(voucher_data.get("sales_invoices", []))
		self._import_purchase_invoices(voucher_data.get("purchase_invoices", []))
		self._import_payment_entries(voucher_data.get("payment_entries", []))
		self._import_journal_entries(voucher_data.get("journal_entries", []))

	def _resolve_account_fuzzy(self, tally_ledger_name):
		"""Find an ERPNext Account by name for this company.

		Tries exact match on name, then on account_name field.
		"""
		exact = frappe.db.get_value(
			"Account", {"name": tally_ledger_name, "company": self.company}, "name"
		)
		if exact:
			return exact
		by_name = frappe.db.get_value(
			"Account", {"account_name": tally_ledger_name, "company": self.company}, "name"
		)
		if by_name:
			return by_name
		return None

	def _resolve_warehouse(self):
		"""Get the default non-group warehouse for this company."""
		return frappe.db.get_value(
			"Warehouse", {"company": self.company, "is_group": 0}, "name"
		)

	@staticmethod
	def _tally_ref(voucher_type, voucher_number):
		"""Build a deterministic Tally reference tag for duplicate detection.

		Stored in a searchable text field on each ERPNext doc so re-running
		the migration skips already-imported vouchers.
		Always returns a non-empty string (bank transactions require reference_no).
		"""
		vt = (voucher_type or "").strip()
		vn = (voucher_number or "").strip()
		if vt and vn:
			return f"TALLY-{vt}-{vn}"
		if vt:
			return f"TALLY-{vt}-NONUM"
		return "TALLY-IMPORT"

	def _voucher_exists(self, doctype, tally_ref, search_field):
		"""Check if a doc with this Tally reference already exists.

		Returns the existing doc name, or None.
		"""
		if not tally_ref or self.dry_run:
			return None
		return frappe.db.get_value(
			doctype,
			{search_field: tally_ref, "company": self.company, "docstatus": ["!=", 2]},
			"name",
		)

	def _submit_doc(self, doc, doctype_label, identifier):
		"""Try to submit a doc. Logs error on failure but doesn't raise."""
		try:
			doc.submit()
			self.results["created"].append({"doctype": doctype_label, "name": doc.name})
			return True
		except Exception as exc:
			self.results["errors"].append({
				"doctype": doctype_label, "name": doc.name,
				"error": f"Insert OK, submit failed: {exc}",
			})
			logger.warning("Submit failed for %s %s: %s", doctype_label, identifier, exc)
			return False

	def _import_sales_invoices(self, invoices):
		"""Import Sales Invoices from transformed voucher data.

		Each invoice dict:
			company, customer, posting_date, due_date, voucher_number,
			narration, place_of_supply, currency,
			items: [{item_code, item_name, qty, rate, amount, uom, warehouse,
			         income_account, hsn_code}],
			taxes: [{charge_type, account_head, rate, tax_amount, description}],
			is_return, tally_guid
		"""
		if not invoices:
			return

		warehouse = self._resolve_warehouse()
		count = 0

		for inv in invoices:
			tally_ref = self._tally_ref(
				inv.get("_tally_voucher_type", "Sales"),
				inv.get("voucher_number", ""),
			)

			# Duplicate detection via po_no field
			existing = self._voucher_exists("Sales Invoice", tally_ref, "po_no")
			if existing:
				self.results["skipped"].append({
					"doctype": "Sales Invoice", "name": existing,
					"reason": f"duplicate (tally ref {tally_ref})",
				})
				continue

			customer_name = inv.get("customer", "")
			if not customer_name:
				self.results["errors"].append({
					"doctype": "Sales Invoice", "name": inv.get("voucher_number", "?"),
					"error": "No customer specified",
				})
				continue

			if not self.dry_run and not frappe.db.exists("Customer", {"customer_name": customer_name}):
				self.results["skipped"].append({
					"doctype": "Sales Invoice", "name": inv.get("voucher_number", "?"),
					"reason": f"Customer not found: {customer_name}",
				})
				continue

			# Build items child table
			items = []
			for item in inv.get("items", []):
				item_code = item.get("item_code", "")
				if not self.dry_run and item_code and not frappe.db.exists("Item", item_code):
					self.results["skipped"].append({
						"doctype": "Sales Invoice Item", "name": item_code,
						"reason": "Item not found",
					})
					continue

				income_account = None
				if item.get("income_account"):
					income_account = self._resolve_account_fuzzy(item["income_account"])

				row = {
					"item_code": item_code or None,
					"item_name": item.get("item_name", item_code or "Service"),
					"qty": item.get("qty", 1),
					"rate": item.get("rate", 0),
					"amount": item.get("amount", 0),
					"uom": item.get("uom", "Nos"),
					"warehouse": item.get("warehouse") or warehouse,
				}
				if income_account:
					row["income_account"] = income_account
				if item.get("hsn_code"):
					row["gst_hsn_code"] = item["hsn_code"]
				items.append(row)

			if not items:
				self.results["skipped"].append({
					"doctype": "Sales Invoice", "name": inv.get("voucher_number", "?"),
					"reason": "No valid items",
				})
				continue

			# Build taxes child table
			taxes = self._build_tax_rows(inv.get("taxes", []))

			doc_dict = {
				"doctype": "Sales Invoice",
				"company": self.company,
				"customer": customer_name,
				"posting_date": inv.get("posting_date"),
				"due_date": inv.get("due_date") or inv.get("posting_date"),
				"set_posting_time": 1,
				"is_return": inv.get("is_return", 0),
				"items": items,
				"taxes": taxes,
				"remarks": inv.get("narration", ""),
				"disable_rounded_total": 1,
				"update_stock": 1,
				"po_no": tally_ref,
			}
			if inv.get("place_of_supply"):
				doc_dict["place_of_supply"] = inv["place_of_supply"]

			if self.dry_run:
				self.results["created"].append({
					"doctype": "Sales Invoice", "name": inv.get("voucher_number", "?"),
				})
				count += 1
				continue

			try:
				doc = frappe.get_doc(doc_dict)
				doc.flags.ignore_permissions = True
				doc.flags.ignore_mandatory = True
				doc.insert(ignore_permissions=True)
				self._submit_doc(doc, "Sales Invoice", inv.get("voucher_number", "?"))
				count += 1
			except frappe.DuplicateEntryError:
				self.results["skipped"].append({
					"doctype": "Sales Invoice", "name": inv.get("voucher_number", "?"),
					"reason": "duplicate",
				})
			except Exception as exc:
				self.results["errors"].append({
					"doctype": "Sales Invoice", "name": inv.get("voucher_number", "?"),
					"error": str(exc),
				})
				logger.warning("Failed Sales Invoice %s: %s", inv.get("voucher_number"), exc)

			if count % 50 == 0 and not self.dry_run:
				frappe.db.commit()

		if not self.dry_run:
			frappe.db.commit()

	def _import_purchase_invoices(self, invoices):
		"""Import Purchase Invoices from transformed voucher data.

		Each invoice dict:
			company, supplier, posting_date, due_date, voucher_number,
			narration, place_of_supply, currency,
			items: [{item_code, item_name, qty, rate, amount, uom, warehouse,
			         expense_account, hsn_code}],
			taxes: [{charge_type, account_head, rate, tax_amount, description}],
			is_return, tally_guid
		"""
		if not invoices:
			return

		warehouse = self._resolve_warehouse()
		count = 0

		for inv in invoices:
			tally_ref = self._tally_ref(
				inv.get("_tally_voucher_type", "Purchase"),
				inv.get("voucher_number", ""),
			)

			# Duplicate detection via bill_no field
			existing = self._voucher_exists("Purchase Invoice", tally_ref, "bill_no")
			if existing:
				self.results["skipped"].append({
					"doctype": "Purchase Invoice", "name": existing,
					"reason": f"duplicate (tally ref {tally_ref})",
				})
				continue

			supplier_name = inv.get("supplier", "")
			if not supplier_name:
				self.results["errors"].append({
					"doctype": "Purchase Invoice", "name": inv.get("voucher_number", "?"),
					"error": "No supplier specified",
				})
				continue

			if not self.dry_run and not frappe.db.exists("Supplier", {"supplier_name": supplier_name}):
				self.results["skipped"].append({
					"doctype": "Purchase Invoice", "name": inv.get("voucher_number", "?"),
					"reason": f"Supplier not found: {supplier_name}",
				})
				continue

			items = []
			for item in inv.get("items", []):
				item_code = item.get("item_code", "")
				if not self.dry_run and item_code and not frappe.db.exists("Item", item_code):
					self.results["skipped"].append({
						"doctype": "Purchase Invoice Item", "name": item_code,
						"reason": "Item not found",
					})
					continue

				expense_account = None
				if item.get("expense_account"):
					expense_account = self._resolve_account_fuzzy(item["expense_account"])

				row = {
					"item_code": item_code or None,
					"item_name": item.get("item_name", item_code or "Service"),
					"qty": item.get("qty", 1),
					"rate": item.get("rate", 0),
					"amount": item.get("amount", 0),
					"uom": item.get("uom", "Nos"),
					"warehouse": item.get("warehouse") or warehouse,
				}
				if expense_account:
					row["expense_account"] = expense_account
				if item.get("hsn_code"):
					row["gst_hsn_code"] = item["hsn_code"]
				items.append(row)

			if not items:
				self.results["skipped"].append({
					"doctype": "Purchase Invoice", "name": inv.get("voucher_number", "?"),
					"reason": "No valid items",
				})
				continue

			taxes = self._build_tax_rows(inv.get("taxes", []))

			doc_dict = {
				"doctype": "Purchase Invoice",
				"company": self.company,
				"supplier": supplier_name,
				"posting_date": inv.get("posting_date"),
				"due_date": inv.get("due_date") or inv.get("posting_date"),
				"set_posting_time": 1,
				"is_return": inv.get("is_return", 0),
				"items": items,
				"taxes": taxes,
				"remarks": inv.get("narration", ""),
				"disable_rounded_total": 1,
				"update_stock": 1,
				"bill_no": tally_ref,
				"bill_date": inv.get("posting_date"),
			}
			if inv.get("place_of_supply"):
				doc_dict["place_of_supply"] = inv["place_of_supply"]

			if self.dry_run:
				self.results["created"].append({
					"doctype": "Purchase Invoice", "name": inv.get("voucher_number", "?"),
				})
				count += 1
				continue

			try:
				doc = frappe.get_doc(doc_dict)
				doc.flags.ignore_permissions = True
				doc.flags.ignore_mandatory = True
				doc.insert(ignore_permissions=True)
				self._submit_doc(doc, "Purchase Invoice", inv.get("voucher_number", "?"))
				count += 1
			except frappe.DuplicateEntryError:
				self.results["skipped"].append({
					"doctype": "Purchase Invoice", "name": inv.get("voucher_number", "?"),
					"reason": "duplicate",
				})
			except Exception as exc:
				self.results["errors"].append({
					"doctype": "Purchase Invoice", "name": inv.get("voucher_number", "?"),
					"error": str(exc),
				})
				logger.warning("Failed Purchase Invoice %s: %s", inv.get("voucher_number"), exc)

			if count % 50 == 0 and not self.dry_run:
				frappe.db.commit()

		if not self.dry_run:
			frappe.db.commit()

	def _build_tax_rows(self, taxes):
		"""Build taxes child table rows, resolving account heads."""
		rows = []
		for tax in taxes:
			account_head = None
			if tax.get("account_head"):
				account_head = self._resolve_account_fuzzy(tax["account_head"])
			if not account_head and not self.dry_run:
				self.results["skipped"].append({
					"doctype": "Tax Row", "name": tax.get("account_head", "?"),
					"reason": "Tax account not found",
				})
				continue
			row = {
				"charge_type": tax.get("charge_type", "Actual"),
				"account_head": account_head or tax.get("account_head", ""),
				"description": tax.get("description", tax.get("account_head", "")),
				"category": "Total",
				"add_deduct_tax": "Add",
			}
			if tax.get("charge_type") == "On Net Total" and tax.get("rate"):
				row["rate"] = tax["rate"]
			else:
				row["tax_amount"] = tax.get("tax_amount", 0)
			rows.append(row)
		return rows

	def _import_payment_entries(self, payments):
		"""Import Payment Entries from transformed voucher data.

		Each payment dict:
			company, payment_type ('Receive' or 'Pay'),
			party_type ('Customer' or 'Supplier'), party,
			paid_from, paid_to, paid_amount, posting_date,
			voucher_number, narration, mode_of_payment,
			references: [{reference_doctype, reference_name, allocated_amount}],
			tally_guid
		"""
		if not payments:
			return

		count = 0

		for pmt in payments:
			tally_ref = self._tally_ref(
				pmt.get("_tally_voucher_type", "Payment"),
				pmt.get("voucher_number", ""),
			)

			# Duplicate detection via reference_no field
			existing = self._voucher_exists("Payment Entry", tally_ref, "reference_no")
			if existing:
				self.results["skipped"].append({
					"doctype": "Payment Entry", "name": existing,
					"reason": f"duplicate (tally ref {tally_ref})",
				})
				continue

			party = pmt.get("party", "")
			party_type = pmt.get("party_type", "")
			payment_type = pmt.get("payment_type", "Receive")

			if not party or not party_type:
				self.results["errors"].append({
					"doctype": "Payment Entry", "name": pmt.get("voucher_number", "?"),
					"error": "Missing party or party_type",
				})
				continue

			paid_amount = abs(pmt.get("paid_amount", 0))
			if paid_amount <= 0:
				self.results["skipped"].append({
					"doctype": "Payment Entry", "name": pmt.get("voucher_number", "?"),
					"reason": "Zero amount",
				})
				continue

			# Resolve accounts
			paid_from = None
			paid_to = None
			if pmt.get("paid_from"):
				paid_from = self._resolve_account_fuzzy(pmt["paid_from"])
			if pmt.get("paid_to"):
				paid_to = self._resolve_account_fuzzy(pmt["paid_to"])

			doc_dict = {
				"doctype": "Payment Entry",
				"company": self.company,
				"payment_type": payment_type,
				"party_type": party_type,
				"party": party,
				"paid_amount": paid_amount,
				"received_amount": paid_amount,
				"posting_date": pmt.get("posting_date"),
				"set_posting_time": 1,
				"remarks": pmt.get("narration", ""),
				"reference_no": tally_ref,
				"reference_date": pmt.get("posting_date"),
			}
			if paid_from:
				doc_dict["paid_from"] = paid_from
			if paid_to:
				doc_dict["paid_to"] = paid_to
			if pmt.get("mode_of_payment"):
				doc_dict["mode_of_payment"] = pmt["mode_of_payment"]

			# Build references (links to invoices for outstanding allocation)
			references = []
			for ref in pmt.get("references", []):
				ref_doctype = ref.get("reference_doctype", "")
				ref_name = ref.get("reference_name", "")
				if not ref_doctype or not ref_name:
					continue
				if not self.dry_run and not frappe.db.exists(ref_doctype, ref_name):
					continue
				references.append({
					"reference_doctype": ref_doctype,
					"reference_name": ref_name,
					"allocated_amount": abs(ref.get("allocated_amount", 0)),
				})
			if references:
				doc_dict["references"] = references

			if self.dry_run:
				self.results["created"].append({
					"doctype": "Payment Entry", "name": pmt.get("voucher_number", "?"),
				})
				count += 1
				continue

			try:
				doc = frappe.get_doc(doc_dict)
				doc.flags.ignore_permissions = True
				doc.flags.ignore_mandatory = True
				doc.insert(ignore_permissions=True)
				self._submit_doc(doc, "Payment Entry", pmt.get("voucher_number", "?"))
				count += 1
			except frappe.DuplicateEntryError:
				self.results["skipped"].append({
					"doctype": "Payment Entry", "name": pmt.get("voucher_number", "?"),
					"reason": "duplicate",
				})
			except Exception as exc:
				self.results["errors"].append({
					"doctype": "Payment Entry", "name": pmt.get("voucher_number", "?"),
					"error": str(exc),
				})
				logger.warning("Failed Payment Entry %s: %s", pmt.get("voucher_number"), exc)

			if count % 50 == 0 and not self.dry_run:
				frappe.db.commit()

		if not self.dry_run:
			frappe.db.commit()

	def _import_journal_entries(self, entries):
		"""Import Journal Entries from transformed voucher data.

		Each entry dict:
			company, posting_date, voucher_number, voucher_type, narration,
			accounts: [{account, debit_in_account_currency, credit_in_account_currency,
			            party_type, party}],
			tally_guid
		"""
		if not entries:
			return

		count = 0

		for entry in entries:
			tally_ref = self._tally_ref(
				entry.get("_tally_voucher_type", "Journal"),
				entry.get("voucher_number", ""),
			)

			# Duplicate detection via cheque_no field
			existing = self._voucher_exists("Journal Entry", tally_ref, "cheque_no")
			if existing:
				self.results["skipped"].append({
					"doctype": "Journal Entry", "name": existing,
					"reason": f"duplicate (tally ref {tally_ref})",
				})
				continue

			rows = []
			for acc in entry.get("accounts", []):
				account_name = acc.get("account", "")
				resolved = self._resolve_account_fuzzy(account_name) if account_name else None
				if not resolved and not self.dry_run:
					self.results["skipped"].append({
						"doctype": "Journal Entry Account", "name": account_name,
						"reason": "Account not found",
					})
					continue

				row = {"account": resolved or account_name}
				debit = acc.get("debit_in_account_currency", 0)
				credit = acc.get("credit_in_account_currency", 0)
				if debit:
					row["debit_in_account_currency"] = abs(debit)
				if credit:
					row["credit_in_account_currency"] = abs(credit)

				if acc.get("party_type") and acc.get("party"):
					row["party_type"] = acc["party_type"]
					row["party"] = acc["party"]

				rows.append(row)

			if not rows:
				self.results["skipped"].append({
					"doctype": "Journal Entry", "name": entry.get("voucher_number", "?"),
					"reason": "No valid accounts",
				})
				continue

			doc_dict = {
				"doctype": "Journal Entry",
				"company": self.company,
				"posting_date": entry.get("posting_date"),
				"set_posting_time": 1,
				"voucher_type": entry.get("voucher_type", "Journal Entry"),
				"user_remark": entry.get("narration", ""),
				"cheque_no": tally_ref,
				"cheque_date": entry.get("posting_date"),
				"accounts": rows,
			}

			if self.dry_run:
				self.results["created"].append({
					"doctype": "Journal Entry", "name": entry.get("voucher_number", "?"),
				})
				count += 1
				continue

			try:
				doc = frappe.get_doc(doc_dict)
				doc.flags.ignore_permissions = True
				doc.flags.ignore_mandatory = True
				doc.insert(ignore_permissions=True)
				self._submit_doc(doc, "Journal Entry", entry.get("voucher_number", "?"))
				count += 1
			except frappe.DuplicateEntryError:
				self.results["skipped"].append({
					"doctype": "Journal Entry", "name": entry.get("voucher_number", "?"),
					"reason": "duplicate",
				})
			except Exception as exc:
				self.results["errors"].append({
					"doctype": "Journal Entry", "name": entry.get("voucher_number", "?"),
					"error": str(exc),
				})
				logger.warning("Failed Journal Entry %s: %s", entry.get("voucher_number"), exc)

			if count % 50 == 0 and not self.dry_run:
				frappe.db.commit()

		if not self.dry_run:
			frappe.db.commit()

	def get_summary(self):
		return {
			"created": len(self.results["created"]),
			"skipped": len(self.results["skipped"]),
			"errors": len(self.results["errors"]),
			"details": self.results,
		}

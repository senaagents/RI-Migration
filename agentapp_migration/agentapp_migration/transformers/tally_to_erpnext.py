"""Map parsed Tally data to ERPNext document dicts.

Each transform function returns a list of dicts ready for
frappe.get_doc(doc_dict).insert().
"""

# Standard Tally root groups and their ERPNext root_type.
# Custom Tally groups use prefixed names like "A-Current Assets" —
# we also check the first character as a heuristic.
ROOT_TYPE_MAP = {
	"Capital Account": "Equity",
	"Reserves & Surplus": "Equity",
	"Current Assets": "Asset",
	"Fixed Assets": "Asset",
	"Investments": "Asset",
	"Loans & Advances (Asset)": "Asset",
	"Misc. Expenses (ASSET)": "Asset",
	"Stock-in-Hand": "Asset",
	"Current Liabilities": "Liability",
	"Loans (Liability)": "Liability",
	"Secured Loans": "Liability",
	"Unsecured Loans": "Liability",
	"Suspense A/c": "Liability",
	"Branch / Divisions": "Liability",
	"Duties & Taxes": "Liability",
	"Provisions": "Liability",
	"Direct Expenses": "Expense",
	"Indirect Expenses": "Expense",
	"Purchase Accounts": "Expense",
	"Direct Incomes": "Income",
	"Indirect Incomes": "Income",
	"Sales Accounts": "Income",
}

# First-character heuristic for custom group names
_PREFIX_ROOT_TYPE = {
	"A": "Asset",
	"L": "Liability",
	"E": "Expense",
	"I": "Income",
}

COUNTRY_MAP = {
	"United States of America": "United States",
}

_SKIP_ACCOUNT_LEDGER_NAMES = {
	"profit & loss a/c",
}

# Tally UOM → ERPNext UOM mapping
UOM_MAP = {
	"Nos": "Nos", "Nos.": "Nos", "nos": "Nos", "NO": "Nos", "No": "Nos",
	"Kgs": "Kg", "Kgs.": "Kg", "kgs": "Kg", "KG": "Kg", "Kg": "Kg",
	"Mtr.": "Meter", "Mtrs": "Meter", "Mtr": "Meter",
	"Sets": "Set", "set": "Set", "Set": "Set",
	"Pcs": "Nos", "pcs": "Nos", "PC": "Nos", "Pc": "Nos",
	"Bags": "Nos", "Bag": "Nos",
	"Bottle": "Nos",
	"Boxes": "Nos", "Box": "Nos",
	"day": "Nos", "Days": "Nos",
	"Dzn": "Nos",
	"hr": "Nos", "Hour": "Nos",
	"ld": "Nos",
	"Litres": "Nos", "Ltr": "Nos", "Liter": "Nos",
	"MBPS": "Nos",
	"min": "Nos",
	"Mth": "Nos",
	"Packet": "Nos", "Pack": "Nos",
	"rol": "Nos", "role": "Nos", "roll": "Nos", "Roll": "Nos",
	"Sq.feet": "Nos", "Sqm.": "Nos",
	"Ton": "Nos", "Tonne": "Nos",
	"Not Applicable": "Nos",
}


def classify_ledger(ledger, groups):
	"""Determine if a ledger is an account, customer, or supplier.

	Walks the parent group chain to find 'Sundry Debtors' or
	'Sundry Creditors' ancestors. Checks group names containing
	those keywords (handles custom names like 'A60-Sundry Debtors').

	Args:
		ledger: Parsed ledger dict with 'parent' key.
		groups: Dict mapping group name -> group dict.

	Returns:
		'customer', 'supplier', or 'account'.
	"""
	parent = ledger.get("parent", "")
	visited = set()
	while parent and parent not in visited:
		visited.add(parent)
		parent_lower = parent.lower()
		if "sundry debtor" in parent_lower or "debtor" in parent_lower:
			return "customer"
		if "sundry creditor" in parent_lower or "creditor" in parent_lower:
			return "supplier"
		group = groups.get(parent)
		if not group:
			break
		parent = group.get("parent", "")
	return "account"


def _resolve_root_type(group_name, groups):
	"""Walk up the group hierarchy to determine ERPNext root_type."""
	visited = set()
	current = group_name
	while current and current not in visited:
		visited.add(current)
		# Check exact match
		for key, root_type in ROOT_TYPE_MAP.items():
			if key.lower() in current.lower():
				return root_type
		# Check prefix heuristic
		if current and current[0] in _PREFIX_ROOT_TYPE:
			return _PREFIX_ROOT_TYPE[current[0]]
		# Walk up
		group = groups.get(current)
		if not group:
			break
		current = group.get("parent", "")
	return "Asset"  # fallback


def _account_name(name, company_abbr):
	"""Format account name with company abbreviation: 'Cash - AI'."""
	return f"{name} - {company_abbr}"


def _normalize_country(country):
	country = country or "India"
	return COUNTRY_MAP.get(country, country)


def transform_accounts(groups, ledgers, company_name, company_abbr):
	"""Transform Tally groups and ledgers into ERPNext Account dicts.

	Groups become is_group=1, ledgers become is_group=0.
	Skips ledgers classified as customer or supplier.
	"""
	groups_by_name = {g["name"]: g for g in groups}
	accounts = []

	# Groups first (is_group=1)
	for g in groups:
		root_type = _resolve_root_type(g["name"], groups_by_name)
		parent = g.get("parent", "")
		report_type = "Balance Sheet" if root_type in ("Asset", "Liability", "Equity") else "Profit and Loss"
		accounts.append({
			"doctype": "Account",
			"account_name": g["name"],
			"parent_account": _account_name(parent, company_abbr) if parent else "",
			"company": company_name,
			"is_group": 1,
			"root_type": root_type if not parent else "",
			"report_type": report_type,
		})

	# Ledgers (is_group=0), skipping customers/suppliers
	for led in ledgers:
		ledger_name = led["name"]
		if ledger_name.lower().strip() in _SKIP_ACCOUNT_LEDGER_NAMES:
			continue
		if ledger_name in groups_by_name:
			continue

		classification = classify_ledger(led, groups_by_name)
		if classification != "account":
			continue
		parent = led.get("parent", "")
		root_type = _resolve_root_type(parent, groups_by_name) if parent else "Asset"
		report_type = "Balance Sheet" if root_type in ("Asset", "Liability", "Equity") else "Profit and Loss"

		account_type = ""
		name_lower = ledger_name.lower()
		parent_lower = parent.lower()
		if "bank" in parent_lower or "bank" in name_lower:
			account_type = "Bank"
		elif "cash" in parent_lower:
			account_type = "Cash"
		elif "receivable" in name_lower:
			account_type = "Receivable"
		elif "payable" in name_lower:
			account_type = "Payable"
		elif "tax" in parent_lower or "gst" in parent_lower or "tds" in parent_lower:
			account_type = "Tax"
		elif "depreciation" in name_lower:
			account_type = "Depreciation"
		elif "fixed asset" in parent_lower:
			account_type = "Fixed Asset"
		elif "stock" in parent_lower and "adjust" in name_lower:
			account_type = "Stock Adjustment"
		elif "stock" in parent_lower:
			account_type = "Stock"

		accounts.append({
			"doctype": "Account",
			"account_name": ledger_name,
			"parent_account": _account_name(parent, company_abbr) if parent else "",
			"company": company_name,
			"is_group": 0,
			"root_type": "",
			"report_type": report_type,
			"account_type": account_type,
		})

	return accounts


def transform_customers(ledgers, groups):
	"""Transform Sundry Debtor ledgers into ERPNext Customer dicts."""
	groups_by_name = {g["name"]: g for g in groups}
	customers = []

	for led in ledgers:
		if classify_ledger(led, groups_by_name) != "customer":
			continue

		# Map Tally debtor sub-group to ERPNext customer group
		customer_group = "All Customer Groups"
		parent = led.get("parent", "")
		if parent and "debtor" in parent.lower():
			# Use the sub-group name as customer group (e.g. "A60 Debtors Butterfly")
			customer_group = parent

		gst_category = "Unregistered"
		gst_type = (led.get("gst_type") or "").lower()
		if gst_type == "regular":
			gst_category = "Registered Regular"
		elif gst_type == "composition":
			gst_category = "Registered Composition"

		customer = {
			"doctype": "Customer",
			"customer_name": led.get("mailing_name") or led["name"],
			"customer_type": "Company" if "pvt" in led["name"].lower() or "ltd" in led["name"].lower() else "Individual",
			"customer_group": customer_group,
			"territory": "India",
			"gst_category": gst_category,
			"gstin": led.get("gstin", ""),
			"pan": led.get("pan", ""),
			"_tally_name": led["name"],
			"_tally_state": led.get("state", ""),
			"_tally_address": led.get("address", ""),
			"_tally_pincode": led.get("pincode", ""),
			"_tally_email": led.get("email", ""),
			"_tally_phone": led.get("phone", ""),
		}
		customers.append(customer)

	return customers


def transform_suppliers(ledgers, groups):
	"""Transform Sundry Creditor ledgers into ERPNext Supplier dicts."""
	groups_by_name = {g["name"]: g for g in groups}
	suppliers = []

	for led in ledgers:
		if classify_ledger(led, groups_by_name) != "supplier":
			continue

		supplier_group = "All Supplier Groups"
		parent = led.get("parent", "")
		if parent:
			supplier_group = parent

		gst_category = "Unregistered"
		gst_type = (led.get("gst_type") or "").lower()
		if gst_type == "regular":
			gst_category = "Registered Regular"
		elif gst_type == "composition":
			gst_category = "Registered Composition"

		supplier = {
			"doctype": "Supplier",
			"supplier_name": led.get("mailing_name") or led["name"],
			"supplier_type": "Company" if "pvt" in led["name"].lower() or "ltd" in led["name"].lower() else "Individual",
			"supplier_group": supplier_group,
			"country": _normalize_country(led.get("country")),
			"gst_category": gst_category,
			"gstin": led.get("gstin", ""),
			"pan": led.get("pan", ""),
			"_tally_name": led["name"],
			"_tally_state": led.get("state", ""),
			"_tally_address": led.get("address", ""),
			"_tally_pincode": led.get("pincode", ""),
			"_tally_email": led.get("email", ""),
			"_tally_phone": led.get("phone", ""),
		}
		suppliers.append(supplier)

	return suppliers


def transform_item_groups(stock_groups):
	"""Transform Tally Stock Groups into ERPNext Item Group dicts."""
	item_groups = []
	for sg in stock_groups:
		item_groups.append({
			"doctype": "Item Group",
			"item_group_name": sg["name"],
			"parent_item_group": sg.get("parent") or "All Item Groups",
			"is_group": 1,
		})
	return item_groups


def transform_items(stock_items):
	"""Transform Tally Stock Items into ERPNext Item dicts."""
	items = []
	for si in stock_items:
		uom = UOM_MAP.get(si.get("base_units", "Nos"), si.get("base_units") or "Nos")
		items.append({
			"doctype": "Item",
			"item_code": si["name"],
			"item_name": si["name"],
			"item_group": si.get("parent") or "All Item Groups",
			"stock_uom": uom,
			"is_stock_item": 1,
			"include_item_in_manufacturing": 0,
			"gst_hsn_code": si.get("hsn_code", ""),
			"opening_stock": si.get("opening_quantity", 0),
			"valuation_rate": si.get("opening_rate", 0),
		})
	return items


def transform_cost_centres(cost_centres, company_name, company_abbr):
	"""Transform Tally Cost Centres into ERPNext Cost Center dicts.

	Skips payroll/employee entries — those are employee names in Tally, not cost centres.
	"""
	result = []
	for cc in cost_centres:
		if cc.get("for_payroll") or cc.get("is_employee_group"):
			continue
		name = cc.get("name", "").strip()
		if not name:
			continue
		result.append({
			"doctype": "Cost Center",
			"cost_center_name": name,
			"company": company_name,
			"parent_cost_center": f"{company_name} - {company_abbr}",
			"is_group": 0,
		})
	return result


def transform_warehouses(godowns, company_name):
	"""Transform Tally Godowns into ERPNext Warehouse dicts."""
	warehouses = []
	for g in godowns:
		parent = g.get("parent", "")
		warehouses.append({
			"doctype": "Warehouse",
			"warehouse_name": g["name"],
			"parent_warehouse": f"{parent} - {company_name[:2].upper()}" if parent else "",
			"company": company_name,
		})
	return warehouses


def transform_all(tally_data, company_name, company_abbr):
	"""Transform all parsed Tally data into ERPNext doc dicts.

	Args:
		tally_data: dict with keys from parsers (groups, ledgers, stock_groups, etc.)
		company_name: ERPNext company name.
		company_abbr: ERPNext company abbreviation (e.g. 'AI').

	Returns:
		dict with keys: accounts, customers, suppliers, item_groups, items, warehouses
	"""
	groups = tally_data.get("groups", [])
	ledgers = tally_data.get("ledgers", [])
	stock_groups = tally_data.get("stock_groups", [])
	stock_items = tally_data.get("stock_items", [])
	godowns = tally_data.get("godowns", [])

	return {
		"accounts": transform_accounts(groups, ledgers, company_name, company_abbr),
		"customers": transform_customers(ledgers, groups),
		"suppliers": transform_suppliers(ledgers, groups),
		"item_groups": transform_item_groups(stock_groups),
		"items": transform_items(stock_items),
		"warehouses": transform_warehouses(godowns, company_name),
	}


# ---------------------------------------------------------------------------
# Voucher transforms
# ---------------------------------------------------------------------------

# Maps Tally base voucher types to ERPNext doctypes
_VCH_TYPE_TO_DOCTYPE = {
	"Sales": "Sales Invoice",
	"Purchase": "Purchase Invoice",
	"Credit Note": "Sales Invoice",  # is_return=1
	"Debit Note": "Purchase Invoice",  # is_return=1
	"Receipt": "Payment Entry",
	"Payment": "Payment Entry",
	"Journal": "Journal Entry",
	"Contra": "Journal Entry",
}


def transform_vouchers(vouchers, voucher_types, company_name, company_abbr):
	"""Transform parsed Tally vouchers into ERPNext document dicts.

	Args:
		vouchers: List of voucher dicts from parse_day_book().
		voucher_types: List of voucher type dicts from parse_collection('VoucherType').
		company_name: ERPNext company name.
		company_abbr: ERPNext company abbreviation.

	Returns:
		dict with keys: sales_invoices, purchase_invoices, payment_entries, journal_entries.
		Each value is a list of ERPNext doc dicts.
	"""
	from agentapp_migration.agentapp_migration.parsers.tally import resolve_voucher_base_type

	result = {
		"sales_invoices": [],
		"purchase_invoices": [],
		"payment_entries": [],
		"journal_entries": [],
	}

	for vch in vouchers:
		if vch.get("is_cancelled") or vch.get("is_optional"):
			continue

		base_type = resolve_voucher_base_type(vch["vch_type"], voucher_types)

		if base_type in ("Sales", "Credit Note"):
			doc = _transform_sales_invoice(vch, company_name, company_abbr, is_return=(base_type == "Credit Note"))
			if doc:
				result["sales_invoices"].append(doc)
		elif base_type in ("Purchase", "Debit Note"):
			doc = _transform_purchase_invoice(vch, company_name, company_abbr, is_return=(base_type == "Debit Note"))
			if doc:
				result["purchase_invoices"].append(doc)
		elif base_type in ("Receipt", "Payment"):
			doc = _transform_payment_entry(vch, company_name, company_abbr, base_type)
			if doc:
				result["payment_entries"].append(doc)
		elif base_type in ("Journal", "Contra"):
			doc = _transform_journal_entry(vch, company_name, company_abbr)
			if doc:
				result["journal_entries"].append(doc)

	return result


def _account_for_ledger(ledger_name, company_abbr):
	"""Build the ERPNext account name from a Tally ledger name."""
	return f"{ledger_name} - {company_abbr}"


def _extract_tax_rows(ledger_entries, company_abbr):
	"""Extract tax ledger entries (non-party, non-rounding) as ERPNext tax rows."""
	taxes = []
	for le in ledger_entries:
		if le.get("is_party_ledger"):
			continue
		if le.get("is_deemed_positive"):
			continue
		ledger = le.get("ledger", "")
		amount = le.get("amount", 0)
		tax_rate = le.get("tax_rate", 0)
		# Skip zero-amount entries and tiny rounding entries
		if abs(amount) < 1.0:
			continue
		taxes.append({
			"charge_type": "Actual",
			"account_head": _account_for_ledger(ledger, company_abbr),
			"tax_amount": amount,
			"description": ledger,
			"_tally_rate": tax_rate,
		})
	return taxes


def _transform_sales_invoice(vch, company_name, company_abbr, is_return=False):
	"""Transform a Sales/Credit Note voucher into an ERPNext Sales Invoice dict."""
	if not vch.get("inventory_entries"):
		return None

	items = []
	for inv in vch["inventory_entries"]:
		uom = UOM_MAP.get(inv.get("uom", "Nos"), inv.get("uom") or "Nos")
		items.append({
			"item_code": inv["item"],
			"item_name": inv["item"],
			"qty": abs(inv.get("qty", 0)),
			"rate": abs(inv.get("rate", 0)),
			"amount": abs(inv.get("amount", 0)),
			"uom": uom,
			"gst_hsn_code": inv.get("hsn", ""),
		})

	if not items:
		return None

	taxes = _extract_tax_rows(vch.get("ledger_entries", []), company_abbr)

	return {
		"doctype": "Sales Invoice",
		"company": company_name,
		"customer": vch.get("party", ""),
		"posting_date": vch.get("date", ""),
		"due_date": vch.get("date", ""),
		"is_return": 1 if is_return else 0,
		"items": items,
		"taxes": taxes,
		"remarks": vch.get("narration", ""),
		"place_of_supply": vch.get("place_of_supply", ""),
		"_tally_vch_type": vch.get("vch_type", ""),
		"_tally_vch_number": vch.get("number", ""),
		"_tally_party_gstin": vch.get("party_gstin", ""),
	}


def _transform_purchase_invoice(vch, company_name, company_abbr, is_return=False):
	"""Transform a Purchase/Debit Note voucher into an ERPNext Purchase Invoice dict."""
	if not vch.get("inventory_entries"):
		return None

	items = []
	for inv in vch["inventory_entries"]:
		uom = UOM_MAP.get(inv.get("uom", "Nos"), inv.get("uom") or "Nos")
		items.append({
			"item_code": inv["item"],
			"item_name": inv["item"],
			"qty": abs(inv.get("qty", 0)),
			"rate": abs(inv.get("rate", 0)),
			"amount": abs(inv.get("amount", 0)),
			"uom": uom,
			"gst_hsn_code": inv.get("hsn", ""),
		})

	if not items:
		return None

	taxes = _extract_tax_rows(vch.get("ledger_entries", []), company_abbr)

	return {
		"doctype": "Purchase Invoice",
		"company": company_name,
		"supplier": vch.get("party", ""),
		"posting_date": vch.get("date", ""),
		"due_date": vch.get("date", ""),
		"is_return": 1 if is_return else 0,
		"items": items,
		"taxes": taxes,
		"remarks": vch.get("narration", ""),
		"_tally_vch_type": vch.get("vch_type", ""),
		"_tally_vch_number": vch.get("number", ""),
		"_tally_party_gstin": vch.get("party_gstin", ""),
	}


def _transform_payment_entry(vch, company_name, company_abbr, base_type):
	"""Transform a Receipt/Payment voucher into an ERPNext Payment Entry dict."""
	ledger_entries = vch.get("ledger_entries", [])
	if not ledger_entries:
		return None

	# In Tally Receipt/Payment vouchers, one side is bank/cash (is_deemed_positive=Yes
	# for Receipt), the other is the party/income/expense ledger.
	bank_ledger = ""
	bank_amount = 0.0
	party_ledger = ""
	party_amount = 0.0
	references = []

	for le in ledger_entries:
		if le.get("is_deemed_positive"):
			bank_ledger = le["ledger"]
			bank_amount = abs(le.get("amount", 0))
		else:
			party_ledger = le["ledger"]
			party_amount = abs(le.get("amount", 0))
			# Collect bill references for reconciliation
			for ba in le.get("bill_allocations", []):
				references.append({
					"reference_name": ba.get("name", ""),
					"reference_type": ba.get("type", ""),
					"allocated_amount": abs(ba.get("amount", 0)),
				})

	# Determine payment type
	if base_type == "Receipt":
		payment_type = "Receive"
	else:
		payment_type = "Pay"

	return {
		"doctype": "Payment Entry",
		"company": company_name,
		"payment_type": payment_type,
		"party_type": "Customer" if base_type == "Receipt" else "Supplier",
		"party": party_ledger,
		"posting_date": vch.get("date", ""),
		"paid_from": _account_for_ledger(party_ledger if payment_type == "Receive" else bank_ledger, company_abbr),
		"paid_to": _account_for_ledger(bank_ledger if payment_type == "Receive" else party_ledger, company_abbr),
		"paid_amount": bank_amount or party_amount,
		"received_amount": bank_amount or party_amount,
		"reference_no": vch.get("number", ""),
		"reference_date": vch.get("date", ""),
		"remarks": vch.get("narration", ""),
		"references": references,
		"_tally_vch_type": vch.get("vch_type", ""),
		"_tally_vch_number": vch.get("number", ""),
		"_tally_bank_ledger": bank_ledger,
	}


def _transform_journal_entry(vch, company_name, company_abbr):
	"""Transform a Journal/Contra voucher into an ERPNext Journal Entry dict."""
	ledger_entries = vch.get("ledger_entries", [])
	if not ledger_entries:
		return None

	rows = []
	for le in ledger_entries:
		amount = le.get("amount", 0)
		row = {
			"account": _account_for_ledger(le["ledger"], company_abbr),
		}
		# Tally: negative = debit. ERPNext JE: separate debit/credit fields.
		if amount < 0:
			row["debit_in_account_currency"] = abs(amount)
		else:
			row["credit_in_account_currency"] = amount
		rows.append(row)

	if not rows:
		return None

	return {
		"doctype": "Journal Entry",
		"company": company_name,
		"posting_date": vch.get("date", ""),
		"voucher_type": "Journal Entry",
		"accounts": rows,
		"remark": vch.get("narration", ""),
		"_tally_vch_type": vch.get("vch_type", ""),
		"_tally_vch_number": vch.get("number", ""),
	}

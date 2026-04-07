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

# Tally UOM → ERPNext UOM mapping
UOM_MAP = {
	"Nos": "Nos",
	"Nos.": "Nos",
	"nos": "Nos",
	"Kgs": "Kg",
	"Kgs.": "Kg",
	"kgs": "Kg",
	"Mtr.": "Meter",
	"Mtrs": "Meter",
	"Mtr": "Meter",
	"Sets": "Set",
	"set": "Set",
	"Pcs": "Nos",
	"pcs": "Nos",
	"rol": "Roll",
	"roll": "Roll",
	"Ltr": "Liter",
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
		classification = classify_ledger(led, groups_by_name)
		if classification != "account":
			continue
		parent = led.get("parent", "")
		root_type = _resolve_root_type(parent, groups_by_name) if parent else "Asset"
		report_type = "Balance Sheet" if root_type in ("Asset", "Liability", "Equity") else "Profit and Loss"

		account_type = ""
		name_lower = led["name"].lower()
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
			"account_name": led["name"],
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
			"country": led.get("country", "India"),
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

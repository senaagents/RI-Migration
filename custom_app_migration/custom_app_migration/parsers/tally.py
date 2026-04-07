"""Parse TallyPrime XML responses into Python data structures.

Handles the three main response formats:
1. List of Accounts (TALLYMESSAGE blocks with CURRENCY/GROUP/LEDGER)
2. Trial Balance (DSPACCNAME/DSPACCINFO pairs)
3. Stock Summary (DSPACCNAME/DSPSTKINFO pairs)
4. TDL Collection responses (entity elements under COLLECTION)
"""

import re
import xml.etree.ElementTree as ET

_INVALID_XML_CHARS = re.compile(r"&#(?:[0-8]|1[0-1]|1[4-9]|2[0-9]|3[01]);")


def _clean_xml(text):
	"""Remove invalid XML character references and stray \\r from Tally output."""
	text = _INVALID_XML_CHARS.sub("", text)
	text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
	text = text.replace("\r", "")
	return text


def _attr(el, attr, default=""):
	"""Get an XML attribute value, stripped of \\r and whitespace."""
	val = el.get(attr, default)
	if val:
		return val.strip().replace("\r", "")
	return default


def _text(el, tag, default=""):
	"""Get text content of a child element, or default."""
	child = el.find(tag)
	if child is not None and child.text:
		return child.text.strip().replace("\r", "")
	return default


def _float(el, tag, default=0.0):
	"""Get float value of a child element."""
	val = _text(el, tag, "")
	if not val:
		return default
	try:
		return float(val)
	except ValueError:
		return default


def _bool(el, tag):
	"""Get boolean from Yes/No text."""
	return _text(el, tag, "").lower() in ("yes", "true", "1")


def parse_list_of_accounts(xml_string):
	"""Parse the 'List of Accounts' XML into structured data.

	Returns:
		dict with keys: company, currencies, groups, ledgers
	"""
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8", errors="replace")
	xml_string = _clean_xml(xml_string)
	root = ET.fromstring(xml_string)

	currencies = []
	groups = []
	ledgers = []
	company = ""

	for tallymsg in root.iter("TALLYMESSAGE"):
		for child in tallymsg:
			if child.tag == "COMPANY":
				company = _attr(child, "NAME") or _text(child, "NAME", "")
			elif child.tag == "CURRENCY":
				currencies.append(_parse_currency(child))
			elif child.tag == "GROUP":
				groups.append(_parse_group(child))
			elif child.tag == "LEDGER":
				ledgers.append(_parse_ledger(child))

	return {
		"company": company,
		"currencies": currencies,
		"groups": groups,
		"ledgers": ledgers,
	}


def _parse_currency(el):
	return {
		"name": _attr(el, "NAME"),
		"mailing_name": _text(el, "MAILINGNAME"),
		"expanded_symbol": _text(el, "EXPANDEDSYMBOL"),
		"decimal_places": int(_float(el, "DECIMALPLACES", 2)),
		"iso_code": _text(el, "ISOCURRENCYCODE"),
	}


def _parse_group(el):
	return {
		"name": _attr(el, "NAME"),
		"parent": _text(el, "PARENT"),
		"guid": _text(el, "GUID"),
		"is_revenue": _bool(el, "ISREVENUE"),
		"is_deemed_positive": _bool(el, "ISDEEMEDPOSITIVE"),
		"affects_gross_profit": _bool(el, "AFFECTSGROSSPROFIT"),
		"is_subledger": _bool(el, "ISADDABLE"),
	}


def _parse_ledger(el):
	# Collect address lines
	address_lines = []
	for addr_list in el.iter("ADDRESS.LIST"):
		for addr in addr_list:
			if addr.text and addr.text.strip():
				address_lines.append(addr.text.strip())

	# Collect mailing details
	mailing_name = _text(el, "MAILINGNAME")
	if not mailing_name:
		for old in el.iter("OLDMAILINGNAME.LIST"):
			for item in old:
				if item.text and item.text.strip():
					mailing_name = item.text.strip()
					break

	return {
		"name": _attr(el, "NAME"),
		"parent": _text(el, "PARENT"),
		"guid": _text(el, "GUID"),
		"opening_balance": _float(el, "OPENINGBALANCE"),
		"closing_balance": _float(el, "CLOSINGBALANCE"),
		"mailing_name": mailing_name or _attr(el, "NAME"),
		"address": "\n".join(address_lines),
		"state": _text(el, "LEDSTATENAME") or _text(el, "PRIORSTATENAME"),
		"country": _text(el, "COUNTRYOFRESIDENCE", "India"),
		"pincode": _text(el, "OLDPINCODE") or _text(el, "PINCODE"),
		"email": _text(el, "EMAIL"),
		"phone": _text(el, "LEDGERPHONE") or _text(el, "LEDGERMOBILE"),
		"pan": _text(el, "INCOMETAXNUMBER"),
		"gstin": _text(el, "GSTREGISTRATIONNUMBER") or _text(el, "PARTYGSTIN"),
		"gst_type": _text(el, "GSTREGISTRATIONTYPE"),
		"currency": _text(el, "CURRENCYNAME", "RS"),
		"is_bill_wise": _bool(el, "ISBILLWISEON"),
		"affects_stock": _bool(el, "AFFECTSSTOCK"),
		"created_by": _text(el, "CREATEDBY"),
	}


def parse_collection(xml_string, entity_type):
	"""Parse a TDL Collection XML response.

	Args:
		xml_string: Raw XML response from get_collection().
		entity_type: One of 'STOCKGROUP', 'STOCKITEM', 'UNIT', 'GODOWN',
		             'COSTCENTRE', 'VOUCHERTYPE'.

	Returns:
		List of dicts, one per entity.
	"""
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8", errors="replace")
	xml_string = _clean_xml(xml_string)
	root = ET.fromstring(xml_string)

	parsers = {
		"STOCKGROUP": _parse_stock_group,
		"STOCKITEM": _parse_stock_item,
		"UNIT": _parse_unit,
		"GODOWN": _parse_godown,
		"COSTCENTRE": _parse_cost_centre,
		"VOUCHERTYPE": _parse_voucher_type,
	}
	parser_fn = parsers.get(entity_type.upper().replace(" ", ""))
	if not parser_fn:
		raise ValueError(f"Unknown entity type: {entity_type}")

	results = []
	for el in root.iter(entity_type.upper().replace(" ", "")):
		results.append(parser_fn(el))
	return results


def _parse_stock_group(el):
	return {
		"name": _attr(el, "NAME") or _text(el, "NAME"),
		"parent": _text(el, "PARENT"),
		"guid": _text(el, "GUID"),
	}


def _parse_stock_item(el):
	return {
		"name": _attr(el, "NAME") or _text(el, "NAME"),
		"parent": _text(el, "PARENT"),
		"base_units": _text(el, "BASEUNITS"),
		"opening_balance": _float(el, "OPENINGBALANCE"),
		"opening_quantity": _float(el, "OPENINGQUANTITY"),
		"opening_rate": _float(el, "OPENINGRATE"),
		"hsn_code": _text(el, "HSNCODE") or _text(el, "GSTHSN"),
		"gst_applicable": _text(el, "GSTAPPLICABLE"),
		"guid": _text(el, "GUID"),
	}


def _parse_unit(el):
	return {
		"name": _attr(el, "NAME") or _text(el, "NAME"),
		"original_name": _text(el, "ORIGINALNAME"),
		"is_simple_unit": _bool(el, "ISSIMPLEUNIT"),
	}


def _parse_godown(el):
	return {
		"name": _attr(el, "NAME") or _text(el, "NAME"),
		"parent": _text(el, "PARENT"),
	}


def _parse_cost_centre(el):
	return {
		"name": _attr(el, "NAME") or _text(el, "NAME"),
		"parent": _text(el, "PARENT"),
		"for_payroll": _bool(el, "FORPAYROLL"),
		"is_employee_group": _bool(el, "ISEMPLOYEEGROUP"),
	}


def _parse_voucher_type(el):
	return {
		"name": _attr(el, "NAME") or _text(el, "NAME"),
		"parent": _text(el, "PARENT"),
		"numbering_method": _text(el, "NUMBERINGMETHOD"),
	}


def parse_trial_balance(xml_string):
	"""Parse the Trial Balance XML response.

	Returns:
		list of dicts: {account, debit, credit}
	"""
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8", errors="replace")
	xml_string = _clean_xml(xml_string)
	root = ET.fromstring(xml_string)

	entries = []
	current_name = None

	for elem in root:
		if elem.tag == "DSPACCNAME":
			current_name = _text(elem, "DSPDISPNAME")
		elif elem.tag == "DSPACCINFO" and current_name:
			dr_el = elem.find("DSPCLDRAMT")
			cr_el = elem.find("DSPCLCRAMT")
			debit = abs(_float(dr_el, "DSPCLDRAMTA")) if dr_el is not None else 0.0
			credit = _float(cr_el, "DSPCLCRAMTA") if cr_el is not None else 0.0
			entries.append({
				"account": current_name,
				"debit": debit,
				"credit": credit,
			})
			current_name = None

	return entries


def parse_stock_summary(xml_string):
	"""Parse the Stock Summary XML response.

	Returns:
		list of dicts: {item, qty, uom, rate, value}
	"""
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8", errors="replace")
	xml_string = _clean_xml(xml_string)
	root = ET.fromstring(xml_string)

	items = []
	current_name = None

	for elem in root:
		if elem.tag == "DSPACCNAME":
			current_name = _text(elem, "DSPDISPNAME")
		elif elem.tag == "DSPSTKINFO" and current_name:
			stkcl = elem.find("DSPSTKCL")
			if stkcl is not None:
				qty_raw = _text(stkcl, "DSPCLQTY", "0")
				# Parse "5307.00 Nos" into qty and uom
				qty, uom = _parse_qty_uom(qty_raw)
				rate = _float(stkcl, "DSPCLRATE")
				value = abs(_float(stkcl, "DSPCLAMTA"))
				items.append({
					"item": current_name,
					"qty": qty,
					"uom": uom,
					"rate": rate,
					"value": value,
				})
			current_name = None

	return items


def parse_stock_item_balances(xml_string):
	"""Parse the Stock Item Balances TDL collection response.

	Returns:
		list of dicts: {item, qty, uom, rate, value, parent}
	"""
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8", errors="replace")
	xml_string = _clean_xml(xml_string)
	root = ET.fromstring(xml_string)

	items = []
	for el in root.iter("STOCKITEM"):
		name = _attr(el, "NAME") or _text(el, "NAME")
		if not name:
			continue
		qty_raw = _text(el, "CLOSINGBALANCE", "0")
		qty, uom = _parse_qty_uom(qty_raw)
		rate_raw = _text(el, "CLOSINGRATE", "0")
		rate, _ = _parse_rate_uom(rate_raw)
		value = abs(_float(el, "CLOSINGVALUE"))
		if qty > 0:
			items.append({
				"item": name,
				"qty": qty,
				"uom": uom,
				"rate": rate,
				"value": value,
				"parent": _text(el, "PARENT"),
			})
	return items


def _parse_qty_uom(raw):
	"""Parse '5307.00 Nos' into (5307.0, 'Nos')."""
	if not raw or not raw.strip():
		return 0.0, ""
	parts = raw.strip().split()
	try:
		qty = float(parts[0])
	except (ValueError, IndexError):
		return 0.0, raw.strip()
	uom = " ".join(parts[1:]) if len(parts) > 1 else ""
	return qty, uom


def _parse_rate_uom(raw):
	"""Parse '3745.00/Nos' into (3745.0, 'Nos')."""
	if not raw or not raw.strip():
		return 0.0, ""
	if "/" in raw:
		parts = raw.strip().split("/", 1)
		try:
			return float(parts[0]), parts[1].strip()
		except (ValueError, IndexError):
			pass
	try:
		return float(raw.strip()), ""
	except ValueError:
		return 0.0, ""


def parse_day_book(xml_string):
	"""Parse the Day Book XML response (all vouchers/transactions).

	Handles two voucher structures:
	- Inventory vouchers (Sales/Purchase): ALLINVENTORYENTRIES.LIST + LEDGERENTRIES.LIST
	- Accounting vouchers (Receipt/Payment/Journal): ALLLEDGERENTRIES.LIST

	Returns:
		list of voucher dicts with keys: vch_type, date, number, party,
		narration, party_gstin, place_of_supply, inventory_entries, ledger_entries.
	"""
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8", errors="replace")
	xml_string = _clean_xml(xml_string)
	root = ET.fromstring(xml_string)

	vouchers = []
	for vch_el in root.iter("VOUCHER"):
		vouchers.append(_parse_voucher(vch_el))
	return vouchers


def _parse_voucher(el):
	"""Parse a single VOUCHER element."""
	# Parse date from YYYYMMDD format
	raw_date = _text(el, "DATE", "")
	date = ""
	if raw_date and len(raw_date) == 8:
		date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"

	vch = {
		"vch_type": el.get("VCHTYPE", "") or _text(el, "VOUCHERTYPENAME"),
		"date": date,
		"number": _text(el, "VOUCHERNUMBER"),
		"party": _text(el, "PARTYLEDGERNAME") or _text(el, "PARTYNAME"),
		"narration": _text(el, "NARRATION"),
		"party_gstin": _text(el, "PARTYGSTIN"),
		"place_of_supply": _text(el, "PLACEOFSUPPLY"),
		"is_cancelled": _bool(el, "ISCANCELLED"),
		"is_optional": _bool(el, "ISOPTIONAL"),
		"guid": _text(el, "GUID"),
		"inventory_entries": [],
		"ledger_entries": [],
	}

	# Parse inventory entries (Sales/Purchase invoices with items)
	for inv_el in el.findall("ALLINVENTORYENTRIES.LIST"):
		entry = _parse_inventory_entry(inv_el)
		if entry:
			vch["inventory_entries"].append(entry)

	# Parse ledger entries on inventory vouchers (party + tax rows)
	for led_el in el.findall("LEDGERENTRIES.LIST"):
		entry = _parse_ledger_entry(led_el)
		if entry:
			vch["ledger_entries"].append(entry)

	# Parse accounting-only vouchers (Receipt/Payment/Journal)
	for led_el in el.findall("ALLLEDGERENTRIES.LIST"):
		entry = _parse_ledger_entry(led_el)
		if entry:
			vch["ledger_entries"].append(entry)

	return vch


def _parse_inventory_entry(el):
	"""Parse an ALLINVENTORYENTRIES.LIST element."""
	item_name = _text(el, "STOCKITEMNAME")
	if not item_name:
		return None

	qty, uom = _parse_qty_uom(_text(el, "ACTUALQTY") or _text(el, "BILLEDQTY", "0"))
	rate, _ = _parse_rate_uom(_text(el, "RATE", "0"))
	amount = _float(el, "AMOUNT")

	# Extract the accounting ledger from the nested ACCOUNTINGALLOCATIONS.LIST
	ledger = ""
	ledger_amount = 0.0
	acct_el = el.find("ACCOUNTINGALLOCATIONS.LIST")
	if acct_el is not None:
		ledger = _text(acct_el, "LEDGERNAME")
		ledger_amount = _float(acct_el, "AMOUNT")

	# Extract GST rates from RATEDETAILS.LIST
	gst_rates = {}
	for rd in el.findall("RATEDETAILS.LIST"):
		duty_head = _text(rd, "GSTRATEDUTYHEAD")
		gst_rate = _float(rd, "GSTRATE")
		if duty_head and gst_rate:
			gst_rates[duty_head] = gst_rate

	return {
		"item": item_name,
		"qty": qty,
		"uom": uom,
		"rate": rate,
		"amount": amount,
		"hsn": _text(el, "GSTHSNNAME"),
		"ledger": ledger,
		"ledger_amount": ledger_amount,
		"is_deemed_positive": _bool(el, "ISDEEMEDPOSITIVE"),
		"gst_rates": gst_rates,
	}


def _parse_ledger_entry(el):
	"""Parse a LEDGERENTRIES.LIST or ALLLEDGERENTRIES.LIST element."""
	ledger = _text(el, "LEDGERNAME")
	if not ledger:
		return None

	amount = _float(el, "AMOUNT")

	# Parse bill allocations
	bill_allocs = []
	for ba in el.findall("BILLALLOCATIONS.LIST"):
		name = _text(ba, "NAME")
		if not name:
			continue
		bill_allocs.append({
			"name": name,
			"type": _text(ba, "BILLTYPE"),
			"amount": _float(ba, "AMOUNT"),
		})

	# Parse bank allocations
	bank_allocs = []
	for ba in el.findall("BANKALLOCATIONS.LIST"):
		bank_party = _text(ba, "BANKPARTYNAME") or _text(ba, "PAYMENTFAVOURING")
		if not bank_party:
			continue
		bank_allocs.append({
			"party": bank_party,
			"transaction_type": _text(ba, "TRANSACTIONTYPE"),
			"instrument_number": _text(ba, "INSTRUMENTNUMBER"),
			"amount": _float(ba, "AMOUNT"),
		})

	# Extract invoice tax rate if present
	tax_rate = 0.0
	rate_el = el.find("RATEOFINVOICETAX.LIST")
	if rate_el is not None:
		for child in rate_el:
			if child.text:
				try:
					tax_rate = float(child.text.strip())
				except ValueError:
					pass

	return {
		"ledger": ledger,
		"amount": amount,
		"is_deemed_positive": _bool(el, "ISDEEMEDPOSITIVE"),
		"is_party_ledger": _bool(el, "ISPARTYLEDGER"),
		"bill_allocations": bill_allocs,
		"bank_allocations": bank_allocs,
		"tax_rate": tax_rate,
	}


def resolve_voucher_base_type(vch_type, voucher_types):
	"""Walk the PARENT chain to find the base Tally voucher type.

	Tally allows custom voucher types (e.g. '61 Sales') that inherit from
	base types ('Sales'). This function resolves to the base type.

	Args:
		vch_type: Raw voucher type name (e.g. '61 Sales').
		voucher_types: List of dicts from parse_collection('VoucherType'),
		               each with 'name' and 'parent'.

	Returns:
		Base type name (e.g. 'Sales'), or the original vch_type if not found.
	"""
	by_name = {vt["name"]: vt for vt in voucher_types}

	# Tally base types have no parent (empty string)
	BASE_TYPES = {
		"Sales", "Purchase", "Receipt", "Payment", "Journal",
		"Contra", "Credit Note", "Debit Note", "Sales Order",
		"Purchase Order", "Delivery Note", "Receipt Note",
		"Stock Journal", "Physical Stock", "Memorandum",
		"Rejections In", "Rejections Out", "Payroll",
	}

	current = vch_type
	visited = set()
	while current and current not in visited:
		if current in BASE_TYPES:
			return current
		visited.add(current)
		vt = by_name.get(current)
		if not vt:
			break
		parent = vt.get("parent", "")
		if not parent:
			break
		current = parent

	# Fallback: substring match for common patterns in custom type names
	# Handles cases like "61 Sales" -> "Sales", "62 Receipts Bank" -> "Receipt"
	vch_lower = vch_type.lower()
	for base in BASE_TYPES:
		if base.lower() in vch_lower:
			return base

	return vch_type

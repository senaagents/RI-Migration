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
	"""Remove invalid XML character references from Tally output."""
	text = _INVALID_XML_CHARS.sub("", text)
	return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def _text(el, tag, default=""):
	"""Get text content of a child element, or default."""
	child = el.find(tag)
	if child is not None and child.text:
		return child.text.strip()
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
				company = child.get("NAME", "") or _text(child, "NAME", "")
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
		"name": el.get("NAME", ""),
		"mailing_name": _text(el, "MAILINGNAME"),
		"expanded_symbol": _text(el, "EXPANDEDSYMBOL"),
		"decimal_places": int(_float(el, "DECIMALPLACES", 2)),
		"iso_code": _text(el, "ISOCURRENCYCODE"),
	}


def _parse_group(el):
	return {
		"name": el.get("NAME", ""),
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
		"name": el.get("NAME", ""),
		"parent": _text(el, "PARENT"),
		"guid": _text(el, "GUID"),
		"opening_balance": _float(el, "OPENINGBALANCE"),
		"closing_balance": _float(el, "CLOSINGBALANCE"),
		"mailing_name": mailing_name or el.get("NAME", ""),
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
		"name": el.get("NAME", "") or _text(el, "NAME"),
		"parent": _text(el, "PARENT"),
		"guid": _text(el, "GUID"),
	}


def _parse_stock_item(el):
	return {
		"name": el.get("NAME", "") or _text(el, "NAME"),
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
		"name": el.get("NAME", "") or _text(el, "NAME"),
		"original_name": _text(el, "ORIGINALNAME"),
		"is_simple_unit": _bool(el, "ISSIMPLEUNIT"),
	}


def _parse_godown(el):
	return {
		"name": el.get("NAME", "") or _text(el, "NAME"),
		"parent": _text(el, "PARENT"),
	}


def _parse_cost_centre(el):
	return {
		"name": el.get("NAME", "") or _text(el, "NAME"),
		"parent": _text(el, "PARENT"),
	}


def _parse_voucher_type(el):
	return {
		"name": el.get("NAME", "") or _text(el, "NAME"),
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

"""TallyPrime XML API client.

Connects to TallyPrime's HTTP/XML server and fetches data using
built-in report exports and TDL collection queries.

Proven XML formats tested against TallyPrime EDU 1.1.7+.
"""

import re
import logging
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 120
_INVALID_XML_CHARS = re.compile(r"&#(?:[0-8]|1[0-1]|1[4-9]|2[0-9]|3[01]);")


class TallyClient:
	def __init__(self, host="localhost", port=9000, timeout=_TIMEOUT):
		self.host = host
		self.port = port
		self.timeout = timeout
		self.base_url = f"http://{host}:{port}"

	def _post(self, xml_payload):
		"""Send XML POST to TallyPrime, return cleaned response text."""
		resp = requests.post(
			self.base_url,
			data=xml_payload.encode("utf-8"),
			headers={"Content-Type": "application/xml"},
			timeout=self.timeout,
		)
		resp.raise_for_status()
		text = resp.text
		# Clean invalid XML character references that Tally sometimes emits
		text = _INVALID_XML_CHARS.sub("", text)
		text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
		return text

	def _build_report_xml(self, report_name, explode=True):
		"""Build XML envelope for a Data export request."""
		explode_flag = "<EXPLODEFLAG>Yes</EXPLODEFLAG>" if explode else ""
		return (
			"<ENVELOPE>"
			"<HEADER>"
			"<VERSION>1</VERSION>"
			"<TALLYREQUEST>Export</TALLYREQUEST>"
			"<TYPE>Data</TYPE>"
			f"<ID>{report_name}</ID>"
			"</HEADER>"
			"<BODY><DESC><STATICVARIABLES>"
			f"{explode_flag}"
			"<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
			"</STATICVARIABLES></DESC></BODY>"
			"</ENVELOPE>"
		)

	def _build_collection_xml(self, name, object_type, fields=None):
		"""Build XML envelope for a TDL Collection export.

		Args:
			name: Collection name (arbitrary identifier).
			object_type: Tally object type (e.g. 'Ledger', 'Stock Item').
			fields: List of NATIVEMETHOD names, or None for all ('*').
		"""
		if fields:
			methods = "".join(f"<NATIVEMETHOD>{f}</NATIVEMETHOD>" for f in fields)
		else:
			methods = "<NATIVEMETHOD>*</NATIVEMETHOD>"
		return (
			"<ENVELOPE>"
			"<HEADER>"
			"<VERSION>1</VERSION>"
			"<TALLYREQUEST>EXPORT</TALLYREQUEST>"
			"<TYPE>COLLECTION</TYPE>"
			f"<ID>{name}</ID>"
			"</HEADER>"
			"<BODY><DESC>"
			"<STATICVARIABLES>"
			"<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
			"</STATICVARIABLES>"
			"<TDL><TDLMESSAGE>"
			f'<COLLECTION NAME="{name}" ISINITIALIZE="Yes">'
			f"<TYPE>{object_type}</TYPE>"
			f"{methods}"
			"</COLLECTION>"
			"</TDLMESSAGE></TDL>"
			"</DESC></BODY>"
			"</ENVELOPE>"
		)

	def test_connection(self):
		"""Test connectivity to TallyPrime.

		Returns:
			dict with keys: connected (bool), company (str or None), error (str or None).
		"""
		try:
			xml = self._build_collection_xml("CompanyInfo", "Company", ["Name"])
			text = self._post(xml)
			root = ET.fromstring(text)
			name_el = root.find(".//NAME")
			company = name_el.text.strip() if name_el is not None and name_el.text else None
			return {"connected": True, "company": company, "error": None}
		except requests.ConnectionError:
			return {"connected": False, "company": None, "error": f"Cannot connect to {self.base_url}"}
		except Exception as exc:
			return {"connected": False, "company": None, "error": str(exc)}

	def get_company_info(self):
		"""Fetch basic company information."""
		xml = self._build_collection_xml(
			"CompanyInfo", "Company",
			["Name", "BasicCurrencyCode", "FormalName", "Country", "StateName",
			 "PINCode", "Phone", "Email", "GSTN", "IncomeTaxNumber"],
		)
		return self._post(xml)

	def get_list_of_accounts(self):
		"""Fetch the full 'List of Accounts' report (all master data)."""
		xml = self._build_report_xml("List of Accounts", explode=True)
		return self._post(xml)

	def get_trial_balance(self):
		"""Fetch the 'Trial Balance' report."""
		xml = self._build_report_xml("Trial Balance", explode=True)
		return self._post(xml)

	def get_stock_summary(self):
		"""Fetch the 'Stock Summary' report."""
		xml = self._build_report_xml("Stock Summary", explode=False)
		return self._post(xml)

	def get_day_book(self, from_date=None, to_date=None, company=None):
		"""Fetch the 'Day Book' report (all vouchers/transactions).

		Args:
			from_date: Start date as YYYYMMDD string (e.g. '20260401').
			to_date: End date as YYYYMMDD string (e.g. '20270331').
			company: Tally company name. If None, uses the active company.
		"""
		static_vars = '<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>'
		if from_date:
			static_vars += f'<SVFROMDATE>{from_date}</SVFROMDATE>'
		if to_date:
			static_vars += f'<SVTODATE>{to_date}</SVTODATE>'
		if company:
			static_vars += f'<SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>'
		xml = (
			"<ENVELOPE>"
			"<HEADER>"
			"<VERSION>1</VERSION>"
			"<TALLYREQUEST>Export</TALLYREQUEST>"
			"<TYPE>Data</TYPE>"
			"<ID>Day Book</ID>"
			"</HEADER>"
			"<BODY><DESC><STATICVARIABLES>"
			f"{static_vars}"
			"</STATICVARIABLES></DESC></BODY>"
			"</ENVELOPE>"
		)
		return self._post(xml)

	def get_stock_item_balances(self):
		"""Fetch Stock Items with closing balance, rate, and value."""
		xml = self._build_collection_xml(
			"StockItemBalances", "Stock Item",
			["Name", "Parent", "BaseUnits", "ClosingBalance", "ClosingRate", "ClosingValue"],
		)
		return self._post(xml)

	def get_collection(self, object_type, fields=None):
		"""Fetch a TDL Collection of any object type.

		Args:
			object_type: e.g. 'Stock Item', 'Stock Group', 'Godown', 'Unit', 'Cost Centre'
			fields: List of field names, or None for all.
		"""
		name = object_type.replace(" ", "")
		xml = self._build_collection_xml(name, object_type, fields)
		return self._post(xml)

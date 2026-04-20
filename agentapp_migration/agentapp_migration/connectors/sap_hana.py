"""SAP HANA connector for SAP Business One migrations.

This module is read-only by design. It supports discovery, table counts, column
inspection, and small samples so migration planning can happen before import.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import logging

logger = logging.getLogger(__name__)


SAP_B1_DISCOVERY_TABLES = {
	"OADM": "Company administration",
	"OBPL": "Branches / business places",
	"OACT": "Chart of Accounts",
	"OCRD": "Business Partners",
	"OCRG": "Business Partner Groups",
	"CRD1": "Business Partner Addresses",
	"OCPR": "Contact Persons",
	"OITM": "Items",
	"OITB": "Item Groups",
	"OITW": "Item Warehouse Balances",
	"OWHS": "Warehouses",
	"OUOM": "Units of Measure",
	"OPLN": "Price Lists",
	"ITM1": "Item Prices",
	"OINV": "A/R Invoices",
	"INV1": "A/R Invoice Lines",
	"OPCH": "A/P Invoices",
	"PCH1": "A/P Invoice Lines",
	"OJDT": "Journal Entries",
	"JDT1": "Journal Entry Lines",
	"ORCT": "Incoming Payments",
	"OVPM": "Outgoing Payments",
	"OIGE": "Goods Issues",
	"IGE1": "Goods Issue Lines",
	"OIGN": "Goods Receipts",
	"IGN1": "Goods Receipt Lines",
	"OWTR": "Inventory Transfers",
	"WTR1": "Inventory Transfer Lines",
	"OWOR": "Production Orders",
	"WOR1": "Production Order Components",
	"OPDN": "Goods Receipt PO",
	"PDN1": "Goods Receipt PO Lines",
	"ODLN": "Delivery Notes",
	"DLN1": "Delivery Note Lines",
	"CUFD": "User-defined field metadata",
}

SAP_B1_MODULE_TABLES = {
	"branches": ["OBPL"],
	"inventory": ["OITM", "OITW", "OWHS"],
	"sales": ["OINV", "INV1", "ODLN", "DLN1"],
	"purchase": ["OPCH", "PCH1", "OPDN", "PDN1"],
	"payments": ["ORCT", "OVPM"],
	"general_ledger": ["OACT", "OJDT", "JDT1"],
	"manufacturing": ["OWOR", "WOR1"],
	"custom_fields": ["CUFD"],
}


class SAPHanaDriverMissing(RuntimeError):
	"""Raised when SAP's optional hdbcli package is not installed."""


def _load_hdbcli_dbapi():
	try:
		from hdbcli import dbapi
	except ImportError as exc:
		raise SAPHanaDriverMissing(
			"SAP HANA driver missing. Install with `pip install hdbcli` or "
			"`pip install agentapp_migration[sap]` in this bench environment."
		) from exc
	return dbapi


def _quote_identifier(value):
	value = str(value or "").strip()
	if not value:
		raise ValueError("identifier cannot be blank")
	return '"' + value.replace('"', '""') + '"'


def _json_safe(value):
	if isinstance(value, decimal.Decimal):
		return int(value) if value == value.to_integral_value() else float(value)
	if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
		return value.isoformat()
	if isinstance(value, bytes):
		return value.decode("utf-8", errors="replace")
	return value


class SAPHanaClient:
	"""Small read-only SAP HANA client for SAP Business One discovery."""

	def __init__(
		self,
		address,
		port=30015,
		user=None,
		password=None,
		schema=None,
		connection=None,
		connect_options=None,
	):
		self.address = address
		self.port = int(port)
		self.user = user
		self.password = password
		self.schema = schema
		self._connection = connection
		self.connect_options = connect_options or {}

	def connect(self):
		if self._connection is not None:
			return self._connection
		dbapi = _load_hdbcli_dbapi()
		options = {
			"address": self.address,
			"port": self.port,
			"user": self.user,
			"password": self.password,
		}
		options.update(self.connect_options)
		self._connection = dbapi.connect(**options)
		return self._connection

	def close(self):
		if self._connection is not None:
			self._connection.close()
			self._connection = None

	def query(self, sql, params=None):
		"""Run a SELECT query and return rows as JSON-safe dicts."""
		cursor = self.connect().cursor()
		try:
			cursor.execute(sql, params or [])
			columns = [col[0] for col in cursor.description or []]
			return [
				{columns[index]: _json_safe(value) for index, value in enumerate(row)}
				for row in cursor.fetchall()
			]
		finally:
			close = getattr(cursor, "close", None)
			if close:
				close()

	def scalar(self, sql, params=None, default=None):
		rows = self.query(sql, params=params)
		if not rows:
			return default
		return next(iter(rows[0].values()), default)

	def test_connection(self):
		try:
			rows = self.query('SELECT CURRENT_USER AS "CURRENT_USER", CURRENT_SCHEMA AS "CURRENT_SCHEMA" FROM DUMMY')
			return {"connected": True, "info": rows[0] if rows else {}, "error": None}
		except Exception as exc:
			logger.exception("SAP HANA connection test failed")
			return {"connected": False, "info": {}, "error": str(exc)}

	def find_sap_b1_schemas(self):
		"""Find schemas that look like SAP B1 company databases."""
		rows = self.query(
			"""
			SELECT SCHEMA_NAME, COUNT(*) AS TABLE_MATCHES
			FROM SYS.TABLES
			WHERE TABLE_NAME IN ('OADM', 'OACT', 'OCRD', 'OITM')
			GROUP BY SCHEMA_NAME
			HAVING COUNT(*) >= 3
			ORDER BY TABLE_MATCHES DESC, SCHEMA_NAME
			"""
		)
		return rows

	def table_exists(self, schema, table):
		count = self.scalar(
			"""
			SELECT COUNT(*) AS TABLE_COUNT
			FROM SYS.TABLES
			WHERE SCHEMA_NAME = ? AND TABLE_NAME = ?
			""",
			[schema, table],
			default=0,
		)
		return int(count or 0) > 0

	def table_count(self, schema, table):
		if not self.table_exists(schema, table):
			return None
		return int(self.scalar(
			f"SELECT COUNT(*) AS ROW_COUNT FROM {_quote_identifier(schema)}.{_quote_identifier(table)}",
			default=0,
		) or 0)

	def describe_table(self, schema, table):
		return self.query(
			"""
			SELECT COLUMN_NAME, DATA_TYPE_NAME, LENGTH, SCALE, IS_NULLABLE, POSITION
			FROM SYS.TABLE_COLUMNS
			WHERE SCHEMA_NAME = ? AND TABLE_NAME = ?
			ORDER BY POSITION
			""",
			[schema, table],
		)

	def sample_table(self, schema, table, limit=10, columns=None):
		limit = max(1, min(int(limit), 100))
		select_columns = "*"
		if columns:
			select_columns = ", ".join(_quote_identifier(col) for col in columns)
		return self.query(
			f"SELECT {select_columns} FROM {_quote_identifier(schema)}.{_quote_identifier(table)} LIMIT {limit}"
		)

	def discover_sap_b1(self, schema=None, sample_limit=3):
		"""Return a SAP B1 readiness snapshot for one HANA schema."""
		schema = schema or self.schema
		if not schema:
			candidates = self.find_sap_b1_schemas()
			if not candidates:
				raise ValueError("No SAP B1-like schema found. Pass schema explicitly.")
			schema = candidates[0]["SCHEMA_NAME"]

		tables = {}
		for table, description in SAP_B1_DISCOVERY_TABLES.items():
			row_count = self.table_count(schema, table)
			tables[table] = {
				"description": description,
				"exists": row_count is not None,
				"row_count": row_count,
			}

		modules = {}
		for module, required_tables in SAP_B1_MODULE_TABLES.items():
			existing = [table for table in required_tables if tables.get(table, {}).get("exists")]
			modules[module] = {
				"detected": bool(existing),
				"tables_present": existing,
				"tables_expected": required_tables,
			}

		samples = {}
		if int(sample_limit or 0) > 0:
			for table in ("OADM", "OBPL", "OACT", "OCRD", "OITM", "OWHS", "CUFD"):
				if tables.get(table, {}).get("exists"):
					samples[table] = self.sample_table(schema, table, limit=sample_limit)

		return {
			"source_type": "SAP Business One on HANA",
			"schema": schema,
			"generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
			"connection": self.test_connection(),
			"tables": tables,
			"modules": modules,
			"samples": samples,
		}

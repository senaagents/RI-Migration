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

	# ------------------------------------------------------------------
	# Extraction helpers
	# ------------------------------------------------------------------
	def _resolve_schema(self, schema):
		schema = schema or self.schema
		if not schema:
			raise ValueError("schema required (set on client or pass explicitly)")
		return schema

	def _qualified(self, schema, table):
		return f"{_quote_identifier(schema)}.{_quote_identifier(table)}"

	def iter_query(self, sql, params=None, page_size=5000, order_by=None):
		"""Yield rows from a SELECT in pages.

		Pagination is done with HANA's `LIMIT n OFFSET k`. The caller-supplied
		`sql` MUST already be deterministic (have an ORDER BY) when `order_by`
		is None; otherwise pass `order_by` (a quoted column expression) and we
		append it. We refuse to paginate without an order — duplicate/missing
		rows are far worse than a slow query.
		"""
		params = list(params or [])
		page_size = max(1, int(page_size))
		if order_by:
			sql = f"{sql} ORDER BY {order_by}"
		elif " ORDER BY " not in sql.upper():
			raise ValueError("iter_query requires ORDER BY for safe pagination")
		offset = 0
		while True:
			page_sql = f"{sql} LIMIT {page_size} OFFSET {offset}"
			rows = self.query(page_sql, params=params)
			if not rows:
				return
			for row in rows:
				yield row
			if len(rows) < page_size:
				return
			offset += page_size

	def _date_clause(self, column, start, end):
		"""Build a `column BETWEEN ? AND ?` clause + param list. Both bounds
		may be None to skip filtering on that side."""
		clauses, params = [], []
		if start is not None:
			clauses.append(f"{_quote_identifier(column)} >= ?")
			params.append(start)
		if end is not None:
			clauses.append(f"{_quote_identifier(column)} <= ?")
			params.append(end)
		return clauses, params

	# ------------------------------------------------------------------
	# Master data extractors
	# ------------------------------------------------------------------
	def get_chart_of_accounts(self, schema=None, page_size=5000):
		"""OACT — full chart, ordered by AcctCode."""
		schema = self._resolve_schema(schema)
		sql = f'SELECT * FROM {self._qualified(schema, "OACT")}'
		return list(self.iter_query(sql, page_size=page_size, order_by='"AcctCode"'))

	def get_item_groups(self, schema=None):
		schema = self._resolve_schema(schema)
		return self.query(f'SELECT * FROM {self._qualified(schema, "OITB")} ORDER BY "ItmsGrpCod"')

	def get_warehouses(self, schema=None):
		schema = self._resolve_schema(schema)
		return self.query(f'SELECT * FROM {self._qualified(schema, "OWHS")} ORDER BY "WhsCode"')

	def get_uoms(self, schema=None):
		schema = self._resolve_schema(schema)
		return self.query(f'SELECT * FROM {self._qualified(schema, "OUOM")} ORDER BY "UomEntry"')

	def get_business_partner_groups(self, schema=None):
		schema = self._resolve_schema(schema)
		return self.query(f'SELECT * FROM {self._qualified(schema, "OCRG")} ORDER BY "GroupCode"')

	def get_payment_terms(self, schema=None):
		schema = self._resolve_schema(schema)
		return self.query(f'SELECT * FROM {self._qualified(schema, "OCTG")} ORDER BY "GroupNum"')

	def get_price_lists(self, schema=None):
		schema = self._resolve_schema(schema)
		return self.query(f'SELECT * FROM {self._qualified(schema, "OPLN")} ORDER BY "ListNum"')

	def get_branches(self, schema=None):
		schema = self._resolve_schema(schema)
		return self.query(f'SELECT * FROM {self._qualified(schema, "OBPL")} ORDER BY "BPLId"')

	def get_tax_codes(self, schema=None):
		"""Return both OSTC (codes) and OSTA (authorities) — OVTG is empty in
		this dataset, callers should not depend on it."""
		schema = self._resolve_schema(schema)
		return {
			"sales_tax_codes": self.query(
				f'SELECT * FROM {self._qualified(schema, "OSTC")} ORDER BY "Code"'
			),
			"sales_tax_authorities": self.query(
				f'SELECT * FROM {self._qualified(schema, "OSTA")} ORDER BY "Code"'
			),
		}

	def get_business_partners(self, schema=None, card_type=None, page_size=2000):
		"""OCRD with CRD7 fiscal data joined by (CardCode, default address).

		One row per BP. CRD1 (multi-row addresses) is exposed via
		`get_business_partner_addresses()`; we don't fan out here.
		`card_type` filters by 'C' / 'S' / 'L' if given.
		"""
		schema = self._resolve_schema(schema)
		sql = f"""
			SELECT bp.*,
				   crd7."TaxId0" AS "CRD7_TaxId0",
				   crd7."TaxId1" AS "CRD7_TaxId1",
				   crd7."TaxId2" AS "CRD7_TaxId2",
				   crd7."TaxId3" AS "CRD7_TaxId3",
				   crd7."TaxId4" AS "CRD7_TaxId4",
				   crd7."TaxId5" AS "CRD7_TaxId5",
				   crd7."TaxId6" AS "CRD7_TaxId6",
				   crd7."TaxId7" AS "CRD7_TaxId7",
				   crd7."TaxId8" AS "CRD7_TaxId8",
				   crd7."TaxId9" AS "CRD7_TaxId9",
				   crd7."TaxId10" AS "CRD7_TaxId10",
				   crd7."TaxId11" AS "CRD7_TaxId11",
				   crd7."TaxId12" AS "CRD7_TaxId12",
				   crd7."TaxId13" AS "CRD7_TaxId13",
				   crd7."TaxId14" AS "CRD7_TaxId14"
			FROM {self._qualified(schema, "OCRD")} bp
			LEFT JOIN {self._qualified(schema, "CRD7")} crd7
				ON bp."CardCode" = crd7."CardCode"
				AND bp."BillToDef" = crd7."Address"
		"""
		params = []
		if card_type:
			sql += ' WHERE bp."CardType" = ?'
			params.append(card_type)
		return list(self.iter_query(sql, params=params, page_size=page_size, order_by='bp."CardCode"'))

	def get_business_partner_addresses(self, schema=None, page_size=5000):
		"""CRD1 — every address row (multiple per BP)."""
		schema = self._resolve_schema(schema)
		sql = f'SELECT * FROM {self._qualified(schema, "CRD1")}'
		return list(self.iter_query(sql, page_size=page_size, order_by='"CardCode", "Address"'))

	def get_items(self, schema=None, include_udfs=True, page_size=2000):
		"""OITM. Includes all U_* UDFs by default (this dataset has ~120 of
		them; the parser layer decides which matter)."""
		schema = self._resolve_schema(schema)
		select_clause = "*" if include_udfs else self._oitm_standard_columns(schema)
		sql = f'SELECT {select_clause} FROM {self._qualified(schema, "OITM")}'
		return list(self.iter_query(sql, page_size=page_size, order_by='"ItemCode"'))

	def _oitm_standard_columns(self, schema):
		rows = self.query(
			"""
			SELECT COLUMN_NAME FROM TABLE_COLUMNS
			WHERE SCHEMA_NAME = ? AND TABLE_NAME = 'OITM' AND COLUMN_NAME NOT LIKE 'U\\_%' ESCAPE '\\'
			ORDER BY POSITION
			""",
			[schema],
		)
		return ", ".join(_quote_identifier(r["COLUMN_NAME"]) for r in rows)

	def get_item_prices(self, schema=None, page_size=10000):
		"""ITM1 — every (item, price-list) row. ~50k rows in LLM dataset."""
		schema = self._resolve_schema(schema)
		sql = f'SELECT * FROM {self._qualified(schema, "ITM1")}'
		return list(self.iter_query(sql, page_size=page_size, order_by='"ItemCode", "PriceList"'))

	# ------------------------------------------------------------------
	# Transactional extractors
	# ------------------------------------------------------------------
	# Map: header-table -> (line-table | None, date-column, cancel-column | None, key-column, line-key)
	_TXN_TABLES = {
		"OINV":  ("INV1", "DocDate", "CANCELED", "DocEntry", "DocEntry"),
		"OPCH":  ("PCH1", "DocDate", "CANCELED", "DocEntry", "DocEntry"),
		"ORIN":  ("RIN1", "DocDate", "CANCELED", "DocEntry", "DocEntry"),
		"ORPC":  ("RPC1", "DocDate", "CANCELED", "DocEntry", "DocEntry"),
		"ORCT":  ("RCT1", "DocDate", "Canceled", "DocEntry", "DocEntry"),
		"OVPM":  ("VPM1", "DocDate", "Canceled", "DocEntry", "DocEntry"),
		"OJDT":  ("JDT1", "RefDate", None,        "TransId",  "TransId"),
		"OIGN":  ("IGN1", "DocDate", "CANCELED", "DocEntry", "DocEntry"),
		"OIGE":  ("IGE1", "DocDate", "CANCELED", "DocEntry", "DocEntry"),
		"OWTR":  ("WTR1", "DocDate", "CANCELED", "DocEntry", "DocEntry"),
		"ODLN":  ("DLN1", "DocDate", "CANCELED", "DocEntry", "DocEntry"),
		"OPDN":  ("PDN1", "DocDate", "CANCELED", "DocEntry", "DocEntry"),
		"OWOR":  ("WOR1", "PostDate", None,       "DocEntry", "DocEntry"),
	}

	def _extract_txn(self, header_table, schema=None, date_from=None, date_to=None,
					 include_canceled=False, page_size=2000, extra_filters=None):
		"""Extract one txn doctype (header + lines, both within the same filter).

		`extra_filters` is a list of (sql_template, params_list) tuples where the
		template uses `{h}` as a placeholder for the header alias (so the same
		fragment works in the unaliased header query and in the join). E.g.
		`('{h}"TransType" = ?', [30])`.
		"""
		schema = self._resolve_schema(schema)
		line_table, date_col, cancel_col, key_col, _ = self._TXN_TABLES[header_table]

		def build(prefix):
			"""prefix is '' for header-only query, 'hdr.' for joined line query."""
			clauses, params = [], []
			if date_from is not None:
				clauses.append(f'{prefix}"{date_col}" >= ?'); params.append(date_from)
			if date_to is not None:
				clauses.append(f'{prefix}"{date_col}" <= ?'); params.append(date_to)
			if cancel_col and not include_canceled:
				clauses.append(f'{prefix}"{cancel_col}" <> ?'); params.append("Y")
			for tmpl, tmpl_params in (extra_filters or []):
				clauses.append(tmpl.format(h=prefix))
				params.extend(tmpl_params)
			where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
			return where, params

		header_where, header_params = build("")
		header_sql = f'SELECT * FROM {self._qualified(schema, header_table)}{header_where}'
		headers = list(self.iter_query(
			header_sql, params=header_params, page_size=page_size,
			order_by=f'"{date_col}", "{key_col}"',
		))

		lines = []
		if line_table and headers:
			line_where, line_params = build("hdr.")
			line_sql = (
				f'SELECT lines.* FROM {self._qualified(schema, line_table)} lines '
				f'JOIN {self._qualified(schema, header_table)} hdr '
				f'ON lines."{key_col}" = hdr."{key_col}"'
				f'{line_where}'
			)
			line_order_col = '"Line_ID"' if line_table == "JDT1" else '"LineNum"'
			lines = list(self.iter_query(
				line_sql, params=line_params, page_size=max(page_size, 5000),
				order_by=f'lines."{key_col}", lines.{line_order_col}',
			))
		return {"headers": headers, "lines": lines, "header_table": header_table,
				"line_table": line_table, "date_column": date_col}

	def get_invoices(self, schema=None, date_from=None, date_to=None, **kw):
		return self._extract_txn("OINV", schema=schema, date_from=date_from, date_to=date_to, **kw)

	def get_purchase_invoices(self, schema=None, date_from=None, date_to=None, **kw):
		return self._extract_txn("OPCH", schema=schema, date_from=date_from, date_to=date_to, **kw)

	def get_credit_memos(self, schema=None, date_from=None, date_to=None, **kw):
		"""Returns both AR (ORIN) and AP (ORPC) credit memos as a dict."""
		return {
			"ar":  self._extract_txn("ORIN", schema=schema, date_from=date_from, date_to=date_to, **kw),
			"ap":  self._extract_txn("ORPC", schema=schema, date_from=date_from, date_to=date_to, **kw),
		}

	def get_payments(self, schema=None, date_from=None, date_to=None, **kw):
		"""Returns both incoming (ORCT) and outgoing (OVPM) payments."""
		return {
			"incoming": self._extract_txn("ORCT", schema=schema, date_from=date_from, date_to=date_to, **kw),
			"outgoing": self._extract_txn("OVPM", schema=schema, date_from=date_from, date_to=date_to, **kw),
		}

	def get_journal_entries(self, schema=None, date_from=None, date_to=None,
							trans_type=30, exclude_reversed=True, page_size=5000, **kw):
		"""OJDT — by default only TransType=30 (manual journals); set
		trans_type=None to get all (which includes the JE side of every other
		document — usually NOT what you want, the per-doctype extractors
		already cover those)."""
		extra_filters = []
		if trans_type is not None:
			extra_filters.append(('{h}"TransType" = ?', [int(trans_type)]))
		if exclude_reversed:
			extra_filters.append(('({h}"StornoToTr" IS NULL OR {h}"StornoToTr" = 0)', []))
		return self._extract_txn(
			"OJDT", schema=schema, date_from=date_from, date_to=date_to,
			extra_filters=extra_filters, page_size=page_size, **kw,
		)

	def get_stock_entries(self, schema=None, date_from=None, date_to=None, **kw):
		"""Goods Receipt (OIGN), Goods Issue (OIGE), and Stock Transfer (OWTR)."""
		return {
			"goods_receipt":  self._extract_txn("OIGN", schema=schema, date_from=date_from, date_to=date_to, **kw),
			"goods_issue":    self._extract_txn("OIGE", schema=schema, date_from=date_from, date_to=date_to, **kw),
			"stock_transfer": self._extract_txn("OWTR", schema=schema, date_from=date_from, date_to=date_to, **kw),
		}

	def get_delivery_notes(self, schema=None, date_from=None, date_to=None, **kw):
		return self._extract_txn("ODLN", schema=schema, date_from=date_from, date_to=date_to, **kw)

	def get_purchase_receipts(self, schema=None, date_from=None, date_to=None, **kw):
		return self._extract_txn("OPDN", schema=schema, date_from=date_from, date_to=date_to, **kw)

	def get_work_orders(self, schema=None, date_from=None, date_to=None,
						exclude_status=("L",), page_size=2000, **kw):
		"""OWOR — production orders. By default skips Status='L' (closed/cancelled).
		Set exclude_status=() to include all."""
		extra_filters = []
		if exclude_status:
			placeholders = ", ".join(["?"] * len(exclude_status))
			extra_filters.append((f'{{h}}"Status" NOT IN ({placeholders})', list(exclude_status)))
		return self._extract_txn(
			"OWOR", schema=schema, date_from=date_from, date_to=date_to,
			extra_filters=extra_filters, page_size=page_size, **kw,
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

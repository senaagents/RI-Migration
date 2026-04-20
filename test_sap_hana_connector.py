import sys
import unittest
from unittest import mock
from pathlib import Path

APP_ROOT = Path(__file__).parent / "agentapp_migration"
sys.path.insert(0, str(APP_ROOT))

from agentapp_migration.connectors.sap_hana import SAPHanaClient, SAPHanaDriverMissing


class FakeCursor:
	def __init__(self, connection):
		self.connection = connection
		self.description = []
		self._rows = []

	def execute(self, sql, params=None):
		self.connection.queries.append((sql, params or []))
		normalized = " ".join(sql.split())
		if "CURRENT_USER" in normalized:
			self.description = [("CURRENT_USER",), ("CURRENT_SCHEMA",)]
			self._rows = [("LLMRO", "LLM_LIVENEW")]
		elif "FROM SYS.TABLES" in normalized and "GROUP BY SCHEMA_NAME" in normalized:
			self.description = [("SCHEMA_NAME",), ("TABLE_MATCHES",)]
			self._rows = [("LLM_LIVENEW", 4)]
		elif "FROM SYS.TABLES" in normalized and "COUNT(*) AS TABLE_COUNT" in normalized:
			table = params[1]
			self.description = [("TABLE_COUNT",)]
			self._rows = [(1 if table in self.connection.tables else 0,)]
		elif "COUNT(*) AS ROW_COUNT" in normalized:
			table = normalized.split('"."')[1].split('"')[0]
			self.description = [("ROW_COUNT",)]
			self._rows = [(self.connection.tables[table],)]
		elif "FROM SYS.TABLE_COLUMNS" in normalized:
			self.description = [("COLUMN_NAME",), ("DATA_TYPE_NAME",), ("LENGTH",), ("SCALE",), ("IS_NULLABLE",), ("POSITION",)]
			self._rows = [("ItemCode", "NVARCHAR", 50, 0, "FALSE", 1)]
		elif 'FROM "LLM_LIVENEW"."OADM"' in normalized:
			self.description = [("CompnyName",)]
			self._rows = [("LLM Appliances Pvt Ltd",)]
		elif 'FROM "LLM_LIVENEW"."OBPL"' in normalized:
			self.description = [("BPLId",), ("BPLName",)]
			self._rows = [(1, "Tamil Nadu")]
		elif 'FROM "LLM_LIVENEW".' in normalized:
			table = normalized.split('"."')[1].split('"')[0]
			self.description = [(f"{table}_sample",)]
			self._rows = [(f"{table}-1",)]
		else:
			raise AssertionError(f"Unhandled SQL: {sql}")

	def fetchall(self):
		return self._rows

	def close(self):
		pass


class FakeConnection:
	def __init__(self):
		self.queries = []
		self.tables = {
			"OADM": 1,
			"OBPL": 6,
			"OACT": 596,
			"OCRD": 2266,
			"OITM": 4923,
			"OITW": 100,
			"OWHS": 29,
			"CUFD": 300,
		}

	def cursor(self):
		return FakeCursor(self)

	def close(self):
		pass


class SAPHanaConnectorTest(unittest.TestCase):
	def test_discover_sap_b1_with_fake_connection(self):
		client = SAPHanaClient("forest.rfgb.net", user="LLMRO", password="secret", connection=FakeConnection())

		result = client.discover_sap_b1(schema="LLM_LIVENEW", sample_limit=1)

		self.assertEqual(result["schema"], "LLM_LIVENEW")
		self.assertTrue(result["connection"]["connected"])
		self.assertEqual(result["tables"]["OITM"]["row_count"], 4923)
		self.assertFalse(result["tables"]["OINV"]["exists"])
		self.assertTrue(result["modules"]["inventory"]["detected"])
		self.assertFalse(result["modules"]["manufacturing"]["detected"])
		self.assertEqual(result["samples"]["OADM"][0]["CompnyName"], "LLM Appliances Pvt Ltd")

	def test_missing_driver_error_is_actionable(self):
		real_import = __import__

		def fake_import(name, *args, **kwargs):
			if name == "hdbcli":
				raise ImportError("forced missing hdbcli")
			return real_import(name, *args, **kwargs)

		with mock.patch("builtins.__import__", side_effect=fake_import):
			with self.assertRaises(SAPHanaDriverMissing) as ctx:
				SAPHanaClient("localhost").connect()
		self.assertIn("pip install hdbcli", str(ctx.exception))


if __name__ == "__main__":
	unittest.main()

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from .config import Settings


def quote_ident(identifier: str) -> str:
    if not identifier or '"' in identifier:
        raise ValueError(f"Invalid SAP identifier: {identifier!r}")
    return f'"{identifier}"'


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, memoryview):
        return value.tobytes().hex()
    return value


class HanaClient(AbstractContextManager["HanaClient"]):
    def __init__(self, settings: Settings):
        self.settings = settings
        self._conn = None

    def __enter__(self) -> "HanaClient":
        try:
            from hdbcli import dbapi
        except ImportError as exc:
            raise RuntimeError("hdbcli is not installed. Run `python -m pip install -e .`.") from exc
        if not self.settings.sap_hana_address or not self.settings.sap_hana_user:
            raise RuntimeError("SAP HANA connection settings are missing. Check `.env`.")
        self._conn = dbapi.connect(
            address=self.settings.sap_hana_address,
            port=self.settings.sap_hana_port,
            user=self.settings.sap_hana_user,
            password=self.settings.sap_hana_password,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("HANA client is not connected.")
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
            columns = [column[0] for column in cursor.description]
            return [{column: json_safe(value) for column, value in zip(columns, row)} for row in cursor.fetchall()]
        finally:
            cursor.close()

    def iter_rows(self, sql: str, params: tuple[Any, ...] = (), batch_size: int = 5000):
        if self._conn is None:
            raise RuntimeError("HANA client is not connected.")
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
            columns = [column[0] for column in cursor.description]
            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    break
                yield [{column: json_safe(value) for column, value in zip(columns, row)} for row in batch]
        finally:
            cursor.close()

    def table_names(self) -> list[str]:
        sql = """
            SELECT TABLE_NAME
            FROM SYS.TABLES
            WHERE SCHEMA_NAME = ?
            ORDER BY TABLE_NAME
        """
        return [str(row["TABLE_NAME"]) for row in self.rows(sql, (self.settings.sap_hana_schema,))]

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        rows = self.rows(sql, params)
        return rows[0] if rows else {}

    def table_sql(self, table_name: str) -> str:
        return f"{quote_ident(self.settings.sap_hana_schema)}.{quote_ident(table_name)}"

    def table_exists(self, table_name: str) -> bool:
        sql = """
            SELECT COUNT(*) AS CNT
            FROM SYS.TABLES
            WHERE SCHEMA_NAME = ? AND TABLE_NAME = ?
        """
        return int(self.one(sql, (self.settings.sap_hana_schema, table_name)).get("CNT") or 0) > 0

    def table_count(self, table_name: str) -> int:
        return int(self.one(f"SELECT COUNT(*) AS CNT FROM {self.table_sql(table_name)}").get("CNT") or 0)

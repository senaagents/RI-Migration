from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .config import Settings
from .hana_client import HanaClient, quote_ident
from .models import SapDocument, SapDocumentLine, SapMasterRecord, SapRawRow, SapRawTableProfile, SyncRun, TableProfile
from .sap_b1 import DOCUMENT_FAMILIES, DOCUMENT_FAMILY_BY_NAME, MASTER_TABLES, DocumentFamily, MasterTable


def _json(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _start_run(session: Session, settings: Settings, sync_type: str, from_date: str | None = None, to_date: str | None = None) -> SyncRun:
    run = SyncRun(sync_type=sync_type, source_schema=settings.sap_hana_schema, from_date=from_date, to_date=to_date)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _finish_run(session: Session, run: SyncRun, status: str = "success", error_message: str | None = None) -> None:
    run.status = status
    run.error_message = error_message
    run.finished_at = datetime.utcnow()
    session.add(run)
    session.commit()


def profile_documents(session: Session, client: HanaClient, from_date: str, to_date: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for family in DOCUMENT_FAMILIES:
        if not client.table_exists(family.header_table):
            continue
        date_column = quote_ident(family.date_column)
        sql = f"""
            SELECT
                COUNT(*) AS ROW_COUNT,
                SUM(CASE WHEN {date_column} BETWEEN ? AND ? THEN 1 ELSE 0 END) AS WINDOW_COUNT,
                MIN({date_column}) AS MIN_DATE,
                MAX({date_column}) AS MAX_DATE
            FROM {client.table_sql(family.header_table)}
        """
        row = client.one(sql, (from_date, to_date))
        profile = TableProfile(
            table_name=family.header_table,
            date_column=family.date_column,
            from_date=from_date,
            to_date=to_date,
            row_count=int(row.get("ROW_COUNT") or 0),
            window_count=int(row.get("WINDOW_COUNT") or 0),
            min_date=row.get("MIN_DATE"),
            max_date=row.get("MAX_DATE"),
        )
        session.execute(
            delete(TableProfile).where(
                TableProfile.table_name == profile.table_name,
                TableProfile.date_column == profile.date_column,
                TableProfile.from_date == profile.from_date,
                TableProfile.to_date == profile.to_date,
            )
        )
        session.add(profile)
        results.append(
            {
                "family": family.name,
                "table": family.header_table,
                "date_column": family.date_column,
                "row_count": profile.row_count,
                "window_count": profile.window_count,
                "min_date": profile.min_date,
                "max_date": profile.max_date,
            }
        )
    session.commit()
    return results


def profile_all_tables(session: Session, client: HanaClient, include_empty: bool = False) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for table_name in client.table_names():
        try:
            row_count = client.table_count(table_name)
            if row_count == 0 and not include_empty:
                continue
            stmt = sqlite_insert(SapRawTableProfile).values(
                table_name=table_name,
                source_row_count=row_count,
                status="profiled",
                error_message=None,
                profiled_at=datetime.utcnow(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["table_name"],
                set_={
                    "source_row_count": stmt.excluded.source_row_count,
                    "status": stmt.excluded.status,
                    "error_message": None,
                    "profiled_at": stmt.excluded.profiled_at,
                },
            )
            session.execute(stmt)
            results.append({"table_name": table_name, "source_row_count": row_count, "status": "profiled"})
        except Exception as exc:
            stmt = sqlite_insert(SapRawTableProfile).values(
                table_name=table_name,
                source_row_count=0,
                status="skipped",
                error_message=str(exc),
                profiled_at=datetime.utcnow(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["table_name"],
                set_={
                    "source_row_count": 0,
                    "staged_row_count": 0,
                    "status": "skipped",
                    "error_message": stmt.excluded.error_message,
                    "profiled_at": stmt.excluded.profiled_at,
                },
            )
            session.execute(stmt)
            results.append({"table_name": table_name, "source_row_count": 0, "status": "error", "error": str(exc)})
        session.commit()
    return results


def sync_raw_tables(
    session: Session,
    client: HanaClient,
    settings: Settings,
    table_names: list[str] | None = None,
    include_empty: bool = False,
    batch_size: int = 5000,
    max_rows_per_table: int = 0,
    progress=None,
) -> dict[str, dict[str, int | str]]:
    run = _start_run(session, settings, "sync_raw_tables")
    result: dict[str, dict[str, int | str]] = {}
    try:
        if table_names is None:
            profile_all_tables(session, client, include_empty=include_empty)
            selected = [
                row[0]
                for row in session.execute(
                    select(SapRawTableProfile.table_name)
                    .where(SapRawTableProfile.source_row_count > 0 if not include_empty else SapRawTableProfile.source_row_count >= 0)
                    .order_by(SapRawTableProfile.table_name)
                ).all()
            ]
        else:
            selected = table_names
        for table_name in selected:
            result[table_name] = _sync_raw_table(
                session,
                client,
                run,
                table_name=table_name,
                batch_size=batch_size,
                max_rows=max_rows_per_table,
            )
            if progress is not None:
                progress(table_name, result[table_name])
        _finish_run(session, run)
        return result
    except Exception as exc:
        _finish_run(session, run, "error", str(exc))
        raise


def _sync_raw_table(
    session: Session,
    client: HanaClient,
    run: SyncRun,
    table_name: str,
    batch_size: int,
    max_rows: int,
) -> dict[str, int | str]:
    if not client.table_exists(table_name):
        return {"source": 0, "staged": 0, "status": "missing"}
    source_count = client.table_count(table_name)
    session.execute(delete(SapRawRow).where(SapRawRow.source_table == table_name))
    session.commit()
    row_ordinal = 0
    now = datetime.utcnow()
    suffix = f" LIMIT {int(max_rows)}" if max_rows else ""
    status = "limited" if max_rows else "staged"
    try:
        for rows in client.iter_rows(f"SELECT * FROM {client.table_sql(table_name)}{suffix}", batch_size=batch_size):
            values = []
            for row in rows:
                row_ordinal += 1
                payload = _json(row)
                values.append(
                    {
                        "sync_run_id": run.id,
                        "source_table": table_name,
                        "row_ordinal": row_ordinal,
                        "payload_sha256": _sha(payload),
                        "payload_json": payload,
                        "extracted_at": now,
                    }
                )
            if values:
                session.execute(sqlite_insert(SapRawRow), values)
                session.commit()
        session.execute(
            sqlite_insert(SapRawTableProfile)
            .values(
                table_name=table_name,
                source_row_count=source_count,
                staged_row_count=row_ordinal,
                status=status,
                error_message=None,
                profiled_at=datetime.utcnow(),
                staged_at=datetime.utcnow(),
            )
            .on_conflict_do_update(
                index_elements=["table_name"],
                set_={
                    "source_row_count": source_count,
                    "staged_row_count": row_ordinal,
                    "status": status,
                    "error_message": None,
                    "staged_at": datetime.utcnow(),
                },
            )
        )
        session.commit()
        return {"source": source_count, "staged": row_ordinal, "status": status}
    except Exception as exc:
        session.execute(
            update(SapRawTableProfile)
            .where(SapRawTableProfile.table_name == table_name)
            .values(status="error", error_message=str(exc), staged_row_count=row_ordinal, staged_at=datetime.utcnow())
        )
        session.commit()
        return {"source": source_count, "staged": row_ordinal, "status": "error", "error": str(exc)}


def sync_masters(session: Session, client: HanaClient, settings: Settings, limit: int = 0) -> dict[str, int]:
    run = _start_run(session, settings, "sync_masters")
    counts: dict[str, int] = {}
    try:
        for table in MASTER_TABLES:
            if not client.table_exists(table.table_name):
                counts[table.table_name] = 0
                continue
            counts[table.table_name] = _sync_master_table(session, client, run, table, limit)
        _finish_run(session, run)
        return counts
    except Exception as exc:
        _finish_run(session, run, "error", str(exc))
        raise


def _sync_master_table(session: Session, client: HanaClient, run: SyncRun, table: MasterTable, limit: int) -> int:
    suffix = f" LIMIT {int(limit)}" if limit else ""
    rows = client.rows(f"SELECT * FROM {client.table_sql(table.table_name)}{suffix}")
    if not limit:
        session.execute(delete(SapMasterRecord).where(SapMasterRecord.source_table == table.table_name))
        session.commit()
    saved = 0
    for row in rows:
        source_key = str(row.get(table.key_column) or "")
        payload = _json(row)
        if table.table_name in {"CRD1", "CRD7", "ITM1", "OITW"}:
            source_key = "|".join(str(row.get(part) or "") for part in _composite_key_parts(table.table_name))
        if table.table_name == "CRD7":
            source_key = f"{source_key}|{_sha(payload)[:16]}"
        if not source_key:
            continue
        stmt = sqlite_insert(SapMasterRecord).values(
            sync_run_id=run.id,
            record_type=table.record_type,
            source_table=table.table_name,
            source_key=source_key,
            source_name=str(row.get(table.name_column) or "") if table.name_column else None,
            payload_sha256=_sha(payload),
            payload_json=payload,
            extracted_at=datetime.utcnow(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["record_type", "source_table", "source_key"],
            set_={
                "sync_run_id": run.id,
                "source_name": stmt.excluded.source_name,
                "payload_sha256": stmt.excluded.payload_sha256,
                "payload_json": stmt.excluded.payload_json,
                "extracted_at": stmt.excluded.extracted_at,
            },
        )
        session.execute(stmt)
        saved += 1
    session.commit()
    return saved


def _resolve_since_floor(
    session: Session,
    *,
    since: str | None,
    since_last_run: bool,
) -> datetime | None:
    """Resolve the audit-column floor for incremental sync.

    Precedence: --since (explicit) > --since-last-run (auto) > None (full window).
    """
    if since:
        return datetime.fromisoformat(since)
    if not since_last_run:
        return None
    floor = session.scalar(
        select(func.max(SyncRun.started_at)).where(
            SyncRun.sync_type == "sync_documents",
            SyncRun.status == "success",
        )
    )
    return floor


def sync_documents(
    session: Session,
    client: HanaClient,
    settings: Settings,
    from_date: str,
    to_date: str,
    family_name: str,
    limit: int = 0,
    since: str | None = None,
    since_last_run: bool = False,
) -> dict[str, dict[str, int]]:
    families = list(DOCUMENT_FAMILIES) if family_name == "all" else [DOCUMENT_FAMILY_BY_NAME[family_name]]
    floor = _resolve_since_floor(session, since=since, since_last_run=since_last_run)
    # Subtract a 24h safety pad: SAP HANA UpdateDate is day-granularity and server-local IST,
    # so a UTC-based floor can miss same-day mutations. UPSERT idempotency makes the over-fetch free.
    floor_for_query = (floor - timedelta(days=1)) if floor else None
    run = _start_run(session, settings, "sync_documents", from_date, to_date)
    result: dict[str, dict[str, int]] = {}
    try:
        for family in families:
            headers = _sync_document_headers(
                session, client, run, family, from_date, to_date, limit, floor_for_query
            )
            lines = _sync_document_lines(session, client, run, family) if family.line_table else 0
            result[family.name] = {"headers": headers, "lines": lines}
        _finish_run(session, run)
        return result
    except Exception as exc:
        _finish_run(session, run, "error", str(exc))
        raise


def _sync_document_headers(
    session: Session,
    client: HanaClient,
    run: SyncRun,
    family: DocumentFamily,
    from_date: str,
    to_date: str,
    limit: int,
    floor: datetime | None = None,
) -> int:
    if not client.table_exists(family.header_table):
        return 0
    suffix = f" LIMIT {int(limit)}" if limit else ""
    date_column = quote_ident(family.date_column)
    key_column = quote_ident(family.key_column)
    use_floor = floor is not None and family.audit_column is not None
    audit_predicate = ""
    params: tuple[Any, ...] = (from_date, to_date)
    if use_floor:
        audit_column = quote_ident(family.audit_column)  # type: ignore[arg-type]
        audit_predicate = f"AND {audit_column} >= ?"
        params = (from_date, to_date, floor.date().isoformat())  # type: ignore[union-attr]
    sql = f"""
        SELECT *
        FROM {client.table_sql(family.header_table)}
        WHERE {date_column} BETWEEN ? AND ?
        {audit_predicate}
        ORDER BY {date_column}, {key_column}
        {suffix}
    """
    rows = client.rows(sql, params)
    saved = 0
    for row in rows:
        posting_date = row.get(family.date_column)
        if posting_date and not (from_date <= str(posting_date)[:10] <= to_date):
            raise RuntimeError(f"{family.header_table} returned out-of-window row {row.get(family.key_column)} dated {posting_date}")
        source_key = str(row.get(family.key_column) or "")
        if not source_key:
            continue
        payload = _json(row)
        stmt = sqlite_insert(SapDocument).values(
            sync_run_id=run.id,
            family=family.name,
            source_table=family.header_table,
            source_key=source_key,
            doc_num=str(row.get("DocNum") or row.get("Number") or ""),
            posting_date=str(posting_date or ""),
            card_code=str(row.get("CardCode") or ""),
            card_name=str(row.get("CardName") or ""),
            status=str(row.get("DocStatus") or ""),
            canceled=str(row.get("CANCELED") or row.get("Canceled") or ""),
            total=_as_float(row.get("DocTotal") or row.get("LocTotal") or row.get("TransTotal")),
            payload_sha256=_sha(payload),
            payload_json=payload,
            extracted_at=datetime.utcnow(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_table", "source_key"],
            set_={
                "sync_run_id": run.id,
                "family": family.name,
                "doc_num": stmt.excluded.doc_num,
                "posting_date": stmt.excluded.posting_date,
                "card_code": stmt.excluded.card_code,
                "card_name": stmt.excluded.card_name,
                "status": stmt.excluded.status,
                "canceled": stmt.excluded.canceled,
                "total": stmt.excluded.total,
                "payload_sha256": stmt.excluded.payload_sha256,
                "payload_json": stmt.excluded.payload_json,
                "extracted_at": stmt.excluded.extracted_at,
            },
        )
        session.execute(stmt)
        saved += 1
    session.commit()
    return saved


def _sync_document_lines(session: Session, client: HanaClient, run: SyncRun, family: DocumentFamily) -> int:
    if not family.line_table or not client.table_exists(family.line_table):
        return 0
    staged_keys = [
        row[0]
        for row in session.execute(
            select(SapDocument.source_key).where(SapDocument.source_table == family.header_table, SapDocument.sync_run_id == run.id)
        ).all()
    ]
    if not staged_keys:
        return 0
    saved = 0
    chunk_size = 500
    for start in range(0, len(staged_keys), chunk_size):
        chunk = staged_keys[start : start + chunk_size]
        placeholders = ", ".join("?" for _ in chunk)
        key_column = quote_ident(family.key_column)
        line_number_column = quote_ident(family.line_number_column)
        sql = f"""
            SELECT *
            FROM {client.table_sql(family.line_table)}
            WHERE {key_column} IN ({placeholders})
            ORDER BY {key_column}, {line_number_column}
        """
        for row in client.rows(sql, tuple(chunk)):
            source_key = str(row.get(family.key_column) or "")
            line_num = int(row.get(family.line_number_column) or 0)
            payload = _json(row)
            stmt = sqlite_insert(SapDocumentLine).values(
                sync_run_id=run.id,
                family=family.name,
                parent_table=family.header_table,
                source_table=family.line_table,
                source_key=source_key,
                line_num=line_num,
                item_code=str(row.get("ItemCode") or ""),
                account_code=str(row.get("AcctCode") or row.get("Account") or ""),
                warehouse_code=str(row.get("WhsCode") or ""),
                quantity=_as_float(row.get("Quantity") or row.get("InQty") or row.get("OutQty")),
                line_total=_as_float(row.get("LineTotal") or row.get("Debit") or row.get("Credit")),
                payload_sha256=_sha(payload),
                payload_json=payload,
                extracted_at=datetime.utcnow(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["source_table", "source_key", "line_num"],
                set_={
                    "sync_run_id": run.id,
                    "family": family.name,
                    "parent_table": family.header_table,
                    "item_code": stmt.excluded.item_code,
                    "account_code": stmt.excluded.account_code,
                    "warehouse_code": stmt.excluded.warehouse_code,
                    "quantity": stmt.excluded.quantity,
                    "line_total": stmt.excluded.line_total,
                    "payload_sha256": stmt.excluded.payload_sha256,
                    "payload_json": stmt.excluded.payload_json,
                    "extracted_at": stmt.excluded.extracted_at,
                },
            )
            session.execute(stmt)
            saved += 1
        session.commit()
    return saved


def report(session: Session) -> dict[str, int]:
    tables = {
        "sync_runs": SyncRun,
        "table_profiles": TableProfile,
        "sap_raw_table_profiles": SapRawTableProfile,
        "sap_raw_rows": SapRawRow,
        "sap_master_records": SapMasterRecord,
        "sap_documents": SapDocument,
        "sap_document_lines": SapDocumentLine,
    }
    return {name: int(session.scalar(select(func.count()).select_from(model)) or 0) for name, model in tables.items()}


def _composite_key_parts(table_name: str) -> tuple[str, ...]:
    return {
        "CRD1": ("CardCode", "AdresType", "Address", "LineNum"),
        "CRD7": ("CardCode", "AddrType", "Address", "LogInstanc"),
        "ITM1": ("ItemCode", "PriceList"),
        "OITW": ("ItemCode", "WhsCode"),
    }.get(table_name, ("DocEntry",))


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

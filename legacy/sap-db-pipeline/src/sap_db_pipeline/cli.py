from __future__ import annotations

import json
from functools import wraps
from typing import Optional

import typer

from .config import get_settings
from .db import get_session, init_db
from .hana_client import HanaClient
from .sap_b1 import DOCUMENT_FAMILY_BY_NAME
from .sync import profile_all_tables, profile_documents, report, sync_documents, sync_masters, sync_raw_tables


app = typer.Typer(no_args_is_help=True, add_completion=False)


def command(name: str):
    def decorator(func):
        @app.command(name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except typer.Exit:
                raise
            except Exception as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(code=1)

        return wrapper

    return decorator


@command("init-db")
def init_db_command() -> None:
    init_db()
    typer.echo("Database schema initialized.")


@command("doctor")
def doctor() -> None:
    settings = get_settings()
    with HanaClient(settings) as client:
        row = client.one("SELECT CURRENT_USER AS USER_NAME, CURRENT_SCHEMA AS SCHEMA_NAME FROM DUMMY")
        table_count = client.one(
            """
            SELECT COUNT(*) AS CNT
            FROM SYS.TABLES
            WHERE SCHEMA_NAME = ?
            """,
            (settings.sap_hana_schema,),
        )
        typer.echo(f"Connected as: {row.get('USER_NAME')}")
        typer.echo(f"Current schema: {row.get('SCHEMA_NAME')}")
        typer.echo(f"Source schema: {settings.sap_hana_schema}")
        typer.echo(f"Tables visible in source schema: {int(table_count.get('CNT') or 0)}")


@command("profile-documents")
def profile_documents_command(
    from_date: str = typer.Option(..., help="Inclusive start date in YYYY-MM-DD format."),
    to_date: str = typer.Option(..., help="Inclusive end date in YYYY-MM-DD format."),
) -> None:
    init_db()
    settings = get_settings()
    with HanaClient(settings) as client, get_session() as session:
        rows = profile_documents(session, client, from_date, to_date)
    for row in rows:
        typer.echo(
            f"{row['table']} {row['family']}: total={row['row_count']} "
            f"window={row['window_count']} min={row['min_date']} max={row['max_date']} date={row['date_column']}"
        )


@command("sync-masters")
def sync_masters_command(limit: int = typer.Option(0, help="Optional per-table row limit for smoke tests.")) -> None:
    init_db()
    settings = get_settings()
    limit = limit or settings.sap_query_limit
    with HanaClient(settings) as client, get_session() as session:
        counts = sync_masters(session, client, settings, limit=limit)
    for table_name, count in counts.items():
        typer.echo(f"{table_name}: {count}")


@command("sync-documents")
def sync_documents_command(
    from_date: str = typer.Option(..., help="Inclusive start date in YYYY-MM-DD format."),
    to_date: str = typer.Option(..., help="Inclusive end date in YYYY-MM-DD format."),
    family: str = typer.Option("all", help="Document family name or all."),
    limit: int = typer.Option(0, help="Optional per-family header limit for smoke tests."),
    since: Optional[str] = typer.Option(None, help="Only re-stage rows whose SAP audit column (UpdateDate / CreateDate) >= this timestamp. ISO format: 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'."),
    since_last_run: bool = typer.Option(False, "--since-last-run", help="Auto-derive --since from MAX(started_at) of last successful sync_documents run."),
) -> None:
    if family != "all" and family not in DOCUMENT_FAMILY_BY_NAME:
        allowed = ", ".join(["all", *DOCUMENT_FAMILY_BY_NAME.keys()])
        raise typer.BadParameter(f"Unknown family. Allowed: {allowed}")
    if since and since_last_run:
        raise typer.BadParameter("Pass --since OR --since-last-run, not both.")
    init_db()
    settings = get_settings()
    limit = limit or settings.sap_query_limit
    with HanaClient(settings) as client, get_session() as session:
        result = sync_documents(
            session, client, settings, from_date, to_date, family,
            limit=limit, since=since, since_last_run=since_last_run,
        )
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@command("profile-all-tables")
def profile_all_tables_command(include_empty: bool = typer.Option(False, help="Include SAP tables with zero rows.")) -> None:
    init_db()
    settings = get_settings()
    with HanaClient(settings) as client, get_session() as session:
        rows = profile_all_tables(session, client, include_empty=include_empty)
    total_tables = len(rows)
    non_empty = sum(1 for row in rows if int(row.get("source_row_count") or 0) > 0)
    total_rows = sum(int(row.get("source_row_count") or 0) for row in rows)
    typer.echo(f"Profiled tables: {total_tables}")
    typer.echo(f"Non-empty tables: {non_empty}")
    typer.echo(f"Source rows: {total_rows}")


@command("sync-raw-tables")
def sync_raw_tables_command(
    table: Optional[list[str]] = typer.Option(None, help="Specific SAP table to stage. Repeat for multiple tables."),
    include_empty: bool = typer.Option(False, help="Include SAP tables with zero rows."),
    batch_size: int = typer.Option(5000, help="Rows fetched/written per batch."),
    max_rows_per_table: int = typer.Option(0, help="Smoke-test row cap per table. Do not use for final staging."),
) -> None:
    init_db()
    settings = get_settings()

    def emit_progress(table_name: str, row: dict) -> None:
        typer.echo(f"{table_name}: source={row.get('source')} staged={row.get('staged')} status={row.get('status')}")

    with HanaClient(settings) as client, get_session() as session:
        result = sync_raw_tables(
            session,
            client,
            settings,
            table_names=table or None,
            include_empty=include_empty,
            batch_size=batch_size,
            max_rows_per_table=max_rows_per_table,
            progress=emit_progress,
        )
    failures = {name: row for name, row in result.items() if row.get("status") == "error"}
    typer.echo(f"Tables staged: {len(result)}")
    if failures:
        typer.echo(f"Tables with errors: {len(failures)}", err=True)
        raise typer.Exit(code=1)


@command("report")
def report_command() -> None:
    init_db()
    with get_session() as session:
        result = report(session)
    for table_name, count in result.items():
        typer.echo(f"{table_name}: {count}")

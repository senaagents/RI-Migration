#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_TABLES = (
    "source_files",
    "field_strings",
    "raw_tlvs",
    "compact_numeric_fields",
    "extended_numeric_fields",
    "company_guess",
    "manager_records",
    "manager_master_records",
    "manager_master_kind_guesses",
    "companies",
    "groups",
    "ledgers",
    "stock_items",
    "units",
    "godowns",
    "cost_centres",
    "voucher_types",
    "voucher_text_pages",
    "voucher_header_candidates",
    "vch_status_slots",
    "voucher_ledger_entry_candidates",
    "voucher_ledger_entries_1800",
    "voucher_inventory_entry_candidates",
    "voucher_inventory_entries_1800",
    "voucher_decode_audit",
    "xml_repair_queue",
    "linkmgr_allocation_pages",
    "voucher_bill_allocation_candidates_1800",
    "voucher_bank_allocation_candidates_1800",
    "voucher_order_allocation_candidates_1800",
    "voucher_statutory_status",
    "voucher_invoice_metadata",
    "voucher_einvoice_archive",
)

SURFACE_TABLES = {
    "src": "source_files",
    "raw": "raw_tlvs",
    "masters": "manager_master_records",
    "kinds": "manager_master_kind_guesses",
    "typed": "voucher_types",
    "headers": "voucher_header_candidates",
    "vstatus": "vch_status_slots",
    "ledger": "voucher_ledger_entries_1800",
    "inv": "voucher_inventory_entries_1800",
    "audit": "voucher_decode_audit",
    "repair": "xml_repair_queue",
    "link": "linkmgr_allocation_pages",
    "bill_alloc": "voucher_bill_allocation_candidates_1800",
    "bank_alloc": "voucher_bank_allocation_candidates_1800",
    "order_alloc": "voucher_order_allocation_candidates_1800",
    "stat": "voucher_statutory_status",
    "einv": "voucher_invoice_metadata",
    "arch": "voucher_einvoice_archive",
}


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def rel_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def table_names(con: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in con.execute("select name from sqlite_master where type='table'")
    }


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in con.execute(f"pragma table_info({qident(table)})")}


def count_table(con: sqlite3.Connection, table: str) -> int:
    return int(con.execute(f"select count(*) from {qident(table)}").fetchone()[0])


def scalar(con: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> Any:
    row = con.execute(sql, args).fetchone()
    if row is None:
        return None
    return row[0]


def rows_as_dicts(
    con: sqlite3.Connection,
    sql: str,
    args: tuple[Any, ...] = (),
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if limit is not None:
        sql = f"{sql} limit {int(limit)}"
    return [dict(row) for row in con.execute(sql, args)]


def safe_ratio(numerator: int | None, denominator: int | None) -> float | None:
    if not denominator:
        return None
    return round((numerator or 0) / denominator, 4)


def source_inventory(
    con: sqlite3.Connection,
    tables: set[str],
    repo_root: Path,
) -> dict[str, Any]:
    if "source_files" not in tables:
        return {
            "row_count": 0,
            "source_dirs": [],
            "missing_file_count": None,
            "existing_file_count": None,
        }

    rows = rows_as_dicts(
        con,
        """
        select file_name, path, role, size, sha256
        from source_files
        order by file_name
        """,
    )
    dirs: dict[str, dict[str, Any]] = {}
    missing_files: list[str] = []
    existing_files = 0
    role_counts: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        path = Path(str(row["path"]))
        parent = path.parent
        key = str(parent)
        entry = dirs.setdefault(
            key,
            {
                "path": key,
                "path_display": rel_path(parent, repo_root),
                "dir_exists": parent.exists(),
                "file_count": 0,
                "existing_files": 0,
                "missing_files": 0,
                "bytes_expected": 0,
            },
        )
        exists = path.exists()
        entry["file_count"] += 1
        entry["bytes_expected"] += int(row["size"] or 0)
        role_counts[str(row["role"])] += 1
        if exists:
            existing_files += 1
            entry["existing_files"] += 1
        else:
            entry["missing_files"] += 1
            missing_files.append(str(row["path"]))

    return {
        "row_count": len(rows),
        "source_dirs": sorted(dirs.values(), key=lambda item: item["path"]),
        "missing_file_count": len(missing_files),
        "existing_file_count": existing_files,
        "role_counts": dict(sorted(role_counts.items())),
        "missing_files_sample": missing_files[:12],
    }


def company_guess(con: sqlite3.Connection, tables: set[str]) -> dict[str, str]:
    if "company_guess" not in tables:
        return {}
    return {
        str(row["key"]): str(row["value"])
        for row in con.execute("select key, value from company_guess order by key")
    }


def master_metrics(con: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    if "manager_records" in tables:
        metrics["manager_records"] = count_table(con, "manager_records")
        cols = table_columns(con, "manager_records")
        if "name" in cols:
            metrics["manager_records_with_names"] = int(
                scalar(con, "select count(*) from manager_records where name is not null")
            )
    if "manager_master_records" in tables:
        metrics["manager_master_records"] = count_table(con, "manager_master_records")
        cols = table_columns(con, "manager_master_records")
        if "name" in cols:
            metrics["manager_master_records_with_names"] = int(
                scalar(con, "select count(*) from manager_master_records where name is not null")
            )
    if "manager_master_kind_guesses" in tables:
        metrics["kind_counts"] = rows_as_dicts(
            con,
            """
            select kind_guess, confidence, count(*) as rows
            from manager_master_kind_guesses
            group by kind_guess, confidence
            order by rows desc, kind_guess, confidence
            """,
        )
    return metrics


def voucher_metrics(con: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if "voucher_header_candidates" not in tables:
        return {}
    cols = table_columns(con, "voucher_header_candidates")
    metrics: dict[str, Any] = {"header_candidates": count_table(con, "voucher_header_candidates")}
    for col in ("guid_guess", "voucher_date_guess", "key_raw", "voucher_number_guess"):
        if col in cols:
            metrics[f"with_{col}"] = int(
                scalar(
                    con,
                    f"select count(*) from voucher_header_candidates where {qident(col)} is not null",
                )
            )
    if {"key_raw", "base_type_code"} <= cols:
        metrics["exportable_key_not_base31"] = int(
            scalar(
                con,
                """
                select count(*)
                from voucher_header_candidates
                where key_raw is not null and coalesce(base_type_code, -1) != 31
                """,
            )
        )
        metrics["base_type_counts"] = rows_as_dicts(
            con,
            """
            select base_type_code, count(*) as rows
            from voucher_header_candidates
            group by base_type_code
            order by rows desc
            """,
            limit=12,
        )
    if "paired_voucher_type_master_id" in cols:
        metrics["typed_header_candidates"] = int(
            scalar(
                con,
                """
                select count(*)
                from voucher_header_candidates
                where paired_voucher_type_master_id is not null
                """,
            )
        )
        name_join = ""
        name_col = "''"
        if "manager_master_records" in tables:
            name_join = """
            left join manager_master_records m
              on m.master_id = h.paired_voucher_type_master_id
            """
            name_col = "coalesce(m.name, '')"
        metrics["top_voucher_types"] = rows_as_dicts(
            con,
            f"""
            select h.paired_voucher_type_master_id as voucher_type_master_id,
                   {name_col} as voucher_type_name,
                   count(*) as rows
            from voucher_header_candidates h
            {name_join}
            group by h.paired_voucher_type_master_id, {name_col}
            order by rows desc
            """,
            limit=15,
        )
    return metrics


def grouped_source_counts(con: sqlite3.Connection, table: str, source_col: str) -> list[dict[str, Any]]:
    return rows_as_dicts(
        con,
        f"""
        select {qident(source_col)} as source, confidence, count(*) as rows
        from {qident(table)}
        group by {qident(source_col)}, confidence
        order by rows desc, source, confidence
        """,
        limit=20,
    )


def ledger_metrics(con: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    final_rows = count_table(con, "voucher_ledger_entries_1800") if (
        "voucher_ledger_entries_1800" in tables
    ) else 0
    candidate_rows = count_table(con, "voucher_ledger_entry_candidates") if (
        "voucher_ledger_entry_candidates" in tables
    ) else 0
    metrics["final_rows"] = final_rows
    metrics["candidate_rows"] = candidate_rows
    metrics["candidate_to_final_ratio"] = safe_ratio(candidate_rows, final_rows)
    if "voucher_ledger_entries_1800" in tables:
        metrics["final_by_sources"] = grouped_source_counts(
            con,
            "voucher_ledger_entries_1800",
            "sources",
        )
        metrics["top_final_voucher_types"] = rows_as_dicts(
            con,
            """
            select coalesce(voucher_type_name, '') as voucher_type_name,
                   count(*) as rows,
                   round(sum(abs(amount_value)), 2) as abs_amount_sum
            from voucher_ledger_entries_1800
            group by coalesce(voucher_type_name, '')
            order by rows desc
            """,
            limit=15,
        )
    if "voucher_ledger_entry_candidates" in tables:
        metrics["candidates_by_source"] = grouped_source_counts(
            con,
            "voucher_ledger_entry_candidates",
            "source",
        )
    return metrics


def unique_inventory_candidate_count(con: sqlite3.Connection) -> int:
    return int(
        scalar(
            con,
            """
            select count(*)
            from (
              select distinct
                voucher_master_id,
                stock_item_master_id,
                coalesce(ledger_master_id, -1) as ledger_master_id,
                coalesce(unit_master_id, -1) as unit_master_id,
                quantity_scaled,
                rate_scaled,
                amount_scaled
              from voucher_inventory_entry_candidates
            )
            """,
        )
    )


def inventory_metrics(con: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    final_rows = count_table(con, "voucher_inventory_entries_1800") if (
        "voucher_inventory_entries_1800" in tables
    ) else 0
    candidate_rows = count_table(con, "voucher_inventory_entry_candidates") if (
        "voucher_inventory_entry_candidates" in tables
    ) else 0
    metrics["final_rows"] = final_rows
    metrics["candidate_rows"] = candidate_rows
    metrics["candidate_to_final_ratio"] = safe_ratio(candidate_rows, final_rows)
    if "voucher_inventory_entry_candidates" in tables:
        metrics["candidate_unique_tuples"] = unique_inventory_candidate_count(con)
        metrics["candidates_by_source"] = grouped_source_counts(
            con,
            "voucher_inventory_entry_candidates",
            "source",
        )
    if "voucher_inventory_entries_1800" in tables:
        metrics["final_by_sources"] = grouped_source_counts(
            con,
            "voucher_inventory_entries_1800",
            "sources",
        )
        metrics["final_null_ledger_rows"] = int(
            scalar(
                con,
                """
                select count(*)
                from voucher_inventory_entries_1800
                where ledger_master_id is null
                """,
            )
        )
        metrics["top_final_voucher_types"] = rows_as_dicts(
            con,
            """
            select coalesce(voucher_type_name, '') as voucher_type_name,
                   count(*) as rows,
                   sum(case when ledger_master_id is null then 1 else 0 end) as null_ledger_rows,
                   round(sum(abs(amount_value)), 2) as abs_amount_sum
            from voucher_inventory_entries_1800
            group by coalesce(voucher_type_name, '')
            order by rows desc
            """,
            limit=15,
        )
    if {"voucher_inventory_entries_1800", "voucher_inventory_entry_candidates"} <= tables:
        final_by_type = {
            row["voucher_type_name"]: row["rows"]
            for row in con.execute(
                """
                select coalesce(voucher_type_name, '') as voucher_type_name,
                       count(*) as rows
                from voucher_inventory_entries_1800
                group by coalesce(voucher_type_name, '')
                """
            )
        }
        candidate_by_type = {
            row["voucher_type_name"]: row["rows"]
            for row in con.execute(
                """
                select coalesce(voucher_type_name, '') as voucher_type_name,
                       count(*) as rows
                from voucher_inventory_entry_candidates
                group by coalesce(voucher_type_name, '')
                """
            )
        }
        suspicious = []
        for voucher_type in sorted(set(final_by_type) | set(candidate_by_type)):
            final = int(final_by_type.get(voucher_type, 0))
            candidate = int(candidate_by_type.get(voucher_type, 0))
            ratio = safe_ratio(candidate, final)
            if (final == 0 and candidate >= 20) or (
                final > 0 and candidate - final >= 50 and candidate / final >= 2.0
            ):
                suspicious.append(
                    {
                        "voucher_type_name": voucher_type,
                        "candidate_rows": candidate,
                        "final_rows": final,
                        "candidate_to_final_ratio": ratio,
                    }
                )
        suspicious.sort(
            key=lambda item: (
                item["candidate_rows"] - item["final_rows"],
                item["candidate_rows"],
            ),
            reverse=True,
        )
        metrics["suspicious_candidate_blowups"] = suspicious[:20]
    return metrics


def audit_metrics(con: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    if "voucher_decode_audit" in tables:
        metrics["rows"] = count_table(con, "voucher_decode_audit")
        metrics["needs_xml_review"] = int(
            scalar(con, "select count(*) from voucher_decode_audit where needs_xml_review = 1")
        )
        metrics["needs_xml_repair"] = int(
            scalar(con, "select count(*) from voucher_decode_audit where needs_xml_repair = 1")
        )
        metrics["review_ratio"] = safe_ratio(metrics["needs_xml_review"], metrics["rows"])
        metrics["repair_ratio"] = safe_ratio(metrics["needs_xml_repair"], metrics["rows"])
        metrics["top_repair_types"] = rows_as_dicts(
            con,
            """
            select coalesce(voucher_type_name, '') as voucher_type_name,
                   count(*) as rows
            from voucher_decode_audit
            where needs_xml_repair = 1
            group by coalesce(voucher_type_name, '')
            order by rows desc
            """,
            limit=12,
        )
    if "xml_repair_queue" in tables:
        metrics["xml_repair_queue_rows"] = count_table(con, "xml_repair_queue")
    return metrics


def auxiliary_metrics(con: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    typed_tables = [
        "companies",
        "groups",
        "ledgers",
        "stock_items",
        "units",
        "godowns",
        "cost_centres",
        "voucher_types",
    ]
    present_typed = [table for table in typed_tables if table in tables]
    if present_typed:
        metrics["typed_masters"] = {
            table: count_table(con, table) for table in present_typed
        }
    if "linkmgr_allocation_pages" in tables:
        metrics["linkmgr"] = {
            "rows": count_table(con, "linkmgr_allocation_pages"),
            "voucher_resolves": int(
                scalar(
                    con,
                    "select count(*) from linkmgr_allocation_pages where voucher_resolves = 1",
                )
            ),
            "ledger_resolves": int(
                scalar(
                    con,
                    "select count(*) from linkmgr_allocation_pages where ledger_resolves = 1",
                )
            ),
        }
    allocation_candidate_tables = [
        "voucher_bill_allocation_candidates_1800",
        "voucher_bank_allocation_candidates_1800",
        "voucher_order_allocation_candidates_1800",
    ]
    present_allocations = [
        table for table in allocation_candidate_tables if table in tables
    ]
    if present_allocations:
        metrics["allocation_candidates"] = {
            table: count_table(con, table) for table in present_allocations
        }
    if "voucher_statutory_status" in tables:
        metrics["statutory"] = {
            "rows": count_table(con, "voucher_statutory_status"),
            "reportable_rows": int(
                scalar(
                    con,
                    """
                    select count(*)
                    from voucher_statutory_status
                    where is_statutory_reportable = 1
                    """,
                )
            ),
            "warning_rows": int(
                scalar(
                    con,
                    """
                    select count(*)
                    from voucher_statutory_status
                    where audit_warnings_json != '[]'
                    """,
                )
            ),
        }
    if "voucher_invoice_metadata" in tables:
        metrics["invoice"] = {
            "rows": count_table(con, "voucher_invoice_metadata"),
            "unique_irns": int(
                scalar(con, "select count(distinct irn) from voucher_invoice_metadata")
            ),
            "mapped_voucher_rows": int(
                scalar(
                    con,
                    """
                    select count(*)
                    from voucher_invoice_metadata
                    where voucher_master_id is not null
                    """,
                )
            ),
            "conflict_rows": int(
                scalar(con, "select count(*) from voucher_invoice_metadata where has_conflict = 1")
            ),
        }
    if "voucher_einvoice_archive" in tables:
        metrics["einvoice_archive"] = {
            "rows": count_table(con, "voucher_einvoice_archive"),
            "with_irn": int(
                scalar(con, "select count(*) from voucher_einvoice_archive where has_irn = 1")
            ),
            "with_ack": int(
                scalar(con, "select count(*) from voucher_einvoice_archive where has_ack = 1")
            ),
            "with_jwt": int(
                scalar(con, "select count(*) from voucher_einvoice_archive where has_jwt = 1")
            ),
        }
    return metrics


def db_metrics(path: Path, repo_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "path_display": rel_path(path, repo_root),
        "size_bytes": path.stat().st_size,
    }
    try:
        con = connect_ro(path)
    except sqlite3.Error as exc:
        result["error"] = str(exc)
        return result

    try:
        tables = table_names(con)
        result["tables"] = sorted(tables)
        result["table_count"] = len(tables)
        result["surface_presence"] = {
            key: table in tables for key, table in SURFACE_TABLES.items()
        }
        result["expected_table_counts"] = {
            table: count_table(con, table) if table in tables else None
            for table in EXPECTED_TABLES
        }
        result["decoded_artifact"] = "source_files" in tables and (
            "manager_records" in tables
            or "manager_master_records" in tables
            or "voucher_header_candidates" in tables
        )
        result["company_guess"] = company_guess(con, tables)
        result["source_inventory"] = source_inventory(con, tables, repo_root)
        result["masters"] = master_metrics(con, tables)
        result["vouchers"] = voucher_metrics(con, tables)
        result["ledger"] = ledger_metrics(con, tables)
        result["inventory"] = inventory_metrics(con, tables)
        result["audit"] = audit_metrics(con, tables)
        result["auxiliary"] = auxiliary_metrics(con, tables)
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    finally:
        con.close()
    return result


def discover_db_paths(out_dir: Path, include_synthetic: bool) -> list[Path]:
    paths = set(out_dir.glob("*.sqlite3"))
    if include_synthetic:
        paths.update(out_dir.glob("synthetic-live/*/after_decoded.sqlite3"))
    return sorted(path for path in paths if path.is_file())


def company_label(db: dict[str, Any]) -> str:
    guess = db.get("company_guess") or {}
    if guess.get("company_name"):
        return str(guess["company_name"])
    source_dirs = (db.get("source_inventory") or {}).get("source_dirs") or []
    if source_dirs:
        return Path(str(source_dirs[0]["path"])).name
    return Path(str(db["path"])).stem


def surface_score(db: dict[str, Any]) -> int:
    presence = db.get("surface_presence") or {}
    return sum(1 for value in presence.values() if value)


def build_summary(report: dict[str, Any]) -> dict[str, Any]:
    dbs = report["databases"]
    decoded = [db for db in dbs if db.get("decoded_artifact") and not db.get("error")]
    by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for db in decoded:
        by_company[company_label(db)].append(db)
    representatives = []
    for company, items in sorted(by_company.items()):
        best = sorted(
            items,
            key=lambda db: (
                surface_score(db),
                db.get("expected_table_counts", {}).get("voucher_inventory_entries_1800") or 0,
                db.get("expected_table_counts", {}).get("voucher_ledger_entries_1800") or 0,
                db.get("size_bytes", 0),
            ),
            reverse=True,
        )[0]
        representatives.append(
            {
                "company": company,
                "db": best["path_display"],
                "variant_count": len(items),
                "surface_score": surface_score(best),
            }
        )

    missing_source = []
    no_source_rows = []
    for db in decoded:
        inv = db.get("source_inventory") or {}
        if inv.get("row_count") == 0:
            no_source_rows.append(
                {
                    "db": db["path_display"],
                    "company": company_label(db),
                }
            )
        if inv.get("missing_file_count"):
            missing_source.append(
                {
                    "db": db["path_display"],
                    "company": company_label(db),
                    "missing_file_count": inv["missing_file_count"],
                    "source_dirs": [
                        item["path_display"] for item in inv.get("source_dirs", [])
                    ],
                }
            )

    return {
        "database_count": len(dbs),
        "decoded_artifact_count": len(decoded),
        "representative_decoded_artifacts": representatives,
        "databases_with_missing_source_files": missing_source,
        "databases_with_no_source_rows": no_source_rows,
    }


def write_csv_summary(report: dict[str, Any], path: Path) -> None:
    fieldnames = [
        "db",
        "company",
        "decoded_artifact",
        "source_files",
        "missing_source_files",
        "masters",
        "kind_rows",
        "voucher_headers",
        "ledger_final",
        "ledger_candidates",
        "inventory_final",
        "inventory_candidates",
        "audit_rows",
        "needs_xml_review",
        "needs_xml_repair",
        "repair_queue",
        "linkmgr_rows",
        "bill_alloc_candidates",
        "bank_alloc_candidates",
        "order_alloc_candidates",
        "stat_rows",
        "invoice_rows",
        "archive_rows",
        "typed_groups",
        "typed_ledgers",
        "typed_stock_items",
        "typed_voucher_types",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for db in report["databases"]:
            counts = db.get("expected_table_counts") or {}
            audit = db.get("audit") or {}
            aux = db.get("auxiliary") or {}
            writer.writerow(
                {
                    "db": db.get("path_display"),
                    "company": company_label(db),
                    "decoded_artifact": int(bool(db.get("decoded_artifact"))),
                    "source_files": (db.get("source_inventory") or {}).get("row_count"),
                    "missing_source_files": (db.get("source_inventory") or {}).get(
                        "missing_file_count"
                    ),
                    "masters": counts.get("manager_master_records"),
                    "kind_rows": counts.get("manager_master_kind_guesses"),
                    "voucher_headers": counts.get("voucher_header_candidates"),
                    "ledger_final": counts.get("voucher_ledger_entries_1800"),
                    "ledger_candidates": counts.get("voucher_ledger_entry_candidates"),
                    "inventory_final": counts.get("voucher_inventory_entries_1800"),
                    "inventory_candidates": counts.get("voucher_inventory_entry_candidates"),
                    "audit_rows": audit.get("rows"),
                    "needs_xml_review": audit.get("needs_xml_review"),
                    "needs_xml_repair": audit.get("needs_xml_repair"),
                    "repair_queue": audit.get("xml_repair_queue_rows"),
                    "linkmgr_rows": (aux.get("linkmgr") or {}).get("rows"),
                    "bill_alloc_candidates": (aux.get("allocation_candidates") or {}).get("voucher_bill_allocation_candidates_1800"),
                    "bank_alloc_candidates": (aux.get("allocation_candidates") or {}).get("voucher_bank_allocation_candidates_1800"),
                    "order_alloc_candidates": (aux.get("allocation_candidates") or {}).get("voucher_order_allocation_candidates_1800"),
                    "stat_rows": (aux.get("statutory") or {}).get("rows"),
                    "invoice_rows": (aux.get("invoice") or {}).get("rows"),
                    "archive_rows": (aux.get("einvoice_archive") or {}).get("rows"),
                    "typed_groups": (aux.get("typed_masters") or {}).get("groups"),
                    "typed_ledgers": (aux.get("typed_masters") or {}).get("ledgers"),
                    "typed_stock_items": (aux.get("typed_masters") or {}).get("stock_items"),
                    "typed_voucher_types": (aux.get("typed_masters") or {}).get("voucher_types"),
                }
            )


def yes_no(value: bool) -> str:
    return "Y" if value else "-"


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    lines.append("# Cross-Company Validation Report")
    lines.append("")
    lines.append(f"Generated: `{report['generated_at']}`")
    lines.append(f"Command: `{report['command']}`")
    lines.append("")
    summary = report["summary"]
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"- SQLite files scanned: {summary['database_count']}; "
        f"decoded artifacts: {summary['decoded_artifact_count']}."
    )
    lines.append(
        "- Regenerate command: "
        "`PYTHONPATH=. python3 -m tally1800 decode-sqlite <company-folder> "
        "out/<company-id>_decoded.sqlite3`."
    )
    missing = summary["databases_with_missing_source_files"]
    no_source_rows = summary["databases_with_no_source_rows"]
    lines.append(f"- Decoded artifacts with missing source files: {len(missing)}.")
    lines.append(f"- Decoded artifacts with zero `source_files` rows: {len(no_source_rows)}.")
    lines.append("")

    lines.append("## Surface Inventory")
    lines.append("")
    headers = [
        "DB",
        "src",
        "raw",
        "masters",
        "kinds",
        "typed",
        "headers",
        "ledger",
        "inv",
        "audit",
        "link",
        "bill",
        "bank",
        "order",
        "stat",
        "einv",
        "arch",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for db in report["databases"]:
        presence = db.get("surface_presence") or {}
        row = [
            db.get("path_display", ""),
            yes_no(presence.get("src", False)),
            yes_no(presence.get("raw", False)),
            yes_no(presence.get("masters", False)),
            yes_no(presence.get("kinds", False)),
            yes_no(presence.get("typed", False)),
            yes_no(presence.get("headers", False)),
            yes_no(presence.get("ledger", False)),
            yes_no(presence.get("inv", False)),
            yes_no(presence.get("audit", False)),
            yes_no(presence.get("link", False)),
            yes_no(presence.get("bill_alloc", False)),
            yes_no(presence.get("bank_alloc", False)),
            yes_no(presence.get("order_alloc", False)),
            yes_no(presence.get("stat", False)),
            yes_no(presence.get("einv", False)),
            yes_no(presence.get("arch", False)),
        ]
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    lines.append("")

    lines.append("## Core Metrics")
    lines.append("")
    headers = [
        "DB",
        "Company",
        "Headers",
        "Masters",
        "Ledger final/cand",
        "Inventory final/cand",
        "Review",
        "Repair",
        "Typed G/L/S/V",
        "Bill/Bank/Order cand",
        "Link/Stat/EInv/Arch",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for db in report["databases"]:
        counts = db.get("expected_table_counts") or {}
        audit = db.get("audit") or {}
        aux = db.get("auxiliary") or {}
        typed = aux.get("typed_masters") or {}
        allocation_candidates = aux.get("allocation_candidates") or {}
        if not db.get("decoded_artifact"):
            continue
        row = [
            db.get("path_display", ""),
            company_label(db),
            counts.get("voucher_header_candidates") or 0,
            counts.get("manager_master_records") or counts.get("manager_records") or 0,
            f"{counts.get('voucher_ledger_entries_1800') or 0}/"
            f"{counts.get('voucher_ledger_entry_candidates') or 0}",
            f"{counts.get('voucher_inventory_entries_1800') or 0}/"
            f"{counts.get('voucher_inventory_entry_candidates') or 0}",
            audit.get("needs_xml_review") or 0,
            audit.get("needs_xml_repair") or 0,
            f"{typed.get('groups') or 0}/"
            f"{typed.get('ledgers') or 0}/"
            f"{typed.get('stock_items') or 0}/"
            f"{typed.get('voucher_types') or 0}",
            f"{allocation_candidates.get('voucher_bill_allocation_candidates_1800') or 0}/"
            f"{allocation_candidates.get('voucher_bank_allocation_candidates_1800') or 0}/"
            f"{allocation_candidates.get('voucher_order_allocation_candidates_1800') or 0}",
            f"{(aux.get('linkmgr') or {}).get('rows') or 0}/"
            f"{(aux.get('statutory') or {}).get('rows') or 0}/"
            f"{(aux.get('invoice') or {}).get('rows') or 0}/"
            f"{(aux.get('einvoice_archive') or {}).get('rows') or 0}",
        ]
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    lines.append("")

    lines.append("## Source Reachability")
    lines.append("")
    lines.append("| DB | Source files | Missing | Source dirs |")
    lines.append("| --- | ---: | ---: | --- |")
    for db in report["databases"]:
        if not db.get("decoded_artifact"):
            continue
        inv = db.get("source_inventory") or {}
        dirs = ", ".join(item["path_display"] for item in inv.get("source_dirs", []))
        lines.append(
            "| "
            + " | ".join(
                [
                    db.get("path_display", ""),
                    str(inv.get("row_count", 0)),
                    str(inv.get("missing_file_count", 0)),
                    dirs or "-",
                ]
            )
            + " |"
        )
    lines.append("")

    lines.append("## Suspicious Inventory Candidate Blowups")
    lines.append("")
    lines.append("| DB | Voucher type | Final rows | Candidate rows | Candidate/final |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    any_blowup = False
    for db in report["databases"]:
        inv = db.get("inventory") or {}
        for item in inv.get("suspicious_candidate_blowups", [])[:8]:
            any_blowup = True
            lines.append(
                "| "
                + " | ".join(
                    [
                        db.get("path_display", ""),
                        item["voucher_type_name"] or "(blank)",
                        str(item["final_rows"]),
                        str(item["candidate_rows"]),
                        str(item["candidate_to_final_ratio"]),
                    ]
                )
                + " |"
            )
    if not any_blowup:
        lines.append("| - | - | 0 | 0 | - |")
    lines.append("")

    lines.append("## Representative Artifacts")
    lines.append("")
    lines.append("| Company | Best DB | Variants | Surface score |")
    lines.append("| --- | --- | ---: | ---: |")
    for item in summary["representative_decoded_artifacts"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    item["company"],
                    item["db"],
                    str(item["variant_count"]),
                    str(item["surface_score"]),
                ]
            )
            + " |"
        )
    lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only cross-company metrics report for decoded Tally .1800 SQLite artifacts."
    )
    parser.add_argument("--out-dir", default="out/cross-company-validation")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--include-synthetic", action="store_true")
    parser.add_argument(
        "--db",
        action="append",
        default=[],
        help="Specific SQLite DB path. Repeatable; overrides discovery when supplied.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.db:
        paths = sorted(Path(item) for item in args.db)
    else:
        paths = discover_db_paths(Path("out"), args.include_synthetic)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": " ".join(sys.argv),
        "database_paths": [rel_path(path, repo_root) for path in paths],
        "databases": [db_metrics(path, repo_root) for path in paths],
    }
    report["summary"] = build_summary(report)

    json_path = out_dir / "cross-company-validation.json"
    md_path = out_dir / "cross-company-validation.md"
    csv_path = out_dir / "cross-company-validation-summary.csv"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(report, md_path)
    write_csv_summary(report, csv_path)

    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "csv": str(csv_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

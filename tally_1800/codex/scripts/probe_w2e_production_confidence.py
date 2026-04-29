#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DBS = (
    "out/070525_decoded_laneA_sales_logical.sqlite3",
    "out/100025_decoded_current.sqlite3",
    "out/100001_decoded_w2e_current.sqlite3",
    "out/300925_decoded_w2e_current.sqlite3",
)

FINAL_TABLE_POLICY = (
    {
        "surface": "voucher_headers",
        "table": "voucher_header_candidates",
        "production_role": "final_with_filter",
        "confidence": "corpus_calibrated_high",
        "production_rule": "key_raw is not null and coalesce(base_type_code, -1) != 31",
        "xml_policy": "do not fetch XML only to confirm headers unless gate validation fails",
    },
    {
        "surface": "typed_masters",
        "table": "companies/groups/ledgers/stock_items/units/godowns/cost_centres/voucher_types",
        "production_role": "final_direct_identity_with_audit_fields",
        "confidence": "source_local_high_for_identity_and_kind",
        "production_rule": "use typed tables; keep fields_json/all_strings_json for unresolved hierarchy/defaults",
        "xml_policy": "fetch narrow XML only for missing rendered fields required by product UI",
    },
    {
        "surface": "ledger_entries",
        "table": "voucher_ledger_entries_1800",
        "production_role": "final_direct_rows",
        "confidence": "corpus_calibrated_high",
        "production_rule": "confidence = 'high' and voucher joins final header set",
        "xml_policy": "fetch XML only for voucher-level audit failures or required unsupported allocation detail",
    },
    {
        "surface": "inventory_entries",
        "table": "voucher_inventory_entries_1800",
        "production_role": "final_direct_rows",
        "confidence": "corpus_calibrated_high",
        "production_rule": "confidence = 'high' and voucher joins final header set",
        "xml_policy": "fetch XML for xml_repair_queue and optionally for review samples",
    },
    {
        "surface": "inventory_candidates",
        "table": "voucher_inventory_entry_candidates",
        "production_role": "review_only",
        "confidence": "medium_or_source_local_candidate",
        "production_rule": "never union into final export without a promoted source gate",
        "xml_policy": "use as repair hints; do not expose as final business rows",
    },
    {
        "surface": "ledger_candidates",
        "table": "voucher_ledger_entry_candidates",
        "production_role": "review_only",
        "confidence": "medium_or_audit",
        "production_rule": "never union into final export without a promoted source gate",
        "xml_policy": "use as missing-row atlas and tax/allocation hints",
    },
    {
        "surface": "linkmgr_allocations",
        "table": "linkmgr_allocation_pages",
        "production_role": "candidate_audit",
        "confidence": "backptr_high_semantics_candidate",
        "production_rule": "voucher/ledger back-pointers are useful; bill/bank/order semantics are not final",
        "xml_policy": "target narrow voucher allocation XML when product needs allocation detail",
    },
    {
        "surface": "allocation_candidates",
        "table": "voucher_bill/bank/order_allocation_candidates_1800",
        "production_role": "review_only",
        "confidence": "raw_source_high_semantics_candidate",
        "production_rule": "raw LinkMgr fields are preserved; do not expose as final allocation rows",
        "xml_policy": "use as targeted XML hints for allocation repair and parser learning",
    },
    {
        "surface": "statutory_status",
        "table": "voucher_statutory_status",
        "production_role": "audit_index",
        "confidence": "source_local_high_for_slot_fields",
        "production_rule": "do not use as unique voucher MASTERID resolver",
        "xml_policy": "do not fetch broad statutory XML from this table alone",
    },
    {
        "surface": "invoice_metadata",
        "table": "voucher_invoice_metadata",
        "production_role": "direct_metadata_archive",
        "confidence": "gated_direct_metadata",
        "production_rule": "view=1 gated IRN/ACK/JWT metadata only",
        "xml_policy": "fetch voucher XML only for conflicts or required missing UI fields",
    },
    {
        "surface": "einvoice_archive",
        "table": "voucher_einvoice_archive",
        "production_role": "audit_archive",
        "confidence": "gated_archive_payload",
        "production_rule": "view>=3 archive/audit payload; do not infer voucher rows from archive alone",
        "xml_policy": "no XML fetch unless archive conflicts with final metadata",
    },
)


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def table_names(con: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in con.execute("select name from sqlite_master where type='table'")
    }


def count_table(con: sqlite3.Connection, tables: set[str], table: str) -> int:
    if table not in tables:
        return 0
    return int(con.execute(f"select count(*) from {qident(table)}").fetchone()[0])


def scalar(con: sqlite3.Connection, sql: str, default: Any = 0) -> Any:
    row = con.execute(sql).fetchone()
    if row is None:
        return default
    return row[0]


def rows(con: sqlite3.Connection, sql: str, limit: int | None = None) -> list[dict[str, Any]]:
    if limit is not None:
        sql = f"{sql} limit {int(limit)}"
    return [dict(row) for row in con.execute(sql)]


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def company_name(con: sqlite3.Connection, tables: set[str], fallback: str) -> str:
    if "company_guess" in tables:
        row = con.execute(
            "select value from company_guess where key = 'company_name' limit 1"
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    if "companies" in tables:
        row = con.execute("select name from companies where name is not null limit 1").fetchone()
        if row and row[0]:
            return str(row[0])
    return fallback


def source_status(con: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if "source_files" not in tables:
        return {"source_files": 0, "existing_files": 0, "missing_files": 0, "source_dirs": []}
    source_rows = rows(con, "select file_name, path from source_files order by file_name")
    dirs = sorted({str(Path(str(row["path"])).parent) for row in source_rows})
    existing = sum(1 for row in source_rows if Path(str(row["path"])).exists())
    return {
        "source_files": len(source_rows),
        "existing_files": existing,
        "missing_files": len(source_rows) - existing,
        "source_dirs": dirs,
    }


def confidence_for_company(con: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    tables = table_names(con)
    quick_check = str(scalar(con, "pragma quick_check", "not_run"))
    source = source_status(con, tables)

    exportable_headers = 0
    keyed_headers = 0
    base31_headers = 0
    no_key_headers = 0
    if "voucher_header_candidates" in tables:
        exportable_headers = int(
            scalar(
                con,
                """
                select count(*)
                from voucher_header_candidates
                where key_raw is not null
                  and coalesce(base_type_code, -1) != 31
                """,
            )
        )
        keyed_headers = int(
            scalar(con, "select count(*) from voucher_header_candidates where key_raw is not null")
        )
        base31_headers = int(
            scalar(
                con,
                """
                select count(*)
                from voucher_header_candidates
                where key_raw is not null
                  and base_type_code = 31
                """,
            )
        )
        no_key_headers = int(
            scalar(con, "select count(*) from voucher_header_candidates where key_raw is null")
        )

    ledger_final = count_table(con, tables, "voucher_ledger_entries_1800")
    ledger_candidates = count_table(con, tables, "voucher_ledger_entry_candidates")
    inventory_final = count_table(con, tables, "voucher_inventory_entries_1800")
    inventory_candidates = count_table(con, tables, "voucher_inventory_entry_candidates")
    audit_rows = count_table(con, tables, "voucher_decode_audit")
    repair_rows = count_table(con, tables, "xml_repair_queue")
    review_rows = 0
    review_only_rows = 0
    clean_rows = 0
    if "voucher_decode_audit" in tables:
        review_rows = int(
            scalar(con, "select count(*) from voucher_decode_audit where needs_xml_review = 1")
        )
        review_only_rows = int(
            scalar(
                con,
                """
                select count(*)
                from voucher_decode_audit
                where needs_xml_review = 1
                  and needs_xml_repair = 0
                """,
            )
        )
        clean_rows = int(
            scalar(
                con,
                """
                select count(*)
                from voucher_decode_audit
                where needs_xml_review = 0
                  and needs_xml_repair = 0
                """,
            )
        )

    typed_counts = {
        table: count_table(con, tables, table)
        for table in (
            "companies",
            "groups",
            "ledgers",
            "stock_items",
            "units",
            "godowns",
            "cost_centres",
            "voucher_types",
        )
    }

    repair_by_type: list[dict[str, Any]] = []
    if "xml_repair_queue" in tables:
        repair_by_type = rows(
            con,
            """
            select coalesce(voucher_type_name, '') as voucher_type_name,
                   count(*) as rows
            from xml_repair_queue
            group by coalesce(voucher_type_name, '')
            order by rows desc, voucher_type_name
            """,
        )

    review_by_type: list[dict[str, Any]] = []
    if "voucher_decode_audit" in tables:
        review_by_type = rows(
            con,
            """
            select coalesce(voucher_type_name, '') as voucher_type_name,
                   count(*) as rows
            from voucher_decode_audit
            where needs_xml_review = 1
              and needs_xml_repair = 0
            group by coalesce(voucher_type_name, '')
            order by rows desc, voucher_type_name
            """,
            limit=12,
        )

    candidate_pressure: list[dict[str, Any]] = []
    if {
        "voucher_inventory_entries_1800",
        "voucher_inventory_entry_candidates",
    } <= tables:
        candidate_pressure = rows(
            con,
            """
            with final as (
              select voucher_type_name, count(*) as final_rows
              from voucher_inventory_entries_1800
              group by voucher_type_name
            ),
            cand as (
              select voucher_type_name, count(*) as candidate_rows
              from voucher_inventory_entry_candidates
              group by voucher_type_name
            )
            select
              coalesce(c.voucher_type_name, f.voucher_type_name, '') as voucher_type_name,
              coalesce(f.final_rows, 0) as final_rows,
              coalesce(c.candidate_rows, 0) as candidate_rows,
              case
                when coalesce(f.final_rows, 0) = 0 then null
                else round(1.0 * coalesce(c.candidate_rows, 0) / f.final_rows, 4)
              end as candidate_to_final_ratio
            from cand c
            left join final f on f.voucher_type_name = c.voucher_type_name
            where coalesce(c.candidate_rows, 0) > 0
            order by
              case
                when coalesce(f.final_rows, 0) = 0 then 999999.0
                else 1.0 * coalesce(c.candidate_rows, 0) / f.final_rows
              end desc,
              candidate_rows desc
            """,
            limit=10,
        )

    linkmgr = {}
    if "linkmgr_allocation_pages" in tables:
        linkmgr = {
            "rows": count_table(con, tables, "linkmgr_allocation_pages"),
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
            "both_resolve": int(
                scalar(
                    con,
                    """
                    select count(*)
                    from linkmgr_allocation_pages
                    where voucher_resolves = 1
                      and ledger_resolves = 1
                    """,
                )
            ),
        }
    allocation_candidates = {
        "bill_rows": count_table(con, tables, "voucher_bill_allocation_candidates_1800"),
        "bank_rows": count_table(con, tables, "voucher_bank_allocation_candidates_1800"),
        "order_rows": count_table(con, tables, "voucher_order_allocation_candidates_1800"),
    }

    statutory = {}
    if "voucher_statutory_status" in tables:
        statutory = {
            "rows": count_table(con, tables, "voucher_statutory_status"),
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

    invoice = {}
    if "voucher_invoice_metadata" in tables:
        invoice = {
            "rows": count_table(con, tables, "voucher_invoice_metadata"),
            "conflict_rows": int(
                scalar(
                    con,
                    "select count(*) from voucher_invoice_metadata where has_conflict = 1",
                )
            ),
        }
    if "voucher_einvoice_archive" in tables:
        invoice["archive_rows"] = count_table(con, tables, "voucher_einvoice_archive")
        invoice["archive_payload_rows"] = int(
            scalar(
                con,
                "select count(*) from voucher_einvoice_archive where is_payload_archive = 1",
            )
        )

    xml_policy = "direct_only"
    if repair_rows:
        xml_policy = "hybrid_required_for_hard_repairs"
    elif review_only_rows:
        xml_policy = "direct_final_with_review_queue"

    return {
        "db": db_path.as_posix(),
        "company": company_name(con, tables, db_path.stem),
        "quick_check": quick_check,
        "source": source,
        "headers": {
            "all_candidates": count_table(con, tables, "voucher_header_candidates"),
            "keyed": keyed_headers,
            "base31_keyed": base31_headers,
            "no_key": no_key_headers,
            "exportable_final": exportable_headers,
        },
        "typed_masters": typed_counts,
        "ledger": {
            "final_rows": ledger_final,
            "candidate_rows": ledger_candidates,
            "candidate_to_final_ratio": ratio(ledger_candidates, ledger_final),
        },
        "inventory": {
            "final_rows": inventory_final,
            "candidate_rows": inventory_candidates,
            "candidate_to_final_ratio": ratio(inventory_candidates, inventory_final),
            "candidate_pressure": candidate_pressure,
        },
        "audit": {
            "audit_rows": audit_rows,
            "clean_rows": clean_rows,
            "review_rows": review_rows,
            "review_only_rows": review_only_rows,
            "repair_rows": repair_rows,
            "repair_by_type": repair_by_type,
            "review_by_type": review_by_type,
        },
        "allocation_and_archive": {
            "linkmgr": linkmgr,
            "allocation_candidates": allocation_candidates,
            "statutory": statutory,
            "invoice": invoice,
        },
        "production_xml_policy": xml_policy,
    }


def summarize(databases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "database_count": len(databases),
        "quick_check_failures": [
            db["db"] for db in databases if db.get("quick_check") != "ok"
        ],
        "missing_source_file_dbs": [
            db["db"] for db in databases if db["source"]["missing_files"] > 0
        ],
        "total_exportable_headers": sum(db["headers"]["exportable_final"] for db in databases),
        "total_ledger_final": sum(db["ledger"]["final_rows"] for db in databases),
        "total_inventory_final": sum(db["inventory"]["final_rows"] for db in databases),
        "total_review_only": sum(db["audit"]["review_only_rows"] for db in databases),
        "total_repairs": sum(db["audit"]["repair_rows"] for db in databases),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, databases: list[dict[str, Any]]) -> None:
    fields = [
        "db",
        "company",
        "quick_check",
        "missing_source_files",
        "exportable_headers",
        "ledger_final",
        "ledger_candidates",
        "inventory_final",
        "inventory_candidates",
        "audit_rows",
        "review_only_rows",
        "repair_rows",
        "xml_policy",
        "typed_groups",
        "typed_ledgers",
        "typed_stock_items",
        "typed_voucher_types",
        "linkmgr_rows",
        "bill_alloc_candidates",
        "bank_alloc_candidates",
        "order_alloc_candidates",
        "stat_rows",
        "invoice_rows",
        "archive_rows",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for db in databases:
            typed = db["typed_masters"]
            link = db["allocation_and_archive"]["linkmgr"]
            alloc = db["allocation_and_archive"]["allocation_candidates"]
            stat = db["allocation_and_archive"]["statutory"]
            inv = db["allocation_and_archive"]["invoice"]
            writer.writerow(
                {
                    "db": db["db"],
                    "company": db["company"],
                    "quick_check": db["quick_check"],
                    "missing_source_files": db["source"]["missing_files"],
                    "exportable_headers": db["headers"]["exportable_final"],
                    "ledger_final": db["ledger"]["final_rows"],
                    "ledger_candidates": db["ledger"]["candidate_rows"],
                    "inventory_final": db["inventory"]["final_rows"],
                    "inventory_candidates": db["inventory"]["candidate_rows"],
                    "audit_rows": db["audit"]["audit_rows"],
                    "review_only_rows": db["audit"]["review_only_rows"],
                    "repair_rows": db["audit"]["repair_rows"],
                    "xml_policy": db["production_xml_policy"],
                    "typed_groups": typed["groups"],
                    "typed_ledgers": typed["ledgers"],
                    "typed_stock_items": typed["stock_items"],
                    "typed_voucher_types": typed["voucher_types"],
                    "linkmgr_rows": link.get("rows", 0),
                    "bill_alloc_candidates": alloc.get("bill_rows", 0),
                    "bank_alloc_candidates": alloc.get("bank_rows", 0),
                    "order_alloc_candidates": alloc.get("order_rows", 0),
                    "stat_rows": stat.get("rows", 0),
                    "invoice_rows": inv.get("rows", 0),
                    "archive_rows": inv.get("archive_rows", 0),
                }
            )


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    databases = payload["databases"]
    summary = payload["summary"]
    lines = [
        "# W2E Production Confidence Contract",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Databases: {summary['database_count']}",
        f"- Quick check failures: {len(summary['quick_check_failures'])}",
        f"- DBs with missing source files: {len(summary['missing_source_file_dbs'])}",
        f"- Exportable headers: {summary['total_exportable_headers']:,}",
        f"- Final ledger rows: {summary['total_ledger_final']:,}",
        f"- Final inventory rows: {summary['total_inventory_final']:,}",
        f"- Review-only vouchers: {summary['total_review_only']:,}",
        f"- Hard XML repair vouchers: {summary['total_repairs']:,}",
        "",
        "## Company Contract",
        "",
        "| DB | Company | Headers | Ledger final/cand | Inventory final/cand | Allocation cand B/B/O | Review-only | Repair | XML policy |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for db in databases:
        alloc = db["allocation_and_archive"]["allocation_candidates"]
        lines.append(
            "| {db} | {company} | {headers:,} | {ledger_final:,}/{ledger_cand:,} | "
            "{inv_final:,}/{inv_cand:,} | {bill:,}/{bank:,}/{order:,} | "
            "{review:,} | {repair:,} | `{policy}` |".format(
                db=db["db"],
                company=db["company"],
                headers=db["headers"]["exportable_final"],
                ledger_final=db["ledger"]["final_rows"],
                ledger_cand=db["ledger"]["candidate_rows"],
                inv_final=db["inventory"]["final_rows"],
                inv_cand=db["inventory"]["candidate_rows"],
                bill=alloc.get("bill_rows", 0),
                bank=alloc.get("bank_rows", 0),
                order=alloc.get("order_rows", 0),
                review=db["audit"]["review_only_rows"],
                repair=db["audit"]["repair_rows"],
                policy=db["production_xml_policy"],
            )
        )

    lines.extend(
        [
            "",
            "## Table Confidence Policy",
            "",
            "| Surface | Table | Role | Confidence | Production rule | XML policy |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in FINAL_TABLE_POLICY:
        lines.append(
            f"| `{item['surface']}` | `{item['table']}` | `{item['production_role']}` | "
            f"`{item['confidence']}` | {item['production_rule']} | {item['xml_policy']} |"
        )

    lines.extend(
        [
            "",
            "## Hard Repair Queues",
            "",
        ]
    )
    for db in databases:
        lines.append(f"### {db['db']}")
        if not db["audit"]["repair_by_type"]:
            lines.append("")
            lines.append("No hard repairs.")
            lines.append("")
            continue
        lines.append("")
        lines.append("| Voucher type | Rows |")
        lines.append("| --- | ---: |")
        for row in db["audit"]["repair_by_type"]:
            lines.append(f"| {row['voucher_type_name'] or '(blank)'} | {row['rows']} |")
        lines.append("")

    lines.extend(
        [
            "## Candidate Pressure",
            "",
            "Top inventory candidate/final ratios per DB. High ratios are review pressure, not final rows.",
            "",
        ]
    )
    for db in databases:
        lines.append(f"### {db['db']}")
        rows_for_db = db["inventory"]["candidate_pressure"]
        if not rows_for_db:
            lines.append("")
            lines.append("No inventory candidates.")
            lines.append("")
            continue
        lines.append("")
        lines.append("| Voucher type | Final | Candidates | Ratio |")
        lines.append("| --- | ---: | ---: | ---: |")
        for row in rows_for_db[:8]:
            ratio_text = "" if row["candidate_to_final_ratio"] is None else str(row["candidate_to_final_ratio"])
            lines.append(
                f"| {row['voucher_type_name'] or '(blank)'} | "
                f"{row['final_rows']} | {row['candidate_rows']} | {ratio_text} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Future Parser Acceptance Gates",
            "",
            "A parser change can be productized only if all applicable gates pass:",
            "",
            "1. `python3 -m py_compile` succeeds for changed Python files.",
            "2. `sqlite3 <decoded-db> 'pragma quick_check'` returns `ok` for every regenerated DB.",
            "3. Hard repairs do not increase on any current-schema baseline unless the handoff explains a deliberate audit-policy change.",
            "4. Candidate rows do not blow up materially; any candidate/final ratio increase above 10% for a voucher family needs source-level justification.",
            "5. On 070525, final rows must not add Rosetta extras for the targeted family. If Rosetta is unavailable, keep the source candidate-only.",
            "6. Final table duplicate tuple groups must remain zero unless row multiplicity is explicitly part of the proved business rule.",
            "7. New XML use must be limited to `xml_repair_queue` or a documented narrow object lookup. No broad XML report fetches.",
            "8. Handoffs must include byte offsets, source labels, numerator/denominator/extras, and remaining misses.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", action="append", type=Path, help="Decoded SQLite DB to include")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("out/w2e-production-confidence"),
        help="Output directory",
    )
    args = parser.parse_args()

    db_paths = args.db or [Path(db) for db in DEFAULT_DBS]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    databases: list[dict[str, Any]] = []
    for db_path in db_paths:
        if not db_path.exists():
            raise SystemExit(f"missing decoded sqlite: {db_path}")
        with connect_ro(db_path) as con:
            databases.append(confidence_for_company(con, db_path))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "databases": databases,
        "table_confidence_policy": list(FINAL_TABLE_POLICY),
        "summary": summarize(databases),
    }

    write_json(args.out_dir / "production-confidence.json", payload)
    write_csv(args.out_dir / "production-confidence-summary.csv", databases)
    write_markdown(args.out_dir / "production-confidence.md", payload)
    print(
        json.dumps(
            {
                "json": str(args.out_dir / "production-confidence.json"),
                "markdown": str(args.out_dir / "production-confidence.md"),
                "csv": str(args.out_dir / "production-confidence-summary.csv"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

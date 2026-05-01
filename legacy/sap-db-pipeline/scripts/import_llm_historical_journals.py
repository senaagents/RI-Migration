from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.utils import flt, getdate

from llm_migration_config import connect_frappe, COMPANY, CUTOFF_TO_DATE, HISTORY_FROM_DATE, SAP_DB


DEFAULT_COST_CENTER = "Main - L"


class SkippedJournal(RuntimeError):
    pass


def _rows(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def _payload(row: sqlite3.Row) -> dict:
    return json.loads(row["payload_json"])


def _ensure_custom_fields(dry_run: bool) -> int:
    created = 0
    fields = [
        (
            "Journal Entry",
            {
                "fieldname": "custom_sap_trans_id",
                "label": "SAP TransId",
                "fieldtype": "Data",
                "unique": 1,
                "read_only": 1,
                "insert_after": "naming_series",
            },
        ),
        (
            "Journal Entry",
            {
                "fieldname": "custom_sap_trans_type",
                "label": "SAP TransType",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "custom_sap_trans_id",
            },
        ),
        (
            "Journal Entry",
            {
                "fieldname": "custom_sap_base_ref",
                "label": "SAP BaseRef",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "custom_sap_trans_type",
            },
        ),
        (
            "Journal Entry Account",
            {
                "fieldname": "custom_sap_line_id",
                "label": "SAP Line ID",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "account",
            },
        ),
        (
            "Journal Entry Account",
            {
                "fieldname": "custom_sap_short_name",
                "label": "SAP ShortName",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "custom_sap_line_id",
            },
        ),
    ]
    for doctype, field in fields:
        name = f"{doctype}-{field['fieldname']}"
        if frappe.db.exists("Custom Field", name):
            continue
        print(f"create Custom Field {name}")
        created += 1
        if not dry_run:
            create_custom_field(doctype, field, ignore_validate=True)
    return created


def _account_map() -> dict[str, dict]:
    rows = frappe.get_all(
        "Account",
        fields=["name", "custom_sap_acct_code", "account_type", "root_type"],
        filters={"company": COMPANY, "custom_sap_acct_code": ["is", "set"]},
        limit_page_length=0,
    )
    return {str(row.custom_sap_acct_code): row for row in rows}


def _party_maps() -> tuple[dict[str, str], dict[str, str]]:
    customers = {
        str(row.custom_sap_card_code): row.name
        for row in frappe.get_all(
            "Customer",
            fields=["name", "custom_sap_card_code"],
            filters={"custom_sap_card_code": ["is", "set"]},
            limit_page_length=0,
        )
    }
    suppliers = {
        str(row.custom_sap_card_code): row.name
        for row in frappe.get_all(
            "Supplier",
            fields=["name", "custom_sap_card_code"],
            filters={"custom_sap_card_code": ["is", "set"]},
            limit_page_length=0,
        )
    }
    return customers, suppliers


def _dimension_maps() -> tuple[dict[str, str], set[str], set[str]]:
    cost_centers = {
        str(row.custom_sap_cc_code): row.name
        for row in frappe.get_all(
            "Cost Center",
            fields=["name", "custom_sap_cc_code"],
            filters={"company": COMPANY, "custom_sap_cc_code": ["is", "set"]},
            limit_page_length=0,
        )
    }
    departments = {row.name for row in frappe.get_all("SAP Department", fields=["name"], limit_page_length=0)}
    products = {row.name for row in frappe.get_all("SAP Product Segment", fields=["name"], limit_page_length=0)}
    return cost_centers, departments, products


def _journal_headers(limit: int | None, offset: int, from_date: str, to_date: str) -> list[sqlite3.Row]:
    sql = """
        select source_key, payload_json
        from sap_documents
        where source_table='OJDT'
          and date(posting_date) between date(?) and date(?)
        order by posting_date, cast(source_key as integer)
    """
    if limit:
        sql += f" limit {int(limit)}"
    if offset:
        sql += f" offset {int(offset)}"
    return _rows(sql, (from_date, to_date))


def _journal_lines(trans_id: str) -> list[dict]:
    return [
        _payload(row)
        for row in _rows(
            """
            select payload_json
            from sap_document_lines
            where parent_table='OJDT' and source_table='JDT1' and source_key=?
            order by line_num
            """,
            (str(trans_id),),
        )
    ]


def _default_cost_center() -> str:
    if frappe.db.exists("Cost Center", DEFAULT_COST_CENTER):
        return DEFAULT_COST_CENTER
    return frappe.db.get_value("Cost Center", {"company": COMPANY, "is_group": 0}, "name")


def _build_doc(header: dict, lines: list[dict], maps: dict) -> frappe.model.document.Document:
    trans_id = str(header["TransId"])
    doc = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": COMPANY,
            "posting_date": getdate(header.get("RefDate") or header.get("TaxDate")),
            "user_remark": header.get("Memo") or f"SAP journal {trans_id}",
            "custom_sap_trans_id": trans_id,
            "custom_sap_trans_type": str(header.get("TransType") or ""),
            "custom_sap_base_ref": str(header.get("BaseRef") or ""),
        }
    )

    total_debit = 0.0
    total_credit = 0.0
    for line in lines:
        debit = flt(line.get("Debit") or 0, 2)
        credit = flt(line.get("Credit") or 0, 2)
        if debit == 0 and credit == 0:
            continue

        account_code = str(line["Account"])
        account = maps["accounts"].get(account_code)
        if not account:
            raise RuntimeError(f"SAP journal {trans_id} missing account mapping for {account_code!r}")

        row = {
            "account": account.name,
            "debit_in_account_currency": debit,
            "credit_in_account_currency": credit,
            "user_remark": line.get("LineMemo"),
            "custom_sap_line_id": str(line.get("Line_ID") or ""),
            "custom_sap_short_name": str(line.get("ShortName") or ""),
        }

        profit_code = line.get("ProfitCode") or line.get("OcrCode")
        if profit_code:
            row["cost_center"] = maps["cost_centers"].get(profit_code)
            if not row["cost_center"]:
                raise RuntimeError(f"SAP journal {trans_id} missing cost center mapping for {profit_code!r}")
        elif account.root_type in {"Income", "Expense"} and maps["default_cost_center"]:
            row["cost_center"] = maps["default_cost_center"]

        if line.get("OcrCode2"):
            row["sap_department"] = line["OcrCode2"]
        if line.get("OcrCode3"):
            row["sap_product_segment"] = line["OcrCode3"]

        short_name = str(line.get("ShortName") or "")
        if short_name and short_name != account_code:
            if account.account_type == "Receivable" and short_name in maps["customers"]:
                row["party_type"] = "Customer"
                row["party"] = maps["customers"][short_name]
            elif account.account_type == "Payable" and short_name in maps["suppliers"]:
                row["party_type"] = "Supplier"
                row["party"] = maps["suppliers"][short_name]

        doc.append("accounts", row)
        total_debit += debit
        total_credit += credit

    if not doc.accounts:
        raise SkippedJournal(f"SAP journal {trans_id} has no non-zero lines")
    if abs(round(total_debit - total_credit, 2)) > 0.01:
        raise RuntimeError(f"SAP journal {trans_id} is imbalanced: debit={total_debit} credit={total_credit}")
    return doc


def main(
    dry_run: bool = True,
    limit: int | None = None,
    offset: int = 0,
    commit_every: int = 250,
    from_date: str = HISTORY_FROM_DATE,
    to_date: str = CUTOFF_TO_DATE,
) -> None:
    connect_frappe(frappe)
    try:
        created_fields = _ensure_custom_fields(dry_run)
        maps = {
            "accounts": _account_map(),
            "cost_centers": None,
            "departments": None,
            "products": None,
            "default_cost_center": _default_cost_center(),
        }
        maps["customers"], maps["suppliers"] = _party_maps()
        maps["cost_centers"], maps["departments"], maps["products"] = _dimension_maps()

        headers = _journal_headers(limit, offset, from_date, to_date)
        created = 0
        already_exists = 0
        skipped_zero = 0
        skipped_by_type = Counter()
        for row in headers:
            header = _payload(row)
            trans_id = str(header["TransId"])
            trans_type = str(header.get("TransType") or "")
            if frappe.db.exists("Journal Entry", {"custom_sap_trans_id": trans_id}):
                already_exists += 1
                continue
            lines = _journal_lines(trans_id)
            try:
                doc = _build_doc(header, lines, maps)
            except SkippedJournal:
                skipped_zero += 1
                continue
            created += 1
            skipped_by_type[trans_type] += 1
            if dry_run:
                continue
            try:
                doc.insert(ignore_permissions=True)
                doc.submit()
            except Exception as exc:
                raise RuntimeError(f"Failed importing SAP OJDT TransId {trans_id}") from exc
            if created % commit_every == 0:
                frappe.db.commit()
                print(f"committed {created} historical journals")

        if not dry_run:
            frappe.db.commit()
        print(
            json.dumps(
                {
                    "custom_fields": created_fields,
                    "headers_checked": len(headers),
                    "from_date": from_date,
                    "to_date": to_date,
                    "offset": offset,
                    "limit": limit,
                    "already_exists": already_exists,
                    "skipped_zero_movement": skipped_zero,
                    "created" if not dry_run else "would_create": created,
                    "by_trans_type": dict(sorted(skipped_by_type.items())),
                    "default_cost_center": maps["default_cost_center"],
                },
                indent=2,
                sort_keys=True,
            )
        )
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import SAP OJDT/JDT1 as historical ERPNext Journal Entries.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--commit-every", type=int, default=250)
    parser.add_argument("--from-date", default=HISTORY_FROM_DATE)
    parser.add_argument("--to-date", default=CUTOFF_TO_DATE)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(
        dry_run=args.dry_run,
        limit=args.limit,
        offset=args.offset,
        commit_every=args.commit_every,
        from_date=args.from_date,
        to_date=args.to_date,
    )

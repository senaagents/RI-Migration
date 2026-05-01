from __future__ import annotations

import json
import sqlite3

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.utils import getdate


from llm_migration_config import connect_frappe, COMPANY, CUTOFF_TO_DATE, NATIVE_FROM_DATE, SAP_DB, SITE, SITES_PATH


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
            "Journal Entry Account",
            {
                "fieldname": "custom_sap_line_id",
                "label": "SAP Line ID",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "account",
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
        fields=["name", "custom_sap_acct_code", "account_type"],
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


def _manual_journal_headers(limit: int | None, from_date: str, to_date: str) -> list[sqlite3.Row]:
    sql = """
        select source_key, payload_json
        from sap_documents
        where source_table='OJDT'
          and json_extract(payload_json, '$.TransType')='30'
          and date(posting_date) between date(?) and date(?)
        order by posting_date, cast(source_key as integer)
    """
    if limit:
        sql += f" limit {int(limit)}"
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


def _build_doc(header: dict, lines: list[dict], maps: dict) -> frappe.model.document.Document:
    doc = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": COMPANY,
            "posting_date": getdate(header["RefDate"]),
            "user_remark": header.get("Memo") or f"SAP manual journal {header['TransId']}",
            "custom_sap_trans_id": str(header["TransId"]),
        }
    )

    for line in lines:
        debit = float(line.get("Debit") or 0)
        credit = float(line.get("Credit") or 0)
        if debit == 0 and credit == 0:
            continue
        account_code = str(line["Account"])
        account = maps["accounts"][account_code]
        row = {
            "account": account.name,
            "debit_in_account_currency": debit,
            "credit_in_account_currency": credit,
            "user_remark": line.get("LineMemo"),
            "custom_sap_line_id": str(line.get("Line_ID")),
        }
        profit_code = line.get("ProfitCode") or line.get("OcrCode")
        if profit_code:
            row["cost_center"] = maps["cost_centers"][profit_code]
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
    if not doc.accounts:
        raise RuntimeError(f"SAP manual journal {header['TransId']} has no non-zero lines")
    return doc


def main(
    dry_run: bool = True,
    limit: int | None = None,
    from_date: str = NATIVE_FROM_DATE,
    to_date: str = CUTOFF_TO_DATE,
) -> None:
    connect_frappe(frappe)
    try:
        created_fields = _ensure_custom_fields(dry_run)
        maps = {}
        maps["accounts"] = _account_map()
        maps["customers"], maps["suppliers"] = _party_maps()
        maps["cost_centers"], maps["departments"], maps["products"] = _dimension_maps()

        headers = _manual_journal_headers(limit, from_date, to_date)
        would_create = 0
        already_exists = 0
        for row in headers:
            header = _payload(row)
            trans_id = str(header["TransId"])
            if frappe.db.exists("Journal Entry", {"custom_sap_trans_id": trans_id}):
                already_exists += 1
                continue
            lines = _journal_lines(trans_id)
            doc = _build_doc(header, lines, maps)
            would_create += 1
            if dry_run:
                continue
            doc.insert(ignore_permissions=True)
            doc.submit()
            if would_create % 100 == 0:
                frappe.db.commit()
                print(f"committed {would_create} manual journals")

        if dry_run:
            print(
                json.dumps(
                    {
                        "custom_fields_to_create": created_fields,
                        "headers_checked": len(headers),
                        "from_date": from_date,
                        "to_date": to_date,
                        "already_exists": already_exists,
                        "would_create": would_create,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        frappe.db.commit()
        print(
            json.dumps(
                {
                    "custom_fields_created": created_fields,
                    "headers_checked": len(headers),
                    "from_date": from_date,
                    "to_date": to_date,
                    "already_exists": already_exists,
                    "created": would_create,
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


if __name__ == "__main__":
    main(dry_run=False)

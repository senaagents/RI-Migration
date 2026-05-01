from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

import frappe

from llm_migration_config import connect_frappe, COMPANY, CUTOFF_TO_DATE, SAP_DB, SITE, SITES_PATH

SALES_TABLES = {"OINV", "ORIN", "ORDR", "ODLN"}
PURCHASE_TABLES = {"OPCH", "ORPC", "OPOR", "OPDN"}
PAYMENT_TABLES = {"ORCT", "OVPM"}
STOCK_TABLES = {"OIGN", "OIGE", "OWTR"}


def _rows(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def _load_payload(row: sqlite3.Row) -> dict:
    return json.loads(row["payload_json"])


def _erp_set(doctype: str, field: str = "name") -> set[str]:
    return {
        str(row[field])
        for row in frappe.get_all(doctype, fields=[field], limit_page_length=0)
        if row.get(field) is not None
    }


def _sap_code_set(doctype: str, field: str = "custom_sap_card_code") -> set[str]:
    return _erp_set(doctype, field)


def _account_map() -> dict[str, str]:
    rows = frappe.get_all(
        "Account",
        fields=["name", "custom_sap_acct_code"],
        filters={"company": COMPANY, "custom_sap_acct_code": ["is", "set"]},
        limit_page_length=0,
    )
    return {str(row.custom_sap_acct_code): row.name for row in rows}


def _cost_center_map() -> dict[str, str]:
    rows = frappe.get_all(
        "Cost Center",
        fields=["name", "custom_sap_cc_code"],
        filters={"company": COMPANY, "custom_sap_cc_code": ["is", "set"]},
        limit_page_length=0,
    )
    return {str(row.custom_sap_cc_code): row.name for row in rows}


def _warehouse_map() -> dict[str, str]:
    rows = frappe.get_all(
        "Warehouse",
        fields=["name", "custom_sap_whs_code"],
        filters={"custom_sap_whs_code": ["is", "set"]},
        limit_page_length=0,
    )
    return {str(row.custom_sap_whs_code): row.name for row in rows}


def _branch_map() -> set[int]:
    rows = frappe.get_all("Branch", fields=["custom_sap_bpl_id"], limit_page_length=0)
    return {int(row.custom_sap_bpl_id) for row in rows if row.custom_sap_bpl_id}


def _tax_templates(kind: str) -> set[str]:
    doctype = "Sales Taxes and Charges Template" if kind == "sales" else "Purchase Taxes and Charges Template"
    prefix = "SAP AR" if kind == "sales" else "SAP AP"
    return _erp_set(doctype) & {
        row.name for row in frappe.get_all(doctype, fields=["name"], filters={"name": ["like", f"{prefix} % - L"]}, limit_page_length=0)
    }


def _missing_header_refs() -> dict:
    customers = _sap_code_set("Customer")
    suppliers = _sap_code_set("Supplier")
    branches = _branch_map()
    sales_templates = _tax_templates("sales")
    purchase_templates = _tax_templates("purchase")

    missing = defaultdict(Counter)
    totals = Counter()
    for row in _rows(
        "select family, source_table, payload_json from sap_documents where date(posting_date) <= date(?)",
        (CUTOFF_TO_DATE,),
    ):
        doc = _load_payload(row)
        source_table = row["source_table"]
        totals[source_table] += 1
        card_code = doc.get("CardCode")
        bpl_id = doc.get("BPLId")
        tax_code = doc.get("VatGroup")

        if source_table in SALES_TABLES and card_code and card_code not in customers:
            missing["customer"][card_code] += 1
        if source_table in PURCHASE_TABLES and card_code and card_code not in suppliers:
            missing["supplier"][card_code] += 1
        if bpl_id and int(bpl_id) not in branches:
            missing["branch"][str(bpl_id)] += 1
        if source_table in SALES_TABLES and tax_code and f"SAP AR {tax_code} - L" not in sales_templates:
            missing["sales_tax_template"][tax_code] += 1
        if source_table in PURCHASE_TABLES and tax_code and f"SAP AP {tax_code} - L" not in purchase_templates:
            missing["purchase_tax_template"][tax_code] += 1

    return {"totals": dict(totals), "missing": _top_missing(missing)}


def _missing_line_refs() -> dict:
    items = _erp_set("Item")
    accounts = _account_map()
    warehouses = _warehouse_map()
    cost_centers = _cost_center_map()
    departments = _erp_set("SAP Department")
    product_segments = _erp_set("SAP Product Segment")

    missing = defaultdict(Counter)
    totals = Counter()
    for row in _rows(
        """
        select l.parent_table, l.source_table, l.item_code, l.account_code, l.warehouse_code, l.payload_json
        from sap_document_lines l
        join sap_documents h on h.source_table = l.parent_table and h.source_key = l.source_key
        where date(h.posting_date) <= date(?)
        """,
        (CUTOFF_TO_DATE,),
    ):
        totals[row["source_table"]] += 1
        payload = _load_payload(row)
        item_code = row["item_code"]
        account_code = row["account_code"]
        whs_code = row["warehouse_code"]

        if item_code and item_code not in items:
            missing["item"][item_code] += 1
        if account_code and str(account_code) not in accounts:
            missing["account"][str(account_code)] += 1
        if whs_code and str(whs_code) not in warehouses:
            missing["warehouse"][str(whs_code)] += 1

        profit_code = payload.get("ProfitCode") or payload.get("OcrCode")
        dept = payload.get("OcrCode2")
        product = payload.get("OcrCode3")
        if profit_code and profit_code not in cost_centers:
            missing["cost_center"][profit_code] += 1
        if dept and dept not in departments:
            missing["sap_department"][dept] += 1
        if product and product not in product_segments:
            missing["sap_product_segment"][product] += 1

    return {"totals": dict(totals), "missing": _top_missing(missing)}


def _payment_account_refs() -> dict:
    accounts = _account_map()
    missing = defaultdict(Counter)
    totals = Counter()
    for table in PAYMENT_TABLES:
        for row in _rows("select payload_json from sap_raw_rows where source_table=?", (table,)):
            doc = _load_payload(row)
            doc_date = doc.get("DocDate") or doc.get("TaxDate") or doc.get("RefDate")
            if doc_date and str(doc_date)[:10] > CUTOFF_TO_DATE:
                continue
            totals[table] += 1
            for field in ("CashAcct", "TrsfrAcct", "CheckAcct", "BpAct"):
                code = doc.get(field)
                if code and str(code) not in accounts:
                    missing[f"{table}.{field}"][str(code)] += 1
    return {"totals": dict(totals), "missing": _top_missing(missing)}


def _top_missing(missing: dict[str, Counter], limit: int = 30) -> dict[str, list[dict]]:
    return {
        key: [{"value": value, "count": count} for value, count in counter.most_common(limit)]
        for key, counter in sorted(missing.items())
        if counter
    }


def main() -> None:
    connect_frappe(frappe)
    try:
        report = {
            "headers": _missing_header_refs(),
            "lines": _missing_line_refs(),
            "payments": _payment_account_refs(),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()

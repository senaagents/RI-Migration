from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.utils import flt, getdate


from llm_migration_config import connect_frappe, COMPANY, CUTOFF_TO_DATE, NATIVE_FROM_DATE, SAP_DB, SITE, SITES_PATH
TAX_ONLY_ITEM = "SAP-TAX-ONLY"
SERVICE_LINE_ITEM = "SAP-SERVICE-LINE"

UOM_MAP = {
    None: "Nos",
    "": "Nos",
    "NOS": "Nos",
    "NO": "Nos",
    "KGS": "KGS",
    "KG": "Kg",
    "Kgs": "KGS",
    "LTR": "LTR",
    "Ltr": "LTR",
    "MTR": "MTR",
    "Mtr": "MTR",
    "SET": "Set",
    "Set": "Set",
    "Manual": "Nos",
}


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
            "Sales Invoice",
            {
                "fieldname": "custom_sap_docentry",
                "label": "SAP DocEntry",
                "fieldtype": "Data",
                "unique": 1,
                "read_only": 1,
                "insert_after": "naming_series",
            },
        ),
        (
            "Sales Invoice",
            {
                "fieldname": "custom_sap_docnum",
                "label": "SAP DocNum",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "custom_sap_docentry",
            },
        ),
        (
            "Sales Invoice",
            {
                "fieldname": "custom_sap_trans_id",
                "label": "SAP TransId",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "custom_sap_docnum",
            },
        ),
        (
            "Sales Invoice Item",
            {
                "fieldname": "custom_sap_line_num",
                "label": "SAP LineNum",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "item_code",
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


def _ensure_account_types(dry_run: bool) -> dict[str, int]:
    updates = {"receivable": 0, "tax": 0}
    receivable = frappe.db.get_value("Account", {"company": COMPANY, "custom_sap_acct_code": "1202101"}, "name")
    if receivable and frappe.db.get_value("Account", receivable, "account_type") != "Receivable":
        print(f"mark Account {receivable} as Receivable")
        updates["receivable"] += 1
        if not dry_run:
            frappe.db.set_value("Account", receivable, "account_type", "Receivable")

    tax_codes = sorted(
        {
            str(row["acct"])
            for row in _rows(
                """
                select distinct acct
                from (
                    select json_extract(payload_json, '$.TaxAcct') as acct
                    from sap_raw_rows
                    where source_table='INV4'
                      and json_extract(payload_json, '$.DocEntry') in (
                        select cast(source_key as integer)
                        from sap_documents
                        where source_table='OINV'
                          and json_extract(payload_json, '$.CANCELED')='N'
                          and date(posting_date) between date(?) and date(?)
                      )
                    union
                    select json_extract(payload_json, '$.TdsAcc') as acct
                    from sap_raw_rows
                    where source_table='INV5'
                      and json_extract(payload_json, '$.AbsEntry') in (
                        select cast(source_key as integer)
                        from sap_documents
                        where source_table='OINV'
                          and json_extract(payload_json, '$.CANCELED')='N'
                          and date(posting_date) between date(?) and date(?)
                      )
                )
                where acct is not null
                """,
                (NATIVE_FROM_DATE, CUTOFF_TO_DATE, NATIVE_FROM_DATE, CUTOFF_TO_DATE),
            )
        }
    )
    for sap_code in tax_codes:
        account = frappe.db.get_value("Account", {"company": COMPANY, "custom_sap_acct_code": sap_code}, "name")
        if account and frappe.db.get_value("Account", account, "account_type") != "Tax":
            print(f"mark Account {account} as Tax")
            updates["tax"] += 1
            if not dry_run:
                frappe.db.set_value("Account", account, "account_type", "Tax")
    return updates


def _expense_account_map() -> dict[str, str]:
    rows = _rows(
        """
        select payload_json
        from sap_raw_rows
        where source_table='OEXD'
        """
    )
    return {
        str(row["ExpnsCode"]): str(row.get("RevAcct") or row.get("ExpnsAcct") or "")
        for row in (_payload(row) for row in rows)
    }

def _ensure_support_items(dry_run: bool) -> int:
    items = [
        (TAX_ONLY_ITEM, "SAP Tax Only Invoice Line", "Placeholder item for SAP B1 tax-only AR invoices."),
        (SERVICE_LINE_ITEM, "SAP Service Invoice Line", "Placeholder item for SAP B1 AR service lines without item codes."),
    ]
    created = 0
    for item_code, item_name, description in items:
        if frappe.db.exists("Item", item_code):
            continue
        print(f"create Item {item_code}")
        created += 1
        if not dry_run:
            frappe.get_doc(
                {
                    "doctype": "Item",
                    "item_code": item_code,
                    "item_name": item_name,
                    "description": description,
                    "item_group": "All Item Groups",
                    "stock_uom": "Nos",
                    "is_stock_item": 0,
                    "is_sales_item": 1,
                    "is_purchase_item": 0,
                }
            ).insert(ignore_permissions=True)
    return created


def _ensure_invoice_customers_enabled(dry_run: bool) -> int:
    card_codes = {
        str(row["card_code"])
        for row in _rows(
            """
            select distinct json_extract(payload_json, '$.CardCode') as card_code
            from sap_documents
            where source_table='OINV'
              and json_extract(payload_json, '$.CANCELED')='N'
              and date(posting_date) between date(?) and date(?)
              and json_extract(payload_json, '$.CardCode') is not null
            """,
            (NATIVE_FROM_DATE, CUTOFF_TO_DATE),
        )
    }
    updated = 0
    for row in frappe.get_all(
        "Customer",
        fields=["name", "custom_sap_card_code", "disabled"],
        filters={"custom_sap_card_code": ["in", sorted(card_codes)]},
        limit_page_length=0,
    ):
        if not row.disabled:
            continue
        print(f"enable Customer {row.name} for historic SAP AR invoice import")
        updated += 1
        if not dry_run:
            frappe.db.set_value("Customer", row.name, "disabled", 0)
    return updated


def _ensure_invoice_items_enabled(dry_run: bool) -> int:
    item_codes = {
        str(row["item_code"])
        for row in _rows(
            """
            select distinct json_extract(l.payload_json, '$.ItemCode') as item_code
            from sap_document_lines l
            join sap_documents h
              on h.source_table='OINV'
             and h.source_key=l.source_key
             and json_extract(h.payload_json, '$.CANCELED')='N'
             and date(h.posting_date) between date(?) and date(?)
            where l.parent_table='OINV'
              and l.source_table='INV1'
              and coalesce(json_extract(l.payload_json, '$.TaxOnly'), 'N')!='Y'
              and coalesce(json_extract(l.payload_json, '$.ItemCode'), '')!=''
            """,
            (NATIVE_FROM_DATE, CUTOFF_TO_DATE),
        )
    }
    updated = 0
    for row in frappe.get_all(
        "Item",
        fields=["name", "disabled"],
        filters={"name": ["in", sorted(item_codes)]},
        limit_page_length=0,
    ):
        if not row.disabled:
            continue
        print(f"enable Item {row.name} for historic SAP AR invoice import")
        updated += 1
        if not dry_run:
            frappe.db.set_value("Item", row.name, "disabled", 0)
    return updated


def _account_map() -> dict[str, str]:
    rows = frappe.get_all(
        "Account",
        fields=["name", "custom_sap_acct_code"],
        filters={"company": COMPANY, "custom_sap_acct_code": ["is", "set"]},
        limit_page_length=0,
    )
    return {str(row.custom_sap_acct_code): row.name for row in rows}


def _customer_map() -> dict[str, str]:
    return {
        str(row.custom_sap_card_code): row.name
        for row in frappe.get_all(
            "Customer",
            fields=["name", "custom_sap_card_code"],
            filters={"custom_sap_card_code": ["is", "set"]},
            limit_page_length=0,
        )
    }


def _warehouse_map() -> dict[str, str]:
    return {
        str(row.custom_sap_whs_code): row.name
        for row in frappe.get_all(
            "Warehouse",
            fields=["name", "custom_sap_whs_code"],
            filters={"company": COMPANY, "custom_sap_whs_code": ["is", "set"]},
            limit_page_length=0,
        )
    }


def _cost_center_map() -> dict[str, str]:
    return {
        str(row.custom_sap_cc_code): row.name
        for row in frappe.get_all(
            "Cost Center",
            fields=["name", "custom_sap_cc_code"],
            filters={"company": COMPANY, "custom_sap_cc_code": ["is", "set"]},
            limit_page_length=0,
        )
    }


def _uom(value: str | None) -> str:
    candidate = UOM_MAP.get(value, value or "Nos")
    if frappe.db.exists("UOM", candidate):
        return candidate
    upper = (candidate or "").upper()
    for uom in frappe.get_all("UOM", fields=["name"], limit_page_length=0):
        if uom.name.upper() == upper:
            return uom.name
    return "Nos"


def _invoice_headers(limit: int | None, offset: int, from_date: str, to_date: str) -> list[sqlite3.Row]:
    sql = """
        select source_key, payload_json
        from sap_documents
        where source_table='OINV'
          and json_extract(payload_json, '$.CANCELED')='N'
          and date(posting_date) between date(?) and date(?)
        order by posting_date, cast(source_key as integer)
    """
    if limit:
        sql += f" limit {int(limit)}"
    if offset:
        sql += f" offset {int(offset)}"
    return _rows(sql, (from_date, to_date))


def _invoice_lines(docentry: str) -> list[dict]:
    return [
        _payload(row)
        for row in _rows(
            """
            select payload_json
            from sap_document_lines
            where parent_table='OINV' and source_table='INV1' and source_key=?
            order by line_num
            """,
            (str(docentry),),
        )
    ]


def _invoice_taxes(docentry: str) -> list[dict]:
    return [
        _payload(row)
        for row in _rows(
            """
            select payload_json
            from sap_raw_rows
            where source_table='INV4'
              and json_extract(payload_json, '$.DocEntry')=?
            order by
              cast(json_extract(payload_json, '$.LineNum') as integer),
              cast(json_extract(payload_json, '$.LineSeq') as integer)
            """,
            (int(docentry),),
        )
    ]


def _invoice_expenses(docentry: str) -> list[dict]:
    return [
        _payload(row)
        for row in _rows(
            """
            select payload_json
            from sap_raw_rows
            where source_table='INV3'
              and json_extract(payload_json, '$.DocEntry')=?
            order by cast(json_extract(payload_json, '$.LineNum') as integer)
            """,
            (int(docentry),),
        )
    ]


def _invoice_withholding(docentry: str) -> list[dict]:
    return [
        _payload(row)
        for row in _rows(
            """
            select payload_json
            from sap_raw_rows
            where source_table='INV5'
              and json_extract(payload_json, '$.AbsEntry')=?
            order by cast(json_extract(payload_json, '$.LineNum') as integer)
            """,
            (int(docentry),),
        )
    ]


def _require(mapping: dict[str, str], key: object, label: str, docentry: str) -> str:
    normalized = str(key or "")
    if normalized in mapping:
        return mapping[normalized]
    raise RuntimeError(f"SAP invoice {docentry} missing {label} mapping for {normalized!r}")


def _build_doc(
    header: dict,
    lines: list[dict],
    taxes: list[dict],
    expenses: list[dict],
    withholding: list[dict],
    maps: dict,
) -> frappe.model.document.Document:
    docentry = str(header["DocEntry"])
    customer = _require(maps["customers"], header.get("CardCode"), "customer", docentry)
    debit_to = _require(maps["accounts"], header.get("CtlAccount"), "debit account", docentry)

    doc = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "company": COMPANY,
            "customer": customer,
            "posting_date": getdate(header["DocDate"]),
            "set_posting_time": 1,
            "due_date": getdate(header.get("DocDueDate") or header["DocDate"]),
            "currency": header.get("DocCur") or "INR",
            "conversion_rate": flt(header.get("DocRate") or 1),
            "selling_price_list": "Standard Selling",
            "price_list_currency": header.get("DocCur") or "INR",
            "plc_conversion_rate": flt(header.get("DocRate") or 1),
            "ignore_pricing_rule": 1,
            "disable_rounded_total": 1,
            "update_stock": 0,
            "debit_to": debit_to,
            "remarks": header.get("Comments") or f"SAP AR invoice {header['DocNum']}",
            "po_no": str(header.get("NumAtCard") or "")[:140],
            "custom_sap_docentry": docentry,
            "custom_sap_docnum": str(header.get("DocNum") or ""),
            "custom_sap_trans_id": str(header.get("TransId") or ""),
        }
    )

    regular_lines = [line for line in lines if line.get("TaxOnly") != "Y"]
    tax_only_lines = [line for line in lines if line.get("TaxOnly") == "Y"]

    for line in regular_lines:
        sap_item_code = str(line.get("ItemCode") or "")
        qty = flt(line.get("Quantity") or 0)
        amount = flt(line.get("LineTotal") or 0)
        is_fractional_qty_line = qty != round(qty)
        is_service_amount_line = ((not sap_item_code or qty == 0) and amount != 0) or (
            is_fractional_qty_line and amount != 0
        )
        item_code = SERVICE_LINE_ITEM if is_service_amount_line else sap_item_code
        qty = 1 if is_service_amount_line else qty
        rate = amount if is_service_amount_line else flt(
            line.get("Price") if line.get("Price") is not None else (amount / qty if qty else amount)
        )
        uom = _uom(line.get("unitMsr") or line.get("UomCode"))
        row = {
            "item_code": item_code,
            "item_name": str(line.get("Dscription") or item_code)[:140],
            "description": line.get("Dscription") or item_code,
            "qty": qty,
            "uom": uom,
            "stock_uom": uom,
            "conversion_factor": 1,
            "rate": rate,
            "amount": amount,
            "income_account": _require(maps["accounts"], line.get("AcctCode"), "income account", docentry),
            "custom_sap_line_num": str(line.get("LineNum")),
        }
        if line.get("WhsCode"):
            row["warehouse"] = _require(maps["warehouses"], line.get("WhsCode"), "warehouse", docentry)
        if line.get("OcrCode"):
            row["cost_center"] = _require(maps["cost_centers"], line.get("OcrCode"), "cost center", docentry)
        if line.get("OcrCode2"):
            row["sap_department"] = line["OcrCode2"]
        if line.get("OcrCode3"):
            row["sap_product_segment"] = line["OcrCode3"]
        doc.append("items", row)

    if not regular_lines and tax_only_lines:
        first_line = tax_only_lines[0]
        row = {
            "item_code": TAX_ONLY_ITEM,
            "item_name": "SAP Tax Only Invoice Line",
            "description": "; ".join(
                str(line.get("Dscription") or "SAP tax-only line") for line in tax_only_lines[:10]
            )[:1000],
            "qty": 1,
            "uom": "Nos",
            "conversion_factor": 1,
            "rate": 0,
            "amount": 0,
            "income_account": _require(maps["accounts"], first_line.get("AcctCode"), "income account", docentry),
            "custom_sap_line_num": "TAXONLY",
        }
        if first_line.get("OcrCode"):
            row["cost_center"] = _require(maps["cost_centers"], first_line.get("OcrCode"), "cost center", docentry)
        if first_line.get("OcrCode2"):
            row["sap_department"] = first_line["OcrCode2"]
        if first_line.get("OcrCode3"):
            row["sap_product_segment"] = first_line["OcrCode3"]
        doc.append("items", row)

    for expense in expenses:
        amount = flt(expense.get("LineTotal") or 0)
        if amount == 0:
            continue
        sap_expense_account = maps["expense_accounts"].get(str(expense.get("ExpnsCode") or ""))
        row = {
            "item_code": SERVICE_LINE_ITEM,
            "item_name": str(expense.get("Comments") or "SAP Freight/Expense")[:140],
            "description": expense.get("Comments") or f"SAP expense code {expense.get('ExpnsCode')}",
            "qty": 1,
            "uom": "Nos",
            "stock_uom": "Nos",
            "conversion_factor": 1,
            "rate": amount,
            "amount": amount,
            "income_account": _require(maps["accounts"], sap_expense_account, "expense income account", docentry),
            "custom_sap_line_num": f"EXP-{expense.get('LineNum')}",
        }
        if expense.get("OcrCode"):
            row["cost_center"] = _require(maps["cost_centers"], expense.get("OcrCode"), "cost center", docentry)
        if expense.get("OcrCode2"):
            row["sap_department"] = expense["OcrCode2"]
        if expense.get("OcrCode3"):
            row["sap_product_segment"] = expense["OcrCode3"]
        doc.append("items", row)

    grouped_taxes = defaultdict(lambda: {"tax_amount": 0.0, "base_sum": 0.0, "rate": 0.0, "sta_code": "", "account": ""})
    for tax in taxes:
        amount = flt(tax.get("TaxSum") or 0)
        if amount == 0:
            continue
        sap_tax_account = str(tax.get("TaxAcct") or "")
        key = (sap_tax_account, str(tax.get("StaCode") or ""), flt(tax.get("TaxRate") or 0))
        grouped_taxes[key]["tax_amount"] += amount
        grouped_taxes[key]["base_sum"] += flt(tax.get("BaseSum") or 0)
        grouped_taxes[key]["rate"] = flt(tax.get("TaxRate") or 0)
        grouped_taxes[key]["sta_code"] = str(tax.get("StaCode") or "")
        grouped_taxes[key]["account"] = _require(maps["accounts"], sap_tax_account, "tax account", docentry)

    for tax in withholding:
        amount = flt(tax.get("WTAmnt") or 0)
        if amount == 0:
            continue
        sap_tax_account = str(tax.get("TdsAcc") or tax.get("TcsIntAcct") or "")
        key = (sap_tax_account, str(tax.get("WTCode") or "SAP-WT"), flt(tax.get("Rate") or 0))
        grouped_taxes[key]["tax_amount"] += amount
        grouped_taxes[key]["base_sum"] += flt(tax.get("TaxbleAmnt") or 0)
        grouped_taxes[key]["rate"] = flt(tax.get("Rate") or 0)
        grouped_taxes[key]["sta_code"] = str(tax.get("WTCode") or "SAP-WT")
        grouped_taxes[key]["account"] = _require(maps["accounts"], sap_tax_account, "withholding tax account", docentry)

    tax_rows = []
    for (_sap_tax_account, _sta_code, _rate), tax in sorted(grouped_taxes.items()):
        tax_rows.append(
            {
                "charge_type": "Actual",
                "account_head": tax["account"],
                "description": tax["sta_code"],
                "tax_amount": round(tax["tax_amount"], 2),
                "base_tax_amount": round(tax["tax_amount"], 2),
                "rate": 0,
                "dont_recompute_tax": 1,
            }
        )

    sap_vat_total = round(
        flt(header.get("VatSum") or 0) - flt(header.get("WTSum") or 0) + flt(header.get("RoundDif") or 0),
        2,
    )
    current_tax_total = round(sum(row["tax_amount"] for row in tax_rows), 2)
    tax_residual = round(sap_vat_total - current_tax_total, 2)
    if tax_rows and tax_residual:
        tax_rows[-1]["tax_amount"] = round(tax_rows[-1]["tax_amount"] + tax_residual, 2)
        tax_rows[-1]["base_tax_amount"] = tax_rows[-1]["tax_amount"]

    for tax_row in tax_rows:
        doc.append(
            "taxes",
            tax_row,
        )

    return doc


def _expected_summary(docentries: list[str]) -> dict:
    if not docentries:
        return {"count": 0, "doc_total": 0.0, "line_total": 0.0, "tax_total": 0.0}
    placeholders = ",".join("?" for _ in docentries)
    header = _rows(
        f"""
        select
          count(*) count,
          sum(json_extract(payload_json, '$.DocTotal')) doc_total,
          sum(
            json_extract(payload_json, '$.VatSum')
            - coalesce(json_extract(payload_json, '$.WTSum'), 0)
            + coalesce(json_extract(payload_json, '$.RoundDif'), 0)
          ) vat_total
        from sap_documents
        where source_table='OINV' and source_key in ({placeholders})
        """,
        tuple(docentries),
    )[0]
    lines = _rows(
        f"""
        select sum(json_extract(payload_json, '$.LineTotal')) line_total
        from sap_document_lines
        where parent_table='OINV' and source_table='INV1' and source_key in ({placeholders})
          and coalesce(json_extract(payload_json, '$.TaxOnly'), 'N')!='Y'
        """,
        tuple(docentries),
    )[0]
    expenses = _rows(
        f"""
        select sum(json_extract(payload_json, '$.LineTotal')) expense_total
        from sap_raw_rows
        where source_table='INV3'
          and cast(json_extract(payload_json, '$.DocEntry') as text) in ({placeholders})
        """,
        tuple(docentries),
    )[0]
    return {
        "count": int(header["count"] or 0),
        "doc_total": round(flt(header["doc_total"] or 0), 2),
        "line_total": round(flt(lines["line_total"] or 0) + flt(expenses["expense_total"] or 0), 2),
        "tax_total": round(flt(header["vat_total"] or 0), 2),
    }


def _erp_summary(docentries: list[str]) -> dict:
    if not docentries:
        return {"count": 0, "grand_total": 0.0, "net_total": 0.0, "tax_total": 0.0}
    rows = frappe.get_all(
        "Sales Invoice",
        fields=["name", "grand_total", "net_total", "total_taxes_and_charges"],
        filters={"custom_sap_docentry": ["in", docentries], "docstatus": 1},
        limit_page_length=0,
    )
    return {
        "count": len(rows),
        "grand_total": round(sum(flt(row.grand_total) for row in rows), 2),
        "net_total": round(sum(flt(row.net_total) for row in rows), 2),
        "tax_total": round(sum(flt(row.total_taxes_and_charges) for row in rows), 2),
    }


def main(
    dry_run: bool = True,
    limit: int | None = None,
    offset: int = 0,
    commit_every: int = 100,
    from_date: str = NATIVE_FROM_DATE,
    to_date: str = CUTOFF_TO_DATE,
) -> None:
    connect_frappe(frappe)
    try:
        created_fields = _ensure_custom_fields(dry_run)
        account_type_updates = _ensure_account_types(dry_run)
        support_items = _ensure_support_items(dry_run)
        enabled_customers = _ensure_invoice_customers_enabled(dry_run)
        enabled_items = _ensure_invoice_items_enabled(dry_run)
        if not dry_run and (
            created_fields
            or any(account_type_updates.values())
            or support_items
            or enabled_customers
            or enabled_items
        ):
            frappe.db.commit()

        maps = {
            "accounts": _account_map(),
            "customers": _customer_map(),
            "warehouses": _warehouse_map(),
            "cost_centers": _cost_center_map(),
            "expense_accounts": _expense_account_map(),
        }
        headers = _invoice_headers(limit, offset, from_date, to_date)
        created_docentries = []
        already_exists = 0
        would_create = 0

        for row in headers:
            header = _payload(row)
            docentry = str(header["DocEntry"])
            if frappe.db.exists("Sales Invoice", {"custom_sap_docentry": docentry}):
                already_exists += 1
                continue
            lines = _invoice_lines(docentry)
            taxes = _invoice_taxes(docentry)
            expenses = _invoice_expenses(docentry)
            withholding = _invoice_withholding(docentry)
            doc = _build_doc(header, lines, taxes, expenses, withholding, maps)
            would_create += 1
            if dry_run:
                created_docentries.append(docentry)
                continue
            try:
                doc.insert(ignore_permissions=True)
                doc.submit()
            except Exception as exc:
                raise RuntimeError(f"Failed importing SAP OINV DocEntry {docentry}") from exc
            created_docentries.append(docentry)
            if would_create % commit_every == 0:
                frappe.db.commit()
                print(f"committed {would_create} sales invoices")

        result = {
            "custom_fields": created_fields,
            "account_type_updates": account_type_updates,
            "support_items": support_items,
            "enabled_customers": enabled_customers,
            "enabled_items": enabled_items,
            "headers_checked": len(headers),
            "from_date": from_date,
            "to_date": to_date,
            "already_exists": already_exists,
            "created" if not dry_run else "would_create": would_create,
            "sap_expected": _expected_summary(created_docentries),
        }
        if not dry_run:
            frappe.db.commit()
            result["erp_imported"] = _erp_summary(created_docentries)
        print(json.dumps(result, indent=2, sort_keys=True))
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import staged SAP OINV/INV1/INV4 rows into ERPNext Sales Invoice.")
    parser.add_argument("--dry-run", action="store_true", help="Build and count invoices without writing to ERPNext.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--commit-every", type=int, default=100)
    parser.add_argument("--from-date", default=NATIVE_FROM_DATE)
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

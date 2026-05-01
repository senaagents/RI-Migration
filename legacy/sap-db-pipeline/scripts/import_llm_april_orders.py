from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from typing import Any

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.utils import flt, getdate

from llm_migration_config import COMPANY, SAP_DB, SITE, connect_frappe


APRIL_FROM_DATE = "2026-04-01"
APRIL_TO_DATE = "2026-04-28"
SALES_SERVICE_ITEM = "SAP-SERVICE-LINE"
PURCHASE_SERVICE_ITEM = "SAP-PURCHASE-SERVICE-LINE"

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


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(row["payload_json"])


def _date(value: Any) -> Any:
    return getdate(str(value or "")[:10])


def _uom(value: str | None) -> str:
    candidate = UOM_MAP.get(value, value or "Nos")
    if frappe.db.exists("UOM", candidate):
        return candidate
    upper = (candidate or "").upper()
    for uom in frappe.get_all("UOM", fields=["name"], limit_page_length=0):
        if uom.name.upper() == upper:
            return uom.name
    return "Nos"


def _ensure_custom_fields(dry_run: bool) -> int:
    fields: list[tuple[str, dict[str, Any]]] = []
    for doctype in ("Sales Order", "Purchase Order"):
        fields.extend(
            [
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_source_table",
                        "label": "SAP Source Table",
                        "fieldtype": "Data",
                        "read_only": 1,
                        "insert_after": "naming_series",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_docentry",
                        "label": "SAP DocEntry",
                        "fieldtype": "Data",
                        "unique": 1,
                        "read_only": 1,
                        "insert_after": "custom_sap_source_table",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_docnum",
                        "label": "SAP DocNum",
                        "fieldtype": "Data",
                        "read_only": 1,
                        "insert_after": "custom_sap_docentry",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_import_window",
                        "label": "SAP Import Window",
                        "fieldtype": "Data",
                        "read_only": 1,
                        "insert_after": "custom_sap_docnum",
                    },
                ),
            ]
        )
    for doctype in ("Sales Order Item", "Purchase Order Item"):
        fields.extend(
            [
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_line_num",
                        "label": "SAP LineNum",
                        "fieldtype": "Data",
                        "read_only": 1,
                        "insert_after": "item_code",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_base_type",
                        "label": "SAP BaseType",
                        "fieldtype": "Data",
                        "read_only": 1,
                        "insert_after": "custom_sap_line_num",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_base_entry",
                        "label": "SAP BaseEntry",
                        "fieldtype": "Data",
                        "read_only": 1,
                        "insert_after": "custom_sap_base_type",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_base_line",
                        "label": "SAP BaseLine",
                        "fieldtype": "Data",
                        "read_only": 1,
                        "insert_after": "custom_sap_base_entry",
                    },
                ),
            ]
        )

    created = 0
    for doctype, field in fields:
        name = f"{doctype}-{field['fieldname']}"
        if frappe.db.exists("Custom Field", name):
            continue
        print(f"create Custom Field {name}")
        created += 1
        if not dry_run:
            create_custom_field(doctype, field, ignore_validate=True)
            frappe.clear_cache(doctype=doctype)
    return created


def _ensure_support_items(dry_run: bool) -> int:
    specs = [
        (
            SALES_SERVICE_ITEM,
            "SAP Service Invoice Line",
            "Placeholder item for SAP B1 sales/service lines without item codes.",
            1,
            0,
        ),
        (
            PURCHASE_SERVICE_ITEM,
            "SAP Purchase Service Line",
            "Placeholder item for SAP B1 purchase/service lines without item codes.",
            0,
            1,
        ),
    ]
    changes = 0
    for item_code, item_name, description, is_sales_item, is_purchase_item in specs:
        if not frappe.db.exists("Item", item_code):
            print(f"create Item {item_code}")
            changes += 1
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
                        "is_sales_item": is_sales_item,
                        "is_purchase_item": is_purchase_item,
                    }
                ).insert(ignore_permissions=True)
            continue
        updates = {}
        if is_sales_item and not frappe.db.get_value("Item", item_code, "is_sales_item"):
            updates["is_sales_item"] = 1
        if is_purchase_item and not frappe.db.get_value("Item", item_code, "is_purchase_item"):
            updates["is_purchase_item"] = 1
        if frappe.db.get_value("Item", item_code, "disabled"):
            updates["disabled"] = 0
        if updates:
            print(f"update Item {item_code}: {sorted(updates)}")
            changes += 1
            if not dry_run:
                frappe.db.set_value("Item", item_code, updates, update_modified=False)
    return changes


def _candidate_headers(source_table: str, limit: int | None) -> list[sqlite3.Row]:
    if source_table == "ORDR":
        ref_parent_tables = ("ODLN", "OINV")
        ref_base_type = 17
    elif source_table == "OPOR":
        ref_parent_tables = ("OPDN", "OPCH")
        ref_base_type = 22
    else:
        raise ValueError(source_table)

    parent_placeholders = ",".join("?" for _ in ref_parent_tables)
    sql = f"""
        with refs as (
            select distinct cast(json_extract(l.payload_json, '$.BaseEntry') as text) as source_key
            from sap_document_lines l
            join sap_documents h
              on h.source_table=l.parent_table
             and h.source_key=l.source_key
            where l.parent_table in ({parent_placeholders})
              and date(h.posting_date) between date(?) and date(?)
              and json_extract(l.payload_json, '$.BaseType')=?
        )
        select d.source_key,
               d.payload_json,
               case
                 when date(d.posting_date) between date(?) and date(?) then 'april'
                 else 'pre_april_reference'
               end as import_reason
        from sap_documents d
        where d.source_table=?
          and json_extract(d.payload_json, '$.CANCELED')='N'
          and (
            date(d.posting_date) between date(?) and date(?)
            or d.source_key in (select source_key from refs)
          )
        order by date(d.posting_date), cast(d.source_key as integer)
    """
    if limit:
        sql += f" limit {int(limit)}"
    params = (
        *ref_parent_tables,
        APRIL_FROM_DATE,
        APRIL_TO_DATE,
        ref_base_type,
        APRIL_FROM_DATE,
        APRIL_TO_DATE,
        source_table,
        APRIL_FROM_DATE,
        APRIL_TO_DATE,
    )
    return _rows(sql, params)


def _document_lines(parent_table: str, docentry: str) -> list[dict[str, Any]]:
    source_table = "RDR1" if parent_table == "ORDR" else "POR1"
    return [
        _payload(row)
        for row in _rows(
            """
            select payload_json
            from sap_document_lines
            where parent_table=? and source_table=? and source_key=?
            order by line_num
            """,
            (parent_table, source_table, str(docentry)),
        )
    ]


def _document_taxes(parent_table: str, docentry: str) -> list[dict[str, Any]]:
    source_table = "RDR4" if parent_table == "ORDR" else "POR4"
    return [
        _payload(row)
        for row in _rows(
            """
            select payload_json
            from sap_raw_rows
            where source_table=?
              and json_extract(payload_json, '$.DocEntry')=?
            order by
              cast(json_extract(payload_json, '$.LineNum') as integer),
              cast(json_extract(payload_json, '$.LineSeq') as integer)
            """,
            (source_table, int(docentry)),
        )
    ]


def _document_expenses(parent_table: str, docentry: str) -> list[dict[str, Any]]:
    source_table = "RDR3" if parent_table == "ORDR" else "POR3"
    return [
        _payload(row)
        for row in _rows(
            """
            select payload_json
            from sap_raw_rows
            where source_table=?
              and json_extract(payload_json, '$.DocEntry')=?
            order by cast(json_extract(payload_json, '$.LineNum') as integer)
            """,
            (source_table, int(docentry)),
        )
    ]


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


def _supplier_map() -> dict[str, str]:
    return {
        str(row.custom_sap_card_code): row.name
        for row in frappe.get_all(
            "Supplier",
            fields=["name", "custom_sap_card_code"],
            filters={"custom_sap_card_code": ["is", "set"]},
            limit_page_length=0,
        )
    }


def _account_map() -> dict[str, str]:
    return {
        str(row.custom_sap_acct_code): row.name
        for row in frappe.get_all(
            "Account",
            fields=["name", "custom_sap_acct_code"],
            filters={"company": COMPANY, "custom_sap_acct_code": ["is", "set"]},
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


def _expense_account_map() -> dict[str, str]:
    return {
        str(row["ExpnsCode"]): str(row.get("RevAcct") or row.get("ExpnsAcct") or "")
        for row in (_payload(row) for row in _rows("select payload_json from sap_raw_rows where source_table='OEXD'"))
    }


def _tax_account_map(kind: str) -> dict[tuple[str, str], str]:
    child_doctype = "Sales Taxes and Charges" if kind == "sales" else "Purchase Taxes and Charges"
    prefix = "SAP AR " if kind == "sales" else "SAP AP "
    result: dict[tuple[str, str], str] = {}
    for row in frappe.get_all(
        child_doctype,
        fields=["parent", "description", "account_head"],
        filters={"parent": ["like", f"{prefix}% - L"]},
        limit_page_length=0,
    ):
        stc_code = row.parent.removeprefix(prefix).removesuffix(" - L")
        if row.description and row.account_head:
            result[(stc_code, row.description)] = row.account_head
            result[("", row.description)] = row.account_head
    return result


def _require(mapping: dict[str, str], key: Any, label: str, docentry: str) -> str:
    normalized = str(key or "")
    if normalized in mapping:
        return mapping[normalized]
    raise RuntimeError(f"SAP order {docentry} missing {label} mapping for {normalized!r}")


def _prepare_setup(headers_by_table: dict[str, list[sqlite3.Row]], dry_run: bool) -> dict[str, int]:
    customer_codes = {str(_payload(row).get("CardCode")) for row in headers_by_table.get("ORDR", [])}
    supplier_codes = {str(_payload(row).get("CardCode")) for row in headers_by_table.get("OPOR", [])}
    sales_items: set[str] = set()
    purchase_items: set[str] = set()

    for table, rows in headers_by_table.items():
        for header_row in rows:
            docentry = str(_payload(header_row)["DocEntry"])
            for line in _document_lines(table, docentry):
                item_code = str(line.get("ItemCode") or "")
                if not item_code:
                    continue
                if table == "ORDR":
                    sales_items.add(item_code)
                else:
                    purchase_items.add(item_code)

    updates = defaultdict(int)
    for doctype, codes in (("Customer", customer_codes), ("Supplier", supplier_codes)):
        code_field = "custom_sap_card_code"
        for row in frappe.get_all(
            doctype,
            fields=["name", "disabled"],
            filters={code_field: ["in", sorted(codes)]},
            limit_page_length=0,
        ):
            if row.disabled:
                print(f"enable {doctype} {row.name} for April order import")
                updates[f"enabled_{doctype.lower()}s"] += 1
                if not dry_run:
                    frappe.db.set_value(doctype, row.name, "disabled", 0, update_modified=False)

    for item_code in sorted(sales_items | purchase_items):
        if not frappe.db.exists("Item", item_code):
            continue
        values = {}
        if frappe.db.get_value("Item", item_code, "disabled"):
            values["disabled"] = 0
        if item_code in sales_items and not frappe.db.get_value("Item", item_code, "is_sales_item"):
            values["is_sales_item"] = 1
        if item_code in purchase_items and not frappe.db.get_value("Item", item_code, "is_purchase_item"):
            values["is_purchase_item"] = 1
        if values:
            print(f"update Item {item_code}: {sorted(values)}")
            updates["item_updates"] += 1
            if not dry_run:
                frappe.db.set_value("Item", item_code, values, update_modified=False)

    return dict(updates)


def _build_sales_order(
    header: dict[str, Any],
    lines: list[dict[str, Any]],
    taxes: list[dict[str, Any]],
    expenses: list[dict[str, Any]],
    maps: dict[str, Any],
    import_reason: str,
) -> frappe.model.document.Document:
    docentry = str(header["DocEntry"])
    customer = _require(maps["customers"], header.get("CardCode"), "customer", docentry)
    transaction_date = _date(header["DocDate"])
    delivery_date = _date(header.get("DocDueDate") or header["DocDate"])
    if delivery_date < transaction_date:
        delivery_date = transaction_date
    doc = frappe.get_doc(
        {
            "doctype": "Sales Order",
            "company": COMPANY,
            "customer": customer,
            "transaction_date": transaction_date,
            "delivery_date": delivery_date,
            "currency": header.get("DocCur") or "INR",
            "conversion_rate": flt(header.get("DocRate") or 1),
            "selling_price_list": "Standard Selling",
            "price_list_currency": header.get("DocCur") or "INR",
            "plc_conversion_rate": flt(header.get("DocRate") or 1),
            "ignore_pricing_rule": 1,
            "disable_rounded_total": 1,
            "po_no": str(header.get("NumAtCard") or "")[:140],
            "custom_sap_source_table": "ORDR",
            "custom_sap_docentry": docentry,
            "custom_sap_docnum": str(header.get("DocNum") or ""),
            "custom_sap_import_window": import_reason,
        }
    )
    for line in lines:
        item_code = str(line.get("ItemCode") or "")
        amount = flt(line.get("LineTotal") or 0)
        qty = flt(line.get("Quantity") or 0)
        if not item_code or qty == 0:
            item_code = SALES_SERVICE_ITEM
            qty = 1
            rate = amount
        else:
            rate = flt(amount / qty) if qty else flt(line.get("Price") or 0)
        row = {
            "item_code": item_code,
            "item_name": str(line.get("Dscription") or item_code)[:140],
            "description": line.get("Dscription") or item_code,
            "delivery_date": max(_date(line.get("ShipDate") or delivery_date), transaction_date),
            "qty": qty,
            "uom": _uom(line.get("unitMsr") or line.get("UomCode")),
            "stock_uom": _uom(line.get("unitMsr") or line.get("UomCode")),
            "conversion_factor": 1,
            "rate": rate,
            "amount": amount,
            "custom_sap_line_num": str(line.get("LineNum")),
            "custom_sap_base_type": str(line.get("BaseType") or ""),
            "custom_sap_base_entry": str(line.get("BaseEntry") or ""),
            "custom_sap_base_line": str(line.get("BaseLine") or ""),
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
    _append_expense_items(doc, "sales", expenses, maps, docentry)
    _append_tax_rows(doc, "sales", header, taxes, maps, docentry)
    return doc


def _build_purchase_order(
    header: dict[str, Any],
    lines: list[dict[str, Any]],
    taxes: list[dict[str, Any]],
    expenses: list[dict[str, Any]],
    maps: dict[str, Any],
    import_reason: str,
) -> frappe.model.document.Document:
    docentry = str(header["DocEntry"])
    supplier = _require(maps["suppliers"], header.get("CardCode"), "supplier", docentry)
    transaction_date = _date(header["DocDate"])
    schedule_date = _date(header.get("DocDueDate") or header["DocDate"])
    if schedule_date < transaction_date:
        schedule_date = transaction_date
    doc = frappe.get_doc(
        {
            "doctype": "Purchase Order",
            "company": COMPANY,
            "supplier": supplier,
            "transaction_date": transaction_date,
            "schedule_date": schedule_date,
            "currency": header.get("DocCur") or "INR",
            "conversion_rate": flt(header.get("DocRate") or 1),
            "buying_price_list": "Standard Buying",
            "price_list_currency": header.get("DocCur") or "INR",
            "plc_conversion_rate": flt(header.get("DocRate") or 1),
            "ignore_pricing_rule": 1,
            "disable_rounded_total": 1,
            "custom_sap_source_table": "OPOR",
            "custom_sap_docentry": docentry,
            "custom_sap_docnum": str(header.get("DocNum") or ""),
            "custom_sap_import_window": import_reason,
        }
    )
    for line in lines:
        item_code = str(line.get("ItemCode") or "")
        amount = flt(line.get("LineTotal") or 0)
        qty = flt(line.get("Quantity") or 0)
        if not item_code or qty == 0:
            item_code = PURCHASE_SERVICE_ITEM
            qty = 1
            rate = amount
        else:
            rate = flt(amount / qty) if qty else flt(line.get("Price") or 0)
        row = {
            "item_code": item_code,
            "item_name": str(line.get("Dscription") or item_code)[:140],
            "description": line.get("Dscription") or item_code,
            "schedule_date": max(_date(line.get("ShipDate") or schedule_date), transaction_date),
            "qty": qty,
            "uom": _uom(line.get("unitMsr") or line.get("UomCode")),
            "stock_uom": _uom(line.get("unitMsr") or line.get("UomCode")),
            "conversion_factor": 1,
            "rate": rate,
            "amount": amount,
            "custom_sap_line_num": str(line.get("LineNum")),
            "custom_sap_base_type": str(line.get("BaseType") or ""),
            "custom_sap_base_entry": str(line.get("BaseEntry") or ""),
            "custom_sap_base_line": str(line.get("BaseLine") or ""),
        }
        if line.get("WhsCode"):
            row["warehouse"] = _require(maps["warehouses"], line.get("WhsCode"), "warehouse", docentry)
        if line.get("AcctCode"):
            row["expense_account"] = _require(maps["accounts"], line.get("AcctCode"), "expense account", docentry)
        if line.get("OcrCode"):
            row["cost_center"] = _require(maps["cost_centers"], line.get("OcrCode"), "cost center", docentry)
        if line.get("OcrCode2"):
            row["sap_department"] = line["OcrCode2"]
        if line.get("OcrCode3"):
            row["sap_product_segment"] = line["OcrCode3"]
        doc.append("items", row)
    _append_expense_items(doc, "purchase", expenses, maps, docentry)
    _append_tax_rows(doc, "purchase", header, taxes, maps, docentry)
    return doc


def _append_expense_items(
    doc: frappe.model.document.Document,
    kind: str,
    expenses: list[dict[str, Any]],
    maps: dict[str, Any],
    docentry: str,
) -> None:
    for expense in expenses:
        amount = flt(expense.get("LineTotal") or 0)
        if amount == 0:
            continue
        sap_account = maps["expense_accounts"].get(str(expense.get("ExpnsCode") or ""))
        item_code = SALES_SERVICE_ITEM if kind == "sales" else PURCHASE_SERVICE_ITEM
        row = {
            "item_code": item_code,
            "item_name": str(expense.get("Comments") or "SAP Freight/Expense")[:140],
            "description": expense.get("Comments") or f"SAP expense code {expense.get('ExpnsCode')}",
            "qty": 1,
            "uom": "Nos",
            "stock_uom": "Nos",
            "conversion_factor": 1,
            "rate": amount,
            "amount": amount,
            "custom_sap_line_num": f"EXP-{expense.get('LineNum')}",
        }
        if kind == "purchase" and sap_account:
            row["expense_account"] = _require(maps["accounts"], sap_account, "expense account", docentry)
        if expense.get("OcrCode"):
            row["cost_center"] = _require(maps["cost_centers"], expense.get("OcrCode"), "cost center", docentry)
        if expense.get("OcrCode2"):
            row["sap_department"] = expense["OcrCode2"]
        if expense.get("OcrCode3"):
            row["sap_product_segment"] = expense["OcrCode3"]
        doc.append("items", row)


def _append_tax_rows(
    doc: frappe.model.document.Document,
    kind: str,
    header: dict[str, Any],
    taxes: list[dict[str, Any]],
    maps: dict[str, Any],
    docentry: str,
) -> None:
    grouped = defaultdict(lambda: {"tax_amount": 0.0, "base_sum": 0.0, "rate": 0.0, "sta_code": "", "account": ""})
    for tax in taxes:
        amount = flt(tax.get("TaxSum") or 0)
        if amount == 0:
            continue
        stc_code = str(tax.get("StcCode") or "")
        sta_code = str(tax.get("StaCode") or "")
        sap_tax_account = str(tax.get("TaxAcct") or "")
        account = maps["accounts"].get(sap_tax_account) if sap_tax_account else None
        if not account:
            account = maps["tax_accounts"][kind].get((stc_code, sta_code)) or maps["tax_accounts"][kind].get(("", sta_code))
        if not account:
            raise RuntimeError(f"SAP order {docentry} missing {kind} tax account for {stc_code}/{sta_code}")
        key = (account, sta_code, flt(tax.get("TaxRate") or 0))
        grouped[key]["tax_amount"] += amount
        grouped[key]["base_sum"] += flt(tax.get("BaseSum") or 0)
        grouped[key]["rate"] = flt(tax.get("TaxRate") or 0)
        grouped[key]["sta_code"] = sta_code
        grouped[key]["account"] = account

    tax_rows = []
    for (_account, _sta_code, _rate), tax in sorted(grouped.items()):
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

    expected_tax = round(flt(header.get("VatSum") or 0) + flt(header.get("RoundDif") or 0), 2)
    current_tax = round(sum(row["tax_amount"] for row in tax_rows), 2)
    residual = round(expected_tax - current_tax, 2)
    if tax_rows and residual:
        tax_rows[-1]["tax_amount"] = round(tax_rows[-1]["tax_amount"] + residual, 2)
        tax_rows[-1]["base_tax_amount"] = tax_rows[-1]["tax_amount"]

    for row in tax_rows:
        doc.append("taxes", row)


def _expected_summary(headers_by_table: dict[str, list[sqlite3.Row]]) -> dict[str, dict[str, float | int]]:
    result = {}
    for table, rows in headers_by_table.items():
        docs = [_payload(row) for row in rows]
        result[table] = {
            "count": len(docs),
            "april": sum(1 for row in rows if row["import_reason"] == "april"),
            "pre_april_reference": sum(1 for row in rows if row["import_reason"] == "pre_april_reference"),
            "doc_total": round(sum(flt(doc.get("DocTotal") or 0) for doc in docs), 2),
            "vat_total": round(sum(flt(doc.get("VatSum") or 0) + flt(doc.get("RoundDif") or 0) for doc in docs), 2),
        }
    return result


def _erp_summary(docentries_by_doctype: dict[str, list[str]]) -> dict[str, dict[str, float | int]]:
    result = {}
    for doctype, docentries in docentries_by_doctype.items():
        if not docentries:
            result[doctype] = {"count": 0, "grand_total": 0.0, "net_total": 0.0, "tax_total": 0.0}
            continue
        rows = frappe.get_all(
            doctype,
            fields=["name", "grand_total", "net_total", "total_taxes_and_charges"],
            filters={"custom_sap_docentry": ["in", docentries], "docstatus": 1},
            limit_page_length=0,
        )
        result[doctype] = {
            "count": len(rows),
            "grand_total": round(sum(flt(row.grand_total) for row in rows), 2),
            "net_total": round(sum(flt(row.net_total) for row in rows), 2),
            "tax_total": round(sum(flt(row.total_taxes_and_charges) for row in rows), 2),
        }
    return result


def main(dry_run: bool = True, limit: int | None = None, commit_every: int = 50) -> None:
    if SITE != "llmnew.localhost":
        raise RuntimeError(f"Refusing to import April orders into {SITE}; set SAP_TARGET_SITE=llmnew.localhost")
    connect_frappe(frappe)
    try:
        custom_fields = _ensure_custom_fields(dry_run)
        support_items = _ensure_support_items(dry_run)
        headers_by_table = {
            "ORDR": _candidate_headers("ORDR", limit),
            "OPOR": _candidate_headers("OPOR", limit),
        }
        setup_updates = _prepare_setup(headers_by_table, dry_run)
        if not dry_run and (custom_fields or support_items or setup_updates):
            frappe.db.commit()

        maps = {
            "customers": _customer_map(),
            "suppliers": _supplier_map(),
            "accounts": _account_map(),
            "warehouses": _warehouse_map(),
            "cost_centers": _cost_center_map(),
            "expense_accounts": _expense_account_map(),
            "tax_accounts": {
                "sales": _tax_account_map("sales"),
                "purchase": _tax_account_map("purchase"),
            },
        }

        created = {"Sales Order": [], "Purchase Order": []}
        already_exists = defaultdict(int)
        would_create = defaultdict(int)

        for table, rows in headers_by_table.items():
            doctype = "Sales Order" if table == "ORDR" else "Purchase Order"
            for row in rows:
                header = _payload(row)
                docentry = str(header["DocEntry"])
                if frappe.db.exists(doctype, {"custom_sap_docentry": docentry}):
                    already_exists[doctype] += 1
                    continue
                lines = _document_lines(table, docentry)
                taxes = _document_taxes(table, docentry)
                expenses = _document_expenses(table, docentry)
                if table == "ORDR":
                    doc = _build_sales_order(header, lines, taxes, expenses, maps, row["import_reason"])
                else:
                    doc = _build_purchase_order(header, lines, taxes, expenses, maps, row["import_reason"])
                would_create[doctype] += 1
                if dry_run:
                    created[doctype].append(docentry)
                    continue
                try:
                    doc.insert(ignore_permissions=True)
                    doc.submit()
                except Exception as exc:
                    raise RuntimeError(f"Failed importing SAP {table} DocEntry {docentry}") from exc
                created[doctype].append(docentry)
                if sum(would_create.values()) % commit_every == 0:
                    frappe.db.commit()
                    print(f"committed {sum(would_create.values())} April/pre-April reference orders")

        result = {
            "custom_fields": custom_fields,
            "support_items": support_items,
            "setup_updates": setup_updates,
            "headers_checked": {table: len(rows) for table, rows in headers_by_table.items()},
            "already_exists": dict(already_exists),
            "would_create" if dry_run else "created": dict(would_create),
            "sap_expected": _expected_summary(headers_by_table),
        }
        if not dry_run:
            frappe.db.commit()
            result["erp_imported"] = _erp_summary(created)
        print(json.dumps(result, indent=2, sort_keys=True))
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import April 2026 SAP Sales/Purchase Orders into ERPNext.")
    parser.add_argument("--commit", action="store_true", help="Write orders into ERPNext.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--commit-every", type=int, default=50)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(dry_run=not args.commit, limit=args.limit, commit_every=args.commit_every)

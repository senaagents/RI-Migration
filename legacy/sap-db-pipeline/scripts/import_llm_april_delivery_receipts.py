from __future__ import annotations

import argparse
import json
from collections import defaultdict
from typing import Any

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.utils import flt

from import_llm_april_orders import (
    APRIL_FROM_DATE,
    APRIL_TO_DATE,
    COMPANY,
    PURCHASE_SERVICE_ITEM,
    SALES_SERVICE_ITEM,
    SITE,
    _account_map,
    _append_expense_items,
    _append_tax_rows,
    _cost_center_map,
    _customer_map,
    _date,
    _document_expenses,
    _document_taxes,
    _expense_account_map,
    _payload,
    _rows,
    _supplier_map,
    _tax_account_map,
    _uom,
    _warehouse_map,
    connect_frappe,
)


def _ensure_custom_fields(dry_run: bool) -> int:
    fields: list[tuple[str, dict[str, Any]]] = []
    for doctype in ("Delivery Note", "Purchase Receipt"):
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
            ]
        )
    for doctype in ("Delivery Note Item", "Purchase Receipt Item"):
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


def _candidate_headers(source_table: str, limit: int | None) -> list[Any]:
    sql = """
        select source_key, payload_json
        from sap_documents
        where source_table=?
          and json_extract(payload_json, '$.CANCELED')='N'
          and date(posting_date) between date(?) and date(?)
        order by date(posting_date), cast(source_key as integer)
    """
    if limit:
        sql += f" limit {int(limit)}"
    return _rows(sql, (source_table, APRIL_FROM_DATE, APRIL_TO_DATE))


def _document_lines(parent_table: str, docentry: str) -> list[dict[str, Any]]:
    source_table = "DLN1" if parent_table == "ODLN" else "PDN1"
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


def _delivery_taxes(docentry: str) -> list[dict[str, Any]]:
    return _raw_child_rows("DLN4", docentry)


def _receipt_taxes(docentry: str) -> list[dict[str, Any]]:
    return _raw_child_rows("PDN4", docentry)


def _delivery_expenses(docentry: str) -> list[dict[str, Any]]:
    return _raw_child_rows("DLN3", docentry)


def _receipt_expenses(docentry: str) -> list[dict[str, Any]]:
    return _raw_child_rows("PDN3", docentry)


def _raw_child_rows(source_table: str, docentry: str) -> list[dict[str, Any]]:
    return [
        _payload(row)
        for row in _rows(
            """
            select payload_json
            from sap_raw_rows
            where source_table=?
              and json_extract(payload_json, '$.DocEntry')=?
            order by
              cast(coalesce(json_extract(payload_json, '$.LineNum'), 0) as integer),
              cast(coalesce(json_extract(payload_json, '$.LineSeq'), 0) as integer)
            """,
            (source_table, int(docentry)),
        )
    ]


def _ensure_setup(headers_by_table: dict[str, list[Any]], dry_run: bool) -> dict[str, int]:
    customer_codes = {str(_payload(row).get("CardCode")) for row in headers_by_table.get("ODLN", [])}
    supplier_codes = {str(_payload(row).get("CardCode")) for row in headers_by_table.get("OPDN", [])}
    sales_items: set[str] = set()
    purchase_items: set[str] = set()
    delivery_item_rates: dict[str, float] = {}
    for table, rows in headers_by_table.items():
        for header_row in rows:
            docentry = str(_payload(header_row)["DocEntry"])
            for line in _document_lines(table, docentry):
                item_code = str(line.get("ItemCode") or "")
                if not item_code:
                    continue
                if table == "ODLN":
                    sales_items.add(item_code)
                    qty = abs(flt(line.get("Quantity") or 0))
                    rate = flt(line.get("StockPrice") or line.get("GrossBuyPr") or 0)
                    if rate <= 0 and qty:
                        rate = abs(flt(line.get("StockValue") or 0)) / qty
                    if rate > 0:
                        delivery_item_rates[item_code] = max(delivery_item_rates.get(item_code, 0), rate)
                else:
                    purchase_items.add(item_code)

    updates = defaultdict(int)
    for doctype, codes in (("Customer", customer_codes), ("Supplier", supplier_codes)):
        for row in frappe.get_all(
            doctype,
            fields=["name", "disabled"],
            filters={"custom_sap_card_code": ["in", sorted(codes)]},
            limit_page_length=0,
        ):
            if row.disabled:
                print(f"enable {doctype} {row.name} for April delivery/receipt import")
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
        if item_code in sales_items and not frappe.db.get_value("Item", item_code, "allow_negative_stock"):
            values["allow_negative_stock"] = 1
        if item_code in delivery_item_rates and flt(frappe.db.get_value("Item", item_code, "valuation_rate") or 0) <= 0:
            values["valuation_rate"] = delivery_item_rates[item_code]
        if values:
            print(f"update Item {item_code}: {sorted(values)}")
            updates["item_updates"] += 1
            if not dry_run:
                frappe.db.set_value("Item", item_code, values, update_modified=False)
    return dict(updates)


def _sales_order_links() -> dict[tuple[str, str], tuple[str, str]]:
    rows = frappe.db.sql(
        """
        select
            so.custom_sap_docentry,
            soi.custom_sap_line_num,
            so.name as parent_name,
            soi.name as child_name
        from `tabSales Order` so
        join `tabSales Order Item` soi on soi.parent=so.name
        where so.docstatus=1 and ifnull(so.custom_sap_docentry, '')!=''
        """,
        as_dict=True,
    )
    return {
        (str(row.custom_sap_docentry), str(row.custom_sap_line_num)): (row.parent_name, row.child_name)
        for row in rows
    }


def _purchase_order_links() -> dict[tuple[str, str], tuple[str, str]]:
    rows = frappe.db.sql(
        """
        select
            po.custom_sap_docentry,
            poi.custom_sap_line_num,
            po.name as parent_name,
            poi.name as child_name
        from `tabPurchase Order` po
        join `tabPurchase Order Item` poi on poi.parent=po.name
        where po.docstatus=1 and ifnull(po.custom_sap_docentry, '')!=''
        """,
        as_dict=True,
    )
    return {
        (str(row.custom_sap_docentry), str(row.custom_sap_line_num)): (row.parent_name, row.child_name)
        for row in rows
    }


def _build_delivery_note(header: dict[str, Any], lines: list[dict[str, Any]], taxes: list[dict[str, Any]], expenses: list[dict[str, Any]], maps: dict[str, Any]) -> frappe.model.document.Document:
    docentry = str(header["DocEntry"])
    customer = _require(maps["customers"], header.get("CardCode"), "customer", docentry)
    posting_date = _date(header["DocDate"])
    doc = frappe.get_doc(
        {
            "doctype": "Delivery Note",
            "company": COMPANY,
            "customer": customer,
            "posting_date": posting_date,
            "posting_time": "00:00:00",
            "set_posting_time": 1,
            "currency": header.get("DocCur") or "INR",
            "conversion_rate": flt(header.get("DocRate") or 1),
            "selling_price_list": "Standard Selling",
            "price_list_currency": header.get("DocCur") or "INR",
            "plc_conversion_rate": flt(header.get("DocRate") or 1),
            "ignore_pricing_rule": 1,
            "disable_rounded_total": 1,
            "po_no": str(header.get("NumAtCard") or "")[:140],
            "custom_sap_source_table": "ODLN",
            "custom_sap_docentry": docentry,
            "custom_sap_docnum": str(header.get("DocNum") or ""),
        }
    )
    for line in lines:
        amount = flt(line.get("LineTotal") or 0)
        qty = flt(line.get("Quantity") or 0)
        item_code = str(line.get("ItemCode") or "")
        is_service = not item_code or qty == 0
        if is_service:
            item_code = SALES_SERVICE_ITEM
            qty = 1
            rate = amount
        else:
            rate = flt(amount / qty) if qty else flt(line.get("Price") or 0)
        row = _base_item_row(line, item_code, qty, rate, amount)
        if line.get("WhsCode") and not is_service:
            row["warehouse"] = _require(maps["warehouses"], line.get("WhsCode"), "warehouse", docentry)
        if (
            not is_service
            and flt(line.get("StockPrice") or line.get("GrossBuyPr") or line.get("StockValue") or 0) == 0
        ):
            row["allow_zero_valuation_rate"] = 1
        if line.get("AcctCode"):
            row["expense_account"] = _require(maps["accounts"], line.get("AcctCode"), "expense account", docentry)
        if line.get("OcrCode"):
            row["cost_center"] = _require(maps["cost_centers"], line.get("OcrCode"), "cost center", docentry)
        if int(line.get("BaseType") or -1) == 17 and line.get("BaseEntry") is not None:
            link = maps["sales_order_links"].get((str(line.get("BaseEntry")), str(line.get("BaseLine"))))
            if link:
                row["against_sales_order"], row["so_detail"] = link
        doc.append("items", row)
    _append_expense_items(doc, "sales", expenses, maps, docentry)
    _append_tax_rows(doc, "sales", header, taxes, maps, docentry)
    return doc


def _build_purchase_receipt(header: dict[str, Any], lines: list[dict[str, Any]], taxes: list[dict[str, Any]], expenses: list[dict[str, Any]], maps: dict[str, Any]) -> frappe.model.document.Document:
    docentry = str(header["DocEntry"])
    supplier = _require(maps["suppliers"], header.get("CardCode"), "supplier", docentry)
    posting_date = _date(header["DocDate"])
    doc = frappe.get_doc(
        {
            "doctype": "Purchase Receipt",
            "company": COMPANY,
            "supplier": supplier,
            "posting_date": posting_date,
            "posting_time": "00:00:00",
            "set_posting_time": 1,
            "currency": header.get("DocCur") or "INR",
            "conversion_rate": flt(header.get("DocRate") or 1),
            "buying_price_list": "Standard Buying",
            "price_list_currency": header.get("DocCur") or "INR",
            "plc_conversion_rate": flt(header.get("DocRate") or 1),
            "ignore_pricing_rule": 1,
            "disable_rounded_total": 1,
            "custom_sap_source_table": "OPDN",
            "custom_sap_docentry": docentry,
            "custom_sap_docnum": str(header.get("DocNum") or ""),
        }
    )
    for line in lines:
        amount = flt(line.get("LineTotal") or 0)
        qty = flt(line.get("Quantity") or 0)
        item_code = str(line.get("ItemCode") or "")
        is_service = not item_code or qty == 0
        if is_service:
            item_code = PURCHASE_SERVICE_ITEM
            qty = 1
            rate = amount
        else:
            rate = flt(amount / qty) if qty else flt(line.get("Price") or 0)
        row = _base_item_row(line, item_code, qty, rate, amount)
        if line.get("WhsCode") and not is_service:
            row["warehouse"] = _require(maps["warehouses"], line.get("WhsCode"), "warehouse", docentry)
        if line.get("AcctCode"):
            row["expense_account"] = _require(maps["accounts"], line.get("AcctCode"), "expense account", docentry)
        if line.get("OcrCode"):
            row["cost_center"] = _require(maps["cost_centers"], line.get("OcrCode"), "cost center", docentry)
        if int(line.get("BaseType") or -1) == 22 and line.get("BaseEntry") is not None:
            link = maps["purchase_order_links"].get((str(line.get("BaseEntry")), str(line.get("BaseLine"))))
            if link:
                row["purchase_order"], row["purchase_order_item"] = link
        doc.append("items", row)
    _append_expense_items(doc, "purchase", expenses, maps, docentry)
    _append_tax_rows(doc, "purchase", header, taxes, maps, docentry)
    return doc


def _base_item_row(line: dict[str, Any], item_code: str, qty: float, rate: float, amount: float) -> dict[str, Any]:
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
        "custom_sap_line_num": str(line.get("LineNum")),
        "custom_sap_base_type": str(line.get("BaseType") or ""),
        "custom_sap_base_entry": str(line.get("BaseEntry") or ""),
        "custom_sap_base_line": str(line.get("BaseLine") or ""),
    }
    if line.get("OcrCode2"):
        row["sap_department"] = line["OcrCode2"]
    if line.get("OcrCode3"):
        row["sap_product_segment"] = line["OcrCode3"]
    return row


def _require(mapping: dict[str, str], key: Any, label: str, docentry: str) -> str:
    normalized = str(key or "")
    if normalized in mapping:
        return mapping[normalized]
    raise RuntimeError(f"SAP delivery/receipt {docentry} missing {label} mapping for {normalized!r}")


def _expected_summary(headers_by_table: dict[str, list[Any]]) -> dict[str, dict[str, float | int]]:
    result = {}
    for table, rows in headers_by_table.items():
        docs = [_payload(row) for row in rows]
        result[table] = {
            "count": len(docs),
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


def main(dry_run: bool = True, limit: int | None = None, commit_every: int = 25) -> None:
    if SITE != "llmnew.localhost":
        raise RuntimeError(f"Refusing to import April delivery/receipts into {SITE}; set SAP_TARGET_SITE=llmnew.localhost")
    connect_frappe(frappe)
    try:
        custom_fields = _ensure_custom_fields(dry_run)
        headers_by_table = {
            "OPDN": _candidate_headers("OPDN", limit),
            "ODLN": _candidate_headers("ODLN", limit),
        }
        setup_updates = _ensure_setup(headers_by_table, dry_run)
        if not dry_run and (custom_fields or setup_updates):
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
            "sales_order_links": _sales_order_links(),
            "purchase_order_links": _purchase_order_links(),
        }

        created = {"Delivery Note": [], "Purchase Receipt": []}
        already_exists = defaultdict(int)
        would_create = defaultdict(int)
        same_day_priority = {"OPDN": 0, "ODLN": 1}
        ordered_rows = sorted(
            [(table, row) for table, rows in headers_by_table.items() for row in rows],
            key=lambda pair: (
                _payload(pair[1]).get("DocDate") or "",
                same_day_priority.get(pair[0], 9),
                int(_payload(pair[1]).get("DocEntry") or 0),
            ),
        )
        for table, row in ordered_rows:
            header = _payload(row)
            docentry = str(header["DocEntry"])
            doctype = "Delivery Note" if table == "ODLN" else "Purchase Receipt"
            if frappe.db.exists(doctype, {"custom_sap_docentry": docentry}):
                already_exists[doctype] += 1
                continue
            lines = _document_lines(table, docentry)
            if table == "ODLN":
                doc = _build_delivery_note(header, lines, _delivery_taxes(docentry), _delivery_expenses(docentry), maps)
            else:
                doc = _build_purchase_receipt(header, lines, _receipt_taxes(docentry), _receipt_expenses(docentry), maps)
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
                print(f"committed {sum(would_create.values())} delivery/receipt docs")

        result = {
            "custom_fields": custom_fields,
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
    parser = argparse.ArgumentParser(description="Import April 2026 SAP Delivery Notes and Purchase Receipts.")
    parser.add_argument("--commit", action="store_true", help="Write documents into ERPNext.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--commit-every", type=int, default=25)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(dry_run=not args.commit, limit=args.limit, commit_every=args.commit_every)

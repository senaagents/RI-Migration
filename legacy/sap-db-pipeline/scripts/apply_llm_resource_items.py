from __future__ import annotations

import json
import sqlite3

import frappe

from llm_migration_config import connect_frappe, SAP_DB, SITE, SITES_PATH


def _sap_rows(table: str) -> list[dict]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select payload_json from sap_raw_rows where source_table=?", (table,)).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _ensure_resource_item(row: dict, dry_run: bool) -> bool:
    item_code = str(row["ResCode"])
    if frappe.db.exists("Item", item_code):
        return False

    item_name = (row.get("ResName") or item_code).strip() or item_code
    group = "Operation Item" if frappe.db.exists("Item Group", "Operation Item") else "Services"
    if not frappe.db.exists("Item Group", group):
        group = "All Item Groups"

    print(f"create resource item {item_code}: {item_name}")
    if dry_run:
        return True

    frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_name[:140],
            "description": item_name,
            "item_group": group,
            "stock_uom": "Nos",
            "is_stock_item": 0,
            "is_sales_item": 1 if row.get("SellRes") == "Y" else 0,
            "is_purchase_item": 1 if row.get("PrchseRes") == "Y" else 0,
            "disabled": 1 if row.get("Deleted") == "Y" or row.get("frozenFor") == "Y" or row.get("validFor") == "N" else 0,
            "include_item_in_manufacturing": 0,
        }
    ).insert(ignore_permissions=True)
    return True


def _audit() -> dict:
    sap_resources = {str(row["ResCode"]) for row in _sap_rows("ORSC")}
    erp_items = {row.name for row in frappe.get_all("Item", fields=["name"], limit_page_length=0)}
    return {
        "sap_resources": len(sap_resources),
        "missing_resource_items": sorted(sap_resources - erp_items),
    }


def main(dry_run: bool = True) -> None:
    connect_frappe(frappe)
    try:
        created = 0
        for row in _sap_rows("ORSC"):
            created += int(_ensure_resource_item(row, dry_run))
        if dry_run:
            audit = _audit()
            print(json.dumps({"would_create": created, "missing_before": len(audit["missing_resource_items"])}, indent=2))
            return
        frappe.db.commit()
        print(json.dumps({"created": created, "audit_after": _audit()}, indent=2, sort_keys=True))
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main(dry_run=False)

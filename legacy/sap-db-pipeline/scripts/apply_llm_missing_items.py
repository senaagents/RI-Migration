from __future__ import annotations

import json
import sqlite3

import frappe

from llm_migration_config import connect_frappe, SAP_DB, SITE, SITES_PATH


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


def _sap_rows(table: str) -> list[dict]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select payload_json from sap_raw_rows where source_table=?", (table,)).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _item_groups() -> dict[int, str]:
    return {int(row["ItmsGrpCod"]): row["ItmsGrpNam"] for row in _sap_rows("OITB")}


def _uom(value: str | None) -> str:
    candidate = UOM_MAP.get(value, value or "Nos")
    if frappe.db.exists("UOM", candidate):
        return candidate
    upper = (candidate or "").upper()
    for uom in frappe.get_all("UOM", fields=["name"], limit_page_length=0):
        if uom.name.upper() == upper:
            return uom.name
    return "Nos"


def _ensure_item(row: dict, groups: dict[int, str], dry_run: bool) -> bool:
    item_code = str(row["ItemCode"])
    if frappe.db.exists("Item", item_code):
        return False
    group = groups.get(int(row["ItmsGrpCod"]), "All Item Groups")
    if not frappe.db.exists("Item Group", group):
        group = "All Item Groups"
    stock_uom = _uom(row.get("InvntryUom") or row.get("BuyUnitMsr") or row.get("SalUnitMsr"))
    item_name = (row.get("ItemName") or item_code).strip() or item_code
    print(f"create item {item_code}: {item_name} [{group}]")
    if dry_run:
        return True
    frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_name[:140],
            "description": item_name,
            "item_group": group,
            "stock_uom": stock_uom,
            "is_stock_item": 1 if row.get("InvntItem") == "Y" else 0,
            "is_sales_item": 1 if row.get("SellItem") == "Y" else 0,
            "is_purchase_item": 1 if row.get("PrchseItem") == "Y" else 0,
            "disabled": 1 if row.get("frozenFor") == "Y" or row.get("validFor") == "N" else 0,
            "include_item_in_manufacturing": 1 if row.get("InvntItem") == "Y" else 0,
            "valuation_method": "Moving Average",
        }
    ).insert(ignore_permissions=True)
    return True


def _audit() -> dict:
    sap_items = {str(row["ItemCode"]) for row in _sap_rows("OITM")}
    erp_items = {row.name for row in frappe.get_all("Item", fields=["name"], limit_page_length=0)}
    return {
        "sap_items": len(sap_items),
        "erp_items": len(erp_items),
        "missing_sap_items": sorted(sap_items - erp_items),
        "erp_only_items": sorted(erp_items - sap_items),
    }


def main(dry_run: bool = True) -> None:
    connect_frappe(frappe)
    try:
        groups = _item_groups()
        created = 0
        for row in _sap_rows("OITM"):
            created += int(_ensure_item(row, groups, dry_run))
        if dry_run:
            audit = _audit()
            print(json.dumps({"would_create": created, "missing_before": len(audit["missing_sap_items"])}, indent=2))
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

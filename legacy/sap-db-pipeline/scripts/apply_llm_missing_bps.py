from __future__ import annotations

import json
import re
import sqlite3

import frappe
from frappe.utils import validate_email_address, validate_phone_number

from llm_migration_config import connect_frappe, SAP_DB, SITE, SITES_PATH


def _sap_rows(table: str) -> list[dict]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select payload_json from sap_raw_rows where source_table=?", (table,)).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _sap_bp_groups() -> dict[int, str]:
    return {int(row["GroupCode"]): row["GroupName"] for row in _sap_rows("OCRG") if row.get("GroupCode") is not None}


def _fallback_name(card_code: str, card_name: str | None) -> str:
    clean = (card_name or "").strip()
    return clean or f"SAP BP {card_code}"


def _dedup_name(doctype: str, base_name: str, card_code: str) -> str:
    if not frappe.db.exists(doctype, base_name):
        return base_name
    return f"{base_name} ({card_code})"


def _valid_email(value: object) -> str | None:
    cleaned = validate_email_address(str(value or ""), throw=False)
    return cleaned or None


def _valid_phone(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if validate_phone_number(raw, throw=False):
        return raw
    for part in re.split(r"[&/;|]", raw):
        candidate = part.strip()
        if validate_phone_number(candidate, throw=False):
            return candidate
    return None


def _currency(value: object) -> str:
    candidate = str(value or "").strip() or "INR"
    if frappe.db.exists("Currency", candidate):
        return candidate
    return "INR"


def _ensure_customer(row: dict, groups: dict[int, str], dry_run: bool) -> bool:
    card_code = str(row["CardCode"])
    if frappe.db.exists("Customer", {"custom_sap_card_code": card_code}):
        return False
    group = groups.get(int(row["GroupCode"]), "Domestic Customers")
    if not frappe.db.exists("Customer Group", group):
        group = "Domestic Customers"
    name = _fallback_name(card_code, row.get("CardName"))
    existing_name = frappe.db.exists("Customer", name)
    if existing_name:
        existing_code = frappe.db.get_value("Customer", existing_name, "custom_sap_card_code")
        if not existing_code:
            print(f"map existing customer {card_code}: {name}")
            if not dry_run:
                frappe.db.set_value("Customer", existing_name, "custom_sap_card_code", card_code, update_modified=False)
            return False
        if existing_code == card_code:
            return False
        name = _dedup_name("Customer", name, card_code)
    print(f"create customer {card_code}: {name} [{group}]")
    if dry_run:
        return True
    frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": name,
            "customer_type": "Company",
            "customer_group": group,
            "territory": "India",
            "default_currency": _currency(row.get("Currency")),
            "mobile_no": _valid_phone(row.get("Cellular")),
            "email_id": _valid_email(row.get("E_Mail")),
            "tax_id": row.get("LicTradNum"),
            "disabled": 1 if row.get("FrozenFor") == "Y" else 0,
            "custom_sap_card_code": card_code,
        }
    ).insert(ignore_permissions=True)
    return True


def _ensure_supplier(row: dict, groups: dict[int, str], dry_run: bool) -> bool:
    card_code = str(row["CardCode"])
    if frappe.db.exists("Supplier", {"custom_sap_card_code": card_code}):
        return False
    group = groups.get(int(row["GroupCode"]), "General Supplier")
    if not frappe.db.exists("Supplier Group", group):
        group = "General Supplier"
    name = _fallback_name(card_code, row.get("CardName"))
    existing_name = frappe.db.exists("Supplier", name)
    if existing_name:
        existing_code = frappe.db.get_value("Supplier", existing_name, "custom_sap_card_code")
        if not existing_code:
            print(f"map existing supplier {card_code}: {name}")
            if not dry_run:
                frappe.db.set_value("Supplier", existing_name, "custom_sap_card_code", card_code, update_modified=False)
            return False
        if existing_code == card_code:
            return False
        name = _dedup_name("Supplier", name, card_code)
    print(f"create supplier {card_code}: {name} [{group}]")
    if dry_run:
        return True
    frappe.get_doc(
        {
            "doctype": "Supplier",
            "supplier_name": name,
            "supplier_type": "Company",
            "supplier_group": group,
            "country": "India",
            "default_currency": _currency(row.get("Currency")),
            "mobile_no": _valid_phone(row.get("Cellular")),
            "email_id": _valid_email(row.get("E_Mail")),
            "tax_id": row.get("LicTradNum"),
            "disabled": 1 if row.get("FrozenFor") == "Y" else 0,
            "custom_sap_card_code": card_code,
        }
    ).insert(ignore_permissions=True)
    return True


def _audit() -> dict:
    bps = _sap_rows("OCRD")
    sap_customers = {str(row["CardCode"]) for row in bps if row.get("CardType") == "C"}
    sap_suppliers = {str(row["CardCode"]) for row in bps if row.get("CardType") == "S"}
    erp_customers = {
        str(row.custom_sap_card_code)
        for row in frappe.get_all("Customer", fields=["custom_sap_card_code"], limit_page_length=0)
        if row.custom_sap_card_code
    }
    erp_suppliers = {
        str(row.custom_sap_card_code)
        for row in frappe.get_all("Supplier", fields=["custom_sap_card_code"], limit_page_length=0)
        if row.custom_sap_card_code
    }
    return {
        "sap_customers": len(sap_customers),
        "erp_mapped_customers": len(erp_customers),
        "missing_customers": sorted(sap_customers - erp_customers),
        "sap_suppliers": len(sap_suppliers),
        "erp_mapped_suppliers": len(erp_suppliers),
        "missing_suppliers": sorted(sap_suppliers - erp_suppliers),
    }


def main(dry_run: bool = True) -> None:
    connect_frappe(frappe)
    try:
        groups = _sap_bp_groups()
        created = {"customers": 0, "suppliers": 0}
        for row in _sap_rows("OCRD"):
            if row.get("CardType") == "C":
                created["customers"] += int(_ensure_customer(row, groups, dry_run))
            elif row.get("CardType") == "S":
                created["suppliers"] += int(_ensure_supplier(row, groups, dry_run))
        if dry_run:
            print(json.dumps({"would_create": created, "audit_before": _audit()}, indent=2, sort_keys=True))
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

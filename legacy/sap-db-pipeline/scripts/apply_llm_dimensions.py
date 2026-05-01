from __future__ import annotations

import json
import sqlite3

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_doctypes_with_dimensions,
)
from erpnext.accounts.doctype.repost_accounting_ledger.repost_accounting_ledger import (
    get_allowed_types_from_settings,
)


from llm_migration_config import connect_frappe, SAP_DB, SITE, SITES_PATH


DIMENSION_TARGETS = {
    2: {
        "doctype": "SAP Department",
        "label": "SAP Department",
        "fieldname": "sap_department",
    },
    3: {
        "doctype": "SAP Product Segment",
        "label": "SAP Product Segment",
        "fieldname": "sap_product_segment",
    },
}


def _sap_rows(table: str) -> list[dict]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select payload_json from sap_raw_rows where source_table=?", (table,)).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _dimension_rows(dim_code: int) -> list[dict]:
    rows = [row for row in _sap_rows("OPRC") if int(row["DimCode"]) == dim_code]
    return sorted(rows, key=lambda row: str(row["PrcCode"]))


def _ensure_custom_doctype(name: str, dry_run: bool) -> bool:
    if frappe.db.exists("DocType", name):
        return False

    print(f"create doctype {name}")
    if dry_run:
        return True

    doc = frappe.get_doc(
        {
            "doctype": "DocType",
            "name": name,
            "module": "Custom",
            "custom": 1,
            "allow_import": 1,
            "autoname": "field:sap_code",
            "title_field": "dimension_name",
            "search_fields": "sap_code,dimension_name",
            "permissions": [
                {
                    "role": "System Manager",
                    "read": 1,
                    "write": 1,
                    "create": 1,
                    "delete": 1,
                    "report": 1,
                    "export": 1,
                    "import": 1,
                },
                {
                    "role": "Accounts Manager",
                    "read": 1,
                    "write": 1,
                    "create": 1,
                    "delete": 0,
                    "report": 1,
                    "export": 1,
                    "import": 1,
                },
            ],
            "fields": [
                {
                    "fieldname": "sap_code",
                    "label": "SAP Code",
                    "fieldtype": "Data",
                    "reqd": 1,
                    "unique": 1,
                    "in_list_view": 1,
                },
                {
                    "fieldname": "dimension_name",
                    "label": "Dimension Name",
                    "fieldtype": "Data",
                    "reqd": 1,
                    "in_list_view": 1,
                },
                {
                    "fieldname": "sap_dim_code",
                    "label": "SAP Dimension Code",
                    "fieldtype": "Int",
                    "read_only": 1,
                },
                {
                    "fieldname": "active",
                    "label": "Active",
                    "fieldtype": "Check",
                    "default": "1",
                },
            ],
        }
    )
    doc.insert(ignore_permissions=True)
    return True


def _ensure_dimension_value(target: dict, row: dict, dry_run: bool) -> bool:
    code = str(row["PrcCode"])
    if not frappe.db.exists("DocType", target["doctype"]):
        print(f"create {target['doctype']} {code}: {row['PrcName']}")
        return True
    if frappe.db.exists(target["doctype"], code):
        return False

    print(f"create {target['doctype']} {code}: {row['PrcName']}")
    if dry_run:
        return True

    frappe.get_doc(
        {
            "doctype": target["doctype"],
            "sap_code": code,
            "dimension_name": row["PrcName"] or code,
            "sap_dim_code": int(row["DimCode"]),
            "active": 1 if row.get("Active") == "Y" else 0,
        }
    ).insert(ignore_permissions=True)
    return True


def _ensure_accounting_dimension(target: dict, dry_run: bool) -> bool:
    if frappe.db.exists("Accounting Dimension", target["label"]):
        doc = frappe.get_doc("Accounting Dimension", target["label"])
        if doc.document_type != target["doctype"] or doc.fieldname != target["fieldname"]:
            raise RuntimeError(
                f"Accounting Dimension {target['label']} points to "
                f"{doc.document_type}/{doc.fieldname}, expected {target['doctype']}/{target['fieldname']}"
            )
        return False

    print(f"create accounting dimension {target['label']}")
    if dry_run:
        return True

    doc = frappe.get_doc(
        {
            "doctype": "Accounting Dimension",
            "document_type": target["doctype"],
            "label": target["label"],
        }
    )
    doc.insert(ignore_permissions=True)
    return True


def _ensure_dimension_fields(target: dict, dry_run: bool) -> int:
    created = 0
    repostable_doctypes = set(get_allowed_types_from_settings(child_doc=True))
    for doctype in dict.fromkeys(get_doctypes_with_dimensions()):
        custom_field_name = f"{doctype}-{target['fieldname']}"
        if frappe.db.exists("Custom Field", custom_field_name):
            continue
        if frappe.get_meta(doctype, cached=False).has_field(target["fieldname"]):
            continue

        print(f"create {doctype} field {target['fieldname']}")
        created += 1
        if dry_run:
            continue

        df = {
            "fieldname": target["fieldname"],
            "label": target["label"],
            "fieldtype": "Link",
            "options": target["doctype"],
            "insert_after": "accounting_dimensions_section",
            "owner": "Administrator",
            "allow_on_submit": 1 if doctype in repostable_doctypes else 0,
        }
        if doctype == "Budget":
            df.update(
                {
                    "insert_after": "cost_center",
                    "depends_on": f"eval:doc.budget_against == '{target['doctype']}'",
                }
            )
        create_custom_field(doctype, df, ignore_validate=True)

        if doctype == "Budget":
            _ensure_budget_dimension_option(target["doctype"])
        frappe.clear_cache(doctype=doctype)
    return created


def _ensure_budget_dimension_option(document_type: str) -> None:
    property_setter = frappe.db.exists("Property Setter", "Budget-budget_against-options")
    if property_setter:
        doc = frappe.get_doc("Property Setter", "Budget-budget_against-options")
        options = doc.value.split("\n")
        if document_type not in options:
            doc.value = doc.value + "\n" + document_type
            doc.save(ignore_permissions=True)
        return

    frappe.get_doc(
        {
            "doctype": "Property Setter",
            "doctype_or_field": "DocField",
            "doc_type": "Budget",
            "field_name": "budget_against",
            "property": "options",
            "property_type": "Text",
            "value": "\nCost Center\nProject\n" + document_type,
        }
    ).insert(ignore_permissions=True)


def _enable_accounting_dimensions(dry_run: bool) -> bool:
    settings = frappe.get_single("Accounts Settings")
    if settings.enable_accounting_dimensions:
        return False

    print("enable accounting dimensions in Accounts Settings")
    if dry_run:
        return True

    settings.enable_accounting_dimensions = 1
    settings.save(ignore_permissions=True)
    return True


def _audit() -> dict:
    result = {
        "accounts_settings_enable_accounting_dimensions": frappe.get_single(
            "Accounts Settings"
        ).enable_accounting_dimensions,
        "accounting_dimensions": frappe.get_all(
            "Accounting Dimension",
            fields=["name", "document_type", "fieldname", "disabled"],
            filters={"name": ["in", [target["label"] for target in DIMENSION_TARGETS.values()]]},
            order_by="name",
        ),
        "values": {},
        "missing_values": {},
        "custom_fields": frappe.get_all(
            "Custom Field",
            fields=["dt", "fieldname", "options"],
            filters={"fieldname": ["in", [target["fieldname"] for target in DIMENSION_TARGETS.values()]]},
            order_by="dt, fieldname",
            limit_page_length=0,
        ),
    }
    for dim_code, target in DIMENSION_TARGETS.items():
        sap_codes = {str(row["PrcCode"]) for row in _dimension_rows(dim_code)}
        erp_codes = {row.name for row in frappe.get_all(target["doctype"], fields=["name"], limit_page_length=0)}
        result["values"][target["doctype"]] = len(erp_codes)
        result["missing_values"][target["doctype"]] = sorted(sap_codes - erp_codes)
    return result


def main(dry_run: bool = True) -> None:
    connect_frappe(frappe)
    try:
        created = {
            "doctypes": 0,
            "values": 0,
            "accounting_dimensions": 0,
            "dimension_fields": 0,
            "settings": 0,
        }
        for dim_code, target in DIMENSION_TARGETS.items():
            created["doctypes"] += int(_ensure_custom_doctype(target["doctype"], dry_run))
            for row in _dimension_rows(dim_code):
                created["values"] += int(_ensure_dimension_value(target, row, dry_run))
            created["accounting_dimensions"] += int(_ensure_accounting_dimension(target, dry_run))
            created["dimension_fields"] += _ensure_dimension_fields(target, dry_run)
        created["settings"] += int(_enable_accounting_dimensions(dry_run))

        if dry_run:
            print(json.dumps({"would_create_or_update": created}, indent=2, sort_keys=True))
            return
        frappe.db.commit()
        print(json.dumps({"created_or_updated": created, "audit_after": _audit()}, indent=2, sort_keys=True))
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main(dry_run=False)

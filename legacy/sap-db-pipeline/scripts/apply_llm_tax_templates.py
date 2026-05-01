from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

import frappe

from llm_migration_config import connect_frappe, COMPANY, SAP_DB, SITE, SITES_PATH

SALES_TAX_TABLES = ("INV4", "RIN4")
PURCHASE_TAX_TABLES = ("PCH4", "RPC4")


def _sap_rows(table: str) -> list[dict]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select payload_json from sap_raw_rows where source_table=?", (table,)).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _ostc() -> dict[str, dict]:
    return {row["Code"]: row for row in _sap_rows("OSTC")}


def _account_map() -> dict[str, str]:
    rows = frappe.get_all(
        "Account",
        fields=["name", "custom_sap_acct_code"],
        filters={"company": COMPANY, "custom_sap_acct_code": ["is", "set"]},
        limit_page_length=0,
    )
    return {str(row.custom_sap_acct_code): row.name for row in rows}


def _template_title(kind: str, stc_code: str) -> str:
    prefix = "SAP AR" if kind == "sales" else "SAP AP"
    return f"{prefix} {stc_code}"


def _template_doctype(kind: str) -> str:
    return "Sales Taxes and Charges Template" if kind == "sales" else "Purchase Taxes and Charges Template"


def _tax_child_doctype(kind: str) -> str:
    return "Sales Taxes and Charges" if kind == "sales" else "Purchase Taxes and Charges"


def _tax_tables(kind: str) -> tuple[str, ...]:
    return SALES_TAX_TABLES if kind == "sales" else PURCHASE_TAX_TABLES


def _tax_components(kind: str, acct_map: dict[str, str]) -> dict[str, list[dict]]:
    grouped = defaultdict(dict)
    skipped_missing_account = defaultdict(int)

    for table in _tax_tables(kind):
        for row in _sap_rows(table):
            stc_code = row.get("StcCode")
            sta_code = row.get("StaCode")
            sap_acct = row.get("TaxAcct")
            if not stc_code or not sta_code:
                continue
            if not sap_acct:
                continue
            account = acct_map.get(str(sap_acct))
            if not account:
                skipped_missing_account[(stc_code, sta_code, sap_acct)] += 1
                continue
            key = (sta_code, str(sap_acct), float(row.get("TaxRate") or 0))
            grouped[stc_code][key] = {
                "sta_code": sta_code,
                "sap_account": str(sap_acct),
                "account_head": account,
                "rate": float(row.get("TaxRate") or 0),
            }

    if skipped_missing_account:
        missing = {
            "|".join(map(str, key)): count for key, count in sorted(skipped_missing_account.items())
        }
        raise RuntimeError(f"Missing ERPNext tax accounts for SAP tax rows: {missing}")

    return {
        stc_code: sorted(
            components.values(),
            key=lambda item: (item["sta_code"], item["sap_account"], item["rate"]),
        )
        for stc_code, components in sorted(grouped.items())
    }


def _ensure_template(kind: str, stc_code: str, components: list[dict], ostc: dict[str, dict], dry_run: bool) -> bool:
    doctype = _template_doctype(kind)
    title = _template_title(kind, stc_code)
    name = f"{title} - L"
    if frappe.db.exists(doctype, name):
        return False

    print(f"create {doctype} {name} with {len(components)} rows")
    if dry_run:
        return True

    sap_tax = ostc.get(stc_code, {})
    doc = frappe.get_doc(
        {
            "doctype": doctype,
            "title": title,
            "company": COMPANY,
            "disabled": 1 if sap_tax.get("Lock") == "Y" else 0,
        }
    )
    for component in components:
        doc.append(
            "taxes",
            {
                "doctype": _tax_child_doctype(kind),
                "charge_type": "On Net Total",
                "account_head": component["account_head"],
                "description": component["sta_code"],
                "rate": component["rate"],
            },
        )
    doc.insert(ignore_permissions=True)
    return True


def _audit() -> dict:
    sales = frappe.get_all(
        "Sales Taxes and Charges Template",
        fields=["name"],
        filters={"name": ["like", "SAP AR % - L"]},
        limit_page_length=0,
    )
    purchase = frappe.get_all(
        "Purchase Taxes and Charges Template",
        fields=["name"],
        filters={"name": ["like", "SAP AP % - L"]},
        limit_page_length=0,
    )
    return {
        "sap_ar_templates": len(sales),
        "sap_ap_templates": len(purchase),
        "sales_template_rows": frappe.db.count(
            "Sales Taxes and Charges", {"parent": ["like", "SAP AR % - L"]}
        ),
        "purchase_template_rows": frappe.db.count(
            "Purchase Taxes and Charges", {"parent": ["like", "SAP AP % - L"]}
        ),
    }


def main(dry_run: bool = True) -> None:
    connect_frappe(frappe)
    try:
        ostc = _ostc()
        acct_map = _account_map()
        created = {"sales": 0, "purchase": 0}
        expected = {}
        for kind in ("sales", "purchase"):
            components_by_code = _tax_components(kind, acct_map)
            expected[kind] = {
                "templates": len(components_by_code),
                "rows": sum(len(rows) for rows in components_by_code.values()),
            }
            for stc_code, components in components_by_code.items():
                created[kind] += int(_ensure_template(kind, stc_code, components, ostc, dry_run))

        if dry_run:
            print(json.dumps({"would_create": created, "expected": expected}, indent=2, sort_keys=True))
            return
        frappe.db.commit()
        print(json.dumps({"created": created, "expected": expected, "audit_after": _audit()}, indent=2, sort_keys=True))
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main(dry_run=False)

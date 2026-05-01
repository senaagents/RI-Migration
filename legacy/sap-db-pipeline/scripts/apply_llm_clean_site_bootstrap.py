from __future__ import annotations

import json
import sqlite3
from datetime import date

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

from llm_migration_config import connect_frappe, COMPANY, CUTOFF_TO_DATE, HISTORY_FROM_DATE, SAP_DB, SITE, SITES_PATH


ROOT_BUCKET_MAP = {
    "100000000000000": "Application of Funds (Assets) - L",
    "200000000000000": "Source of Funds (Liabilities) - L",
    "300000000000000": "Equity - L",
    "400000000000000": "Income - L",
    "500000000000000": "Expenses - L",
}

ROOT_BUCKET_MASK = {
    "100000000000000": 1,
    "200000000000000": 2,
    "300000000000000": 3,
    "400000000000000": 4,
    "500000000000000": 5,
}

INACTIVE_ROOT_BUCKETS = {
    "600000000000000",
    "700000000000000",
    "800000000000000",
    "900000000000000",
    "A00000000000000",
}

ROOT_TYPES = {
    1: ("Asset", "Balance Sheet"),
    2: ("Liability", "Balance Sheet"),
    3: ("Equity", "Balance Sheet"),
    4: ("Income", "Profit and Loss"),
    5: ("Expense", "Profit and Loss"),
}

ACCOUNT_TYPE_BY_CODE = {
    "1202101": "Receivable",
    "1202102": "Receivable",
    "2201001": "Payable",
    "2201002": "Payable",
    "1203101": "Cash",
    "1203102": "Cash",
    "1203104": "Cash",
    "1203209": "Bank",
    "1203212": "Bank",
    "2101001": "Bank",
    "2101012": "Bank",
    "2101015": "Bank",
}


def _rows(table: str) -> list[dict]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select payload_json from sap_master_records where source_table=? order by source_key",
            (table,),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "select payload_json from sap_raw_rows where source_table=? order by row_ordinal",
                (table,),
            ).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _ensure_custom_field(dt: str, field: dict, dry_run: bool) -> bool:
    name = f"{dt}-{field['fieldname']}"
    if frappe.db.exists("Custom Field", name):
        return False
    print(f"create Custom Field {name}")
    if not dry_run:
        create_custom_field(dt, field, ignore_validate=True)
        frappe.clear_cache(doctype=dt)
    return True


def _ensure_custom_fields(dry_run: bool) -> int:
    fields = [
        (
            "Account",
            {
                "fieldname": "custom_sap_acct_code",
                "label": "SAP Account Code",
                "fieldtype": "Data",
                "insert_after": "account_number",
                "unique": 1,
                "read_only": 1,
                "no_copy": 1,
            },
        ),
        (
            "Customer",
            {
                "fieldname": "custom_sap_card_code",
                "label": "SAP Card Code",
                "fieldtype": "Data",
                "insert_after": "customer_name",
                "unique": 1,
                "read_only": 1,
                "no_copy": 1,
            },
        ),
        (
            "Supplier",
            {
                "fieldname": "custom_sap_card_code",
                "label": "SAP Card Code",
                "fieldtype": "Data",
                "insert_after": "supplier_name",
                "unique": 1,
                "read_only": 1,
                "no_copy": 1,
            },
        ),
        (
            "Warehouse",
            {
                "fieldname": "custom_sap_whs_code",
                "label": "SAP Warehouse Code",
                "fieldtype": "Data",
                "insert_after": "warehouse_name",
            },
        ),
        (
            "Warehouse",
            {
                "fieldname": "custom_sap_whs_type",
                "label": "SAP Warehouse Type",
                "fieldtype": "Data",
                "insert_after": "custom_sap_whs_code",
            },
        ),
        (
            "Cost Center",
            {
                "fieldname": "custom_sap_cc_code",
                "label": "SAP Cost Center Code",
                "fieldtype": "Data",
                "insert_after": "cost_center_name",
            },
        ),
        (
            "Branch",
            {
                "fieldname": "custom_sap_bpl_id",
                "label": "SAP BPL ID",
                "fieldtype": "Int",
                "insert_after": "branch",
            },
        ),
    ]
    return sum(int(_ensure_custom_field(dt, field, dry_run)) for dt, field in fields)


def _ensure_fiscal_years(dry_run: bool) -> int:
    start_year = int(HISTORY_FROM_DATE[:4])
    if HISTORY_FROM_DATE[5:10] < "04-01":
        start_year -= 1
    end_year = int(CUTOFF_TO_DATE[:4])
    if CUTOFF_TO_DATE[5:10] <= "03-31":
        end_year -= 1

    created = 0
    for year in range(start_year, end_year + 1):
        name = f"{year}-{year + 1}"
        if frappe.db.exists("Fiscal Year", name):
            continue
        print(f"create Fiscal Year {name}")
        created += 1
        if dry_run:
            continue
        doc = frappe.get_doc(
            {
                "doctype": "Fiscal Year",
                "year": name,
                "year_start_date": date(year, 4, 1),
                "year_end_date": date(year + 1, 3, 31),
            }
        )
        doc.append("companies", {"company": COMPANY})
        doc.insert(ignore_permissions=True)
    return created


def _dedup_tree_name(doctype: str, base: str, suffix: str) -> str:
    if not frappe.db.exists(doctype, base):
        return base
    return f"{base} ({suffix})"


def _ensure_uoms(dry_run: bool) -> int:
    created = 0
    for row in _rows("OUOM"):
        code = (row.get("UomCode") or row.get("UomName") or "").strip()
        if not code:
            continue
        name = {"NOS": "Nos", "KG": "Kg", "SET": "Set"}.get(code, code)
        if frappe.db.exists("UOM", name):
            continue
        print(f"create UOM {name}")
        created += 1
        if dry_run:
            continue
        frappe.get_doc(
            {
                "doctype": "UOM",
                "uom_name": name,
                "enabled": 0 if row.get("Locked") == "Y" else 1,
                "must_be_whole_number": 1 if code in {"NOS", "SET"} else 0,
            }
        ).insert(ignore_permissions=True)
    return created


def _ensure_bp_groups(dry_run: bool) -> int:
    created = 0
    for row in _rows("OCRG"):
        group_name = (row.get("GroupName") or "").strip()
        if not group_name:
            continue
        if row.get("GroupType") == "C":
            if frappe.db.exists("Customer Group", group_name):
                continue
            print(f"create Customer Group {group_name}")
            created += 1
            if not dry_run:
                frappe.get_doc(
                    {
                        "doctype": "Customer Group",
                        "customer_group_name": group_name,
                        "parent_customer_group": "All Customer Groups",
                        "is_group": 0,
                    }
                ).insert(ignore_permissions=True, ignore_mandatory=True)
        elif row.get("GroupType") == "S":
            if frappe.db.exists("Supplier Group", group_name):
                continue
            print(f"create Supplier Group {group_name}")
            created += 1
            if not dry_run:
                frappe.get_doc(
                    {
                        "doctype": "Supplier Group",
                        "supplier_group_name": group_name,
                        "parent_supplier_group": "All Supplier Groups",
                        "is_group": 0,
                    }
                ).insert(ignore_permissions=True, ignore_mandatory=True)
    return created


def _ensure_item_groups(dry_run: bool) -> int:
    created = 0
    for row in _rows("OITB"):
        group_name = (row.get("ItmsGrpNam") or "").strip()
        if not group_name or frappe.db.exists("Item Group", group_name):
            continue
        print(f"create Item Group {group_name}")
        created += 1
        if dry_run:
            continue
        frappe.get_doc(
            {
                "doctype": "Item Group",
                "item_group_name": group_name,
                "parent_item_group": "All Item Groups",
                "is_group": 0,
            }
        ).insert(ignore_permissions=True)
    return created


def _ensure_branches(dry_run: bool) -> int:
    created = 0
    has_mapping = frappe.db.has_column("Branch", "custom_sap_bpl_id")
    for row in _rows("OBPL"):
        bpl_id = int(row["BPLId"])
        if has_mapping and frappe.db.exists("Branch", {"custom_sap_bpl_id": bpl_id}):
            continue
        branch = (row.get("BPLName") or f"SAP Branch {bpl_id}").strip()
        name = _dedup_tree_name("Branch", branch, str(bpl_id))
        print(f"create Branch {name} for SAP BPL {bpl_id}")
        created += 1
        if dry_run:
            continue
        frappe.get_doc(
            {
                "doctype": "Branch",
                "branch": name,
                "custom_sap_bpl_id": bpl_id,
            }
        ).insert(ignore_permissions=True)
    return created


def _ensure_warehouses(dry_run: bool) -> int:
    parent = f"All Warehouses - {frappe.get_cached_value('Company', COMPANY, 'abbr')}"
    created = 0
    has_mapping = frappe.db.has_column("Warehouse", "custom_sap_whs_code")
    for row in _rows("OWHS"):
        code = str(row["WhsCode"])
        if has_mapping and frappe.db.exists("Warehouse", {"company": COMPANY, "custom_sap_whs_code": code}):
            continue
        warehouse_name = (row.get("WhsName") or code).strip()
        if frappe.db.exists("Warehouse", f"{warehouse_name} - L"):
            warehouse_name = f"{warehouse_name} ({code})"
        print(f"create Warehouse {warehouse_name} for SAP {code}")
        created += 1
        if dry_run:
            continue
        frappe.get_doc(
            {
                "doctype": "Warehouse",
                "warehouse_name": warehouse_name,
                "company": COMPANY,
                "parent_warehouse": parent,
                "is_group": 0,
                "disabled": 1 if row.get("Inactive") == "Y" else 0,
                "custom_sap_whs_code": code,
                "custom_sap_whs_type": row.get("WhsType") or row.get("DropShip"),
            }
        ).insert(ignore_permissions=True)
    return created


def _ensure_cost_centers(dry_run: bool) -> int:
    root = f"{COMPANY} - {frappe.get_cached_value('Company', COMPANY, 'abbr')}"
    created = 0
    has_mapping = frappe.db.has_column("Cost Center", "custom_sap_cc_code")
    for row in _rows("OPRC"):
        if int(row["DimCode"]) != 1:
            continue
        code = str(row["PrcCode"])
        if has_mapping and frappe.db.exists("Cost Center", {"company": COMPANY, "custom_sap_cc_code": code}):
            continue
        name = (row.get("PrcName") or code).strip()
        if frappe.db.exists("Cost Center", f"{name} - L"):
            name = f"{name} ({code})"
        print(f"create Cost Center {name} for SAP {code}")
        created += 1
        if dry_run:
            continue
        frappe.get_doc(
            {
                "doctype": "Cost Center",
                "cost_center_name": name,
                "company": COMPANY,
                "parent_cost_center": root,
                "is_group": 0,
                "disabled": 0 if row.get("Active") == "Y" else 1,
                "custom_sap_cc_code": code,
            }
        ).insert(ignore_permissions=True)
    return created


def _account_doc_name(account: dict) -> str:
    return f"{account['AcctCode']} - {account['AcctName']} - L"


def _parent_account_name(account: dict, accounts: dict[str, dict]) -> str | None:
    parent_code = account.get("FatherNum")
    if not parent_code:
        return None
    parent_code = str(parent_code)
    if parent_code in ROOT_BUCKET_MAP:
        return ROOT_BUCKET_MAP[parent_code]
    parent = accounts.get(parent_code)
    return _account_doc_name(parent) if parent else None


def _ensure_account(account: dict, accounts: dict[str, dict], dry_run: bool) -> bool:
    code = str(account["AcctCode"])
    has_mapping = frappe.db.has_column("Account", "custom_sap_acct_code")
    if code in INACTIVE_ROOT_BUCKETS:
        print(f"skip inactive SAP root bucket {code}")
        return False
    if code in ROOT_BUCKET_MAP:
        root_account = ROOT_BUCKET_MAP[code]
        if not frappe.db.exists("Account", root_account):
            root_type, report_type = ROOT_TYPES[ROOT_BUCKET_MASK[code]]
            print(f"create root Account {root_account}")
            if not dry_run:
                frappe.get_doc(
                    {
                        "doctype": "Account",
                        "company": COMPANY,
                        "account_name": root_account.removesuffix(" - L"),
                        "is_group": 1,
                        "root_type": root_type,
                        "report_type": report_type,
                    }
                ).insert(ignore_permissions=True, ignore_mandatory=True)
            return True
        current = frappe.db.get_value("Account", root_account, "custom_sap_acct_code") if has_mapping else None
        if current != code:
            print(f"map SAP root {code} -> {root_account}")
            if not dry_run and has_mapping:
                frappe.db.set_value(
                    "Account",
                    root_account,
                    "custom_sap_acct_code",
                    code,
                    update_modified=False,
                )
        return False

    if has_mapping and frappe.db.exists("Account", {"company": COMPANY, "custom_sap_acct_code": code}):
        return False
    existing = frappe.db.get_value("Account", {"company": COMPANY, "account_number": code}, "name")
    if existing:
        print(f"map existing Account {existing} to SAP {code}")
        if not dry_run and has_mapping:
            frappe.db.set_value("Account", existing, "custom_sap_acct_code", code, update_modified=False)
        return False

    parent_account = _parent_account_name(account, accounts)
    if not parent_account:
        raise RuntimeError(f"No ERPNext parent account for SAP account {code}")
    group_mask = int(account["GroupMask"])
    root_type, report_type = ROOT_TYPES[group_mask]
    account_name = str(account.get("AcctName") or code).strip()
    is_group = 0 if account.get("Postable") == "Y" else 1
    doc = {
        "doctype": "Account",
        "company": COMPANY,
        "account_number": code,
        "account_name": account_name,
        "parent_account": parent_account,
        "is_group": is_group,
        "root_type": root_type,
        "report_type": report_type,
        "account_currency": account.get("ActCurr") if account.get("ActCurr") not in {None, "##"} else "INR",
        "custom_sap_acct_code": code,
    }
    account_type = ACCOUNT_TYPE_BY_CODE.get(code)
    if account_type:
        doc["account_type"] = account_type
    print(f"create Account {code} under {parent_account}")
    if not dry_run:
        frappe.get_doc(doc).insert(ignore_permissions=True)
    return True


def _ensure_coa(dry_run: bool) -> int:
    accounts = {str(row["AcctCode"]): row for row in _rows("OACT")}
    created = 0
    for code, account in sorted(accounts.items(), key=lambda item: (int(item[1]["Levels"]), item[0])):
        created += int(_ensure_account(account, accounts, dry_run))
    return created


def _audit() -> dict:
    return {
        "company": COMPANY,
        "site": SITE,
        "fiscal_years": frappe.db.count("Fiscal Year"),
        "accounts": frappe.db.count("Account", {"company": COMPANY}),
        "sap_mapped_accounts": frappe.db.count(
            "Account", {"company": COMPANY, "custom_sap_acct_code": ["is", "set"]}
        ),
        "customers": frappe.db.count("Customer"),
        "suppliers": frappe.db.count("Supplier"),
        "items": frappe.db.count("Item"),
        "warehouses": frappe.db.count("Warehouse", {"company": COMPANY}),
        "sap_mapped_warehouses": frappe.db.count(
            "Warehouse", {"company": COMPANY, "custom_sap_whs_code": ["is", "set"]}
        ),
        "cost_centers": frappe.db.count("Cost Center", {"company": COMPANY}),
        "sap_mapped_cost_centers": frappe.db.count(
            "Cost Center", {"company": COMPANY, "custom_sap_cc_code": ["is", "set"]}
        ),
        "branches": frappe.db.count("Branch"),
    }


def main(dry_run: bool = True) -> None:
    connect_frappe(frappe)
    try:
        created = {
            "custom_fields": _ensure_custom_fields(dry_run),
            "fiscal_years": _ensure_fiscal_years(dry_run),
            "uoms": _ensure_uoms(dry_run),
            "bp_groups": _ensure_bp_groups(dry_run),
            "item_groups": _ensure_item_groups(dry_run),
            "branches": _ensure_branches(dry_run),
            "warehouses": _ensure_warehouses(dry_run),
            "cost_centers": _ensure_cost_centers(dry_run),
            "accounts": _ensure_coa(dry_run),
        }
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

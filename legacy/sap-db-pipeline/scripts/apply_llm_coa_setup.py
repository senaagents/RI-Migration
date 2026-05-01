from __future__ import annotations

import json
import sqlite3

import frappe

from llm_migration_config import connect_frappe, COMPANY, SAP_DB, SITE, SITES_PATH

ROOT_BUCKET_MAP = {
    "100000000000000": "Application of Funds (Assets) - L",
    "200000000000000": "Source of Funds (Liabilities) - L",
    "300000000000000": "Equity - L",
    "400000000000000": "Income - L",
    "500000000000000": "Expenses - L",
}

INACTIVE_ROOT_BUCKETS = {
    "600000000000000",
    "700000000000000",
    "800000000000000",
    "900000000000000",
    "A00000000000000",
}

MISSING_POSTABLE_ACCOUNTS = {
    "1103207": "ICICI PRUDENTIAL INDIA OPPORTUNITIES FUND",
    "1103208": "NIPPON INDIA LARGE CAP FUND",
    "1103209": "NIPPON INDIA MIDCAP FUND",
    "1103210": "ADITYA BIRLA SUNLIFE FLEXICAP FUND",
    "1103211": "SBI BANKING & FINANCIAL SERVICES FUND",
    "1103212": "PARAG PARIKH FLEXICAP FUND",
    "1103213": "HELIOS FLEXICAP FUND",
}

PARENT_ACCOUNT = "1103200 - KOTAK INVESTMENT-MUTUAL FUNDS - L"


def _sap_oact() -> dict[str, dict]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select payload_json from sap_raw_rows where source_table='OACT'").fetchall()
    records = [json.loads(row["payload_json"]) for row in rows]
    return {str(row["AcctCode"]): row for row in records}


def _ensure_account_custom_field(dry_run: bool) -> None:
    if frappe.db.exists("Custom Field", {"dt": "Account", "fieldname": "custom_sap_acct_code"}):
        print("Account.custom_sap_acct_code exists")
        return
    print("create Account.custom_sap_acct_code")
    if dry_run:
        return
    frappe.get_doc(
        {
            "doctype": "Custom Field",
            "dt": "Account",
            "fieldname": "custom_sap_acct_code",
            "label": "SAP Account Code",
            "fieldtype": "Data",
            "insert_after": "account_number",
            "unique": 1,
            "read_only": 1,
            "no_copy": 1,
            "description": "Original SAP Business One OACT.AcctCode used for migration mapping.",
        }
    ).insert(ignore_permissions=True)
    frappe.clear_cache(doctype="Account")


def _set_account_mapping(account_name: str, sap_code: str, dry_run: bool) -> None:
    if not frappe.db.has_column("Account", "custom_sap_acct_code"):
        print(f"map {sap_code} -> {account_name}")
        return
    current = frappe.db.get_value("Account", account_name, "custom_sap_acct_code")
    if current == sap_code:
        return
    print(f"map {sap_code} -> {account_name}")
    if not dry_run:
        frappe.db.set_value("Account", account_name, "custom_sap_acct_code", sap_code, update_modified=False)


def _populate_account_mappings(dry_run: bool) -> None:
    for row in frappe.get_all("Account", filters={"company": COMPANY}, fields=["name", "account_number"]):
        if row.account_number:
            _set_account_mapping(row.name, row.account_number, dry_run)
    for sap_code, account_name in ROOT_BUCKET_MAP.items():
        _set_account_mapping(account_name, sap_code, dry_run)
    for sap_code in sorted(INACTIVE_ROOT_BUCKETS):
        print(f"skip inactive SAP root bucket {sap_code}; no ERPNext account mapping needed")


def _create_missing_postable_accounts(sap_accounts: dict[str, dict], dry_run: bool) -> None:
    if not frappe.db.exists("Account", PARENT_ACCOUNT):
        raise RuntimeError(f"Parent account does not exist: {PARENT_ACCOUNT}")
    parent = frappe.get_doc("Account", PARENT_ACCOUNT)
    for sap_code, fallback_name in MISSING_POSTABLE_ACCOUNTS.items():
        existing = frappe.db.get_value("Account", {"company": COMPANY, "account_number": sap_code}, "name")
        if existing:
            _set_account_mapping(existing, sap_code, dry_run)
            continue
        sap = sap_accounts.get(sap_code) or {}
        account_name = sap.get("AcctName") or fallback_name
        print(f"create postable account {sap_code} - {account_name} under {PARENT_ACCOUNT}")
        if dry_run:
            continue
        doc = frappe.get_doc(
            {
                "doctype": "Account",
                "company": COMPANY,
                "account_number": sap_code,
                "account_name": account_name,
                "parent_account": PARENT_ACCOUNT,
                "is_group": 0,
                "root_type": parent.root_type,
                "report_type": parent.report_type,
                "account_currency": parent.account_currency or "INR",
                "custom_sap_acct_code": sap_code,
            }
        )
        doc.insert(ignore_permissions=True)


def _fix_account_name(dry_run: bool) -> None:
    account = frappe.db.get_value("Account", {"company": COMPANY, "account_number": "1204202"}, "name")
    if not account:
        raise RuntimeError("Account 1204202 not found")
    current = frappe.db.get_value("Account", account, "account_name")
    if current == "SALARY ADVANCE":
        return
    print(f"fix account 1204202 name: {current!r} -> 'SALARY ADVANCE'")
    if not dry_run:
        frappe.db.set_value("Account", account, "account_name", "SALARY ADVANCE", update_modified=False)
        expected_name = "1204202 - SALARY ADVANCE - L"
        if account != expected_name and not frappe.db.exists("Account", expected_name):
            frappe.rename_doc("Account", account, expected_name, force=True, merge=False)


def _audit() -> dict:
    sap_accounts = _sap_oact()
    erp_rows = frappe.get_all(
        "Account",
        filters={"company": COMPANY},
        fields=["name", "account_number", "account_name", "is_group", "custom_sap_acct_code"],
        limit_page_length=0,
    )
    erp_numbers = {str(row.account_number): row for row in erp_rows if row.account_number}
    missing = sorted(code for code in sap_accounts if code not in erp_numbers and code not in ROOT_BUCKET_MAP and code not in INACTIVE_ROOT_BUCKETS)
    return {
        "sap_oact": len(sap_accounts),
        "erp_accounts": len(erp_rows),
        "mapped_numbered_accounts": len(erp_numbers),
        "missing_unmapped_sap_codes": missing,
        "custom_sap_acct_code_populated": sum(1 for row in erp_rows if row.custom_sap_acct_code),
    }


def main(dry_run: bool = True) -> None:
    connect_frappe(frappe)
    try:
        sap_accounts = _sap_oact()
        _ensure_account_custom_field(dry_run)
        _populate_account_mappings(dry_run)
        _create_missing_postable_accounts(sap_accounts, dry_run)
        _fix_account_name(dry_run)
        if dry_run:
            print("dry run complete")
            return
        frappe.db.commit()
        print(json.dumps(_audit(), indent=2, sort_keys=True))
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main(dry_run=False)

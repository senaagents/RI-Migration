from __future__ import annotations

import json
import sqlite3

import frappe

from llm_migration_config import connect_frappe, COMPANY, SAP_DB, SITE, SITES_PATH

MODE_DEFAULTS = {
    "Cash": ("1203104", "Cash"),
    "Cheque": ("2101012", "Bank"),
    "Wire Transfer": ("2101012", "Bank"),
    "Bank Draft": ("2101012", "Bank"),
    "Credit Card": ("2101012", "Bank"),
}

CASH_ACCOUNT_CODES = {"1203101", "1203102", "1203104"}


def _sap_rows(table: str) -> list[dict]:
    with sqlite3.connect(SAP_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select payload_json from sap_raw_rows where source_table=?", (table,)).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _account_by_sap_code(code: str | None) -> str | None:
    if not code:
        return None
    return frappe.db.get_value("Account", {"company": COMPANY, "custom_sap_acct_code": str(code)}, "name")


def _ensure_bank(row: dict, dry_run: bool) -> bool:
    bank_name = row.get("BankName") or row.get("BankCode")
    if not bank_name or frappe.db.exists("Bank", bank_name):
        return False
    print(f"create Bank {bank_name}")
    if dry_run:
        return True
    doc = frappe.get_doc(
        {
            "doctype": "Bank",
            "bank_name": bank_name,
            "swift_number": row.get("SwiftNum"),
        }
    )
    doc.insert(ignore_permissions=True)
    return True


def _bank_name_by_code() -> dict[str, str]:
    result = {}
    for row in _sap_rows("ODSC"):
        if row.get("BankCode") and row.get("BankName"):
            result[str(row["BankCode"])] = row["BankName"]
    return result


def _ensure_bank_account(row: dict, bank_by_code: dict[str, str], dry_run: bool) -> bool:
    account = _account_by_sap_code(row.get("GLAccount"))
    if not account:
        raise RuntimeError(f"No ERPNext account mapped for SAP house-bank GLAccount {row.get('GLAccount')}")

    bank_name = bank_by_code.get(str(row.get("BankCode")), row.get("BankCode"))
    account_no = str(row.get("Account") or "")
    account_name = row.get("AcctName") or frappe.get_cached_value("Account", account, "account_name")
    existing = frappe.db.exists(
        "Bank Account",
        {"company": COMPANY, "account": account, "is_company_account": 1},
    )
    if existing:
        return False

    print(f"create Bank Account {account_name} {account_no} -> {account}")
    if dry_run:
        return True

    frappe.get_doc(
        {
            "doctype": "Bank Account",
            "account_name": f"{account_name} {account_no}".strip()[:140],
            "bank": bank_name,
            "bank_account_no": account_no[:64],
            "branch_code": row.get("Branch") or row.get("SwiftNum"),
            "is_company_account": 1,
            "company": COMPANY,
            "account": account,
            "is_default": 1 if row.get("BankCode") == "HDFC" else 0,
        }
    ).insert(ignore_permissions=True)
    return True


def _ensure_account_type(account: str, account_type: str, dry_run: bool) -> bool:
    current = frappe.db.get_value("Account", account, "account_type")
    if current == account_type:
        return False
    print(f"set Account {account} account_type={account_type}")
    if dry_run:
        return True
    frappe.db.set_value("Account", account, "account_type", account_type, update_modified=False)
    return True


def _ensure_mode_defaults(dry_run: bool) -> int:
    changed = 0
    for mode, (sap_account_code, mode_type) in MODE_DEFAULTS.items():
        account = _account_by_sap_code(sap_account_code)
        if not account:
            raise RuntimeError(f"No ERPNext account mapped for mode {mode} SAP account {sap_account_code}")
        if not frappe.db.exists("Mode of Payment", mode):
            print(f"create Mode of Payment {mode}")
            changed += 1
            if not dry_run:
                frappe.get_doc(
                    {
                        "doctype": "Mode of Payment",
                        "mode_of_payment": mode,
                        "enabled": 1,
                        "type": mode_type,
                    }
                ).insert(ignore_permissions=True)
        else:
            current_type = frappe.db.get_value("Mode of Payment", mode, "type")
            if current_type != mode_type:
                print(f"set Mode of Payment {mode} type={mode_type}")
                changed += 1
                if not dry_run:
                    frappe.db.set_value("Mode of Payment", mode, "type", mode_type, update_modified=False)

        existing_for_company = frappe.db.exists(
            "Mode of Payment Account",
            {"parent": mode, "company": COMPANY},
        )
        if existing_for_company:
            current_account = frappe.db.get_value(
                "Mode of Payment Account", existing_for_company, "default_account"
            )
            if current_account != account:
                print(f"update Mode of Payment Account {mode} -> {account}")
                changed += 1
                if not dry_run:
                    frappe.db.set_value(
                        "Mode of Payment Account",
                        existing_for_company,
                        "default_account",
                        account,
                        update_modified=False,
                    )
        else:
            print(f"add Mode of Payment Account {mode} -> {account}")
            changed += 1
            if not dry_run:
                doc = frappe.get_doc("Mode of Payment", mode)
                doc.append("accounts", {"company": COMPANY, "default_account": account})
                doc.save(ignore_permissions=True)
    return changed


def _audit() -> dict:
    return {
        "banks": frappe.db.count("Bank"),
        "company_bank_accounts": frappe.db.count(
            "Bank Account", {"company": COMPANY, "is_company_account": 1}
        ),
        "mode_accounts": frappe.get_all(
            "Mode of Payment Account",
            fields=["parent", "company", "default_account"],
            filters={"company": COMPANY},
            order_by="parent, default_account",
            limit_page_length=0,
        ),
    }


def main(dry_run: bool = True) -> None:
    connect_frappe(frappe)
    try:
        created = {
            "banks": 0,
            "bank_accounts": 0,
            "account_types": 0,
            "mode_defaults": 0,
        }
        for row in _sap_rows("ODSC"):
            created["banks"] += int(_ensure_bank(row, dry_run))

        bank_by_code = _bank_name_by_code()
        for row in _sap_rows("DSC1"):
            account = _account_by_sap_code(row.get("GLAccount"))
            if account:
                created["account_types"] += int(_ensure_account_type(account, "Bank", dry_run))
            created["bank_accounts"] += int(_ensure_bank_account(row, bank_by_code, dry_run))

        for sap_code in CASH_ACCOUNT_CODES:
            account = _account_by_sap_code(sap_code)
            if account:
                created["account_types"] += int(_ensure_account_type(account, "Cash", dry_run))

        created["mode_defaults"] += _ensure_mode_defaults(dry_run)

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

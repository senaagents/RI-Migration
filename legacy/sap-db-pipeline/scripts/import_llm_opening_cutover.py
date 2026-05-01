from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.utils import getdate

from llm_migration_config import COMPANY, SITE, connect_frappe


CUTOFF_DATE = "2026-03-31"
OPENING_DATE = "2026-04-01"
COMPANY_CURRENCY = "INR"
TEMPORARY_OPENING_ACCOUNT = "Temporary Opening - L"
DEFAULT_COST_CENTER = "Main - L"
OPENING_DIR = Path("/Users/aakashchid/workshop/sap-db-pipeline/migration-control/opening-cutover")
PARTY_LINES_CSV = OPENING_DIR / f"party-open-lines-as-of-{CUTOFF_DATE}.csv"

AR_TYPES = {"13", "14", "24"}
AP_TYPES = {"18", "19", "46"}
SOURCE_TABLE = {
    "13": "OINV",
    "14": "ORIN",
    "18": "OPCH",
    "19": "ORPC",
    "24": "ORCT",
    "46": "OVPM",
    "30": "OJDT",
}


def _load_rows(limit: int | None = None) -> list[dict[str, str]]:
    with PARTY_LINES_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda row: (row["Account"], row["PartyOrShortName"], row["RefDate"], int(row["TransId"]), int(row["Line_ID"])))
    if limit:
        return rows[:limit]
    return rows


def _ensure_custom_fields(dry_run: bool) -> int:
    fields: list[tuple[str, dict[str, Any]]] = []
    for doctype in ("Sales Invoice", "Purchase Invoice"):
        fields.extend(
            [
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_opening_key",
                        "label": "SAP Opening Key",
                        "fieldtype": "Data",
                        "unique": 1,
                        "read_only": 1,
                        "insert_after": "naming_series",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_source_table",
                        "label": "SAP Source Table",
                        "fieldtype": "Data",
                        "read_only": 1,
                        "insert_after": "custom_sap_opening_key",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_docentry",
                        "label": "SAP DocEntry",
                        "fieldtype": "Data",
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
                        "fieldname": "custom_sap_trans_id",
                        "label": "SAP TransId",
                        "fieldtype": "Data",
                        "read_only": 1,
                        "insert_after": "custom_sap_docnum",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_opening_cutoff",
                        "label": "SAP Opening Cutoff",
                        "fieldtype": "Date",
                        "read_only": 1,
                        "insert_after": "custom_sap_trans_id",
                    },
                ),
                (
                    doctype,
                    {
                        "fieldname": "custom_sap_original_due_date",
                        "label": "SAP Original Due Date",
                        "fieldtype": "Date",
                        "read_only": 1,
                        "insert_after": "custom_sap_opening_cutoff",
                    },
                ),
            ]
        )

    fields.extend(
        [
            (
                "Journal Entry",
                {
                    "fieldname": "custom_sap_opening_key",
                    "label": "SAP Opening Key",
                    "fieldtype": "Data",
                    "unique": 1,
                    "read_only": 1,
                    "insert_after": "naming_series",
                },
            ),
            (
                "Journal Entry",
                {
                    "fieldname": "custom_sap_opening_cutoff",
                    "label": "SAP Opening Cutoff",
                    "fieldtype": "Date",
                    "read_only": 1,
                    "insert_after": "custom_sap_opening_key",
                },
            ),
            (
                "Journal Entry Account",
                {
                    "fieldname": "custom_sap_opening_source_key",
                    "label": "SAP Opening Source Key",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "insert_after": "account",
                },
            ),
        ]
    )

    created = 0
    for doctype, field in fields:
        name = f"{doctype}-{field['fieldname']}"
        if frappe.db.exists("Custom Field", name):
            continue
        created += 1
        print(f"create Custom Field {name}")
        if not dry_run:
            create_custom_field(doctype, field, ignore_validate=True)
            frappe.clear_cache(doctype=doctype)
    return created


def _account_map() -> dict[str, frappe._dict]:
    rows = frappe.get_all(
        "Account",
        fields=["name", "custom_sap_acct_code", "account_type"],
        filters={"company": COMPANY, "custom_sap_acct_code": ["is", "set"]},
        limit_page_length=0,
    )
    return {str(row.custom_sap_acct_code): row for row in rows}


def _party_maps() -> tuple[dict[str, str], dict[str, str]]:
    customers = {
        str(row.custom_sap_card_code): row.name
        for row in frappe.get_all(
            "Customer",
            fields=["name", "custom_sap_card_code"],
            filters={"custom_sap_card_code": ["is", "set"]},
            limit_page_length=0,
        )
    }
    suppliers = {
        str(row.custom_sap_card_code): row.name
        for row in frappe.get_all(
            "Supplier",
            fields=["name", "custom_sap_card_code"],
            filters={"custom_sap_card_code": ["is", "set"]},
            limit_page_length=0,
        )
    }
    return customers, suppliers


def _source_key(row: dict[str, str]) -> str:
    return f"OPEN-{CUTOFF_DATE}-{row['TransType']}-{row['TransId']}-{row['Line_ID']}"


def _source_table(row: dict[str, str]) -> str:
    return SOURCE_TABLE.get(str(row["TransType"]), f"TransType-{row['TransType']}")


def _amounts(row: dict[str, str]) -> tuple[float, str, float]:
    base_amount = abs(float(row["OpenSignedAsOfCutoff"] or 0))
    fc_amount = abs(float(row.get("OpenFCSignedAsOfCutoff") or 0))
    currency = (row.get("FCCurrency") or row.get("SourceCurrency") or COMPANY_CURRENCY).strip() or COMPANY_CURRENCY
    if currency != COMPANY_CURRENCY and fc_amount > 0.005:
        return fc_amount, currency, base_amount / fc_amount
    return base_amount, COMPANY_CURRENCY, 1.0


def _party_for_row(row: dict[str, str], accounts: dict[str, frappe._dict], customers: dict[str, str], suppliers: dict[str, str]) -> tuple[str, str]:
    account = accounts[row["Account"]]
    sap_party = row["PartyOrShortName"]
    if account.account_type == "Receivable":
        return "Customer", customers[sap_party]
    if account.account_type == "Payable":
        return "Supplier", suppliers[sap_party]
    raise RuntimeError(f"{row['Account']} is not Receivable/Payable")


def _erp_account(row: dict[str, str], accounts: dict[str, frappe._dict]) -> str:
    return accounts[row["Account"]].name


def _is_standard_opening_invoice(row: dict[str, str]) -> bool:
    signed = float(row["OpenSignedAsOfCutoff"] or 0)
    trans_type = str(row["TransType"])
    return (trans_type == "13" and signed > 0) or (trans_type == "18" and signed < 0)


def _invoice_exists(doctype: str, key: str) -> bool:
    return bool(frappe.db.exists(doctype, {"custom_sap_opening_key": key}))


def _journal_exists(key: str) -> bool:
    return bool(frappe.db.exists("Journal Entry", {"custom_sap_opening_key": key}))


def _build_opening_invoice(row: dict[str, str], maps: dict[str, Any]) -> frappe.model.document.Document:
    trans_type = str(row["TransType"])
    doctype = "Sales Invoice" if trans_type == "13" else "Purchase Invoice"
    party_type, party = _party_for_row(row, maps["accounts"], maps["customers"], maps["suppliers"])
    account = _erp_account(row, maps["accounts"])
    amount, currency, conversion_rate = _amounts(row)
    source_key = _source_key(row)

    doc_data: dict[str, Any] = {
        "doctype": doctype,
        "company": COMPANY,
        "is_opening": "Yes",
        "set_posting_time": 1,
        "posting_date": OPENING_DATE,
        "due_date": getdate(OPENING_DATE),
        "currency": currency,
        "conversion_rate": conversion_rate,
        "disable_rounded_total": 1,
        "ignore_pricing_rule": 1,
        "update_stock": 0,
        "cost_center": DEFAULT_COST_CENTER,
        "remarks": f"SAP opening {row['SourceKind']} {row.get('SourceDocNum') or row['BaseRef']} as of {CUTOFF_DATE}",
        "custom_sap_opening_key": source_key,
        "custom_sap_source_table": _source_table(row),
        "custom_sap_docentry": row.get("SourceDocEntry"),
        "custom_sap_docnum": row.get("SourceDocNum"),
        "custom_sap_trans_id": row.get("TransId"),
        "custom_sap_opening_cutoff": CUTOFF_DATE,
        "custom_sap_original_due_date": getdate(row["DueDate"] or OPENING_DATE),
    }
    item = {
        "item_name": f"SAP Opening {party_type} Balance",
        "description": f"{row['SourceKind']} {row.get('SourceDocNum') or row['BaseRef']} open as of {CUTOFF_DATE}",
        "qty": 1,
        "rate": amount,
        "uom": "Nos",
        "conversion_factor": 1,
        "cost_center": DEFAULT_COST_CENTER,
    }
    if doctype == "Sales Invoice":
        doc_data.update({"customer": party, "debit_to": account, "is_pos": 0})
        item["income_account"] = TEMPORARY_OPENING_ACCOUNT
    else:
        doc_data.update(
            {
                "supplier": party,
                "credit_to": account,
                "bill_no": f"SAP-{_source_table(row)}-{row.get('SourceDocNum') or row['BaseRef']}",
                "bill_date": getdate(row["RefDate"] or OPENING_DATE),
            }
        )
        item["expense_account"] = TEMPORARY_OPENING_ACCOUNT
    doc_data["items"] = [item]
    return frappe.get_doc(doc_data)


def _append_signed_row(
    doc: frappe.model.document.Document,
    *,
    account: str,
    signed_amount: float,
    party_type: str | None = None,
    party: str | None = None,
    source_key: str | None = None,
    remark: str | None = None,
) -> None:
    row: dict[str, Any] = {
        "account": account,
        "debit_in_account_currency": signed_amount if signed_amount > 0 else 0,
        "credit_in_account_currency": abs(signed_amount) if signed_amount < 0 else 0,
        "user_remark": remark,
    }
    if party_type and party:
        row["party_type"] = party_type
        row["party"] = party
    if source_key:
        row["custom_sap_opening_source_key"] = source_key
    doc.append("accounts", row)


def _build_party_balance_journal(row: dict[str, str], maps: dict[str, Any]) -> frappe.model.document.Document:
    source_key = _source_key(row)
    signed = float(row["OpenSignedAsOfCutoff"] or 0)
    party_type, party = _party_for_row(row, maps["accounts"], maps["customers"], maps["suppliers"])
    account = _erp_account(row, maps["accounts"])
    doc = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": COMPANY,
            "posting_date": OPENING_DATE,
            "is_opening": "Yes",
            "custom_sap_opening_key": source_key,
            "custom_sap_opening_cutoff": CUTOFF_DATE,
            "user_remark": f"SAP opening party balance {source_key}: {row['SourceKind']}",
        }
    )
    _append_signed_row(
        doc,
        account=account,
        signed_amount=signed,
        party_type=party_type,
        party=party,
        source_key=source_key,
        remark=f"SAP {row['SourceKind']} open line",
    )
    _append_signed_row(
        doc,
        account=TEMPORARY_OPENING_ACCOUNT,
        signed_amount=-signed,
        source_key=source_key,
        remark="Temporary opening offset",
    )
    return doc


def _build_walkback_journal(batch_rows: list[dict[str, str]], maps: dict[str, Any], batch_no: int) -> frappe.model.document.Document:
    key = f"OPEN-CLEAR-{CUTOFF_DATE}-{batch_no:05d}"
    doc = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": COMPANY,
            "posting_date": OPENING_DATE,
            "is_opening": "Yes",
            "custom_sap_opening_key": key,
            "custom_sap_opening_cutoff": CUTOFF_DATE,
            "user_remark": f"SAP opening walk-back clearing batch {batch_no} as of {CUTOFF_DATE}",
        }
    )
    temp_signed = 0.0
    for row in batch_rows:
        signed = float(row["OpenSignedAsOfCutoff"] or 0)
        source_key = _source_key(row)
        party_type, party = _party_for_row(row, maps["accounts"], maps["customers"], maps["suppliers"])
        _append_signed_row(
            doc,
            account=_erp_account(row, maps["accounts"]),
            signed_amount=-signed,
            party_type=party_type,
            party=party,
            source_key=source_key,
            remark=f"Walk back historical party balance for {source_key}",
        )
        temp_signed += signed
    _append_signed_row(
        doc,
        account=TEMPORARY_OPENING_ACCOUNT,
        signed_amount=temp_signed,
        source_key=key,
        remark="Temporary opening clearing control",
    )
    return doc


def _validate_maps(rows: list[dict[str, str]], maps: dict[str, Any]) -> dict[str, list[str]]:
    missing: dict[str, set[str]] = {"accounts": set(), "customers": set(), "suppliers": set()}
    for row in rows:
        account = maps["accounts"].get(row["Account"])
        if not account:
            missing["accounts"].add(row["Account"])
            continue
        sap_party = row["PartyOrShortName"]
        if account.account_type == "Receivable" and sap_party not in maps["customers"]:
            missing["customers"].add(sap_party)
        if account.account_type == "Payable" and sap_party not in maps["suppliers"]:
            missing["suppliers"].add(sap_party)
    return {key: sorted(value) for key, value in missing.items()}


def _chunks(rows: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def main(dry_run: bool = True, limit: int | None = None, batch_size: int = 100) -> None:
    if SITE != "llmnew.localhost":
        raise RuntimeError(f"Refusing to import opening cutover into {SITE}; set SAP_TARGET_SITE=llmnew.localhost")
    if limit and not dry_run:
        raise RuntimeError("Refusing a partial real opening import. Run dry-run with --limit, or real import without --limit.")

    connect_frappe(frappe)
    try:
        rows = _load_rows(limit)
        created_fields = _ensure_custom_fields(dry_run)
        maps: dict[str, Any] = {}
        maps["accounts"] = _account_map()
        maps["customers"], maps["suppliers"] = _party_maps()
        missing = _validate_maps(rows, maps)
        if any(missing.values()):
            raise RuntimeError(f"Missing mappings: {json.dumps(missing, indent=2, sort_keys=True)}")

        invoice_rows = [row for row in rows if _is_standard_opening_invoice(row)]
        party_journal_rows = [row for row in rows if not _is_standard_opening_invoice(row)]
        already_invoices = 0
        already_party_journals = 0
        would_create_invoices = 0
        would_create_party_journals = 0
        would_create_walkbacks = 0

        if dry_run:
            for row in invoice_rows:
                doctype = "Sales Invoice" if row["TransType"] == "13" else "Purchase Invoice"
                if _invoice_exists(doctype, _source_key(row)):
                    already_invoices += 1
                else:
                    would_create_invoices += 1
            for row in party_journal_rows:
                if _journal_exists(_source_key(row)):
                    already_party_journals += 1
                else:
                    would_create_party_journals += 1
            for batch_no, _batch in enumerate(_chunks(rows, batch_size), start=1):
                if not _journal_exists(f"OPEN-CLEAR-{CUTOFF_DATE}-{batch_no:05d}"):
                    would_create_walkbacks += 1
            print(
                json.dumps(
                    {
                        "site": SITE,
                        "custom_fields_to_create": created_fields,
                        "source_rows": len(rows),
                        "opening_invoice_rows": len(invoice_rows),
                        "party_journal_rows": len(party_journal_rows),
                        "already_invoices": already_invoices,
                        "already_party_journals": already_party_journals,
                        "would_create_invoices": would_create_invoices,
                        "would_create_party_journals": would_create_party_journals,
                        "would_create_walkback_journals": would_create_walkbacks,
                        "batch_size": batch_size,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return

        batch_no = 0
        created_invoices = 0
        created_party_journals = 0
        created_walkbacks = 0
        for batch in _chunks(rows, batch_size):
            batch_no += 1
            for row in batch:
                source_key = _source_key(row)
                if _is_standard_opening_invoice(row):
                    doctype = "Sales Invoice" if row["TransType"] == "13" else "Purchase Invoice"
                    if _invoice_exists(doctype, source_key):
                        continue
                    doc = _build_opening_invoice(row, maps)
                    doc.insert(ignore_permissions=True)
                    doc.submit()
                    created_invoices += 1
                else:
                    if _journal_exists(source_key):
                        continue
                    doc = _build_party_balance_journal(row, maps)
                    doc.insert(ignore_permissions=True)
                    doc.submit()
                    created_party_journals += 1

            clear_key = f"OPEN-CLEAR-{CUTOFF_DATE}-{batch_no:05d}"
            if not _journal_exists(clear_key):
                clear_doc = _build_walkback_journal(batch, maps, batch_no)
                clear_doc.insert(ignore_permissions=True)
                clear_doc.submit()
                created_walkbacks += 1

            frappe.db.commit()
            print(
                f"committed batch {batch_no}: invoices={created_invoices}, "
                f"party_journals={created_party_journals}, walkbacks={created_walkbacks}"
            )

        print(
            json.dumps(
                {
                    "custom_fields_created": created_fields,
                    "source_rows": len(rows),
                    "created_invoices": created_invoices,
                    "created_party_journals": created_party_journals,
                    "created_walkback_journals": created_walkbacks,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        frappe.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import LLM April 1 opening AR/AP cutover documents.")
    parser.add_argument("--commit", action="store_true", help="Write documents to ERPNext.")
    parser.add_argument("--limit", type=int, default=None, help="Dry-run only: limit source rows.")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    main(dry_run=not args.commit, limit=args.limit, batch_size=args.batch_size)

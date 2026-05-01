from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.utils import flt

from llm_migration_config import COMPANY, SITE, connect_frappe


CUTOFF_DATE = "2026-03-31"
OPENING_DATE = "2026-04-01"
OPENING_TIME = "00:00:00"
TEMPORARY_OPENING_ACCOUNT = "Temporary Opening - L"
DEFAULT_COST_CENTER = "Main - L"
OPENING_DIR = Path("/Users/aakashchid/workshop/sap-db-pipeline/migration-control/opening-cutover")
STOCK_CSV = OPENING_DIR / f"stock-opening-as-of-{CUTOFF_DATE}.csv"


def _load_rows(limit: int | None = None) -> list[dict[str, str]]:
    with STOCK_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda row: (row["Warehouse"], row["ItemCode"], row["InventoryAccount"]))
    if limit:
        return rows[:limit]
    return rows


def _aggregate_item_warehouse_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["ItemCode"], row["Warehouse"])
        bucket = grouped.setdefault(
            key,
            {
                "ItemCode": row["ItemCode"],
                "Warehouse": row["Warehouse"],
                "InventoryAccount": set(),
                "InventoryAccountName": set(),
                "Qty": 0.0,
                "StockValue": 0.0,
                "MovementRows": 0,
                "SourceRows": 0,
            },
        )
        if row.get("InventoryAccount"):
            bucket["InventoryAccount"].add(row["InventoryAccount"])
        if row.get("InventoryAccountName"):
            bucket["InventoryAccountName"].add(row["InventoryAccountName"])
        bucket["Qty"] += float(row["Qty"] or 0)
        bucket["StockValue"] += float(row["StockValue"] or 0)
        bucket["MovementRows"] += int(float(row.get("MovementRows") or 0))
        bucket["SourceRows"] += 1

    aggregated: list[dict[str, str]] = []
    for bucket in grouped.values():
        accounts = sorted(bucket["InventoryAccount"])
        account_names = sorted(bucket["InventoryAccountName"])
        aggregated.append(
            {
                "ItemCode": bucket["ItemCode"],
                "Warehouse": bucket["Warehouse"],
                "InventoryAccount": accounts[0] if len(accounts) == 1 else "MULTI" if accounts else "",
                "InventoryAccountName": account_names[0]
                if len(account_names) == 1
                else " / ".join(account_names[:4]),
                "Qty": str(round(bucket["Qty"], 9)),
                "StockValue": str(round(bucket["StockValue"], 9)),
                "ValuationRate": "",
                "MovementRows": str(bucket["MovementRows"]),
                "SourceRows": str(bucket["SourceRows"]),
            }
        )
    aggregated.sort(key=lambda row: (row["Warehouse"], row["ItemCode"], row["InventoryAccount"]))
    return aggregated


def _ensure_custom_fields(dry_run: bool) -> int:
    fields: list[tuple[str, dict[str, Any]]] = [
        (
            "Stock Reconciliation",
            {
                "fieldname": "custom_sap_stock_opening_key",
                "label": "SAP Stock Opening Key",
                "fieldtype": "Data",
                "unique": 1,
                "read_only": 1,
                "insert_after": "purpose",
            },
        ),
        (
            "Stock Reconciliation",
            {
                "fieldname": "custom_sap_opening_cutoff",
                "label": "SAP Opening Cutoff",
                "fieldtype": "Date",
                "read_only": 1,
                "insert_after": "custom_sap_stock_opening_key",
            },
        ),
        (
            "Stock Entry",
            {
                "fieldname": "custom_sap_stock_opening_key",
                "label": "SAP Stock Opening Key",
                "fieldtype": "Data",
                "unique": 1,
                "read_only": 1,
                "insert_after": "stock_entry_type",
            },
        ),
        (
            "Stock Entry",
            {
                "fieldname": "custom_sap_opening_cutoff",
                "label": "SAP Opening Cutoff",
                "fieldtype": "Date",
                "read_only": 1,
                "insert_after": "custom_sap_stock_opening_key",
            },
        ),
        (
            "Journal Entry",
            {
                "fieldname": "custom_sap_stock_opening_key",
                "label": "SAP Stock Opening Key",
                "fieldtype": "Data",
                "unique": 1,
                "read_only": 1,
                "insert_after": "custom_sap_opening_cutoff",
            },
        ),
    ]
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


def _warehouse_map() -> dict[str, str]:
    return {
        str(row.custom_sap_whs_code): row.name
        for row in frappe.get_all(
            "Warehouse",
            fields=["name", "custom_sap_whs_code"],
            filters={"company": COMPANY, "custom_sap_whs_code": ["is", "set"]},
            limit_page_length=0,
        )
    }


def _item_map() -> dict[str, frappe._dict]:
    return {
        row.item_code: row
        for row in frappe.get_all(
            "Item",
            fields=[
                "item_code",
                "is_stock_item",
                "disabled",
                "has_batch_no",
                "has_serial_no",
                "allow_negative_stock",
            ],
            limit_page_length=0,
        )
    }


def _validate_maps(rows: list[dict[str, str]], warehouses: dict[str, str], items: dict[str, frappe._dict]) -> dict[str, list[str]]:
    missing_items = sorted({row["ItemCode"] for row in rows if row["ItemCode"] not in items})
    nonstock_items = sorted(
        {row["ItemCode"] for row in rows if row["ItemCode"] in items and not items[row["ItemCode"]].is_stock_item}
    )
    batch_or_serial = sorted(
        {
            row["ItemCode"]
            for row in rows
            if row["ItemCode"] in items
            and (items[row["ItemCode"]].has_batch_no or items[row["ItemCode"]].has_serial_no)
        }
    )
    missing_warehouses = sorted({row["Warehouse"] for row in rows if row["Warehouse"] not in warehouses})
    return {
        "missing_items": missing_items,
        "nonstock_items": nonstock_items,
        "batch_or_serial_items": batch_or_serial,
        "missing_warehouses": missing_warehouses,
    }


def _chunks(rows: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def _valuation_rate(row: dict[str, str]) -> float:
    qty = abs(float(row["Qty"] or 0))
    value = abs(float(row["StockValue"] or 0))
    if qty <= 0 or value <= 0:
        return 0.0
    return value / qty


def _enable_required_items(rows: list[dict[str, str]], dry_run: bool) -> dict[str, int]:
    disabled_items = sorted(
        {
            row["ItemCode"]
            for row in rows
            if frappe.db.get_value("Item", row["ItemCode"], "disabled")
        }
    )
    negative_items = sorted({row["ItemCode"] for row in rows if float(row["Qty"] or 0) < 0})
    negative_items_to_update = [
        item for item in negative_items if not frappe.db.get_value("Item", item, "allow_negative_stock")
    ]
    negative_item_rates: dict[str, float] = {}
    for row in rows:
        if float(row["Qty"] or 0) >= 0:
            continue
        negative_item_rates[row["ItemCode"]] = max(
            negative_item_rates.get(row["ItemCode"], 0.0),
            _valuation_rate(row) or 0.000001,
        )
    valuation_rate_updates = []
    for item, rate in sorted(negative_item_rates.items()):
        current_rate = float(frappe.db.get_value("Item", item, "valuation_rate") or 0)
        if current_rate <= 0 and rate > 0:
            valuation_rate_updates.append((item, rate))

    if disabled_items:
        print(f"enable {len(disabled_items)} disabled stock items for opening stock")
        if not dry_run:
            for item in disabled_items:
                frappe.db.set_value("Item", item, "disabled", 0, update_modified=False)

    if negative_items_to_update:
        print(f"allow negative stock on {len(negative_items_to_update)} items with negative SAP opening balances")
        if not dry_run:
            for item in negative_items_to_update:
                frappe.db.set_value("Item", item, "allow_negative_stock", 1, update_modified=False)

    if valuation_rate_updates:
        print(f"set valuation rate on {len(valuation_rate_updates)} negative-opening items")
        if not dry_run:
            for item, rate in valuation_rate_updates:
                frappe.db.set_value("Item", item, "valuation_rate", rate, update_modified=False)

    return {
        "disabled_items_to_enable": len(disabled_items),
        "negative_stock_items_to_update": len(negative_items_to_update),
        "negative_item_valuation_rates_to_update": len(valuation_rate_updates),
    }


def _stock_reconciliation_exists(key: str) -> bool:
    return bool(frappe.db.exists("Stock Reconciliation", {"custom_sap_stock_opening_key": key}))


def _stock_entry_exists(key: str) -> bool:
    return bool(frappe.db.exists("Stock Entry", {"custom_sap_stock_opening_key": key}))


def _stock_clearing_exists(key: str) -> bool:
    return bool(frappe.db.exists("Journal Entry", {"custom_sap_stock_opening_key": key}))


def _build_stock_reconciliation(batch: list[dict[str, str]], batch_no: int, warehouses: dict[str, str]) -> frappe.model.document.Document:
    key = f"OPEN-STOCK-RECO-AGG-{CUTOFF_DATE}-{batch_no:05d}"
    doc = frappe.get_doc(
        {
            "doctype": "Stock Reconciliation",
            "purpose": "Opening Stock",
            "company": COMPANY,
            "set_posting_time": 1,
            "posting_date": OPENING_DATE,
            "posting_time": OPENING_TIME,
            "expense_account": TEMPORARY_OPENING_ACCOUNT,
            "cost_center": DEFAULT_COST_CENTER,
            "custom_sap_stock_opening_key": key,
            "custom_sap_opening_cutoff": CUTOFF_DATE,
        }
    )
    for row in batch:
        rate = _valuation_rate(row)
        doc.append(
            "items",
            {
                "item_code": row["ItemCode"],
                "warehouse": warehouses[row["Warehouse"]],
                "qty": float(row["Qty"]),
                "valuation_rate": rate,
                "allow_zero_valuation_rate": 1 if rate == 0 else 0,
            },
        )
    return doc


def _build_negative_stock_entry(batch: list[dict[str, str]], batch_no: int, warehouses: dict[str, str]) -> frappe.model.document.Document:
    key = f"OPEN-STOCK-NEG-AGG-{CUTOFF_DATE}-{batch_no:05d}"
    doc = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Issue",
            "purpose": "Material Issue",
            "company": COMPANY,
            "is_opening": "Yes",
            "set_posting_time": 1,
            "posting_date": OPENING_DATE,
            "posting_time": OPENING_TIME,
            "custom_sap_stock_opening_key": key,
            "custom_sap_opening_cutoff": CUTOFF_DATE,
        }
    )
    for row in batch:
        rate = _valuation_rate(row)
        doc.append(
            "items",
            {
                "item_code": row["ItemCode"],
                "s_warehouse": warehouses[row["Warehouse"]],
                "qty": abs(float(row["Qty"])),
                "basic_rate": rate,
                "valuation_rate": rate,
                "allow_zero_valuation_rate": 1 if rate == 0 else 0,
                "expense_account": TEMPORARY_OPENING_ACCOUNT,
                "cost_center": DEFAULT_COST_CENTER,
            },
        )
    return doc


def _make_stock_clearing(vouchers: list[tuple[str, str]], key: str) -> frappe.model.document.Document | None:
    if not vouchers:
        return None
    conditions = " OR ".join(["(voucher_type=%s AND voucher_no=%s)" for _ in vouchers])
    params: list[str] = []
    for voucher_type, voucher_no in vouchers:
        params.extend([voucher_type, voucher_no])
    rows = frappe.db.sql(
        f"""
        SELECT account, ROUND(SUM(debit-credit), 9) AS signed
        FROM `tabGL Entry`
        WHERE company=%s
          AND is_cancelled=0
          AND ({conditions})
        GROUP BY account
        HAVING ABS(signed) > 0.000001
        ORDER BY account
        """,
        tuple([COMPANY, *params]),
        as_dict=True,
    )
    if not rows:
        return None
    doc = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "voucher_type": "Periodic Accounting Entry",
            "company": COMPANY,
            "posting_date": OPENING_DATE,
            "is_opening": "Yes",
            "custom_sap_stock_opening_key": key,
            "user_remark": f"SAP stock opening GL walk-back {key}",
        }
    )
    for row in rows:
        signed = float(row.signed or 0)
        doc.append(
            "accounts",
            {
                "account": row.account,
                "debit_in_account_currency": abs(signed) if signed < 0 else 0,
                "credit_in_account_currency": signed if signed > 0 else 0,
                "user_remark": "Reverse stock opening GL effect; stock ledger remains",
            },
        )
    return doc


def _stock_value_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    positive = [row for row in rows if float(row["Qty"] or 0) > 0]
    negative = [row for row in rows if float(row["Qty"] or 0) < 0]
    value_only = [
        row
        for row in rows
        if abs(float(row["Qty"] or 0)) <= 0.000001 and abs(float(row["StockValue"] or 0)) > 0.005
    ]
    neg_positive_value = [row for row in negative if float(row["StockValue"] or 0) > 0.005]
    return {
        "source_rows": len(rows),
        "positive_rows": len(positive),
        "negative_rows": len(negative),
        "zero_qty_value_only_rows": len(value_only),
        "zero_qty_value_only_value": round(sum(float(row["StockValue"] or 0) for row in value_only), 2),
        "positive_qty": round(sum(float(row["Qty"] or 0) for row in positive), 6),
        "positive_value": round(sum(float(row["StockValue"] or 0) for row in positive), 2),
        "negative_qty": round(sum(float(row["Qty"] or 0) for row in negative), 6),
        "negative_value": round(sum(float(row["StockValue"] or 0) for row in negative), 2),
        "negative_qty_positive_value_rows": len(neg_positive_value),
        "negative_qty_positive_value": round(sum(float(row["StockValue"] or 0) for row in neg_positive_value), 2),
    }


def main(dry_run: bool = True, limit: int | None = None, batch_size: int = 100) -> None:
    if SITE != "llmnew.localhost":
        raise RuntimeError(f"Refusing to import stock opening into {SITE}; set SAP_TARGET_SITE=llmnew.localhost")
    if batch_size > 100:
        raise RuntimeError("Stock Reconciliation queues async above 100 rows; use --batch-size <= 100")
    if limit and not dry_run:
        raise RuntimeError("Refusing partial real stock opening import. Use --limit only for dry-run.")

    connect_frappe(frappe)
    try:
        raw_rows = _load_rows(limit)
        rows = _aggregate_item_warehouse_rows(raw_rows)
        custom_fields = _ensure_custom_fields(dry_run)
        warehouses = _warehouse_map()
        items = _item_map()
        missing = _validate_maps(rows, warehouses, items)
        if any(missing.values()):
            raise RuntimeError(f"Missing stock opening mappings: {json.dumps(missing, indent=2, sort_keys=True)}")

        setup_updates = _enable_required_items(rows, dry_run)
        positive_rows = [row for row in rows if float(row["Qty"] or 0) > 0]
        negative_rows = [row for row in rows if float(row["Qty"] or 0) < 0]
        positive_batches = _chunks(positive_rows, batch_size)
        negative_batches = _chunks(negative_rows, batch_size)

        if dry_run:
            print(
                json.dumps(
                    {
                        "site": SITE,
                        "custom_fields_to_create": custom_fields,
                        **setup_updates,
                        "raw_source_rows": len(raw_rows),
                        "aggregated_item_warehouse_rows": len(rows),
                        **_stock_value_summary(rows),
                        "stock_reconciliation_batches": len(positive_batches),
                        "negative_stock_entry_batches": len(negative_batches),
                        "batch_size": batch_size,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return

        created_reconciliations = 0
        created_negative_entries = 0
        created_clearing_entries = 0

        for batch_no, batch in enumerate(positive_batches, start=1):
            vouchers: list[tuple[str, str]] = []
            key = f"OPEN-STOCK-RECO-AGG-{CUTOFF_DATE}-{batch_no:05d}"
            if not _stock_reconciliation_exists(key):
                doc = _build_stock_reconciliation(batch, batch_no, warehouses)
                doc.insert(ignore_permissions=True)
                doc.submit()
                vouchers.append(("Stock Reconciliation", doc.name))
                created_reconciliations += 1

            clearing_key = f"OPEN-STOCK-CLEAR-RECO-AGG-{CUTOFF_DATE}-{batch_no:05d}"
            if vouchers and not _stock_clearing_exists(clearing_key):
                clearing = _make_stock_clearing(vouchers, clearing_key)
                if clearing:
                    clearing.insert(ignore_permissions=True)
                    clearing.submit()
                    created_clearing_entries += 1
            frappe.db.commit()
            print(f"committed stock reconciliation batch {batch_no}/{len(positive_batches)}")

        for batch_no, batch in enumerate(negative_batches, start=1):
            vouchers = []
            key = f"OPEN-STOCK-NEG-AGG-{CUTOFF_DATE}-{batch_no:05d}"
            if not _stock_entry_exists(key):
                doc = _build_negative_stock_entry(batch, batch_no, warehouses)
                doc.insert(ignore_permissions=True)
                doc.submit()
                vouchers.append(("Stock Entry", doc.name))
                created_negative_entries += 1

            clearing_key = f"OPEN-STOCK-CLEAR-NEG-AGG-{CUTOFF_DATE}-{batch_no:05d}"
            if vouchers and not _stock_clearing_exists(clearing_key):
                clearing = _make_stock_clearing(vouchers, clearing_key)
                if clearing:
                    clearing.insert(ignore_permissions=True)
                    clearing.submit()
                    created_clearing_entries += 1
            frappe.db.commit()
            print(f"committed negative stock batch {batch_no}/{len(negative_batches)}")

        print(
            json.dumps(
                {
                    "custom_fields_created": custom_fields,
                    **setup_updates,
                    "raw_source_rows": len(raw_rows),
                    "aggregated_item_warehouse_rows": len(rows),
                    "created_stock_reconciliations": created_reconciliations,
                    "created_negative_stock_entries": created_negative_entries,
                    "created_clearing_entries": created_clearing_entries,
                    **_stock_value_summary(rows),
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        frappe.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import LLM stock opening as of April 1.")
    parser.add_argument("--commit", action="store_true", help="Write documents to ERPNext.")
    parser.add_argument("--limit", type=int, default=None, help="Dry-run only: limit source rows.")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    main(dry_run=not args.commit, limit=args.limit, batch_size=args.batch_size)

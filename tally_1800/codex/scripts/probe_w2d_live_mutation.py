#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from scripts.synthetic_tally_lab import (
    LabError,
    _line_error,
    _status_counts,
    _tally_amount,
    _xml,
    _xml_attr,
    acquire_lock,
    copy_snapshot,
    guard_company,
    import_envelope,
    list_companies,
    master_message,
    post_xml,
    preflight_voucher_number,
    release_lock,
    save_artifact,
)
from tally1800.synthetic_diff import diff_snapshots


DEFAULT_FILES = [
    "TranMgr.1800",
    "VchStatus.1800",
    "StatStatus.1800",
    "Manager.1800",
    "LinkMgr.1800",
    "Aggr.1800",
    "ExtMngr.1800",
    "SecTran.1800",
    "Company.1800",
    "CmpSave.1800",
    "Index.1800",
    "TMESSAGE.TSF",
    "TSTATE.TSF",
]

DEFAULT_STOCK_ITEM = "CODX_SYNT_ITEM_MANUAL1"
DEFAULT_UNIT = "PCS"
DEFAULT_SALES_LEDGER = "CODX_SYNTH_LEDGER_SALXML1A"
DEFAULT_PARTY_LEDGER = "CODX_SYNTH_LEDGER_PARTYXML1A"
DEFAULT_PURCHASE_LEDGER = "CODX_SYNTH_LEDGER_PURXML1A"
DEFAULT_SUPPLIER_LEDGER = "CODX_SYNTH_LEDGER_SUPXML1A"
DEFAULT_BANK_LEDGER = "CODX_SYNTH_LEDGER_BANK2149A"


def _inventory_line(
    *,
    stock_item: str,
    unit: str,
    ledger: str,
    qty: str,
    rate: str,
    amount: str,
    purchase: bool,
) -> str:
    line_amount = f"-{_tally_amount(amount)}" if purchase else _tally_amount(amount)
    deemed = "Yes" if purchase else "No"
    return f"""
<ALLINVENTORYENTRIES.LIST>
<STOCKITEMNAME>{_xml(stock_item)}</STOCKITEMNAME>
<ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>
<RATE>{_xml(rate)}/{_xml(unit)}</RATE>
<AMOUNT>{line_amount}</AMOUNT>
<ACTUALQTY>{_xml(qty)} {_xml(unit)}</ACTUALQTY>
<BILLEDQTY>{_xml(qty)} {_xml(unit)}</BILLEDQTY>
<ACCOUNTINGALLOCATIONS.LIST>
<LEDGERNAME>{_xml(ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>
<AMOUNT>{line_amount}</AMOUNT>
</ACCOUNTINGALLOCATIONS.LIST>
</ALLINVENTORYENTRIES.LIST>"""


def build_two_line_sales(args: argparse.Namespace, suffix: str) -> tuple[str, dict[str, str], list[str]]:
    voucher_number = f"CODX-W2D-SAL2-{suffix}"
    narration = f"CODX_SYNTH_W2D_SALES2_{suffix}"
    amount1 = "91.17"
    amount2 = "182.68"
    total = "273.85"
    voucher_xml = f"""
<VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
<DATE>20260401</DATE>
<VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
<VOUCHERNUMBER>{_xml(voucher_number)}</VOUCHERNUMBER>
<PARTYLEDGERNAME>{_xml(args.party_ledger)}</PARTYLEDGERNAME>
<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
<ISINVOICE>Yes</ISINVOICE>
<NARRATION>{_xml(narration)}</NARRATION>
{_inventory_line(stock_item=args.stock_item, unit=args.unit, ledger=args.sales_ledger, qty="2.10", rate="43.4142857", amount=amount1, purchase=False)}
{_inventory_line(stock_item=args.stock_item, unit=args.unit, ledger=args.sales_ledger, qty="5.20", rate="35.1307692", amount=amount2, purchase=False)}
<LEDGERENTRIES.LIST>
<LEDGERNAME>{_xml(args.party_ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<AMOUNT>-{_tally_amount(total)}</AMOUNT>
</LEDGERENTRIES.LIST>
</VOUCHER>"""
    meta = {
        "voucher_number": voucher_number,
        "voucher_type": "Sales",
        "narration": narration,
        "date": "2026-04-01",
        "stock_item": args.stock_item,
        "unit": args.unit,
        "sales_ledger": args.sales_ledger,
        "party_ledger": args.party_ledger,
        "qty1": "2.10",
        "rate1": "43.4142857",
        "amount1": amount1,
        "qty2": "5.20",
        "rate2": "35.1307692",
        "amount2": amount2,
        "total": total,
    }
    numbers = ["2.10", amount1, "5.20", amount2, total]
    return import_envelope(args.company, "Vouchers", [master_message(voucher_xml)]), meta, numbers


def build_two_line_purchase(args: argparse.Namespace, suffix: str) -> tuple[str, dict[str, str], list[str]]:
    voucher_number = f"CODX-W2D-PUR2-{suffix}"
    narration = f"CODX_SYNTH_W2D_PURCHASE2_{suffix}"
    amount1 = "77.77"
    amount2 = "199.98"
    total = "277.75"
    voucher_xml = f"""
<VOUCHER VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Invoice Voucher View">
<DATE>20260401</DATE>
<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
<VOUCHERNUMBER>{_xml(voucher_number)}</VOUCHERNUMBER>
<PARTYLEDGERNAME>{_xml(args.supplier_ledger)}</PARTYLEDGERNAME>
<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
<ISINVOICE>Yes</ISINVOICE>
<NARRATION>{_xml(narration)}</NARRATION>
{_inventory_line(stock_item=args.stock_item, unit=args.unit, ledger=args.purchase_ledger, qty="1.30", rate="59.8230769", amount=amount1, purchase=True)}
{_inventory_line(stock_item=args.stock_item, unit=args.unit, ledger=args.purchase_ledger, qty="4.40", rate="45.45", amount=amount2, purchase=True)}
<LEDGERENTRIES.LIST>
<LEDGERNAME>{_xml(args.supplier_ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{_tally_amount(total)}</AMOUNT>
</LEDGERENTRIES.LIST>
</VOUCHER>"""
    meta = {
        "voucher_number": voucher_number,
        "voucher_type": "Purchase",
        "narration": narration,
        "date": "2026-04-01",
        "stock_item": args.stock_item,
        "unit": args.unit,
        "purchase_ledger": args.purchase_ledger,
        "supplier_ledger": args.supplier_ledger,
        "qty1": "1.30",
        "rate1": "59.8230769",
        "amount1": amount1,
        "qty2": "4.40",
        "rate2": "45.45",
        "amount2": amount2,
        "total": total,
    }
    numbers = ["1.30", amount1, "4.40", amount2, total]
    return import_envelope(args.company, "Vouchers", [master_message(voucher_xml)]), meta, numbers


def build_stock_journal(args: argparse.Namespace, suffix: str) -> tuple[str, dict[str, str], list[str]]:
    voucher_number = f"CODX-W2D-STKJ-{suffix}"
    narration = f"CODX_SYNTH_W2D_STOCKJOURNAL_{suffix}"
    out_amount = "151.51"
    in_amount = "262.62"
    voucher_xml = f"""
<VOUCHER VCHTYPE="Stock Journal" ACTION="Create" OBJVIEW="Stock Journal Voucher View">
<DATE>20260401</DATE>
<VOUCHERTYPENAME>Stock Journal</VOUCHERTYPENAME>
<VOUCHERNUMBER>{_xml(voucher_number)}</VOUCHERNUMBER>
<PERSISTEDVIEW>Stock Journal Voucher View</PERSISTEDVIEW>
<NARRATION>{_xml(narration)}</NARRATION>
<INVENTORYENTRIESOUT.LIST>
<STOCKITEMNAME>{_xml(args.stock_item)}</STOCKITEMNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<RATE>31.565/{_xml(args.unit)}</RATE>
<AMOUNT>-{_tally_amount(out_amount)}</AMOUNT>
<ACTUALQTY>4.80 {_xml(args.unit)}</ACTUALQTY>
</INVENTORYENTRIESOUT.LIST>
<INVENTORYENTRIESIN.LIST>
<STOCKITEMNAME>{_xml(args.stock_item)}</STOCKITEMNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<RATE>38.62/{_xml(args.unit)}</RATE>
<AMOUNT>{_tally_amount(in_amount)}</AMOUNT>
<ACTUALQTY>6.80 {_xml(args.unit)}</ACTUALQTY>
</INVENTORYENTRIESIN.LIST>
</VOUCHER>"""
    meta = {
        "voucher_number": voucher_number,
        "voucher_type": "Stock Journal",
        "narration": narration,
        "date": "2026-04-01",
        "stock_item": args.stock_item,
        "unit": args.unit,
        "out_qty": "4.80",
        "out_rate": "31.565",
        "out_amount": out_amount,
        "in_qty": "6.80",
        "in_rate": "38.62",
        "in_amount": in_amount,
    }
    numbers = ["4.80", out_amount, "6.80", in_amount]
    return import_envelope(args.company, "Vouchers", [master_message(voucher_xml)]), meta, numbers


def build_payment_bill(args: argparse.Namespace, suffix: str) -> tuple[str, dict[str, str], list[str]]:
    voucher_number = f"CODX-W2D-BILL-{suffix}"
    narration = f"CODX_SYNTH_W2D_BILLALLOC_{suffix}"
    bill_name = f"W2D-BILL-{suffix}"
    amount = "123.21"
    voucher_xml = f"""
<VOUCHER VCHTYPE="Payment" ACTION="Create" OBJVIEW="Accounting Voucher View">
<DATE>20260401</DATE>
<VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
<VOUCHERNUMBER>{_xml(voucher_number)}</VOUCHERNUMBER>
<PARTYLEDGERNAME>{_xml(args.supplier_ledger)}</PARTYLEDGERNAME>
<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
<NARRATION>{_xml(narration)}</NARRATION>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>{_xml(args.supplier_ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{_tally_amount(amount)}</AMOUNT>
<BILLALLOCATIONS.LIST>
<NAME>{_xml(bill_name)}</NAME>
<BILLTYPE>New Ref</BILLTYPE>
<AMOUNT>{_tally_amount(amount)}</AMOUNT>
</BILLALLOCATIONS.LIST>
</ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>Cash</LEDGERNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<AMOUNT>-{_tally_amount(amount)}</AMOUNT>
</ALLLEDGERENTRIES.LIST>
</VOUCHER>"""
    meta = {
        "voucher_number": voucher_number,
        "voucher_type": "Payment",
        "narration": narration,
        "date": "2026-04-01",
        "supplier_ledger": args.supplier_ledger,
        "cash_ledger": "Cash",
        "bill_name": bill_name,
        "amount": amount,
    }
    return import_envelope(args.company, "Vouchers", [master_message(voucher_xml)]), meta, [amount]


BUILDERS: dict[str, Callable[[argparse.Namespace, str], tuple[str, dict[str, str], list[str]]]] = {
    "two-line-sales": build_two_line_sales,
    "two-line-purchase": build_two_line_purchase,
    "stock-journal": build_stock_journal,
    "payment-bill": build_payment_bill,
}


def run_mutation(args: argparse.Namespace) -> dict[str, object]:
    suffix = args.suffix or datetime.now().strftime("%H%M%S")
    run_id = args.run_id or f"W2D_{args.mode.upper().replace('-', '_')}_{suffix}"
    out_root = Path(args.out_dir).expanduser().resolve() / run_id
    before_dir = out_root / "before"
    after_dir = out_root / "after"
    out_root.mkdir(parents=True, exist_ok=True)

    lock_file = Path(args.lock_file).expanduser().resolve()
    fd = acquire_lock(lock_file)
    try:
        companies = list_companies(args.host, args.port, args.timeout)
        target = guard_company(companies, args.company, args.expected_company_number)
        company_dir = Path(args.company_dir).expanduser().resolve()
        files = args.file or DEFAULT_FILES
        before_snapshot = copy_snapshot(company_dir, before_dir, files)

        voucher_xml, meta, numbers = BUILDERS[args.mode](args, suffix)
        existing_voucher, preflight_request, preflight_response = preflight_voucher_number(
            args.host,
            args.port,
            args.timeout,
            args.company,
            meta["voucher_number"],
        )
        artifacts: dict[str, str] = {
            "voucher_preflight_request": save_artifact(
                out_root,
                f"preflight-voucher-{meta['voucher_number']}-request",
                preflight_request,
            ),
            "voucher_preflight_response": save_artifact(
                out_root,
                f"preflight-voucher-{meta['voucher_number']}-response",
                preflight_response,
            ),
            "voucher_import_request": save_artifact(out_root, "voucher-import-request", voucher_xml),
        }
        if existing_voucher:
            raise LabError(f"Voucher already exists: {meta['voucher_number']} {existing_voucher}")

        response = post_xml(args.host, args.port, voucher_xml, args.timeout)
        artifacts["voucher_import_response"] = save_artifact(out_root, "voucher-import-response", response)
        line_error = _line_error(response)
        counts = _status_counts(response)
        if line_error:
            raise LabError(f"Tally import returned LINEERROR: {line_error}")
        if counts.get("errors", 0):
            raise LabError(f"Tally import returned errors: {counts}")
        if counts.get("exceptions", 0):
            raise LabError(f"Tally import returned exceptions: {counts}")
        if not (counts.get("created", 0) or counts.get("altered", 0) or counts.get("deleted", 0)):
            raise LabError(f"Tally import did not create/alter/delete an object: {counts}")
        time.sleep(args.flush_wait_seconds)
        after_snapshot = copy_snapshot(company_dir, after_dir, files)
        sentinels = [
            str(value)
            for key, value in meta.items()
            if key in {"voucher_number", "narration", "bill_name"}
        ]
        diff = diff_snapshots(
            before_dir,
            after_dir,
            files=files,
            sentinels=sentinels,
            numbers=numbers,
            dates=[meta["date"]],
            delta_limit=args.delta_limit,
        )
        diff_path = out_root / "synthetic-diff.json"
        diff_path.write_text(json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {
            "ok": True,
            "run_id": run_id,
            "mode": args.mode,
            "host": args.host,
            "port": args.port,
            "company": target,
            "out_dir": str(out_root),
            "before_snapshot": before_snapshot,
            "after_snapshot": after_snapshot,
            "xml_artifacts": artifacts,
            "voucher": meta,
            "voucher_import": {"line_error": line_error, "counts": counts, "response": response},
            "diff_path": str(diff_path),
            "diff_summary": diff.get("summary"),
        }
    finally:
        release_lock(fd, lock_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="W2D guarded custom live synthetic mutations.")
    parser.add_argument("--mode", choices=sorted(BUILDERS), required=True)
    parser.add_argument("--host", default="10.211.55.3")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--company", default="Test Synthetic")
    parser.add_argument("--expected-company-number", default="100000")
    parser.add_argument("--company-dir", default="live-tally-data/100000")
    parser.add_argument("--out-dir", default="out/synthetic-live")
    parser.add_argument("--run-id")
    parser.add_argument("--suffix")
    parser.add_argument("--stock-item", default=DEFAULT_STOCK_ITEM)
    parser.add_argument("--unit", default=DEFAULT_UNIT)
    parser.add_argument("--sales-ledger", default=DEFAULT_SALES_LEDGER)
    parser.add_argument("--party-ledger", default=DEFAULT_PARTY_LEDGER)
    parser.add_argument("--purchase-ledger", default=DEFAULT_PURCHASE_LEDGER)
    parser.add_argument("--supplier-ledger", default=DEFAULT_SUPPLIER_LEDGER)
    parser.add_argument("--bank-ledger", default=DEFAULT_BANK_LEDGER)
    parser.add_argument("--file", action="append")
    parser.add_argument("--flush-wait-seconds", type=float, default=2.0)
    parser.add_argument("--delta-limit", type=int, default=80)
    parser.add_argument("--lock-file", default="/tmp/tally_synthetic_lab.lock")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_mutation(args)
    except LabError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), flush=True)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

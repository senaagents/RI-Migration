#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import http.client
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from tally1800.synthetic_diff import diff_snapshots


DATA_FILE_SUFFIXES = {".1800", ".900", ".tsf"}
INVALID_XML_CHARS = re.compile(r"&#(?:[0-8]|1[0-1]|1[4-9]|2[0-9]|3[01]);")
DEFAULT_FILES = [
    "Company.1800",
    "Manager.1800",
    "TranMgr.1800",
    "LinkMgr.1800",
    "VchStatus.1800",
    "StatStatus.1800",
    "Aggr.1800",
    "ExtMngr.1800",
    "CmpSave.1800",
    "SecTran.1800",
]
ACCOUNTING_VOUCHER_TYPES = {
    "payment": "Payment",
    "receipt": "Receipt",
    "journal": "Journal",
    "contra": "Contra",
}
ACCOUNTING_PREFIXES = {
    "payment": "PAY",
    "receipt": "RCT",
    "journal": "JRN",
    "contra": "CTR",
}
MASTER_MODE_TYPES = {
    "ledger": "Ledger",
    "unit": "Unit",
    "stock-group": "Stock Group",
    "stock-item": "Stock Item",
}
MASTER_PREFLIGHT_FIELDS = {
    "Ledger": ["Name", "Parent", "MasterID", "GUID"],
    "Unit": ["Name", "OriginalName", "FormalName", "IsSimpleUnit", "MasterID", "GUID"],
    "Stock Group": ["Name", "Parent", "MasterID", "GUID"],
    "Stock Item": ["Name", "BaseUnits", "Parent", "MasterID", "GUID"],
}


class LabError(RuntimeError):
    pass


def _xml(value: object) -> str:
    return escape(str(value), {'"': "&quot;"})


def _xml_attr(value: object) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _tally_amount(value: str | float | int) -> str:
    text = str(value)
    if text.startswith("-"):
        return text
    return text


def _line_error(xml_text: str) -> str | None:
    match = re.search(r"<LINEERROR>(.*?)</LINEERROR>", xml_text, re.DOTALL)
    if not match:
        return None
    return (
        match.group(1)
        .replace("&apos;", "'")
        .replace("&quot;", '"')
        .replace("&amp;", "&")
        .strip()
    )


def _clean_tally_xml(xml_text: str) -> str:
    # Tally can emit illegal XML character references in otherwise usable responses.
    # This mirrors the tally-db-pipeline HTTP client cleaning step.
    xml_text = INVALID_XML_CHARS.sub("", xml_text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_text)


def _status_counts(xml_text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tag in ["CREATED", "ALTERED", "DELETED", "ERRORS", "IGNORED", "LASTVCHID", "LASTMID"]:
        match = re.search(fr"<{tag}>(.*?)</{tag}>", xml_text, re.DOTALL | re.IGNORECASE)
        if not match:
            continue
        try:
            counts[tag.lower()] = int(match.group(1).strip())
        except ValueError:
            pass
    return counts


def _object_tag(object_type: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", object_type).upper()


def _child_text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _safe_file_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "artifact"


def post_xml(host: str, port: int, xml_payload: str, timeout: int) -> str:
    request = urllib.request.Request(
        f"http://{host}:{port}",
        data=xml_payload.encode("utf-8"),
        headers={"Content-Type": "text/xml"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _clean_tally_xml(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError) as exc:
        raise LabError(f"Tally HTTP request failed: {exc}") from exc


def company_collection_xml() -> str:
    return """<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>Export</TALLYREQUEST>
<TYPE>Collection</TYPE>
<ID>CompanyInfo</ID>
</HEADER>
<BODY><DESC>
<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
<TDL><TDLMESSAGE>
<COLLECTION NAME="CompanyInfo" ISMODIFY="No">
<TYPE>Company</TYPE>
<FETCH>Name,GUID,CompanyNumber,StartingFrom,BooksFrom,EndingAt</FETCH>
</COLLECTION>
</TDLMESSAGE></TDL>
</DESC></BODY>
</ENVELOPE>"""


def list_companies(host: str, port: int, timeout: int) -> list[dict[str, str]]:
    xml_text = post_xml(host, port, company_collection_xml(), timeout)
    line_error = _line_error(xml_text)
    if line_error:
        raise LabError(f"Company discovery failed: {line_error}")
    root = ET.fromstring(xml_text)
    rows = []
    for company in root.iter("COMPANY"):
        row = {"name": company.get("NAME", "")}
        for child in company:
            tag = child.tag.upper()
            text = (child.text or "").strip()
            if tag == "NAME" and text:
                row["name"] = text
            elif tag in {"GUID", "COMPANYNUMBER", "STARTINGFROM", "BOOKSFROM", "ENDINGAT"}:
                row[tag.lower()] = text
        rows.append(row)
    return rows


def guard_company(companies: list[dict[str, str]], company: str, expected_number: str | None) -> dict[str, str]:
    matches = [row for row in companies if row.get("name") == company]
    if not matches:
        names = ", ".join(row.get("name", "") for row in companies)
        raise LabError(f"Target company {company!r} not visible. Visible companies: {names}")
    target = matches[0]
    if "synthetic" not in company.lower() and not company.upper().startswith("CODX_SYNTH"):
        raise LabError(f"Refusing to write to non-synthetic-looking company name: {company!r}")
    if expected_number and target.get("companynumber") != expected_number:
        raise LabError(
            f"Company number mismatch for {company!r}: expected {expected_number}, got {target.get('companynumber')}"
        )
    return target


def export_collection_xml(company: str, object_type: str, fields: list[str]) -> str:
    methods = "".join(f"<NATIVEMETHOD>{_xml(field)}</NATIVEMETHOD>" for field in fields)
    name = f"Codex{re.sub(r'[^A-Za-z0-9]', '', object_type)}Probe"
    return f"""<ENVELOPE>
<HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>{name}</ID></HEADER>
<BODY><DESC>
<STATICVARIABLES><SVCURRENTCOMPANY>{_xml(company)}</SVCURRENTCOMPANY><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
<TDL><TDLMESSAGE>
<COLLECTION NAME="{_xml_attr(name)}" ISINITIALIZE="Yes">
<TYPE>{_xml(object_type)}</TYPE>
{methods}
</COLLECTION>
</TDLMESSAGE></TDL>
</DESC></BODY>
</ENVELOPE>"""


def parse_collection_rows(xml_text: str, object_type: str) -> list[dict[str, str]]:
    line_error = _line_error(xml_text)
    if line_error:
        raise LabError(f"{object_type} preflight returned LINEERROR: {line_error}")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise LabError(f"{object_type} preflight returned malformed XML: {exc}") from exc
    tag = _object_tag(object_type)
    rows: list[dict[str, str]] = []
    for el in root.iter(tag):
        row = {"name": (el.get("NAME") or _child_text(el, "NAME")).strip()}
        for child in el:
            text = (child.text or "").strip()
            if text:
                row[child.tag.lower()] = text
        rows.append(row)
    return rows


def find_collection_name(xml_text: str, object_type: str, name: str) -> dict[str, str] | None:
    wanted = name.strip().casefold()
    for row in parse_collection_rows(xml_text, object_type):
        if row.get("name", "").casefold() == wanted:
            return row
    return None


def preflight_master(
    host: str,
    port: int,
    timeout: int,
    company: str,
    object_type: str,
    name: str,
) -> tuple[dict[str, str] | None, str, str]:
    fields = MASTER_PREFLIGHT_FIELDS.get(object_type, ["Name", "MasterID", "GUID"])
    request_xml = export_collection_xml(company, object_type, fields)
    response_xml = post_xml(host, port, request_xml, timeout)
    return find_collection_name(response_xml, object_type, name), request_xml, response_xml


def preflight_voucher_number(
    host: str,
    port: int,
    timeout: int,
    company: str,
    voucher_number: str,
) -> tuple[dict[str, str] | None, str, str]:
    # Synthetic companies are tiny. Keep this lightweight: no full detail, no child rows.
    request_xml = export_collection_xml(
        company,
        "Voucher",
        ["VoucherNumber", "VoucherTypeName", "Date", "MasterID", "GUID"],
    )
    response_xml = post_xml(host, port, request_xml, timeout)
    rows = parse_collection_rows(response_xml, "Voucher")
    wanted = voucher_number.strip().casefold()
    for row in rows:
        number = row.get("vouchernumber", "") or row.get("name", "")
        if number.casefold() == wanted:
            return row, request_xml, response_xml
    return None, request_xml, response_xml


def import_envelope(company: str, report_name: str, tally_messages: list[str]) -> str:
    return f"""<ENVELOPE>
<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
<BODY>
<IMPORTDATA>
<REQUESTDESC>
<REPORTNAME>{_xml(report_name)}</REPORTNAME>
<STATICVARIABLES><SVCURRENTCOMPANY>{_xml(company)}</SVCURRENTCOMPANY></STATICVARIABLES>
</REQUESTDESC>
<REQUESTDATA>
{''.join(tally_messages)}
</REQUESTDATA>
</IMPORTDATA>
</BODY>
</ENVELOPE>"""


def master_message(body: str) -> str:
    return f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{body}</TALLYMESSAGE>'


def build_master_messages(run_id: str) -> dict[str, str]:
    ledger_cash = f"CODX_SYNTH_{run_id}_CASH"
    ledger_sales = f"CODX_SYNTH_{run_id}_SALES"
    ledger_party = f"CODX_SYNTH_{run_id}_PARTY"
    unit = f"CSU{run_id[-4:]}"
    unit_formal_name = f"Codex Synthetic Unit {run_id[-4:]}"
    stock_group = f"CODX_SYNTH_{run_id}_STOCKGROUP"
    item = f"CODX_SYNTH_{run_id}_ITEM"

    messages = [
        master_message(
            f"""
<LEDGER NAME="{_xml_attr(ledger_cash)}" ACTION="Create">
<NAME>{_xml(ledger_cash)}</NAME>
<PARENT>Cash-in-Hand</PARENT>
<OPENINGBALANCE>0</OPENINGBALANCE>
</LEDGER>"""
        ),
        master_message(
            f"""
<LEDGER NAME="{_xml_attr(ledger_sales)}" ACTION="Create">
<NAME>{_xml(ledger_sales)}</NAME>
<PARENT>Sales Accounts</PARENT>
<OPENINGBALANCE>0</OPENINGBALANCE>
</LEDGER>"""
        ),
        master_message(
            f"""
<LEDGER NAME="{_xml_attr(ledger_party)}" ACTION="Create">
<NAME>{_xml(ledger_party)}</NAME>
<PARENT>Sundry Debtors</PARENT>
<OPENINGBALANCE>0</OPENINGBALANCE>
</LEDGER>"""
        ),
        master_message(
            f"""
<UNIT NAME="{_xml_attr(unit)}" ACTION="Create">
<NAME>{_xml(unit)}</NAME>
<ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
<MAILINGNAME>{_xml(unit_formal_name)}</MAILINGNAME>
<ORIGINALNAME>{_xml(unit_formal_name)}</ORIGINALNAME>
</UNIT>"""
        ),
        master_message(
            f"""
<STOCKGROUP NAME="{_xml_attr(stock_group)}" ACTION="Create">
<NAME>{_xml(stock_group)}</NAME>
<PARENT/>
</STOCKGROUP>"""
        ),
        master_message(
            f"""
<STOCKITEM NAME="{_xml_attr(item)}" ACTION="Create">
<NAME>{_xml(item)}</NAME>
<PARENT>{_xml(stock_group)}</PARENT>
<BASEUNITS>{_xml(unit)}</BASEUNITS>
<OPENINGBALANCE>0</OPENINGBALANCE>
<OPENINGVALUE>0</OPENINGVALUE>
</STOCKITEM>"""
        ),
    ]
    return {
        "ledger_cash": ledger_cash,
        "ledger_sales": ledger_sales,
        "ledger_party": ledger_party,
        "unit": unit,
        "unit_formal_name": unit_formal_name,
        "stock_group": stock_group,
        "item": item,
        "xml": import_envelope("{}", "All Masters", messages),
    }


def build_ledger_import(company: str, suffix: str, parent: str) -> tuple[str, dict[str, str]]:
    ledger_name = f"CODX_SYNTH_LEDGER_{suffix}"
    xml = import_envelope(
        company,
        "All Masters",
        [
            master_message(
                f"""
<LEDGER NAME="{_xml_attr(ledger_name)}" ACTION="Create">
<NAME>{_xml(ledger_name)}</NAME>
<PARENT>{_xml(parent)}</PARENT>
<OPENINGBALANCE>0</OPENINGBALANCE>
</LEDGER>"""
            )
        ],
    )
    return xml, {"ledger": ledger_name}


def build_unit_import(company: str, symbol: str, formal_name: str) -> tuple[str, dict[str, str]]:
    xml = import_envelope(
        company,
        "All Masters",
        [
            master_message(
                f"""
<UNIT NAME="{_xml_attr(symbol)}" ACTION="Create">
<NAME>{_xml(symbol)}</NAME>
<MAILINGNAME>{_xml(formal_name)}</MAILINGNAME>
<ORIGINALNAME>{_xml(formal_name)}</ORIGINALNAME>
<ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
</UNIT>"""
            )
        ],
    )
    return xml, {"unit": symbol, "formal_name": formal_name}


def build_stock_group_import(company: str, stock_group_name: str) -> tuple[str, dict[str, str]]:
    xml = import_envelope(
        company,
        "All Masters",
        [
            master_message(
                f"""
<STOCKGROUP NAME="{_xml_attr(stock_group_name)}" ACTION="Create">
<NAME>{_xml(stock_group_name)}</NAME>
<PARENT/>
</STOCKGROUP>"""
            )
        ],
    )
    return xml, {"stock_group": stock_group_name}


def build_stock_item_import(
    company: str,
    stock_item_name: str,
    base_unit: str,
    parent: str,
) -> tuple[str, dict[str, str]]:
    parent_xml = f"<PARENT>{_xml(parent)}</PARENT>" if parent else "<PARENT/>"
    xml = import_envelope(
        company,
        "All Masters",
        [
            master_message(
                f"""
<STOCKITEM NAME="{_xml_attr(stock_item_name)}" ACTION="Create">
<NAME>{_xml(stock_item_name)}</NAME>
{parent_xml}
<BASEUNITS>{_xml(base_unit)}</BASEUNITS>
<OPENINGBALANCE>0</OPENINGBALANCE>
<OPENINGVALUE>0</OPENINGVALUE>
</STOCKITEM>"""
            )
        ],
    )
    return xml, {"stock_item": stock_item_name, "base_unit": base_unit, "stock_group": parent}


def build_accounting_voucher_import(
    company: str,
    mode: str,
    suffix: str,
    debit_ledger: str,
    credit_ledger: str,
    amount: str,
) -> tuple[str, dict[str, str]]:
    voucher_type = ACCOUNTING_VOUCHER_TYPES[mode]
    prefix = ACCOUNTING_PREFIXES[mode]
    voucher_number = f"CODX-{prefix}-{suffix}"
    narration = f"CODX_SYNTH_{mode.upper()}_{suffix}"
    party_ledger = credit_ledger if mode == "receipt" else debit_ledger
    voucher_xml = f"""
<VOUCHER VCHTYPE="{_xml_attr(voucher_type)}" ACTION="Create" OBJVIEW="Accounting Voucher View">
<DATE>20260401</DATE>
<VOUCHERTYPENAME>{_xml(voucher_type)}</VOUCHERTYPENAME>
<VOUCHERNUMBER>{_xml(voucher_number)}</VOUCHERNUMBER>
<PARTYLEDGERNAME>{_xml(party_ledger)}</PARTYLEDGERNAME>
<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
<NARRATION>{_xml(narration)}</NARRATION>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>{_xml(debit_ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{_xml(amount)}</AMOUNT>
</ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>{_xml(credit_ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<AMOUNT>-{_xml(amount)}</AMOUNT>
</ALLLEDGERENTRIES.LIST>
</VOUCHER>"""
    meta = {
        "voucher_number": voucher_number,
        "voucher_type": voucher_type,
        "date": "2026-04-01",
        "amount": amount,
        "debit_ledger": debit_ledger,
        "credit_ledger": credit_ledger,
        "party_ledger": party_ledger,
        "narration": narration,
    }
    return import_envelope(company, "Vouchers", [master_message(voucher_xml)]), meta


def build_payment_voucher_import(
    company: str,
    suffix: str,
    debit_ledger: str,
    credit_ledger: str,
    amount: str,
) -> tuple[str, dict[str, str]]:
    return build_accounting_voucher_import(company, "payment", suffix, debit_ledger, credit_ledger, amount)


def build_voucher_import(company: str, run_id: str, names: dict[str, str]) -> tuple[str, dict[str, str]]:
    voucher_number = f"CODX-{run_id[-8:]}"
    date_text = "20260401"
    qty = "7.50"
    rate = "13.37"
    amount = "100.275"
    voucher_xml = f"""
<VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
<DATE>{date_text}</DATE>
<VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
<VOUCHERNUMBER>{_xml(voucher_number)}</VOUCHERNUMBER>
<PARTYLEDGERNAME>{_xml(names['ledger_party'])}</PARTYLEDGERNAME>
<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
<ISINVOICE>Yes</ISINVOICE>
<NARRATION>CODX_SYNTH_{_xml(run_id)}_NARRATION</NARRATION>
<ALLINVENTORYENTRIES.LIST>
<STOCKITEMNAME>{_xml(names['item'])}</STOCKITEMNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<RATE>{rate}/{_xml(names['unit'])}</RATE>
<AMOUNT>{_tally_amount(amount)}</AMOUNT>
<ACTUALQTY>{qty} {_xml(names['unit'])}</ACTUALQTY>
<BILLEDQTY>{qty} {_xml(names['unit'])}</BILLEDQTY>
<ACCOUNTINGALLOCATIONS.LIST>
<LEDGERNAME>{_xml(names['ledger_sales'])}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{_tally_amount(amount)}</AMOUNT>
</ACCOUNTINGALLOCATIONS.LIST>
</ALLINVENTORYENTRIES.LIST>
<LEDGERENTRIES.LIST>
<LEDGERNAME>{_xml(names['ledger_party'])}</LEDGERNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<AMOUNT>-{_tally_amount(amount)}</AMOUNT>
</LEDGERENTRIES.LIST>
</VOUCHER>"""
    meta = {
        "voucher_number": voucher_number,
        "date": "2026-04-01",
        "qty": qty,
        "rate": rate,
        "amount": amount,
    }
    return import_envelope(company, "Vouchers", [master_message(voucher_xml)]), meta


def build_sales_voucher_import(
    company: str,
    suffix: str,
    stock_item: str,
    unit: str,
    sales_ledger: str,
    party_ledger: str,
    qty: str,
    rate: str,
    amount: str,
) -> tuple[str, dict[str, str]]:
    voucher_number = f"CODX-SAL-{suffix}"
    narration = f"CODX_SYNTH_SALES_{suffix}_NARRATION"
    voucher_xml = f"""
<VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
<DATE>20260401</DATE>
<VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
<VOUCHERNUMBER>{_xml(voucher_number)}</VOUCHERNUMBER>
<PARTYLEDGERNAME>{_xml(party_ledger)}</PARTYLEDGERNAME>
<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
<ISINVOICE>Yes</ISINVOICE>
<NARRATION>{_xml(narration)}</NARRATION>
<ALLINVENTORYENTRIES.LIST>
<STOCKITEMNAME>{_xml(stock_item)}</STOCKITEMNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<RATE>{_xml(rate)}/{_xml(unit)}</RATE>
<AMOUNT>{_tally_amount(amount)}</AMOUNT>
<ACTUALQTY>{_xml(qty)} {_xml(unit)}</ACTUALQTY>
<BILLEDQTY>{_xml(qty)} {_xml(unit)}</BILLEDQTY>
<ACCOUNTINGALLOCATIONS.LIST>
<LEDGERNAME>{_xml(sales_ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{_tally_amount(amount)}</AMOUNT>
</ACCOUNTINGALLOCATIONS.LIST>
</ALLINVENTORYENTRIES.LIST>
<LEDGERENTRIES.LIST>
<LEDGERNAME>{_xml(party_ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<AMOUNT>-{_tally_amount(amount)}</AMOUNT>
</LEDGERENTRIES.LIST>
</VOUCHER>"""
    meta = {
        "voucher_number": voucher_number,
        "voucher_type": "Sales",
        "date": "2026-04-01",
        "qty": qty,
        "rate": rate,
        "amount": amount,
        "stock_item": stock_item,
        "unit": unit,
        "sales_ledger": sales_ledger,
        "party_ledger": party_ledger,
        "narration": narration,
    }
    return import_envelope(company, "Vouchers", [master_message(voucher_xml)]), meta


def build_purchase_voucher_import(
    company: str,
    suffix: str,
    stock_item: str,
    unit: str,
    purchase_ledger: str,
    party_ledger: str,
    qty: str,
    rate: str,
    amount: str,
) -> tuple[str, dict[str, str]]:
    voucher_number = f"CODX-PUR-{suffix}"
    narration = f"CODX_SYNTH_PURCHASE_{suffix}_NARRATION"
    voucher_xml = f"""
<VOUCHER VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Invoice Voucher View">
<DATE>20260401</DATE>
<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
<VOUCHERNUMBER>{_xml(voucher_number)}</VOUCHERNUMBER>
<PARTYLEDGERNAME>{_xml(party_ledger)}</PARTYLEDGERNAME>
<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
<ISINVOICE>Yes</ISINVOICE>
<NARRATION>{_xml(narration)}</NARRATION>
<ALLINVENTORYENTRIES.LIST>
<STOCKITEMNAME>{_xml(stock_item)}</STOCKITEMNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<RATE>{_xml(rate)}/{_xml(unit)}</RATE>
<AMOUNT>-{_tally_amount(amount)}</AMOUNT>
<ACTUALQTY>{_xml(qty)} {_xml(unit)}</ACTUALQTY>
<BILLEDQTY>{_xml(qty)} {_xml(unit)}</BILLEDQTY>
<ACCOUNTINGALLOCATIONS.LIST>
<LEDGERNAME>{_xml(purchase_ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<AMOUNT>-{_tally_amount(amount)}</AMOUNT>
</ACCOUNTINGALLOCATIONS.LIST>
</ALLINVENTORYENTRIES.LIST>
<LEDGERENTRIES.LIST>
<LEDGERNAME>{_xml(party_ledger)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{_tally_amount(amount)}</AMOUNT>
</LEDGERENTRIES.LIST>
</VOUCHER>"""
    meta = {
        "voucher_number": voucher_number,
        "voucher_type": "Purchase",
        "date": "2026-04-01",
        "qty": qty,
        "rate": rate,
        "amount": amount,
        "stock_item": stock_item,
        "unit": unit,
        "purchase_ledger": purchase_ledger,
        "party_ledger": party_ledger,
        "narration": narration,
    }
    return import_envelope(company, "Vouchers", [master_message(voucher_xml)]), meta


def copy_snapshot(company_dir: Path, out_dir: Path, files: list[str]) -> dict[str, object]:
    if not company_dir.exists():
        raise LabError(f"Company folder does not exist: {company_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    wanted = set(files)
    for path in company_dir.iterdir():
        if not path.is_file():
            continue
        if files and path.name not in wanted:
            continue
        if path.suffix.lower() not in DATA_FILE_SUFFIXES and path.name not in wanted:
            continue
        shutil.copy2(path, out_dir / path.name)
        copied.append(path.name)
    if not copied:
        raise LabError(f"No Tally data files copied from {company_dir}")
    return {"dir": str(out_dir), "files": sorted(copied)}


def acquire_lock(lock_file: Path) -> int:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise LabError(f"Tally lab lock exists: {lock_file}") from exc
    os.write(fd, f"{os.getpid()} {int(time.time())}\n".encode("utf-8"))
    return fd


def release_lock(fd: int, lock_file: Path) -> None:
    try:
        os.close(fd)
    finally:
        try:
            lock_file.unlink()
        except FileNotFoundError:
            pass


def save_artifact(out_root: Path, label: str, text: str) -> str:
    path = out_root / f"{_safe_file_label(label)}.xml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def run_lab(args: argparse.Namespace) -> dict[str, object]:
    run_id = args.run_id or datetime.now().strftime("%Y%m%d%H%M%S%f")
    suffix = args.suffix or f"{datetime.now().strftime('%H%M%S')}{os.getpid() % 1000:03d}"
    out_root = Path(args.out_dir).expanduser().resolve() / run_id
    before_dir = out_root / "before"
    after_dir = out_root / "after"
    out_root.mkdir(parents=True, exist_ok=True)

    lock_file = Path(args.lock_file).expanduser().resolve()
    fd = acquire_lock(lock_file)
    try:
        companies = list_companies(args.host, args.port, args.timeout)
        target = guard_company(companies, args.company, args.expected_company_number)

        result: dict[str, object] = {
            "run_id": run_id,
            "host": args.host,
            "port": args.port,
            "company": target,
            "out_dir": str(out_root),
            "dry_run": args.dry_run,
        }

        if args.verify_only:
            return result

        company_dir = Path(args.company_dir).expanduser().resolve() if args.company_dir else None
        if not company_dir and not args.allow_no_snapshot and not args.dry_run:
            raise LabError(
                "Refusing to import without --company-dir. Need before/after .1800 snapshots for this experiment."
            )
        if args.mode == "full" and not args.allow_full_inventory_bundle:
            raise LabError(
                "Refusing --mode full by default. The bundled unit/item/voucher import can wedge Tally; "
                "split inventory experiments into unit-only, item-only, then voucher-only runs, or pass "
                "--allow-full-inventory-bundle deliberately."
            )

        files = args.file or DEFAULT_FILES
        if company_dir:
            result["before_snapshot"] = copy_snapshot(company_dir, before_dir, files)

        master_xml: str | None = None
        voucher_xml: str | None = None
        voucher_meta: dict[str, str] = {}
        sentinels: dict[str, str] = {}
        master_preflight_target: tuple[str, str] | None = None
        voucher_preflight_number: str | None = None
        if args.mode == "ledger":
            master_xml, sentinels = build_ledger_import(args.company, suffix, args.ledger_parent)
            master_preflight_target = ("Ledger", sentinels["ledger"])
        elif args.mode == "unit":
            symbol = args.unit_symbol or f"CU{suffix}"
            formal_name = args.unit_formal_name or symbol
            master_xml, sentinels = build_unit_import(args.company, symbol, formal_name)
            master_preflight_target = ("Unit", sentinels["unit"])
        elif args.mode == "stock-group":
            stock_group_name = args.stock_group_name or f"CODX_SYNTH_STOCKGROUP_{suffix}"
            master_xml, sentinels = build_stock_group_import(args.company, stock_group_name)
            master_preflight_target = ("Stock Group", sentinels["stock_group"])
        elif args.mode == "stock-item":
            stock_item_name = args.stock_item_name or f"CODX_SYNTH_ITEM_{suffix}"
            if not args.base_unit:
                raise LabError("--base-unit is required for --mode stock-item")
            master_xml, sentinels = build_stock_item_import(
                args.company,
                stock_item_name,
                args.base_unit,
                args.stock_parent,
            )
            master_preflight_target = ("Stock Item", sentinels["stock_item"])
        elif args.mode in ACCOUNTING_VOUCHER_TYPES:
            if not args.debit_ledger or not args.credit_ledger:
                raise LabError(f"--debit-ledger and --credit-ledger are required for --mode {args.mode}")
            voucher_xml, voucher_meta = build_accounting_voucher_import(
                args.company,
                args.mode,
                suffix,
                args.debit_ledger,
                args.credit_ledger,
                args.amount,
            )
            sentinels = {
                "voucher_number": voucher_meta["voucher_number"],
                "narration": voucher_meta["narration"],
                "voucher_type": voucher_meta["voucher_type"],
                "debit_ledger": args.debit_ledger,
                "credit_ledger": args.credit_ledger,
            }
            voucher_preflight_number = voucher_meta["voucher_number"]
        elif args.mode == "sales-voucher":
            stock_item_name = args.stock_item_name
            unit_name = args.base_unit or args.unit_symbol
            if not stock_item_name:
                raise LabError("--stock-item-name is required for --mode sales-voucher")
            if not unit_name:
                raise LabError("--base-unit or --unit-symbol is required for --mode sales-voucher")
            if not args.sales_ledger or not args.party_ledger:
                raise LabError("--sales-ledger and --party-ledger are required for --mode sales-voucher")
            voucher_xml, voucher_meta = build_sales_voucher_import(
                args.company,
                suffix,
                stock_item_name,
                unit_name,
                args.sales_ledger,
                args.party_ledger,
                args.qty,
                args.rate,
                args.amount,
            )
            sentinels = {
                "voucher_number": voucher_meta["voucher_number"],
                "narration": voucher_meta["narration"],
                "voucher_type": voucher_meta["voucher_type"],
                "stock_item": stock_item_name,
                "unit": unit_name,
                "sales_ledger": args.sales_ledger,
                "party_ledger": args.party_ledger,
            }
            voucher_preflight_number = voucher_meta["voucher_number"]
        elif args.mode == "purchase-voucher":
            stock_item_name = args.stock_item_name
            unit_name = args.base_unit or args.unit_symbol
            if not stock_item_name:
                raise LabError("--stock-item-name is required for --mode purchase-voucher")
            if not unit_name:
                raise LabError("--base-unit or --unit-symbol is required for --mode purchase-voucher")
            if not args.purchase_ledger or not args.party_ledger:
                raise LabError("--purchase-ledger and --party-ledger are required for --mode purchase-voucher")
            voucher_xml, voucher_meta = build_purchase_voucher_import(
                args.company,
                suffix,
                stock_item_name,
                unit_name,
                args.purchase_ledger,
                args.party_ledger,
                args.qty,
                args.rate,
                args.amount,
            )
            sentinels = {
                "voucher_number": voucher_meta["voucher_number"],
                "narration": voucher_meta["narration"],
                "voucher_type": voucher_meta["voucher_type"],
                "stock_item": stock_item_name,
                "unit": unit_name,
                "purchase_ledger": args.purchase_ledger,
                "party_ledger": args.party_ledger,
            }
            voucher_preflight_number = voucher_meta["voucher_number"]
        else:
            names = build_master_messages(f"{run_id}_{suffix}")
            names["xml"] = names["xml"].replace(
                "<SVCURRENTCOMPANY>{}</SVCURRENTCOMPANY>",
                f"<SVCURRENTCOMPANY>{_xml(args.company)}</SVCURRENTCOMPANY>",
            )
            master_xml = names["xml"]
            voucher_xml, voucher_meta = build_voucher_import(args.company, f"{run_id}_{suffix}", names)
            sentinels = {
                key: value
                for key, value in names.items()
                if key != "xml"
            } | {"narration": f"CODX_SYNTH_{run_id}_{suffix}_NARRATION"}
            voucher_preflight_number = voucher_meta["voucher_number"]
        result["mode"] = args.mode
        result["suffix"] = suffix
        result["sentinels"] = sentinels
        if voucher_meta:
            result["voucher"] = voucher_meta

        artifacts: dict[str, str] = {}
        if not args.dry_run and not args.skip_object_preflight:
            preflight: dict[str, object] = {}
            if master_preflight_target:
                object_type, object_name = master_preflight_target
                existing, request_xml, response_xml = preflight_master(
                    args.host,
                    args.port,
                    args.timeout,
                    args.company,
                    object_type,
                    object_name,
                )
                if args.save_xml_artifacts:
                    artifacts["master_preflight_request"] = save_artifact(
                        out_root, f"preflight-{object_type}-{object_name}-request", request_xml
                    )
                    artifacts["master_preflight_response"] = save_artifact(
                        out_root, f"preflight-{object_type}-{object_name}-response", response_xml
                    )
                preflight["master"] = {
                    "object_type": object_type,
                    "name": object_name,
                    "exists": existing is not None,
                    "existing": existing,
                }
                if existing:
                    if args.if_exists == "skip":
                        master_xml = None
                        result["skipped_import"] = {
                            "reason": "master_exists",
                            "object_type": object_type,
                            "name": object_name,
                            "existing": existing,
                        }
                    else:
                        raise LabError(
                            f"{object_type} {object_name!r} already exists in {args.company!r}; "
                            "use a unique name/suffix or pass --if-exists skip."
                        )
            if voucher_preflight_number and voucher_xml:
                existing_voucher, request_xml, response_xml = preflight_voucher_number(
                    args.host,
                    args.port,
                    args.timeout,
                    args.company,
                    voucher_preflight_number,
                )
                if args.save_xml_artifacts:
                    artifacts["voucher_preflight_request"] = save_artifact(
                        out_root, f"preflight-voucher-{voucher_preflight_number}-request", request_xml
                    )
                    artifacts["voucher_preflight_response"] = save_artifact(
                        out_root, f"preflight-voucher-{voucher_preflight_number}-response", response_xml
                    )
                preflight["voucher"] = {
                    "voucher_number": voucher_preflight_number,
                    "exists": existing_voucher is not None,
                    "existing": existing_voucher,
                }
                if existing_voucher:
                    if args.if_exists == "skip":
                        voucher_xml = None
                        result["skipped_import"] = {
                            "reason": "voucher_exists",
                            "voucher_number": voucher_preflight_number,
                            "existing": existing_voucher,
                        }
                    else:
                        raise LabError(
                            f"Voucher {voucher_preflight_number!r} already exists in {args.company!r}; "
                            "use a unique --suffix or pass --if-exists skip."
                        )
            if preflight:
                result["preflight"] = preflight
        if artifacts:
            result["xml_artifacts"] = artifacts

        if args.dry_run:
            if master_xml:
                result["master_import_xml"] = master_xml
            if voucher_xml:
                result["voucher_import_xml"] = voucher_xml
            return result

        if master_xml:
            if args.save_xml_artifacts:
                artifacts["master_import_request"] = save_artifact(out_root, "master-import-request", master_xml)
            master_response = post_xml(args.host, args.port, master_xml, args.timeout)
            time.sleep(args.request_delay_ms / 1000)
            if args.save_xml_artifacts:
                artifacts["master_import_response"] = save_artifact(out_root, "master-import-response", master_response)
            result["master_import"] = {
                "line_error": _line_error(master_response),
                "counts": _status_counts(master_response),
                "response": master_response,
            }
        if voucher_xml:
            if args.save_xml_artifacts:
                artifacts["voucher_import_request"] = save_artifact(out_root, "voucher-import-request", voucher_xml)
            voucher_response = post_xml(args.host, args.port, voucher_xml, args.timeout)
            if args.save_xml_artifacts:
                artifacts["voucher_import_response"] = save_artifact(out_root, "voucher-import-response", voucher_response)
            result["voucher_import"] = {
                "line_error": _line_error(voucher_response),
                "counts": _status_counts(voucher_response),
                "response": voucher_response,
            }
        if artifacts:
            result["xml_artifacts"] = artifacts
        line_errors = []
        if result.get("master_import", {}).get("line_error"):
            line_errors.append(f"master: {result['master_import']['line_error']}")
        if result.get("voucher_import", {}).get("line_error"):
            line_errors.append(f"voucher: {result['voucher_import']['line_error']}")
        if line_errors:
            raise LabError("Tally import returned LINEERROR: " + "; ".join(line_errors))

        time.sleep(args.flush_wait_seconds)
        if company_dir:
            result["after_snapshot"] = copy_snapshot(company_dir, after_dir, files)
            sentinels = list(result["sentinels"].values())
            numbers = [voucher_meta[key] for key in ("qty", "rate", "amount") if key in voucher_meta]
            diff = diff_snapshots(
                before_dir,
                after_dir,
                files=files,
                sentinels=sentinels,
                numbers=numbers,
                dates=[voucher_meta["date"]] if voucher_meta else [],
                delta_limit=args.delta_limit,
            )
            diff_path = out_root / "synthetic-diff.json"
            diff_path.write_text(json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            result["diff_path"] = str(diff_path)
            result["diff_summary"] = diff.get("summary")
        return result
    finally:
        release_lock(fd, lock_file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded live Tally synthetic write + .1800 snapshot diff lab.")
    parser.add_argument("--host", default="10.211.55.3")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--company", default="Test Synthetic")
    parser.add_argument("--expected-company-number", default="100000")
    parser.add_argument("--company-dir", help="Readable live/copy Tally company folder, e.g. .../Data/100000")
    parser.add_argument("--out-dir", default="out/synthetic-live")
    parser.add_argument("--run-id")
    parser.add_argument("--suffix", help="Short unique suffix for injected object names.")
    parser.add_argument(
        "--mode",
        choices=[
            "ledger",
            "unit",
            "stock-group",
            "stock-item",
            "payment",
            "receipt",
            "journal",
            "contra",
            "sales-voucher",
            "purchase-voucher",
            "full",
        ],
        default="ledger",
        help=(
            "Create one object per run. ledger/unit/stock-group/stock-item create one master; "
            "payment/receipt/journal/contra/sales-voucher/purchase-voucher import one voucher; "
            "full is dangerous legacy bundle."
        ),
    )
    parser.add_argument("--ledger-parent", default="Cash-in-Hand")
    parser.add_argument("--unit-symbol", help="Simple unit symbol, e.g. PCS.")
    parser.add_argument("--unit-formal-name", help="Simple unit formal name, e.g. Pieces.")
    parser.add_argument("--stock-group-name")
    parser.add_argument("--stock-item-name")
    parser.add_argument("--stock-parent", default="", help="Stock group parent for --mode stock-item; blank means Primary.")
    parser.add_argument("--base-unit", help="Base unit symbol for stock items/sales vouchers.")
    parser.add_argument("--sales-ledger")
    parser.add_argument("--purchase-ledger")
    parser.add_argument("--party-ledger")
    parser.add_argument("--qty", default="3.00")
    parser.add_argument("--rate", default="44.44")
    parser.add_argument("--debit-ledger")
    parser.add_argument("--credit-ledger", default="Cash")
    parser.add_argument("--amount", default="123.45")
    parser.add_argument(
        "--if-exists",
        choices=["error", "skip"],
        default="error",
        help="How to handle preflight duplicate master/voucher names.",
    )
    parser.add_argument("--file", action="append", help="Specific Tally data file to snapshot/diff; repeatable.")
    parser.add_argument("--request-delay-ms", type=int, default=250)
    parser.add_argument("--flush-wait-seconds", type=float, default=2.0)
    parser.add_argument("--delta-limit", type=int, default=50)
    parser.add_argument("--lock-file", default="/tmp/tally_synthetic_lab.lock")
    parser.add_argument("--verify-only", action="store_true", help="Only verify Tally visibility and company guard.")
    parser.add_argument("--dry-run", action="store_true", help="Build requests but do not POST imports.")
    parser.add_argument(
        "--skip-object-preflight",
        action="store_true",
        help="Skip lightweight duplicate preflight. Avoid except when the preflight surface is known bad.",
    )
    parser.add_argument(
        "--no-save-xml-artifacts",
        dest="save_xml_artifacts",
        action="store_false",
        help="Do not write request/response XML artifacts into the run directory.",
    )
    parser.set_defaults(save_xml_artifacts=True)
    parser.add_argument(
        "--allow-no-snapshot",
        action="store_true",
        help="Allow writes without a readable company folder. Normally disabled because it loses binary diff evidence.",
    )
    parser.add_argument(
        "--allow-full-inventory-bundle",
        action="store_true",
        help="Dangerous: allow --mode full bundled unit/item/voucher import. Prefer split inventory experiments.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_lab(args)
    except LabError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

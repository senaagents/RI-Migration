from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import escape
from pathlib import Path


INVALID_XML_CHARS = re.compile(r"&#(?:0?[0-8]|1[0-9]|2[0-9]|3[0-1]);|[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    tally_type: str
    tag: str
    fields: tuple[str, ...]
    xml_db_table: str | None = None


SPECS = [
    CollectionSpec(
        "company",
        "Company",
        "COMPANY",
        (
            "Name",
            "Guid",
            "FormalName",
            "Country",
            "StateName",
            "PINCode",
            "Email",
            "GSTN",
            "IncomeTaxNumber",
        ),
        "companies",
    ),
    CollectionSpec(
        "groups",
        "Group",
        "GROUP",
        ("Name", "Guid", "Parent", "AlterId", "IsRevenue", "IsDeemedPositive"),
        "groups",
    ),
    CollectionSpec(
        "ledgers",
        "Ledger",
        "LEDGER",
        (
            "Name",
            "Guid",
            "Parent",
            "AlterId",
            "MasterId",
            "OpeningBalance",
            "ClosingBalance",
            "PartyGstin",
            "MailingName",
            "Address",
            "StateName",
            "CountryName",
            "PINCode",
            "Email",
        ),
        "ledgers",
    ),
    CollectionSpec(
        "stock_items",
        "Stock Item",
        "STOCKITEM",
        (
            "Name",
            "Guid",
            "Parent",
            "AlterId",
            "MasterId",
            "BaseUnits",
            "OpeningBalance",
            "ClosingBalance",
            "OpeningValue",
            "ClosingValue",
            "GSTApplicable",
        ),
        "stock_items",
    ),
    CollectionSpec(
        "voucher_types",
        "Voucher Type",
        "VOUCHERTYPE",
        ("Name", "Guid", "Parent", "AlterId", "NumberingMethod"),
        "voucher_types",
    ),
    CollectionSpec(
        "godowns",
        "Godown",
        "GODOWN",
        ("Name", "Guid", "Parent", "AlterId"),
        "godowns",
    ),
    CollectionSpec(
        "units",
        "Unit",
        "UNIT",
        ("Name", "Guid", "OriginalName", "DecimalPlaces", "AlterId"),
        "units",
    ),
    CollectionSpec(
        "vouchers_basic",
        "Voucher",
        "VOUCHER",
        (
            "Date",
            "VoucherTypeName",
            "VoucherNumber",
            "Guid",
            "AlterId",
            "MasterId",
            "PartyLedgerName",
            "PartyName",
            "PartyGSTIN",
            "Narration",
            "EffectiveDate",
            "IsCancelled",
            "IsOptional",
        ),
        "vouchers",
    ),
]


def clean_xml(text: str) -> str:
    return INVALID_XML_CHARS.sub("", text)


def element_text(el: ET.Element, tag: str) -> str:
    child = el.find(tag.upper()) or el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def collection_xml(spec: CollectionSpec, company: str, from_date: str, to_date: str) -> str:
    methods = "".join(f"<NATIVEMETHOD>{escape(field)}</NATIVEMETHOD>" for field in spec.fields)
    date_xml = ""
    if spec.name == "vouchers_basic":
        date_xml = f"<SVFROMDATE>{escape(from_date)}</SVFROMDATE><SVTODATE>{escape(to_date)}</SVTODATE>"
    return f"""<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>EXPORT</TALLYREQUEST>
<TYPE>COLLECTION</TYPE>
<ID>{escape(spec.name)}</ID>
</HEADER>
<BODY><DESC>
<STATICVARIABLES>
<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>
{date_xml}
</STATICVARIABLES>
<TDL><TDLMESSAGE>
<COLLECTION NAME="{escape(spec.name)}" ISINITIALIZE="Yes">
<TYPE>{escape(spec.tally_type)}</TYPE>
{methods}
</COLLECTION>
</TDLMESSAGE></TDL>
</DESC></BODY>
</ENVELOPE>"""


def post_xml(url: str, payload: str, timeout: int) -> tuple[str, int]:
    started = time.monotonic()
    req = urllib.request.Request(
        url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return raw, int((time.monotonic() - started) * 1000)


def parse_collection(raw_xml: str, spec: CollectionSpec) -> list[dict[str, str]]:
    root = ET.fromstring(clean_xml(raw_xml))
    rows = []
    for el in root.iter(spec.tag):
        row = {field: element_text(el, field) for field in spec.fields}
        if not row.get("Name"):
            row["Name"] = el.attrib.get("NAME", "").strip()
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, str]], fields: tuple[str, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def fetch_live(args: argparse.Namespace, out_dir: Path) -> dict[str, list[dict[str, str]]]:
    url = f"http://{args.host}:{args.port}"
    fetched = {}
    for spec in SPECS:
        payload = collection_xml(spec, args.company, args.from_date, args.to_date)
        (out_dir / f"{spec.name}-request.xml").write_text(payload, encoding="utf-8")
        raw, ms = post_xml(url, payload, args.timeout)
        (out_dir / f"{spec.name}-response.xml").write_text(raw, encoding="utf-8")
        rows = parse_collection(raw, spec)
        write_csv(out_dir / f"{spec.name}.csv", rows, spec.fields)
        fetched[spec.name] = rows
        print({"fetched": spec.name, "rows": len(rows), "bytes": len(raw), "ms": ms}, flush=True)
    return fetched


def sql_name_set(db: Path, table: str) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {r[0] for r in conn.execute(f"select name from {table} where name is not null")}


def sql_voucher_keys(db: Path) -> set[tuple[str, str, str]]:
    with sqlite3.connect(db) as conn:
        return {
            (r[0] or "", r[1] or "", r[2] or "")
            for r in conn.execute(
                "select voucher_type_name, voucher_number, ifnull(guid,'') from vouchers"
            )
        }


def decoded_sets(decoded_db: Path) -> dict[str, set[str]]:
    with sqlite3.connect(decoded_db) as conn:
        manager_names = {r[0] for r in conn.execute("select name from manager_records where name is not null")}
        manager_text = {
            r[0]
            for r in conn.execute(
                "select text from field_strings where file_name='Manager.1800' and text is not null"
            )
        }
        tran_text = {
            r[0]
            for r in conn.execute(
                "select text from field_strings where file_name='TranMgr.1800' and text is not null"
            )
        }
        all_text = {
            r[0]
            for r in conn.execute("select text from field_strings where text is not null")
        }
    return {
        "manager_names": manager_names,
        "manager_text": manager_text,
        "tran_text": tran_text,
        "all_text": all_text,
    }


def compare_sets(label: str, expected: set[str], actual: set[str], sample_limit: int = 25) -> dict:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    return {
        "label": label,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "matched_count": len(expected & actual),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "match_pct": round((len(expected & actual) / len(expected) * 100) if expected else 100.0, 4),
        "missing_sample": missing[:sample_limit],
        "extra_sample": extra[:sample_limit],
    }


def build_report(args: argparse.Namespace, out_dir: Path, live: dict[str, list[dict[str, str]]]) -> dict:
    decoded = decoded_sets(args.decoded_db)
    report: dict[str, object] = {
        "company": args.company,
        "from_date": args.from_date,
        "to_date": args.to_date,
        "live_counts": {name: len(rows) for name, rows in live.items()},
        "comparisons": [],
    }

    comparisons = report["comparisons"]
    assert isinstance(comparisons, list)

    table_pairs = [
        ("groups", "groups"),
        ("ledgers", "ledgers"),
        ("stock_items", "stock_items"),
        ("voucher_types", "voucher_types"),
        ("godowns", "godowns"),
        ("units", "units"),
    ]
    for live_name, table in table_pairs:
        live_names = {r.get("Name", "") for r in live.get(live_name, []) if r.get("Name")}
        xml_db_names = sql_name_set(args.xml_db, table)
        comparisons.append(compare_sets(f"live_xml_vs_xml_db:{table}", xml_db_names, live_names))
        comparisons.append(
            compare_sets(
                f"xml_db_vs_1800_decoded_manager_names:{table}",
                xml_db_names,
                decoded["manager_names"],
            )
        )
        comparisons.append(
            compare_sets(
                f"xml_db_vs_1800_decoded_manager_text:{table}",
                xml_db_names,
                decoded["manager_text"],
            )
        )

    live_voucher_keys = {
        (r.get("VoucherTypeName", ""), r.get("VoucherNumber", ""), r.get("Guid", ""))
        for r in live.get("vouchers_basic", [])
    }
    xml_voucher_keys = sql_voucher_keys(args.xml_db)
    comparisons.append(
        {
            **compare_sets(
                "live_xml_vs_xml_db:voucher_keys",
                {"|".join(k) for k in xml_voucher_keys},
                {"|".join(k) for k in live_voucher_keys},
            ),
            "note": "key = voucher_type_name|voucher_number|guid",
        }
    )

    with sqlite3.connect(args.xml_db) as conn:
        xml_party_names = {
            r[0]
            for r in conn.execute(
                "select distinct party_name from vouchers where party_name is not null and party_name != ''"
            )
        }
        xml_party_gstins = {
            r[0]
            for r in conn.execute(
                "select distinct party_gstin from vouchers where party_gstin is not null and party_gstin != ''"
            )
        }
        xml_voucher_numbers = {
            r[0]
            for r in conn.execute(
                "select distinct voucher_number from vouchers where voucher_number is not null and voucher_number != ''"
            )
        }
    comparisons.append(
        compare_sets("xml_db_voucher_party_names_vs_1800_tranmgr_text", xml_party_names, decoded["tran_text"])
    )
    comparisons.append(
        compare_sets("xml_db_voucher_party_gstins_vs_1800_tranmgr_text", xml_party_gstins, decoded["tran_text"])
    )
    comparisons.append(
        compare_sets("xml_db_voucher_numbers_vs_1800_tranmgr_text", xml_voucher_numbers, decoded["tran_text"])
    )

    (out_dir / "diff-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def write_markdown(report: dict, out_path: Path) -> None:
    lines = [
        "# Live XML / XML SQLite / `.1800` SQLite Diff",
        "",
        f"Company: `{report['company']}`",
        f"Period: `{report['from_date']}` to `{report['to_date']}`",
        "",
        "## Live Counts",
        "",
        "| Collection | Rows |",
        "| --- | ---: |",
    ]
    for name, count in report["live_counts"].items():
        lines.append(f"| `{name}` | {count:,} |")
    lines.extend(["", "## Comparisons", "", "| Check | Expected | Actual | Matched | Missing | Match % |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for cmp in report["comparisons"]:
        lines.append(
            f"| `{cmp['label']}` | {cmp['expected_count']:,} | {cmp['actual_count']:,} | "
            f"{cmp['matched_count']:,} | {cmp['missing_count']:,} | {cmp['match_pct']} |"
        )
    lines.extend(["", "## Missing Samples", ""])
    for cmp in report["comparisons"]:
        if cmp["missing_count"]:
            lines.append(f"### {cmp['label']}")
            lines.append("")
            for value in cmp["missing_sample"]:
                lines.append(f"- `{value}`")
            lines.append("")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="10.211.55.3")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--company", default="Avinash Industries - Chennai Unit - 2025-26")
    parser.add_argument("--from-date", default="20250401")
    parser.add_argument("--to-date", default="20260331")
    parser.add_argument("--xml-db", type=Path, default=Path("/Users/aakashchid/workshop/sena/office/avinash/tally-migration-2025-04-01/avinash_tally_live_2026-04-25.sqlite3"))
    parser.add_argument("--decoded-db", type=Path, default=Path("out/070525_decoded.sqlite3"))
    parser.add_argument("--out-dir", type=Path, default=Path("out/live-xml-diff-070525"))
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    live = fetch_live(args, args.out_dir)
    report = build_report(args, args.out_dir, live)
    write_markdown(report, args.out_dir / "diff-report.md")
    print(json.dumps({"report": str(args.out_dir / "diff-report.md")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


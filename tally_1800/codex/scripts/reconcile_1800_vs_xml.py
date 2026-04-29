#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


PIPELINE_SRC = Path("/Users/aakashchid/workshop/tally-db-pipeline/src")
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from tally_db_pipeline.parsers import _clean_xml, parse_vouchers  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
XML_DB = Path(
    "/Users/aakashchid/workshop/sena/office/avinash/tally-migration-2025-04-01/"
    "avinash_tally_live_2026-04-25.sqlite3"
)
DECODED_DB = ROOT / "out/070525_decoded.sqlite3"
LIVE_DIR = ROOT / "out/live-xml-diff-070525"
PROFILE_DB = ROOT / "out/live_profile_scratch.sqlite3"
OUT_DIR = ROOT / "out/reconcile-070525"
FROM_DATE = "2025-04-01"
TO_DATE = "2026-03-31"
COMPANY = "Avinash Industries - Chennai Unit - 2025-26"


MASTER_SPECS = {
    "groups": ("groups-response.xml", "GROUP", "groups"),
    "ledgers": ("ledgers-response.xml", "LEDGER", "ledgers"),
    "stock_items": ("stock_items-response.xml", "STOCKITEM", "stock_items"),
    "voucher_types": ("voucher_types-response.xml", "VOUCHERTYPE", "voucher_types"),
    "godowns": ("godowns-response.xml", "GODOWN", "godowns"),
    "units": ("units-response.xml", "UNIT", "units"),
}


def text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip().replace("\r", "")
    return ""


def live_master_names(xml_path: Path, tag: str) -> set[str]:
    raw = xml_path.read_text(encoding="utf-8", errors="replace")
    root = ET.fromstring(_clean_xml(raw))
    names = set()
    for el in root.iter(tag):
        name = (el.get("NAME") or text(el, "NAME")).strip()
        if name:
            names.add(name)
    return names


def sql_name_set(conn: sqlite3.Connection, table: str, where: str = "") -> set[str]:
    suffix = f" where {where}" if where else ""
    return {
        r[0]
        for r in conn.execute(f"select name from {table}{suffix}")
        if r[0] is not None and str(r[0]).strip()
    }


def compare(label: str, expected: set[str], actual: set[str], sample_limit: int = 30) -> dict:
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


def profile_payload() -> str:
    with sqlite3.connect(PROFILE_DB) as conn:
        row = conn.execute(
            """
            select response_xml
            from raw_payloads
            where request_type = 'voucher_profile_collection_range'
            order by id desc
            limit 1
            """
        ).fetchone()
    if not row:
        raise RuntimeError(f"No profile payload found in {PROFILE_DB}")
    return row[0]


def xml_db_vouchers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        select voucher_type_name, voucher_date, voucher_number, guid, master_id,
               party_name, party_gstin
        from vouchers
        where voucher_date between ? and ?
        """,
        (FROM_DATE, TO_DATE),
    ).fetchall()
    return [
        {
            "voucher_type_name": r[0] or "",
            "voucher_date": r[1] or "",
            "voucher_number": r[2] or "",
            "guid": r[3] or "",
            "master_id": r[4] or "",
            "party_name": r[5] or "",
            "party_gstin": r[6] or "",
        }
        for r in rows
    ]


def voucher_key(row: dict) -> str:
    return "|".join(
        [
            row.get("voucher_type_name", ""),
            row.get("voucher_date", ""),
            row.get("voucher_number", ""),
            row.get("guid", ""),
            row.get("master_id", ""),
        ]
    )


def write_samples(path: Path, values: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["value"])
        for value in values:
            writer.writerow([value])


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "company": COMPANY,
        "period": {"from": FROM_DATE, "to": TO_DATE},
        "inputs": {
            "xml_db": str(XML_DB),
            "decoded_1800_db": str(DECODED_DB),
            "live_xml_dir": str(LIVE_DIR),
            "pipeline_profile_db": str(PROFILE_DB),
        },
        "comparisons": [],
        "counts": {},
    }

    comparisons: list[dict] = report["comparisons"]  # type: ignore[assignment]
    counts: dict = report["counts"]  # type: ignore[assignment]

    with sqlite3.connect(XML_DB) as xml_conn, sqlite3.connect(DECODED_DB) as decoded_conn:
        decoded_manager_names = sql_name_set(decoded_conn, "manager_records")
        decoded_manager_text = {
            r[0]
            for r in decoded_conn.execute(
                "select distinct text from field_strings where file_name='Manager.1800' and text is not null"
            )
            if r[0]
        }
        decoded_tran_text = {
            r[0]
            for r in decoded_conn.execute(
                "select distinct text from field_strings where file_name='TranMgr.1800' and text is not null"
            )
            if r[0]
        }
        decoded_all_text = {
            r[0]
            for r in decoded_conn.execute("select distinct text from field_strings where text is not null")
            if r[0]
        }

        counts["decoded_1800"] = {
            "manager_names": len(decoded_manager_names),
            "manager_text_distinct": len(decoded_manager_text),
            "tran_text_distinct": len(decoded_tran_text),
            "all_text_distinct": len(decoded_all_text),
        }

        for label, (file_name, tag, table) in MASTER_SPECS.items():
            live_names = live_master_names(LIVE_DIR / file_name, tag)
            xml_names = sql_name_set(xml_conn, table)
            counts[label] = {"live_xml": len(live_names), "xml_db": len(xml_names)}
            comparisons.append(compare(f"live_xml_vs_xml_db:{label}", live_names, xml_names))
            comparisons.append(
                compare(f"xml_db_vs_1800_manager_records:{label}", xml_names, decoded_manager_names)
            )
            comparisons.append(compare(f"xml_db_vs_1800_manager_text:{label}", xml_names, decoded_manager_text))
            comparisons.append(compare(f"xml_db_vs_1800_all_text:{label}", xml_names, decoded_all_text))

        live_vouchers = parse_vouchers(profile_payload())
        xml_vouchers = xml_db_vouchers(xml_conn)
        counts["vouchers"] = {"live_profile": len(live_vouchers), "xml_db_fy": len(xml_vouchers)}
        live_guid = {v["guid"] for v in live_vouchers if v.get("guid")}
        xml_guid = {v["guid"] for v in xml_vouchers if v.get("guid")}
        live_master_ids = {v["master_id"] for v in live_vouchers if v.get("master_id")}
        xml_master_ids = {v["master_id"] for v in xml_vouchers if v.get("master_id")}
        live_keys = {voucher_key(v) for v in live_vouchers}
        xml_keys = {voucher_key(v) for v in xml_vouchers}

        comparisons.append(compare("live_profile_vs_xml_db:voucher_guid", live_guid, xml_guid))
        comparisons.append(compare("live_profile_vs_xml_db:voucher_master_id", live_master_ids, xml_master_ids))
        comparisons.append(compare("live_profile_vs_xml_db:voucher_composite_key", live_keys, xml_keys))

        xml_party_names = {v["party_name"] for v in xml_vouchers if v["party_name"]}
        xml_party_gstins = {v["party_gstin"] for v in xml_vouchers if v["party_gstin"]}
        xml_voucher_numbers = {v["voucher_number"] for v in xml_vouchers if v["voucher_number"]}
        xml_voucher_guids = xml_guid
        xml_voucher_master_ids = xml_master_ids
        comparisons.append(compare("xml_db_party_names_vs_1800_tranmgr_text", xml_party_names, decoded_tran_text))
        comparisons.append(compare("xml_db_party_gstins_vs_1800_tranmgr_text", xml_party_gstins, decoded_tran_text))
        comparisons.append(compare("xml_db_voucher_numbers_vs_1800_tranmgr_text", xml_voucher_numbers, decoded_tran_text))
        comparisons.append(compare("xml_db_voucher_guids_vs_1800_tranmgr_text", xml_voucher_guids, decoded_tran_text))
        comparisons.append(compare("xml_db_voucher_master_ids_vs_1800_tranmgr_text", xml_voucher_master_ids, decoded_tran_text))

        live_type_counts = Counter(v["voucher_type_name"] for v in live_vouchers)
        xml_type_counts = Counter(v["voucher_type_name"] for v in xml_vouchers)
        voucher_type_count_diffs = []
        for key in sorted(set(live_type_counts) | set(xml_type_counts)):
            if live_type_counts[key] != xml_type_counts[key]:
                voucher_type_count_diffs.append(
                    {
                        "voucher_type_name": key,
                        "live": live_type_counts[key],
                        "xml_db": xml_type_counts[key],
                        "delta": live_type_counts[key] - xml_type_counts[key],
                    }
                )
        report["voucher_type_count_diffs"] = voucher_type_count_diffs

    for cmp in comparisons:
        if cmp["missing_count"]:
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", cmp["label"])
            write_samples(OUT_DIR / f"{safe}-missing.csv", cmp["missing_sample"])
        if cmp["extra_count"]:
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", cmp["label"])
            write_samples(OUT_DIR / f"{safe}-extra.csv", cmp["extra_sample"])

    (OUT_DIR / "reconcile-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        "# Reconciliation: Live XML vs XML SQLite vs `.1800` SQLite",
        "",
        f"Company: `{COMPANY}`",
        f"Period: `{FROM_DATE}` to `{TO_DATE}`",
        "",
        "## Counts",
        "",
        "```json",
        json.dumps(counts, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Comparisons",
        "",
        "| Check | Expected | Actual | Matched | Missing | Extra | Match % |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cmp in comparisons:
        lines.append(
            f"| `{cmp['label']}` | {cmp['expected_count']:,} | {cmp['actual_count']:,} | "
            f"{cmp['matched_count']:,} | {cmp['missing_count']:,} | {cmp['extra_count']:,} | "
            f"{cmp['match_pct']} |"
        )
    lines.extend(["", "## Voucher Type Count Diffs", ""])
    diffs = report["voucher_type_count_diffs"]
    if diffs:
        lines.extend(["| Voucher Type | Live | XML DB | Delta |", "| --- | ---: | ---: | ---: |"])
        for row in diffs:  # type: ignore[assignment]
            lines.append(
                f"| `{row['voucher_type_name']}` | {row['live']} | {row['xml_db']} | {row['delta']} |"
            )
    else:
        lines.append("No voucher type count diffs.")
    lines.extend(["", "## Notes", ""])
    lines.append(
        "- `live_xml_vs_xml_db` is the strongest check here; it compares fresh Tally XML to the existing XML-derived SQLite."
    )
    lines.append(
        "- `.1800` decoded SQLite is currently field/string-level, not full typed voucher tables, so its checks are coverage checks against decoded text/name sets."
    )
    lines.append(
        "- Missing/extra samples are written as CSV files next to this report."
    )
    (OUT_DIR / "reconcile-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_DIR / "reconcile-report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


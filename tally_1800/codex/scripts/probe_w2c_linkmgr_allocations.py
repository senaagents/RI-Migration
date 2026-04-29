#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_070525_DB = ROOT / "out/070525_decoded_laneA_sales_logical.sqlite3"
DEFAULT_100025_DB = ROOT / "out/100025_decoded_current.sqlite3"
DEFAULT_LINKMGR_V0 = ROOT / "out/lane-b/070525_linkmgr_v0.sqlite3"
DEFAULT_ROSETTA = (
    ROOT.parent / "tallydatacrack-claude/corpus/rosetta.sqlite3"
)


def connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def scalar_row(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = con.execute(sql, params).fetchone()
    return dict(row) if row is not None else {}


def print_kv(section: str, values: dict[str, Any]) -> None:
    print(f"[{section}]")
    for key in sorted(values):
        print(f"{key}={values[key]}")
    print()


def table_names(con: sqlite3.Connection) -> set[str]:
    return {str(row["name"]) for row in con.execute("select name from sqlite_master where type='table'")}


def linkmgr_counts(db_path: Path, label: str) -> None:
    con = connect_ro(db_path)
    try:
        values = scalar_row(
            con,
            """
            select count(*) pages,
                   sum(case when page_class='allocation' then 1 else 0 end) allocation_pages,
                   sum(case when page_class='allocation' and voucher_master_id is not null then 1 else 0 end) voucher_ptr_pages,
                   sum(case when page_class='allocation' and ledger_master_id is not null then 1 else 0 end) ledger_ptr_pages,
                   sum(case when page_class='allocation' and voucher_resolves=1 then 1 else 0 end) voucher_resolves,
                   sum(case when page_class='allocation' and ledger_resolves=1 then 1 else 0 end) ledger_resolves,
                   sum(case when page_class='allocation' and voucher_resolves=1 and ledger_resolves=1 then 1 else 0 end) both_resolve,
                   sum(case when page_class='allocation' and voucher_resolves=1 and ledger_resolves=0 then 1 else 0 end) voucher_only_resolve,
                   sum(case when page_class='allocation' and voucher_resolves=0 and ledger_resolves=1 then 1 else 0 end) ledger_only_resolve,
                   sum(case when page_class='allocation' and voucher_resolves=0 and ledger_resolves=0 then 1 else 0 end) neither_resolve
            from linkmgr_allocation_pages
            """,
        )
        print_kv(f"{label}_linkmgr_pages", values)
    finally:
        con.close()


def rosetta_allocation_counts(rosetta_path: Path) -> None:
    con = connect_ro(rosetta_path)
    try:
        counts = Counter()
        for row in con.execute(
            """
            select e.bill_allocations_json, e.bank_allocations_json
            from voucher_ledger_entries e
            """
        ):
            counts["ledger_entries"] += 1
            for kind, raw in (
                ("bill", row["bill_allocations_json"]),
                ("bank", row["bank_allocations_json"]),
            ):
                if raw and raw not in {"[]", ""}:
                    counts[f"{kind}_entries"] += 1
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        counts[f"{kind}_json_errors"] += 1
                        continue
                    if isinstance(parsed, list):
                        counts[f"{kind}_rows"] += len(parsed)
                    else:
                        counts[f"{kind}_json_not_list"] += 1
        print_kv("rosetta_allocations_070525", dict(counts))
    finally:
        con.close()


def rosetta_pair_overlap(decoded_path: Path, rosetta_path: Path) -> None:
    con = connect_ro(decoded_path)
    try:
        con.execute(
            "attach database ? as r",
            (f"file:{rosetta_path.resolve().as_posix()}?mode=ro",),
        )
        values = scalar_row(
            con,
            """
            with lm as (
              select distinct voucher_master_id, ledger_name
              from linkmgr_allocation_pages
              where page_class='allocation'
                and voucher_resolves=1
                and ledger_resolves=1
                and ledger_name is not null
            ),
            re as (
              select cast(v.master_id as int) voucher_master_id,
                     e.ledger_name,
                     case when e.bill_allocations_json is not null
                            and e.bill_allocations_json not in ('', '[]')
                          then 1 else 0 end has_bill,
                     case when e.bank_allocations_json is not null
                            and e.bank_allocations_json not in ('', '[]')
                          then 1 else 0 end has_bank
              from r.voucher_ledger_entries e
              join r.vouchers v on v.id=e.voucher_id
              where v.master_id is not null
                and (
                  (e.bill_allocations_json is not null and e.bill_allocations_json not in ('', '[]'))
                  or
                  (e.bank_allocations_json is not null and e.bank_allocations_json not in ('', '[]'))
                )
            )
            select count(*) allocation_ledger_entries,
                   sum(has_bill) bill_entries,
                   sum(has_bank) bank_entries,
                   sum(case when lm.voucher_master_id is not null then 1 else 0 end) entries_with_linkmgr_pair,
                   sum(case when has_bill=1 and lm.voucher_master_id is not null then 1 else 0 end) bill_entries_with_linkmgr_pair,
                   sum(case when has_bank=1 and lm.voucher_master_id is not null then 1 else 0 end) bank_entries_with_linkmgr_pair
            from re
            left join lm using(voucher_master_id, ledger_name)
            """,
        )
        print_kv("current_page_pair_overlap_070525", values)
    finally:
        con.close()


def bank_field_coverage(v0_path: Path) -> None:
    con = connect_ro(v0_path)
    try:
        coverage = scalar_row(
            con,
            """
            select count(*) bank_rows,
                   sum(payment_mode is not null and payment_mode!='') payment_mode,
                   sum(branch is not null and branch!='') branch,
                   sum(utr_or_instrument is not null and utr_or_instrument!='') utr_or_instrument,
                   sum(instrument_number is not null and instrument_number!='') instrument_number,
                   sum(bank_name is not null and bank_name!='') bank_name,
                   sum(account_number is not null and account_number!='') account_number,
                   sum(ac_payee is not null and ac_payee!='') ac_payee,
                   sum(print_status is not null and print_status!='') print_status,
                   sum(ledger_name is not null and ledger_name!='') ledger_name,
                   sum(allocation_guid is not null and allocation_guid!='') allocation_guid
            from bank_allocations
            """,
        )
        print_kv("bank_field_coverage_070525_v0", coverage)

        field_counts: Counter[tuple[str, str]] = Counter()
        field_distinct: dict[tuple[str, str], set[str]] = {}
        for row in con.execute("select tlvs_json from bank_allocations"):
            for item in json.loads(row["tlvs_json"] or "[]"):
                key = (str(item.get("fid")), str(item.get("type")))
                field_counts[key] += 1
                field_distinct.setdefault(key, set()).add(str(item.get("val")))

        print("[bank_tlv_field_counts_070525_v0]")
        for (fid, typ), count in field_counts.most_common():
            distinct = len(field_distinct[(fid, typ)])
            samples = sorted(field_distinct[(fid, typ)])[:3]
            print(f"{fid}/{typ} count={count} distinct_values={distinct} samples={samples}")
        print()
    finally:
        con.close()


def gap_100025(db_path: Path) -> None:
    con = connect_ro(db_path)
    try:
        link_values = scalar_row(
            con,
            """
            with l as (
              select voucher_master_id link_value, count(*) link_pages
              from linkmgr_allocation_pages
              where page_class='allocation'
                and voucher_master_id is not null
              group by voucher_master_id
            ),
            h as (select master_id from voucher_header_candidates),
            d as (
              select voucher_date_serial
              from voucher_header_candidates
              where voucher_date_serial is not null
              group by voucher_date_serial
            )
            select count(*) distinct_link_values,
                   sum(link_pages) link_pages,
                   min(link_value) min_link_value,
                   max(link_value) max_link_value,
                   sum(case when h.master_id is not null then 1 else 0 end) values_matching_master_id,
                   sum(case when h.master_id is not null then link_pages else 0 end) pages_matching_master_id,
                   sum(case when d.voucher_date_serial is not null then 1 else 0 end) values_matching_date_serial,
                   sum(case when d.voucher_date_serial is not null then link_pages else 0 end) pages_matching_date_serial
            from l
            left join h on h.master_id=l.link_value
            left join d on d.voucher_date_serial=l.link_value
            """,
        )
        print_kv("100025_0x232a_0d_gap", link_values)

        headers = scalar_row(
            con,
            """
            select count(*) headers,
                   min(master_id) min_master_id,
                   max(master_id) max_master_id,
                   min(voucher_date_serial) min_voucher_date_serial,
                   max(voucher_date_serial) max_voucher_date_serial
            from voucher_header_candidates
            """,
        )
        print_kv("100025_headers", headers)

        date_join = scalar_row(
            con,
            """
            with l as (
              select voucher_master_id link_value, count(*) link_pages
              from linkmgr_allocation_pages
              where page_class='allocation'
                and voucher_master_id is not null
              group by voucher_master_id
            ),
            dc as (
              select voucher_date_serial, count(*) header_count
              from voucher_header_candidates
              where voucher_date_serial is not null
              group by voucher_date_serial
            )
            select count(*) matched_link_values,
                   sum(link_pages) matched_link_pages,
                   sum(header_count) distinct_link_value_header_join_rows,
                   sum(link_pages * header_count) page_header_join_rows,
                   max(header_count) max_headers_for_one_link_value
            from l
            join dc on dc.voucher_date_serial=l.link_value
            """,
        )
        print_kv("100025_forced_date_join_risk", date_join)

        if "compact_numeric_fields" in table_names(con):
            compact = scalar_row(
                con,
                """
                select count(distinct value) distinct_values,
                       min(value) min_value,
                       max(value) max_value,
                       sum(case when value between 45000 and 45400 then 1 else 0 end) rows_in_link_value_range,
                       sum(case when value between 309000 and 340000 then 1 else 0 end) rows_in_header_master_range
                from compact_numeric_fields
                where field_id=0x0bbb and type_hex='0006'
                """,
            )
            print_kv("100025_compact_0x0bbb_0006", compact)
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only W2C LinkMgr allocation reconciliation probe."
    )
    parser.add_argument("--db-070525", type=Path, default=DEFAULT_070525_DB)
    parser.add_argument("--db-100025", type=Path, default=DEFAULT_100025_DB)
    parser.add_argument("--linkmgr-v0", type=Path, default=DEFAULT_LINKMGR_V0)
    parser.add_argument("--rosetta", type=Path, default=DEFAULT_ROSETTA)
    args = parser.parse_args()

    linkmgr_counts(args.db_070525, "070525")
    linkmgr_counts(args.db_100025, "100025")
    rosetta_allocation_counts(args.rosetta)
    rosetta_pair_overlap(args.db_070525, args.rosetta)
    bank_field_coverage(args.linkmgr_v0)
    gap_100025(args.db_100025)


if __name__ == "__main__":
    main()

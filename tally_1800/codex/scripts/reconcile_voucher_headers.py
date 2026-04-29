from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path


ROSETTA_KEY_RE = re.compile(r".*-([0-9a-fA-F]{8}):([0-9a-fA-F]{8})$")


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def rosetta_key_suffix(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    text = str(value)
    if text.isdigit():
        number = int(text)
        return number >> 32, number & 0xFFFFFFFF
    match = ROSETTA_KEY_RE.match(text)
    if match:
        return int(match.group(1), 16), int(match.group(2), 16)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_sqlite", type=Path)
    parser.add_argument("--rosetta", type=Path, required=True)
    parser.add_argument("--internal-base-type", type=int, default=31)
    args = parser.parse_args()

    conn = sqlite3.connect(args.decoded_sqlite)
    ro = sqlite3.connect(args.rosetta)

    candidates = {
        int(row[0]): row
        for row in conn.execute(
            """
            select master_id, guid_guess, voucher_date_guess, key_raw, base_type_code,
                   key_offset, voucher_number_guess, party_guess, narration_guess,
                   page_group_id, page_group_page_count
            from voucher_header_candidates
            """
        )
    }
    key_candidates = {mid: row for mid, row in candidates.items() if row[3] is not None}
    exportable = {
        mid: row
        for mid, row in key_candidates.items()
        if row[4] != args.internal_base_type
    }

    rosetta_rows = {
        int(row[0]): row
        for row in ro.execute(
            """
            select master_id, guid, voucher_date, voucher_key, voucher_number,
                   party_name, narration, voucher_type_name, base_voucher_type
            from vouchers
            where master_id is not null and master_id != ''
            """
        )
    }

    matched_ids = set(exportable) & set(rosetta_rows)
    print(f"decoded_candidates_all={len(candidates)}")
    print(f"decoded_candidates_with_key={len(key_candidates)}")
    print(f"decoded_exportable_base_type_not_{args.internal_base_type}={len(exportable)}")
    print(f"rosetta_vouchers={len(rosetta_rows)}")
    print(f"matched_master_ids={len(matched_ids)}")
    print(f"missing_rosetta_master_ids={len(set(rosetta_rows) - set(exportable))}")
    print(f"extra_exportable_master_ids={len(set(exportable) - set(rosetta_rows))}")

    guid_ok = date_ok = key_ok = 0
    key_total = 0
    number_ok = party_ok = narration_ok = 0
    number_total = party_total = narration_total = 0
    for mid in matched_ids:
        cand = exportable[mid]
        ros = rosetta_rows[mid]
        _, guid_guess, date_guess, key_raw, _base_type, key_offset, number_guess, party_guess, narration_guess, _group_id, _group_count = cand
        _mid, guid, voucher_date, voucher_key, voucher_number, party_name, narration, _vt, _base = ros
        if guid_guess == guid:
            guid_ok += 1
        if date_guess == voucher_date:
            date_ok += 1
        suffix = rosetta_key_suffix(voucher_key)
        if suffix and key_raw:
            key_total += 1
            parts = key_raw.split("-")
            if len(parts) == 3 and int(parts[0], 16) == suffix[0] and int(parts[2], 16) == suffix[1]:
                key_ok += 1
        if voucher_number:
            number_total += 1
            if normalize_text(number_guess) == normalize_text(voucher_number):
                number_ok += 1
        if party_name:
            party_total += 1
            if normalize_text(party_guess) == normalize_text(party_name):
                party_ok += 1
        if narration:
            narration_total += 1
            cn = normalize_text(narration_guess)
            rn = normalize_text(narration)
            if cn and (cn in rn or rn in cn):
                narration_ok += 1

    print(f"guid_exact={guid_ok}/{len(matched_ids)}")
    print(f"date_exact={date_ok}/{len(matched_ids)}")
    print(f"voucher_key_suffix_exact={key_ok}/{key_total}")
    print(f"same_page_number_exact={number_ok}/{number_total}")
    print(f"same_page_party_exact={party_ok}/{party_total}")
    print(f"same_page_narration_contains={narration_ok}/{narration_total}")

    internal = [
        row
        for row in conn.execute(
            """
            select master_id, voucher_date_guess, key_raw, key_offset
            from voucher_header_candidates
            where key_raw is not null and base_type_code = ?
            order by master_id
            """,
            (args.internal_base_type,),
        )
    ]
    print(f"internal_base_type_{args.internal_base_type}_count={len(internal)}")
    if internal:
        print("internal_sample=" + "; ".join(str(tuple(row)) for row in internal[:10]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

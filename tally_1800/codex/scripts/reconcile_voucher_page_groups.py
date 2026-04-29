from __future__ import annotations

import argparse
import re
import sqlite3
import struct
from collections import defaultdict
from pathlib import Path


def norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_sqlite", type=Path)
    parser.add_argument("--tranmgr", type=Path, required=True)
    parser.add_argument("--rosetta", type=Path, required=True)
    args = parser.parse_args()

    conn = sqlite3.connect(args.decoded_sqlite)
    ro = sqlite3.connect(args.rosetta)

    candidates = {
        int(row[0]): row
        for row in conn.execute(
            """
            select master_id, page_group_id, page_group_page_count
            from voucher_header_candidates
            where key_raw is not null and base_type_code != 31
            """
        )
    }
    groups = {row[1] for row in candidates.values()}
    texts_by_group: dict[int, list[str]] = defaultdict(list)
    data = args.tranmgr.read_bytes()
    page_group = [
        struct.unpack_from("<I", data, page_no * 512 + 4)[0]
        for page_no in range(len(data) // 512)
    ]
    for page_no, text in conn.execute(
        """
        select page_no, decoded_text
        from raw_tlvs
        where file_name = 'TranMgr.1800'
          and decoded_text is not null
        """
    ):
        if page_no < len(page_group):
            group_id = page_group[page_no]
            if group_id in groups:
                texts_by_group[group_id].append(text)

    number_total = number_group = party_total = party_group = narr_total = narr_group = 0
    inv_total = inv_item_group = inv_ledger_total = inv_ledger_group = 0
    for mid, voucher_number, party_name, narration in ro.execute(
        """
        select cast(master_id as integer), coalesce(voucher_number, ''),
               coalesce(party_name, ''), coalesce(narration, '')
        from vouchers
        """
    ):
        cand = candidates.get(mid)
        if not cand:
            continue
        group_id = cand[1]
        vals = [norm(v) for v in texts_by_group.get(group_id, [])]
        if voucher_number:
            number_total += 1
            if norm(voucher_number) in vals:
                number_group += 1
        if party_name:
            party_total += 1
            if norm(party_name) in vals:
                party_group += 1
        if narration:
            narr_total += 1
            rn = norm(narration)
            if any(rn and (rn in v or v in rn) for v in vals):
                narr_group += 1

    for mid, item_name, ledger_name in ro.execute(
        """
        select cast(v.master_id as integer), coalesce(e.item_name, ''),
               coalesce(e.ledger_name, '')
        from voucher_inventory_entries e
        join vouchers v on v.id = e.voucher_id
        """
    ):
        cand = candidates.get(mid)
        if not cand:
            continue
        vals = [norm(v) for v in texts_by_group.get(cand[1], [])]
        if item_name:
            inv_total += 1
            if norm(item_name) in vals:
                inv_item_group += 1
        if ledger_name:
            inv_ledger_total += 1
            if norm(ledger_name) in vals:
                inv_ledger_group += 1

    print(f"exportable_candidates={len(candidates)}")
    print(f"page_group_count_min_max={min((r[2] for r in candidates.values()), default=0)},{max((r[2] for r in candidates.values()), default=0)}")
    print(f"group_number_exact={number_group}/{number_total}")
    print(f"group_party_exact={party_group}/{party_total}")
    print(f"group_narration_contains={narr_group}/{narr_total}")
    print(f"group_inventory_item_exact={inv_item_group}/{inv_total}")
    print(f"group_inventory_ledger_exact={inv_ledger_group}/{inv_ledger_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

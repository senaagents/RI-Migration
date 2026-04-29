from __future__ import annotations

import argparse
import json
import sqlite3
import struct
from collections import defaultdict
from pathlib import Path


def manager_name_to_id(decoded_sqlite: Path) -> dict[str, int]:
    conn = sqlite3.connect(decoded_sqlite)
    out = {}
    for name, header_json in conn.execute(
        "select name, header_words_json from manager_records where name is not null"
    ):
        try:
            out[name] = int(json.loads(header_json)[1])
        except Exception:
            continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_sqlite", type=Path)
    parser.add_argument("--manager-decoded", type=Path, required=True)
    parser.add_argument("--tranmgr", type=Path, required=True)
    parser.add_argument("--rosetta", type=Path, required=True)
    parser.add_argument("--limit-per-type", type=int, default=2000)
    args = parser.parse_args()

    conn = sqlite3.connect(args.decoded_sqlite)
    ro = sqlite3.connect(args.rosetta)
    name_to_id = manager_name_to_id(args.manager_decoded)
    data = args.tranmgr.read_bytes()
    page_count = len(data) // 512
    page_group = [struct.unpack_from("<I", data, page_no * 512 + 4)[0] for page_no in range(page_count)]
    pages_by_group: dict[int, list[int]] = defaultdict(list)
    for page_no, group_id in enumerate(page_group):
        pages_by_group[group_id].append(page_no)

    candidates = {
        int(row[0]): int(row[1])
        for row in conn.execute(
            """
            select master_id, page_group_id
            from voucher_header_candidates
            where key_raw is not null and base_type_code != 31
            """
        )
    }

    voucher_types = [
        row[0]
        for row in ro.execute(
            """
            select voucher_type_name
            from voucher_inventory_entries e
            join vouchers v on v.id = e.voucher_id
            group by voucher_type_name
            order by count(*) desc
            """
        )
    ]
    for voucher_type in voucher_types:
        total = found = found_u32 = 0
        examples = []
        for mid, item_name in ro.execute(
            """
            select cast(v.master_id as integer), e.item_name
            from voucher_inventory_entries e
            join vouchers v on v.id = e.voucher_id
            where v.voucher_type_name = ?
            limit ?
            """,
            (voucher_type, args.limit_per_type),
        ):
            if mid not in candidates or item_name not in name_to_id:
                continue
            total += 1
            item_id = name_to_id[item_name]
            needle4 = struct.pack("<I", item_id)
            needle2 = struct.pack("<H", item_id) if item_id < 65536 else None
            hit = None
            for page_no in pages_by_group[candidates[mid]]:
                page = data[page_no * 512 : (page_no + 1) * 512]
                offset = page.find(needle4)
                if offset >= 0:
                    hit = (page_no, offset, "u32")
                    break
                if needle2 is not None:
                    offset = page.find(needle2)
                    if offset >= 0:
                        hit = (page_no, offset, "u16")
                        break
            if hit:
                found += 1
                if hit[2] == "u32":
                    found_u32 += 1
                if len(examples) < 3:
                    examples.append((mid, item_name, item_id, hit))
        if total:
            print(
                f"{voucher_type}\t{found}/{total}\t{found * 100 / total:.1f}%\t"
                f"u32={found_u32}\texamples={examples}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

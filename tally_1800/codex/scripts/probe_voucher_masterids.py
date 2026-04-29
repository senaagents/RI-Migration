from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from tally1800.probe import iter_compact_numeric_fields


MASTERID_FIELD = 0x0BBB
MASTERID_TYPE = "0006"


def load_rosetta_master_ids(path: Path) -> set[int]:
    conn = sqlite3.connect(path)
    return {
        int(row[0])
        for row in conn.execute(
            "select master_id from vouchers where master_id is not null and master_id != ''"
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tranmgr", type=Path)
    parser.add_argument("--rosetta", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    rosetta_ids = load_rosetta_master_ids(args.rosetta)
    values: dict[int, tuple[int, int]] = {}
    duplicates = 0
    examples = []
    total = 0
    for hit in iter_compact_numeric_fields(
        args.tranmgr,
        field_ids={MASTERID_FIELD},
        type_hexes={MASTERID_TYPE},
    ):
        total += 1
        if hit.value in values:
            duplicates += 1
        else:
            values[hit.value] = (hit.offset, hit.page_no)
        if hit.value in rosetta_ids and len(examples) < args.limit:
            examples.append(hit)

    matched = set(values) & rosetta_ids
    missing = rosetta_ids - set(values)
    extras = set(values) - rosetta_ids

    print(f"field=0x{MASTERID_FIELD:04x} type={MASTERID_TYPE}")
    print(f"total_hits={total}")
    print(f"distinct_values={len(values)}")
    print(f"duplicate_values={duplicates}")
    print(f"rosetta_master_ids={len(rosetta_ids)}")
    print(f"matched={len(matched)}")
    print(f"missing={len(missing)}")
    print(f"extras={len(extras)}")
    if missing:
        print("missing_sample=" + ",".join(str(v) for v in sorted(missing)[: args.limit]))
    if extras:
        print("extras_sample=" + ",".join(str(v) for v in sorted(extras)[: args.limit]))
    for hit in examples:
        print(
            f"offset=0x{hit.offset:x} page={hit.page_no} "
            f"master_id={hit.value} hex=0x{hit.value:08x}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

from tally1800.probe import iter_tlvs


VOUCHER_KEY_FIELD = 0x0BBE
KEY_RE = re.compile(r".*-([0-9a-fA-F]{8}):([0-9a-fA-F]{8})$")


def decode_composite_key(text: str) -> int | None:
    match = KEY_RE.match(text)
    if not match:
        return None
    high = int(match.group(1), 16)
    low = int(match.group(2), 16)
    return (high << 32) + low


def load_rosetta_keys(path: Path) -> dict[str, tuple[str, ...]]:
    conn = sqlite3.connect(path)
    rows = conn.execute(
        """
        select voucher_key, master_id, guid, voucher_date, voucher_type_name,
               voucher_number, party_name, narration
        from vouchers
        where voucher_key is not null and voucher_key != ''
        """
    ).fetchall()
    return {str(row[0]): tuple("" if value is None else str(value) for value in row[1:]) for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tranmgr", type=Path)
    parser.add_argument("--rosetta", type=Path)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    rosetta = load_rosetta_keys(args.rosetta) if args.rosetta else {}
    total = 0
    parseable: set[str] = set()
    matched: set[str] = set()
    examples = []

    for hit in iter_tlvs(args.tranmgr):
        if hit.field_id != VOUCHER_KEY_FIELD or not isinstance(hit.decoded, str):
            continue
        total += 1
        key_int = decode_composite_key(hit.decoded)
        if key_int is None:
            continue
        key = str(key_int)
        parseable.add(key)
        if rosetta and key in rosetta:
            matched.add(key)
            if len(examples) < args.limit:
                examples.append((hit.offset, hit.page_no, hit.decoded, key, rosetta[key]))

    print(f"field=0x{VOUCHER_KEY_FIELD:04x}")
    print(f"total={total}")
    print(f"parseable_distinct={len(parseable)}")
    if rosetta:
        print(f"rosetta_keys={len(rosetta)}")
        print(f"matched_distinct={len(matched)}")
    for offset, page_no, raw, key, row in examples:
        print()
        print(f"offset=0x{offset:x} page={page_no} raw={raw} decimal={key}")
        print(
            "master_id={0} guid={1} date={2} type={3} number={4} party={5} narration={6}".format(
                *row
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

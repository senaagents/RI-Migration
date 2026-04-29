from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from datetime import date, timedelta
from pathlib import Path


PAGE_SIZE = 512
SLOT_SIZE = 0x80
SLOT_BASES = (0x80, 0x100, 0x180)


def serial_to_date(value: int) -> str | None:
    # Tally date serials observed here line up with 1899-12-31.
    if not 1 <= value <= 200000:
        return None
    try:
        return (date(1899, 12, 31) + timedelta(days=value)).isoformat()
    except OverflowError:
        return None


def iter_slots(path: Path):
    data = path.read_bytes()
    page_count = len(data) // PAGE_SIZE
    for page_no in range(1, page_count):
        page = data[page_no * PAGE_SIZE : (page_no + 1) * PAGE_SIZE]
        for base in SLOT_BASES:
            slot = page[base : base + SLOT_SIZE]
            if len(slot) != SLOT_SIZE or not any(slot):
                continue
            words = [struct.unpack_from("<I", slot, off)[0] for off in range(0, SLOT_SIZE, 4)]
            yield {
                "page_no": page_no,
                "slot_offset": base,
                "absolute_offset": page_no * PAGE_SIZE + base,
                "words": words,
                "date_serial_0x28": words[0x28 // 4],
                "date_0x28": serial_to_date(words[0x28 // 4]),
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    slots = list(iter_slots(args.file))
    flag_counter = Counter(slot["words"][1] for slot in slots)
    date_counter = Counter(slot["date_0x28"] for slot in slots if slot["date_0x28"])
    result = {
        "file": str(args.file),
        "size": args.file.stat().st_size,
        "page_count": args.file.stat().st_size // PAGE_SIZE,
        "non_empty_slots": len(slots),
        "word_0x04_counts": flag_counter.most_common(20),
        "date_0x28_counts_top": date_counter.most_common(20),
        "samples": slots[: args.limit],
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

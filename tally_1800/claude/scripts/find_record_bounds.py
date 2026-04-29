"""Look at the bytes immediately BEFORE each field=0x0067 (name) occurrence.
Goal: find a constant pattern that marks record-boundary in Manager.1800.
"""
import struct
import sys
from collections import Counter
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"
NAME_FIELD = 0x0067


def find_name_records(data: bytes) -> list[int]:
    """Return offsets of every '02 10 67 00 00 0f [len] [valid utf16le]'."""
    out = []
    idx = 0
    while True:
        p = data.find(MARKER, idx)
        if p < 0:
            break
        idx = p + 1
        if p + 8 > len(data):
            continue
        fid = struct.unpack_from("<H", data, p + 2)[0]
        if fid != NAME_FIELD:
            continue
        if data[p + 4 : p + 6] != TYPE_STRING_UTF16:
            continue
        length = struct.unpack_from("<H", data, p + 6)[0]
        if length < 2 or length % 2 != 0 or p + 8 + length > len(data):
            continue
        val = data[p + 8 : p + 8 + length]
        if val[-2:] != b"\x00\x00":
            continue
        try:
            text = val[:-2].decode("utf-16le")
            if not all(c.isprintable() or c in "\t\n " for c in text):
                continue
        except UnicodeDecodeError:
            continue
        out.append(p)
    return out


def main(path: Path):
    data = path.read_bytes()
    name_offsets = find_name_records(data)
    print(f"Name-field (0x67) records found: {len(name_offsets)}\n")

    # Bytes BEFORE the name field — look at varying prefix lengths
    prefix_counter = {n: Counter() for n in (4, 8, 12, 16, 20, 24, 32)}
    for off in name_offsets:
        for n in prefix_counter:
            if off >= n:
                prefix_counter[n][bytes(data[off - n : off])] += 1

    # Look for shared prefixes
    for n, counter in prefix_counter.items():
        print(f"Common prefix-{n} preceding name field (top 5):")
        for prefix, count in counter.most_common(5):
            pct = count / len(name_offsets) * 100
            print(f"  {prefix.hex()}  ({prefix!r:<60})  {count:>5} ({pct:.1f}%)")
        print()

    # Show 5 random surrounding contexts
    print("--- 5 sample contexts (32 bytes before, 16 after each name marker) ---")
    import random
    sample = random.sample(name_offsets, min(5, len(name_offsets)))
    for off in sample:
        s = max(0, off - 32)
        e = min(len(data), off + 32)
        chunk = data[s:e]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        print(f"\n@ name-marker-offset 0x{off:08x}:")
        for i in range(0, len(chunk), 16):
            row = chunk[i : i + 16]
            hexrow = " ".join(f"{b:02x}" for b in row)
            ascrow = "".join(chr(b) if 0x20 <= b < 0x7f else "." for b in row)
            print(f"  0x{s+i:08x}  {hexrow:<47}  |{ascrow}|")


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Manager.1800"
    )
    main(p)

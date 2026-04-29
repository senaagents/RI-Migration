"""TLV scanner — finds and decodes all `02 10 [id] [type] [len] [value]` records
in a .1800 file.

Hypothesis (from Company.1800 dissection):
  marker:  02 10                  (constant 2 bytes)
  id:      u16 LE                 (field id; meaning depends on parent record type)
  type:    2 bytes                (00 0f = utf-16le string with u16 byte length)
                                  (other type codes TBD: ints, dates, refs)
  len:     u16 LE                 (byte length of value, includes null term for strings)
  value:   `len` bytes            (for strings: utf-16le ending with 00 00)

Output: every `02 10` we find, with parsed fields when type is recognized.
"""
import struct
import sys
from collections import Counter
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"


def scan(data: bytes) -> list[dict]:
    out = []
    idx = 0
    while True:
        p = data.find(MARKER, idx)
        if p < 0:
            break
        if p + 8 > len(data):
            break
        field_id = struct.unpack_from("<H", data, p + 2)[0]
        type_bytes = data[p + 4 : p + 6]
        length = struct.unpack_from("<H", data, p + 6)[0]
        rec = {
            "off": p,
            "field_id": field_id,
            "type": type_bytes.hex(),
            "len": length,
            "value": None,
            "decoded": None,
        }
        end = p + 8 + length
        if end <= len(data):
            value = data[p + 8 : end]
            rec["value"] = value
            if type_bytes == TYPE_STRING_UTF16:
                # strip trailing null (00 00) if present
                v = value
                if len(v) >= 2 and v[-2:] == b"\x00\x00":
                    v = v[:-2]
                try:
                    rec["decoded"] = v.decode("utf-16le", errors="replace")
                except Exception:
                    rec["decoded"] = None
        out.append(rec)
        idx = p + 1  # advance by 1 in case markers overlap; will mostly skip-by-len
    return out


def report(records: list[dict], data_len: int):
    print(f"Found {len(records)} TLV markers across {data_len} bytes "
          f"(density: 1 per {data_len // max(1, len(records))} bytes)\n")

    # Type-code histogram
    type_counter = Counter(r["type"] for r in records)
    print("Type-code histogram:")
    for t, c in type_counter.most_common(10):
        print(f"  type={t}  count={c}")
    print()

    # Length sanity
    valid_strings = [r for r in records if r["type"] == "000f" and r["decoded"] is not None]
    print(f"String records (type=000f) decoded successfully: {len(valid_strings)}")
    print()

    # Field-id histogram for strings
    print("Top 20 string field IDs and a sample value each:")
    field_counter = Counter(r["field_id"] for r in valid_strings)
    for fid, count in field_counter.most_common(20):
        sample = next(r["decoded"] for r in valid_strings if r["field_id"] == fid)
        sample = (sample[:60] + "...") if len(sample) > 60 else sample
        print(f"  id=0x{fid:04x} ({fid:>5})  count={count:>4}  e.g. {sample!r}")
    print()

    # Show all unique decoded strings (limited)
    unique = sorted(set(r["decoded"] for r in valid_strings if r["decoded"]))
    print(f"Unique decoded strings: {len(unique)}")
    print("First 30 unique:")
    for s in unique[:30]:
        print(f"  {s!r}")


def main(path: Path):
    data = path.read_bytes()
    print(f"== {path} ({len(data)} bytes) ==\n")
    records = scan(data)
    report(records, len(data))


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Company.1800"
    )
    main(p)

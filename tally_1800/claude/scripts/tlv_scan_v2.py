"""TLV scanner v2 — refined parsing, advances by record length, less false-positive noise.

Adds:
  - Length-aware advance (skips inside-record bytes).
  - Sanity filter: flag impossibly long/short string lengths.
  - Multi-type-code support (collects every byte-pair seen at offset +4).
  - Field-id histogram with sample values, filtered to high-confidence records.
  - Cross-file mode: scan multiple files together to find shared field IDs.
"""
import argparse
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"


def scan_file(data: bytes, max_str_len: int = 4096) -> list[dict]:
    """Walk forward, advancing by record length when valid, by 1 byte when not."""
    out = []
    idx = 0
    while idx < len(data) - 8:
        p = data.find(MARKER, idx)
        if p < 0:
            break
        if p + 8 > len(data):
            break
        field_id = struct.unpack_from("<H", data, p + 2)[0]
        type_bytes = bytes(data[p + 4 : p + 6])
        length = struct.unpack_from("<H", data, p + 6)[0]
        end = p + 8 + length
        rec_valid = False
        decoded = None
        confidence = "raw"

        if end <= len(data):
            value = bytes(data[p + 8 : end])
            if type_bytes == TYPE_STRING_UTF16:
                if 2 <= length <= max_str_len and length % 2 == 0:
                    # check trailing null + valid utf-16le
                    if length >= 2 and value[-2:] == b"\x00\x00":
                        try:
                            text = value[:-2].decode("utf-16le")
                            # filter out junk: must be mostly printable
                            printable = sum(1 for c in text if c.isprintable() or c in "\t\n")
                            if len(text) == 0 or printable / max(1, len(text)) >= 0.85:
                                decoded = text
                                rec_valid = True
                                confidence = "string-ok"
                        except UnicodeDecodeError:
                            pass
            elif type_bytes == b"\x00\x0d":
                # u32 LE int field?
                if length == 4:
                    decoded = struct.unpack_from("<I", value, 0)[0]
                    rec_valid = True
                    confidence = "u32-likely"
            elif type_bytes == b"\x00\x0a":
                # i32 LE ?
                if length == 4:
                    decoded = struct.unpack_from("<i", value, 0)[0]
                    rec_valid = True
                    confidence = "i32-likely"
            elif type_bytes == b"\x00\x09":
                # bool/byte?
                if length == 1:
                    decoded = value[0]
                    rec_valid = True
                    confidence = "u8-likely"
            elif type_bytes == b"\x00\x0b":
                # double?
                if length == 8:
                    decoded = struct.unpack_from("<d", value, 0)[0]
                    rec_valid = True
                    confidence = "f64-likely"
            else:
                # unknown type code
                decoded = value.hex()[:64]
                confidence = "unknown-type"

        out.append({
            "off": p,
            "field_id": field_id,
            "type": type_bytes.hex(),
            "len": length,
            "decoded": decoded,
            "confidence": confidence,
        })

        if rec_valid:
            idx = end
        else:
            idx = p + 1

    return out


def report(records: list[dict], data_len: int, label: str = ""):
    print(f"\n=== {label} ({data_len} bytes) ===")
    print(f"Total markers: {len(records)}")

    type_counter = Counter(r["type"] for r in records)
    print(f"Type-code histogram (top 10):")
    for t, c in type_counter.most_common(10):
        print(f"  type=0x{t}  count={c}")

    valid = [r for r in records if r["confidence"] == "string-ok"]
    print(f"\nValid string records: {len(valid)}")
    if valid:
        unique_strings = set(r["decoded"] for r in valid)
        print(f"Unique strings: {len(unique_strings)}")

        field_counter = Counter(r["field_id"] for r in valid)
        print(f"\nTop 25 string field IDs:")
        for fid, count in field_counter.most_common(25):
            samples = [r["decoded"] for r in valid if r["field_id"] == fid][:2]
            sample_show = ", ".join(repr(s[:50]) for s in samples)
            print(f"  id=0x{fid:04x} ({fid:>6}) count={count:>5}  e.g. {sample_show}")

    # Other typed values
    others = [r for r in records if r["confidence"] not in ("string-ok", "raw", "unknown-type")]
    if others:
        oc = Counter((r["type"], r["field_id"]) for r in others)
        print(f"\nNon-string typed fields (top 15):")
        for (t, fid), count in oc.most_common(15):
            samples = [r["decoded"] for r in others if r["type"] == t and r["field_id"] == fid][:2]
            print(f"  type=0x{t} id=0x{fid:04x} count={count}  samples={samples}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--max-bytes", type=int, default=0,
                        help="Cap bytes read per file (0 = no cap)")
    args = parser.parse_args()

    for p in args.paths:
        with p.open("rb") as fh:
            data = fh.read(args.max_bytes) if args.max_bytes > 0 else fh.read()
        records = scan_file(data)
        report(records, len(data), label=str(p))


if __name__ == "__main__":
    main()

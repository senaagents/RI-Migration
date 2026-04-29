"""Probe TranMgr.1800 — check if same TLV format applies to vouchers.

Strategy:
  1. Sample first 50 MB of TranMgr.1800.
  2. Run TLV scanner; histogram field IDs.
  3. Cross-reference: do we find known voucher numbers / dates / party names?
  4. Identify the voucher-name (or voucher-number) field id.
"""
import sqlite3
import struct
import sys
from collections import Counter
from pathlib import Path


MARKER = b"\x02\x10"


def all_tlvs(data: bytes):
    idx = 0
    while idx < len(data) - 8:
        p = data.find(MARKER, idx)
        if p < 0 or p + 8 > len(data):
            break
        fid = struct.unpack_from("<H", data, p + 2)[0]
        type_bytes = bytes(data[p + 4 : p + 6])
        length = struct.unpack_from("<H", data, p + 6)[0]
        end = p + 8 + length
        if end > len(data):
            idx = p + 1
            continue
        value = bytes(data[p + 8 : end])
        decoded = None
        valid = False
        if type_bytes == b"\x00\x0f":
            if length >= 2 and length % 2 == 0 and value[-2:] == b"\x00\x00":
                try:
                    text = value[:-2].decode("utf-16le")
                    if not text or all(c.isprintable() or c in "\t\n " for c in text):
                        decoded = text
                        valid = True
                except UnicodeDecodeError:
                    pass
        elif type_bytes in (b"\x00\x0d", b"\x00\x06", b"\x00\x08") and length == 4:
            decoded = struct.unpack_from("<I", value, 0)[0]
            valid = True
        elif type_bytes == b"\x00\x0a" and length == 4:
            decoded = struct.unpack_from("<i", value, 0)[0]
            valid = True
        elif type_bytes == b"\x00\x0b" and length == 8:
            decoded = struct.unpack_from("<d", value, 0)[0]
            valid = True
        if valid:
            yield (p, fid, type_bytes, length, decoded)
            idx = end
        else:
            idx = p + 1


def main():
    src = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/TranMgr.1800")
    rosetta = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")

    sample_bytes = 50 * 1024 * 1024
    print(f"Reading first {sample_bytes:,} bytes of {src.name} (full size {src.stat().st_size:,})...")
    with src.open("rb") as fh:
        data = fh.read(sample_bytes)

    print("Scanning TLVs...")
    records = list(all_tlvs(data))
    print(f"Total TLVs: {len(records)}")

    by_type = Counter(r[2].hex() for r in records)
    print("Type histogram:")
    for t, c in by_type.most_common(8):
        print(f"  type=0x{t}  count={c}")

    string_records = [r for r in records if r[2] == b"\x00\x0f"]
    print(f"\nString records: {len(string_records)}")

    fid_counter = Counter(r[1] for r in string_records)
    print("\nTop 30 string field IDs in TranMgr.1800:")
    for fid, c in fid_counter.most_common(30):
        samples = [r[4] for r in string_records if r[1] == fid][:2]
        sample_show = ", ".join(repr(s[:50]) for s in samples)
        print(f"  fid=0x{fid:04x} ({fid:>5}) count={c:>6}  e.g. {sample_show}")

    # Try to find known voucher numbers from Rosetta
    print("\n--- Cross-reference with Rosetta vouchers ---")
    conn = sqlite3.connect(str(rosetta))
    print("Rosetta voucher columns:")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(vouchers)")]
    print(f"  {cols}")
    print("Sample vouchers:")
    for row in conn.execute("SELECT * FROM vouchers LIMIT 3"):
        print(f"  {row}")


if __name__ == "__main__":
    main()

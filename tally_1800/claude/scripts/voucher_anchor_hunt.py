"""Full TranMgr.1800 TLV histogram, looking for fields whose count ≈ 30,545
(the voucher count from Rosetta).
"""
import struct
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
        type_bytes = bytes(data[p+4:p+6])
        length = struct.unpack_from("<H", data, p + 6)[0]
        end = p + 8 + length
        if end > len(data):
            idx = p + 1
            continue
        # Validate via lightweight check
        valid = False
        if type_bytes == b"\x00\x0f":
            v = data[p+8:end]
            if length >= 2 and length % 2 == 0 and v[-2:] == b"\x00\x00":
                # don't bother decoding here — count all valid string TLVs
                valid = True
        elif type_bytes in (b"\x00\x0d", b"\x00\x06", b"\x00\x08") and length == 4:
            valid = True
        elif type_bytes == b"\x00\x0a" and length == 4:
            valid = True
        elif type_bytes == b"\x00\x09" and length == 1:
            valid = True
        elif type_bytes == b"\x00\x0b" and length == 8:
            valid = True
        if valid:
            yield (p, fid, type_bytes.hex(), length)
            idx = end
        else:
            idx = p + 1


def main():
    path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/TranMgr.1800")
    print(f"Reading {path} ({path.stat().st_size:,} bytes)...")
    data = path.read_bytes()
    print("Streaming TLV histogram...")

    fid_type_counter = Counter()
    total = 0
    for off, fid, type_hex, ln in all_tlvs(data):
        fid_type_counter[(fid, type_hex)] += 1
        total += 1
    print(f"Total valid TLVs: {total:,}")
    print()

    # Rosetta voucher counts (target anchors)
    targets = {
        30545: "vouchers",
        40781: "voucher_ledger_entries",
        115505: "voucher_inventory_entries",
        117: "voucher_types",
    }

    print("Field-id+type with count near voucher counts (±10%):")
    for (fid, t), count in fid_type_counter.most_common(150):
        for target, label in targets.items():
            lo, hi = int(target * 0.85), int(target * 1.15)
            if lo <= count <= hi:
                print(f"  fid=0x{fid:04x} ({fid:>5}) type=0x{t}  count={count:>6}  ~ {label} ({target})")
                break

    print("\nFull top-50 (fid, type) by count:")
    for (fid, t), count in fid_type_counter.most_common(50):
        print(f"  fid=0x{fid:04x} ({fid:>5}) type=0x{t}  count={count}")


if __name__ == "__main__":
    main()

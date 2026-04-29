"""Find which TLV field IDs hold voucher anchors (party name, narration, voucher number, date)."""
import sqlite3
import struct
from collections import Counter
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"


def find_field(data: bytes, name: str) -> tuple[int | None, int]:
    enc = name.encode("utf-16le") + b"\x00\x00"
    pos = data.find(enc)
    if pos < 0 or pos < 8:
        return None, pos
    hdr = data[pos - 8 : pos]
    if hdr[:2] != MARKER:
        return None, pos
    fid = struct.unpack_from("<H", hdr, 2)[0]
    type_bytes = hdr[4:6]
    length = struct.unpack_from("<H", hdr, 6)[0]
    if type_bytes != TYPE_STRING_UTF16 or length != len(enc):
        return None, pos
    return fid, pos


def main():
    src = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/TranMgr.1800")
    rosetta = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")

    print(f"Loading first 200 MB of TranMgr.1800...")
    with src.open("rb") as fh:
        data = fh.read(200 * 1024 * 1024)

    conn = sqlite3.connect(str(rosetta))

    print("\n--- Probing for VOUCHER party names ---")
    fid_counter = Counter()
    not_found = 0
    no_tlv = 0
    sample = list(conn.execute("SELECT DISTINCT party_name FROM vouchers WHERE party_name != '' LIMIT 200"))
    for (name,) in sample:
        fid, pos = find_field(data, name)
        if pos < 0:
            not_found += 1
        elif fid is None:
            no_tlv += 1
        else:
            fid_counter[fid] += 1
    print(f"  searched {len(sample)} party names: found_with_tlv={sum(fid_counter.values())} no_tlv={no_tlv} not_found={not_found}")
    for fid, c in fid_counter.most_common(8):
        print(f"    fid=0x{fid:04x} ({fid:>5}) count={c}")

    print("\n--- Probing for VOUCHER narrations ---")
    fid_counter = Counter()
    not_found = 0
    no_tlv = 0
    sample = list(conn.execute("SELECT DISTINCT narration FROM vouchers WHERE narration != '' LIMIT 100"))
    for (narration,) in sample:
        fid, pos = find_field(data, narration)
        if pos < 0:
            not_found += 1
        elif fid is None:
            no_tlv += 1
        else:
            fid_counter[fid] += 1
    print(f"  searched {len(sample)} narrations: found={sum(fid_counter.values())} no_tlv={no_tlv} not_found={not_found}")
    for fid, c in fid_counter.most_common(8):
        print(f"    fid=0x{fid:04x} ({fid:>5}) count={c}")

    print("\n--- Probing for VOUCHER GUIDs ---")
    fid_counter = Counter()
    not_found = 0
    no_tlv = 0
    sample = list(conn.execute("SELECT DISTINCT guid FROM vouchers LIMIT 200"))
    for (guid,) in sample:
        fid, pos = find_field(data, guid)
        if pos < 0:
            not_found += 1
        elif fid is None:
            no_tlv += 1
        else:
            fid_counter[fid] += 1
    print(f"  searched {len(sample)} GUIDs: found={sum(fid_counter.values())} no_tlv={no_tlv} not_found={not_found}")
    for fid, c in fid_counter.most_common(8):
        print(f"    fid=0x{fid:04x} ({fid:>5}) count={c}")

    print("\n--- Probing for VOUCHER voucher_key ---")
    fid_counter = Counter()
    not_found = 0
    no_tlv = 0
    sample = list(conn.execute("SELECT DISTINCT voucher_key FROM vouchers WHERE voucher_key IS NOT NULL LIMIT 200"))
    for (vk,) in sample:
        fid, pos = find_field(data, vk)
        if pos < 0:
            not_found += 1
        elif fid is None:
            no_tlv += 1
        else:
            fid_counter[fid] += 1
    print(f"  searched {len(sample)} voucher_keys: found={sum(fid_counter.values())} no_tlv={no_tlv} not_found={not_found}")
    for fid, c in fid_counter.most_common(8):
        print(f"    fid=0x{fid:04x} ({fid:>5}) count={c}")


if __name__ == "__main__":
    main()

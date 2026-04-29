"""Find which TLV field id holds the actual ledger / group / stock-item name.

For each known ledger/group/stock-item name in Rosetta:
  - Search Manager.1800 for its UTF-16LE bytes.
  - Look back 8 bytes — should be the TLV header `02 10 [fid] [type] [len]`.
  - Count which field IDs hit which Rosetta name set.
"""
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"


def find_field_for_name(data: bytes, name: str) -> tuple[int | None, int]:
    """Search for `name` in UTF-16LE; if preceded by valid TLV header, return field_id and offset."""
    enc = name.encode("utf-16le") + b"\x00\x00"
    pos = data.find(enc)
    if pos < 0:
        return None, -1
    # Look back 8 bytes for the TLV header
    if pos < 8:
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
    manager_path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Manager.1800")
    rosetta_path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")

    print("Reading Manager.1800...")
    data = manager_path.read_bytes()

    conn = sqlite3.connect(str(rosetta_path))

    print("\n--- LEDGERS ---")
    field_counter = Counter()
    not_found = 0
    no_tlv = 0
    sample = list(conn.execute("SELECT name FROM ledgers ORDER BY name LIMIT 500"))
    for (name,) in sample:
        if not name:
            continue
        fid, pos = find_field_for_name(data, name)
        if pos < 0:
            not_found += 1
        elif fid is None:
            no_tlv += 1
        else:
            field_counter[fid] += 1
    print(f"  searched 500 ledgers: found_with_tlv={sum(field_counter.values())} no_tlv_header={no_tlv} not_found={not_found}")
    print(f"  field ids -> count:")
    for fid, c in field_counter.most_common(10):
        print(f"    fid=0x{fid:04x} ({fid:>5}) count={c}")

    print("\n--- GROUPS ---")
    field_counter = Counter()
    not_found = 0
    no_tlv = 0
    for (name,) in conn.execute("SELECT name FROM groups"):
        if not name:
            continue
        fid, pos = find_field_for_name(data, name)
        if pos < 0:
            not_found += 1
        elif fid is None:
            no_tlv += 1
        else:
            field_counter[fid] += 1
    total = sum(field_counter.values())
    print(f"  searched {total + no_tlv + not_found} groups: found={total} no_tlv={no_tlv} miss={not_found}")
    print(f"  field ids:")
    for fid, c in field_counter.most_common(10):
        print(f"    fid=0x{fid:04x} ({fid:>5}) count={c}")

    print("\n--- STOCK ITEMS ---")
    field_counter = Counter()
    not_found = 0
    no_tlv = 0
    for (name,) in conn.execute("SELECT name FROM stock_items LIMIT 500"):
        if not name:
            continue
        fid, pos = find_field_for_name(data, name)
        if pos < 0:
            not_found += 1
        elif fid is None:
            no_tlv += 1
        else:
            field_counter[fid] += 1
    total = sum(field_counter.values())
    print(f"  searched 500 stock items: found={total} no_tlv={no_tlv} miss={not_found}")
    for fid, c in field_counter.most_common(10):
        print(f"    fid=0x{fid:04x} ({fid:>5}) count={c}")

    print("\n--- VOUCHER TYPES ---")
    field_counter = Counter()
    not_found = 0
    no_tlv = 0
    for (name,) in conn.execute("SELECT name FROM voucher_types"):
        if not name:
            continue
        fid, pos = find_field_for_name(data, name)
        if pos < 0:
            not_found += 1
        elif fid is None:
            no_tlv += 1
        else:
            field_counter[fid] += 1
    total = sum(field_counter.values())
    print(f"  searched: found={total} no_tlv={no_tlv} miss={not_found}")
    for fid, c in field_counter.most_common(10):
        print(f"    fid=0x{fid:04x} ({fid:>5}) count={c}")

    print("\n--- GODOWNS ---")
    field_counter = Counter()
    not_found = 0
    for (name,) in conn.execute("SELECT name FROM godowns"):
        if not name:
            continue
        fid, pos = find_field_for_name(data, name)
        if pos < 0:
            not_found += 1
        else:
            field_counter[fid] += 1 if fid else 0
    total = sum(field_counter.values())
    print(f"  found={total} miss={not_found}")
    for fid, c in field_counter.most_common(10):
        print(f"    fid=0x{fid:04x} ({fid:>5}) count={c}")


if __name__ == "__main__":
    main()

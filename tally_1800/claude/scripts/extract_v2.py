"""Extract records from Manager.1800 using 0x0002 (true name field) + record-type marker.

Workflow:
  1. Walk all TLVs in stream order.
  2. Each occurrence of TLV(fid=0x0002, type=000f) starts a new record IF preceded
     by a recognizable record-prologue (`2b 0a 00 0d [type:4] 00 00`).
  3. Otherwise (e.g. fid=0x0002 inside a record as an attribute), it's a regular
     field of the current record.
  4. Group all subsequent fields into the current record until next record-start.
  5. Write to SQLite, one table per record-type marker.
  6. Validate against Rosetta.
"""
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"
NAME_FIELD = 0x0002  # primary name id

PROLOGUE_HDR = b"\x2b\x0a\x00\x0d"  # 4 bytes; followed by 4-byte type marker, then 00 00, then 02 10
PROLOGUE_LEN = 12  # `2b 0a 00 0d [4] 00 00 [next field starts]`  -- actually `2b 0a 00 0d [4] 00 00` is 10 bytes


def all_tlvs(data: bytes):
    """Yield (off, fid, type_bytes, length, value, decoded_or_raw)."""
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
        if type_bytes == TYPE_STRING_UTF16:
            if length >= 2 and length % 2 == 0 and value[-2:] == b"\x00\x00":
                try:
                    text = value[:-2].decode("utf-16le")
                    if not text or all(c.isprintable() or c in "\t\n " for c in text):
                        decoded = text
                        valid = True
                except UnicodeDecodeError:
                    pass
        elif type_bytes == b"\x00\x0d" and length == 4:
            decoded = struct.unpack_from("<I", value, 0)[0]
            valid = True
        elif type_bytes == b"\x00\x0a" and length == 4:
            decoded = struct.unpack_from("<i", value, 0)[0]
            valid = True
        elif type_bytes == b"\x00\x09" and length == 1:
            decoded = value[0]
            valid = True
        elif type_bytes == b"\x00\x0b" and length == 8:
            decoded = struct.unpack_from("<d", value, 0)[0]
            valid = True
        elif type_bytes == b"\x00\x06" and length == 4:
            # u32 with possible 0xFFFFFFFF sentinel
            decoded = struct.unpack_from("<I", value, 0)[0]
            valid = True
        elif type_bytes == b"\x00\x08" and length == 4:
            decoded = struct.unpack_from("<I", value, 0)[0]
            valid = True
        if valid:
            yield (p, fid, type_bytes, length, value, decoded)
            idx = end
        else:
            idx = p + 1


def get_record_type_marker(data: bytes, name_off: int) -> bytes | None:
    """If the bytes preceding the name field match `2b 0a 00 0d [4] 00 00`, return the 4-byte type marker."""
    # name_off points at the `02 10` of the name field. The prologue is 10 bytes BEFORE.
    # Prologue: `2b 0a 00 0d [type_marker:4] 00 00`
    if name_off < 10:
        return None
    pre = bytes(data[name_off - 10 : name_off])
    if pre[:4] != PROLOGUE_HDR:
        return None
    if pre[8:10] != b"\x00\x00":
        return None
    return pre[4:8]


def extract_records(data: bytes):
    current = None
    last_yield_off = -1
    for off, fid, tb, ln, val, dec in all_tlvs(data):
        if fid == NAME_FIELD and tb == TYPE_STRING_UTF16 and dec is not None:
            type_marker = get_record_type_marker(data, off)
            if type_marker is not None:
                # Start of a new record
                if current is not None:
                    yield current
                current = {
                    "name": dec,
                    "name_off": off,
                    "type_marker": type_marker,
                    "fields": defaultdict(list),
                }
                current["fields"][fid].append(dec)
                continue
        if current is not None:
            current["fields"][fid].append(dec)
    if current is not None:
        yield current


def main():
    manager_path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Manager.1800")
    rosetta_path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")

    print("Reading Manager.1800...")
    data = manager_path.read_bytes()

    print("Extracting records...")
    records = list(extract_records(data))
    print(f"Total records (with valid prologue): {len(records)}")

    by_marker = Counter(r["type_marker"].hex() for r in records)
    print("\nRecord-type markers (top 20):")
    for m, c in by_marker.most_common(20):
        print(f"  marker=0x{m}  count={c}")

    # Load Rosetta
    print("\nLoading Rosetta...")
    conn = sqlite3.connect(str(rosetta_path))
    sets = {
        "ledgers": set(r[0] for r in conn.execute("SELECT name FROM ledgers")),
        "groups": set(r[0] for r in conn.execute("SELECT name FROM groups")),
        "stock_items": set(r[0] for r in conn.execute("SELECT name FROM stock_items")),
        "voucher_types": set(r[0] for r in conn.execute("SELECT name FROM voucher_types")),
        "godowns": set(r[0] for r in conn.execute("SELECT name FROM godowns")),
        "cost_centres": set(r[0] for r in conn.execute("SELECT name FROM cost_centres")),
        "units": set(r[0] for r in conn.execute("SELECT name FROM units")),
    }
    rosetta_counts = {k: len(v) for k, v in sets.items()}
    print(f"Rosetta sets: {rosetta_counts}")

    # Per-marker, classify
    print("\nPer record-type marker classification (>=10 records each):")
    by_marker_records = defaultdict(list)
    for r in records:
        by_marker_records[r["type_marker"].hex()].append(r)

    for marker, recs in sorted(by_marker_records.items(), key=lambda x: -len(x[1])):
        if len(recs) < 5:
            continue
        names = set(r["name"] for r in recs if r["name"])
        # Best-fit set
        best = max(sets.items(), key=lambda kv: len(names & kv[1]))
        match_count = len(names & best[1])
        match_pct = 100 * match_count / max(1, len(names))
        sample = list(names)[:3]
        print(f"  marker=0x{marker:8s}  recs={len(recs):>5}  uniq_names={len(names):>5}  "
              f"best_match={best[0]:<14s} ({match_count}/{len(best[1])} = {match_pct:.0f}%)  samples={sample}")


if __name__ == "__main__":
    main()

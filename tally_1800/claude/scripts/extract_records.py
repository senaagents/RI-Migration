"""Walk TLV stream, split into records using the name-field (0x67) as record-start.
Extract record types using the byte pattern preceding each name.
Validate against Rosetta SQLite.

Strategy:
  - Sequentially scan all `02 10` TLVs (Tally fields).
  - Each time we hit field 0x0067 (name), close previous record and start new one.
  - Capture: record_type_marker (the 4 bytes preceding `2b 0a 00 0d` if present),
    name, surname, all subsequent string/typed fields up to next name.
  - Cross-check name set against Rosetta ledger / group / stock-item names.
"""
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"
NAME_FIELD = 0x0067


def all_tlvs(data: bytes):
    """Yield (off, field_id, type_bytes, length, value_or_none)."""
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
        # Validate string fields
        valid = True
        decoded = None
        if type_bytes == TYPE_STRING_UTF16:
            if length < 2 or length % 2 != 0 or value[-2:] != b"\x00\x00":
                valid = False
            else:
                try:
                    text = value[:-2].decode("utf-16le")
                    if text and not all(c.isprintable() or c in "\t\n " for c in text):
                        valid = False
                    else:
                        decoded = text
                except UnicodeDecodeError:
                    valid = False
        if valid:
            yield (p, fid, type_bytes, length, value, decoded)
            idx = end
        else:
            idx = p + 1


def get_record_type_marker(data: bytes, name_off: int) -> bytes:
    """Return the 4-byte record-type marker preceding the name field, or b'' if not found."""
    # Look back 8 bytes for `[type:4 bytes] 02 10` framing — pattern: ?? ?? ?? ?? 02 10 67 00
    # Actually the 92% prefix was `2b 0a 00 0d [marker:4]` before `02 10 67`
    if name_off < 8:
        return b""
    pre = data[name_off - 8 : name_off]
    if pre[:4] == b"\x2b\x0a\x00\x0d":
        return bytes(pre[4:8])
    return b""


def extract_records(data: bytes):
    """Yield records as dicts of { 'name', 'surname', 'fields': {fid: [values]}, 'type_marker' }."""
    current = None
    last_name_off = None
    for off, fid, tb, ln, val, dec in all_tlvs(data):
        if fid == NAME_FIELD and tb == TYPE_STRING_UTF16 and dec is not None:
            # New record; flush previous
            if current is not None:
                yield current
            current = {
                "name": dec,
                "name_off": off,
                "type_marker": get_record_type_marker(data, off),
                "fields": defaultdict(list),
            }
            current["fields"][fid].append(dec)
        elif current is not None and dec is not None:
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
    print(f"Total records: {len(records)}")

    # Group by record-type marker
    by_marker = Counter(r["type_marker"].hex() for r in records)
    print("\nTop 10 record-type markers:")
    for m, c in by_marker.most_common(10):
        print(f"  marker=0x{m:8s}  count={c}")

    # Pull all extracted names, dedupe
    all_names = set(r["name"] for r in records if r["name"])
    print(f"\nUnique extracted names: {len(all_names)}")

    # Load Rosetta names
    print("\nLoading Rosetta...")
    conn = sqlite3.connect(str(rosetta_path))
    rosetta_ledgers = set(row[0] for row in conn.execute("SELECT DISTINCT name FROM ledgers"))
    rosetta_groups = set(row[0] for row in conn.execute("SELECT DISTINCT name FROM groups"))
    rosetta_stock = set(row[0] for row in conn.execute("SELECT DISTINCT name FROM stock_items"))
    rosetta_voucher_types = set(row[0] for row in conn.execute("SELECT DISTINCT name FROM voucher_types"))
    rosetta_godowns = set(row[0] for row in conn.execute("SELECT DISTINCT name FROM godowns"))
    rosetta_cc = set(row[0] for row in conn.execute("SELECT DISTINCT name FROM cost_centres"))
    rosetta_units = set(row[0] for row in conn.execute("SELECT DISTINCT name FROM units"))
    print(f"  ledgers={len(rosetta_ledgers)}  groups={len(rosetta_groups)}  stock_items={len(rosetta_stock)}")
    print(f"  voucher_types={len(rosetta_voucher_types)}  godowns={len(rosetta_godowns)}")
    print(f"  cost_centres={len(rosetta_cc)}  units={len(rosetta_units)}")

    # Validate
    print("\nMatch report:")
    print(f"  extracted ∩ ledgers       = {len(all_names & rosetta_ledgers):>5} / {len(rosetta_ledgers)} rosetta ledgers")
    print(f"  extracted ∩ groups        = {len(all_names & rosetta_groups):>5} / {len(rosetta_groups)} rosetta groups")
    print(f"  extracted ∩ stock_items   = {len(all_names & rosetta_stock):>5} / {len(rosetta_stock)} rosetta stock_items")
    print(f"  extracted ∩ voucher_types = {len(all_names & rosetta_voucher_types):>5} / {len(rosetta_voucher_types)} rosetta voucher_types")
    print(f"  extracted ∩ godowns       = {len(all_names & rosetta_godowns):>5} / {len(rosetta_godowns)} rosetta godowns")
    print(f"  extracted ∩ cost_centres  = {len(all_names & rosetta_cc):>5} / {len(rosetta_cc)} rosetta cost_centres")
    print(f"  extracted ∩ units         = {len(all_names & rosetta_units):>5} / {len(rosetta_units)} rosetta units")

    # Names per record-type marker
    print("\nMarker -> sample names + match-set guesses:")
    by_marker_records = defaultdict(list)
    for r in records:
        by_marker_records[r["type_marker"].hex()].append(r["name"])
    for marker, names in sorted(by_marker_records.items(), key=lambda x: -len(x[1]))[:8]:
        names_set = set(names)
        ldg = len(names_set & rosetta_ledgers)
        grp = len(names_set & rosetta_groups)
        stk = len(names_set & rosetta_stock)
        vt = len(names_set & rosetta_voucher_types)
        gd = len(names_set & rosetta_godowns)
        cc = len(names_set & rosetta_cc)
        un = len(names_set & rosetta_units)
        print(f"  marker=0x{marker or '(empty)':>8}  count={len(names):>5}  uniq={len(names_set):>5}  "
              f"ldg={ldg:>4} grp={grp:>3} stk={stk:>4} vt={vt:>3} gd={gd:>3} cc={cc:>3} un={un:>3}")
        for n in names[:3]:
            print(f"      sample: {n!r}")


if __name__ == "__main__":
    main()

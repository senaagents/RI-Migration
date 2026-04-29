"""End-to-end: extract records from Manager.1800 -> SQLite.

Schema mirrors the Rosetta SQLite for direct comparability:
  master_records (record_kind, raw_name, name, sequence, fields_json)
  ledgers, groups, stock_items, voucher_types, godowns, cost_centres, units

Classification: each record is assigned to the Rosetta set whose name set it
matches. Records that match nothing are written to `unclassified`.
"""
import json
import re
import sqlite3
import struct
import sys
from collections import defaultdict
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"
NAME_FIELD = 0x0002
PREFIX_RE = re.compile(r"^[A-Z]\d{0,3}[A-Z]?-")


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
        if type_bytes == TYPE_STRING_UTF16:
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
        elif type_bytes == b"\x00\x09" and length == 1:
            decoded = value[0]
            valid = True
        elif type_bytes == b"\x00\x0b" and length == 8:
            decoded = struct.unpack_from("<d", value, 0)[0]
            valid = True
        if valid:
            yield (p, fid, type_bytes.hex(), length, decoded)
            idx = end
        else:
            idx = p + 1


def strip_prefix(name: str) -> str:
    m = PREFIX_RE.match(name)
    return name[m.end():] if m else name


def extract(data: bytes):
    current = None
    for off, fid, type_hex, ln, dec in all_tlvs(data):
        if fid == NAME_FIELD and type_hex == "000f" and dec is not None:
            if current is not None:
                yield current
            current = {
                "name_off": off,
                "raw_name": dec,
                "name": strip_prefix(dec),
                "fields": defaultdict(list),
                "field_types": {},
            }
            current["fields"][fid].append(dec)
            current["field_types"][fid] = "000f"
        elif current is not None:
            current["fields"][fid].append(dec)
            current["field_types"].setdefault(fid, type_hex)
    if current is not None:
        yield current


def classify(records, sets):
    """Assign each record to a kind, picking the Rosetta set with strongest membership.
    Returns list of (record, kind_or_None)."""
    out = []
    for r in records:
        nm = r["name"]
        raw = r["raw_name"]
        # Try both stripped and raw against each set
        candidates = []
        for label, st in sets.items():
            if nm in st:
                candidates.append(label)
            elif raw in st:
                candidates.append(label)
        # Prefer non-ledgers when conflict (since 'ledgers' is the largest, it wins by accident often)
        priority = ["units", "godowns", "voucher_types", "cost_centres", "stock_items", "groups", "ledgers"]
        for p in priority:
            if p in candidates:
                out.append((r, p))
                break
        else:
            out.append((r, None))
    return out


def main():
    src = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Manager.1800")
    rosetta = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")
    out_path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/070525_master.sqlite3")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    print(f"Source: {src} ({src.stat().st_size} bytes)")
    print(f"Rosetta: {rosetta}")
    print(f"Output:  {out_path}")

    print("\nLoading rosetta sets...")
    rconn = sqlite3.connect(str(rosetta))
    sets = {
        "ledgers":       set(r[0] for r in rconn.execute("SELECT name FROM ledgers")),
        "groups":        set(r[0] for r in rconn.execute("SELECT name FROM groups")),
        "stock_items":   set(r[0] for r in rconn.execute("SELECT name FROM stock_items")),
        "voucher_types": set(r[0] for r in rconn.execute("SELECT name FROM voucher_types")),
        "godowns":       set(r[0] for r in rconn.execute("SELECT name FROM godowns")),
        "cost_centres":  set(r[0] for r in rconn.execute("SELECT name FROM cost_centres")),
        "units":         set(r[0] for r in rconn.execute("SELECT name FROM units")),
    }

    print("Reading & extracting...")
    data = src.read_bytes()
    records = list(extract(data))
    print(f"  {len(records)} candidate records")

    print("Classifying...")
    classified = classify(records, sets)

    by_kind = defaultdict(list)
    for r, k in classified:
        by_kind[k or "unclassified"].append(r)
    print("Counts by kind:")
    for k, recs in sorted(by_kind.items(), key=lambda x: -len(x[1])):
        print(f"  {k:<14} {len(recs):>6}")

    # Write SQLite
    print("\nWriting SQLite...")
    conn = sqlite3.connect(str(out_path))
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA journal_mode=MEMORY")

    conn.execute("""
        CREATE TABLE master_records (
            id INTEGER PRIMARY KEY,
            kind TEXT,
            sequence INTEGER,
            raw_name TEXT,
            name TEXT,
            name_offset INTEGER,
            field_count INTEGER,
            fields_json TEXT
        )
    """)
    conn.execute("CREATE INDEX idx_kind ON master_records(kind)")
    conn.execute("CREATE INDEX idx_name ON master_records(name)")

    for kind in ["ledgers", "groups", "stock_items", "voucher_types",
                 "godowns", "cost_centres", "units", "unclassified"]:
        conn.execute(f"""
            CREATE TABLE {kind} (
                id INTEGER PRIMARY KEY,
                sequence INTEGER,
                raw_name TEXT,
                name TEXT,
                name_offset INTEGER,
                fields_json TEXT
            )
        """)

    seq = 0
    for r, kind in classified:
        seq += 1
        # Convert defaultdict + handle multi-values per field
        fields_dict = {f"0x{fid:04x}": (vals if len(vals) > 1 else vals[0])
                       for fid, vals in r["fields"].items()}
        fields_json = json.dumps(fields_dict, ensure_ascii=False, default=str)
        kind_or_unk = kind or "unclassified"
        conn.execute(
            "INSERT INTO master_records(kind, sequence, raw_name, name, name_offset, field_count, fields_json) VALUES (?,?,?,?,?,?,?)",
            (kind_or_unk, seq, r["raw_name"], r["name"], r["name_off"], sum(len(v) for v in r["fields"].values()), fields_json),
        )
        conn.execute(
            f"INSERT INTO {kind_or_unk}(sequence, raw_name, name, name_offset, fields_json) VALUES (?,?,?,?,?)",
            (seq, r["raw_name"], r["name"], r["name_off"], fields_json),
        )

    conn.commit()

    # Quick validation report
    print("\n=== validation vs Rosetta ===")
    for kind, st in sets.items():
        ours = set(row[0] for row in conn.execute(f"SELECT name FROM {kind}"))
        intersection = ours & st
        only_ours = ours - st
        only_rosetta = st - ours
        print(f"{kind:<14}  rosetta={len(st):>5}  ours={len(ours):>5}  match={len(intersection):>5}  "
              f"missing_from_ours={len(only_rosetta):>4}  extra_in_ours={len(only_ours):>4}")

    conn.close()
    print(f"\nWrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

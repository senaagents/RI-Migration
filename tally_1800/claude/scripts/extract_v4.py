"""Extract v4: split on EITHER 0x0002 OR 0x01f7 as record-start anchor.

Reasoning: some ledgers (mostly expense ledgers and bank-linked records) have
their primary name in field 0x01f7 instead of 0x0002. Without this, those
records get squashed as sub-fields of the prior 0x0002 record, producing the
156 "missing" ledgers in the diff.
"""
import json
import re
import sqlite3
import struct
from collections import defaultdict
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"
NAME_FIELDS = (0x0002, 0x01f7)  # either is a record-start anchor
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
        is_record_start = (fid in NAME_FIELDS and type_hex == "000f" and dec is not None)
        if is_record_start:
            if current is not None:
                yield current
            current = {
                "name_off": off,
                "name_fid": fid,
                "raw_name": dec,
                "name": strip_prefix(dec),
                "fields": defaultdict(list),
            }
            current["fields"][fid].append(dec)
        elif current is not None:
            current["fields"][fid].append(dec)
    if current is not None:
        yield current


def classify(records, sets):
    out = []
    priority = ["units", "godowns", "voucher_types", "cost_centres",
                "stock_items", "groups", "ledgers"]
    for r in records:
        nm = r["name"]
        raw = r["raw_name"]
        candidates = []
        for label, st in sets.items():
            if nm in st or raw in st:
                candidates.append(label)
        kind = None
        for p in priority:
            if p in candidates:
                kind = p
                break
        out.append((r, kind))
    return out


def main():
    src = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Manager.1800")
    rosetta = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")
    out_path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/070525_master_v4.sqlite3")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

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

    print("Reading Manager.1800...")
    data = src.read_bytes()
    print(f"  {len(data):,} bytes")

    print("Extracting (v4: 0x0002 OR 0x01f7 starts records)...")
    records = list(extract(data))
    print(f"  {len(records):,} records")

    print("Classifying...")
    classified = classify(records, sets)
    by_kind = defaultdict(list)
    for r, k in classified:
        by_kind[k or "unclassified"].append(r)
    for k in ("ledgers", "groups", "stock_items", "voucher_types",
              "godowns", "cost_centres", "units", "unclassified"):
        print(f"  {k:<14}: {len(by_kind[k]):>6}")

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
            name_fid INTEGER,
            name_offset INTEGER,
            field_count INTEGER,
            fields_json TEXT
        )
    """)
    conn.execute("CREATE INDEX idx_kind ON master_records(kind)")
    conn.execute("CREATE INDEX idx_name ON master_records(name)")

    for kind in ("ledgers", "groups", "stock_items", "voucher_types",
                 "godowns", "cost_centres", "units", "unclassified"):
        conn.execute(f"""
            CREATE TABLE {kind} (
                id INTEGER PRIMARY KEY,
                sequence INTEGER,
                raw_name TEXT,
                name TEXT,
                name_fid INTEGER,
                name_offset INTEGER,
                fields_json TEXT
            )
        """)

    seq = 0
    for r, kind in classified:
        seq += 1
        fields_dict = {f"0x{fid:04x}": (vals if len(vals) > 1 else vals[0])
                       for fid, vals in r["fields"].items()}
        fields_json = json.dumps(fields_dict, ensure_ascii=False, default=str)
        kind_or = kind or "unclassified"
        conn.execute(
            "INSERT INTO master_records(kind, sequence, raw_name, name, name_fid, name_offset, field_count, fields_json) VALUES (?,?,?,?,?,?,?,?)",
            (kind_or, seq, r["raw_name"], r["name"], r["name_fid"], r["name_off"],
             sum(len(v) for v in r["fields"].values()), fields_json),
        )
        conn.execute(
            f"INSERT INTO {kind_or}(sequence, raw_name, name, name_fid, name_offset, fields_json) VALUES (?,?,?,?,?,?)",
            (seq, r["raw_name"], r["name"], r["name_fid"], r["name_off"], fields_json),
        )

    conn.commit()

    print("\n=== Validation vs Rosetta ===")
    print(f"{'kind':<14} {'rosetta':>8} {'ours':>8} {'match':>8} {'missing':>8} {'extra':>8} {'%':>6}")
    grand_match = 0
    grand_total = 0
    for kind, st in sets.items():
        ours_name = set(r[0] for r in conn.execute(f"SELECT name FROM {kind}"))
        ours_raw = set(r[0] for r in conn.execute(f"SELECT raw_name FROM {kind}"))
        ours = ours_name | ours_raw
        match = len(ours & st)
        missing = len(st - ours)
        extra = len(ours - st)
        pct = 100 * match / max(1, len(st))
        print(f"{kind:<14} {len(st):>8} {len(ours):>8} {match:>8} {missing:>8} {extra:>8} {pct:>6.1f}")
        grand_match += match
        grand_total += len(st)
    print(f"{'TOTAL':<14} {grand_total:>8} {'':>8} {grand_match:>8} {'':>8} {'':>8} {100*grand_match/grand_total:>6.1f}")

    conn.close()
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()

"""Extract records from Manager.1800 — v3.

Approach:
  - Walk all valid TLVs.
  - Every TLV(fid=0x0002, type=000f, valid string) is treated as a record-start.
  - All fields between two consecutive name fields belong to that record.
  - Stored names often have sort-key prefixes ("L-Capital Account"); we keep
    both raw_name and a stripped name for matching.
  - Classify records by best-fit Rosetta match across stripped names.
"""
import re
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"
NAME_FIELD = 0x0002

# Tally sort prefixes seen so far: "L-", "A00-", "A60-", "Z-", etc.
PREFIX_RE = re.compile(r"^[A-Z]\d{0,3}[A-Z]?-")


def all_tlvs(data: bytes):
    """Yield every valid TLV record (advances by record length)."""
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
            yield (p, fid, type_bytes, length, decoded)
            idx = end
        else:
            idx = p + 1


def strip_prefix(name: str) -> str:
    """Remove Tally sort prefixes like 'L-', 'A60-', 'Z00-'. Returns stripped name."""
    m = PREFIX_RE.match(name)
    if m:
        return name[m.end():]
    return name


def extract(data: bytes):
    """Yield (record_dict)."""
    current = None
    for off, fid, tb, ln, dec in all_tlvs(data):
        if fid == NAME_FIELD and tb == TYPE_STRING_UTF16 and dec is not None:
            if current is not None:
                yield current
            current = {
                "name_off": off,
                "raw_name": dec,
                "name": strip_prefix(dec),
                "fields": defaultdict(list),
            }
            current["fields"][fid].append(dec)
        elif current is not None:
            current["fields"][fid].append(dec)
    if current is not None:
        yield current


def main():
    data = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Manager.1800").read_bytes()
    print(f"Manager.1800: {len(data)} bytes")

    print("Extracting records...")
    records = list(extract(data))
    print(f"Total records (anchored on 0x0002 name): {len(records)}")

    raw_names = [r["raw_name"] for r in records]
    stripped = [r["name"] for r in records]
    raw_set = set(raw_names)
    stripped_set = set(stripped)
    print(f"Unique raw names: {len(raw_set)}, after prefix strip: {len(stripped_set)}")

    # Show common prefixes
    prefix_counter = Counter()
    for raw in raw_names:
        m = PREFIX_RE.match(raw)
        prefix_counter[m.group(0) if m else ""] += 1
    print("\nCommon prefixes:")
    for p, c in prefix_counter.most_common(15):
        print(f"  {p!r:<10} count={c}")

    # Load Rosetta sets
    conn = sqlite3.connect("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")
    sets = {
        "ledgers": set(r[0] for r in conn.execute("SELECT name FROM ledgers")),
        "groups": set(r[0] for r in conn.execute("SELECT name FROM groups")),
        "stock_items": set(r[0] for r in conn.execute("SELECT name FROM stock_items")),
        "voucher_types": set(r[0] for r in conn.execute("SELECT name FROM voucher_types")),
        "godowns": set(r[0] for r in conn.execute("SELECT name FROM godowns")),
        "cost_centres": set(r[0] for r in conn.execute("SELECT name FROM cost_centres")),
        "units": set(r[0] for r in conn.execute("SELECT name FROM units")),
    }
    print("\nRosetta totals: " + " ".join(f"{k}={len(v)}" for k, v in sets.items()))

    print("\nMatch report (stripped names against Rosetta):")
    for label, st in sets.items():
        hit = len(stripped_set & st)
        print(f"  stripped ∩ {label:<14} = {hit:>5} / {len(st)} ({100*hit/max(1,len(st)):.0f}%)")

    print("\nMatch report (RAW names — including prefix — against Rosetta):")
    for label, st in sets.items():
        hit = len(raw_set & st)
        print(f"  raw ∩ {label:<14} = {hit:>5} / {len(st)}")

    # Group records by prefix and check Rosetta classification
    print("\nPer-prefix Rosetta best-fit:")
    by_prefix = defaultdict(list)
    for r in records:
        m = PREFIX_RE.match(r["raw_name"])
        by_prefix[m.group(0) if m else ""].append(r)

    for prefix, recs in sorted(by_prefix.items(), key=lambda x: -len(x[1])):
        if len(recs) < 5:
            continue
        names_set = set(r["name"] for r in recs)
        scores = {label: len(names_set & st) for label, st in sets.items()}
        best, best_count = max(scores.items(), key=lambda kv: kv[1])
        pct = 100 * best_count / max(1, len(names_set))
        sample = list(names_set)[:3]
        print(f"  prefix={prefix!r:<8} recs={len(recs):>5} uniq={len(names_set):>5}  "
              f"best={best:<14} match={best_count}/{len(sets[best])} ({pct:.0f}%)  e.g.={sample}")


if __name__ == "__main__":
    main()

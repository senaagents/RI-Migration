"""Extract v5: page-oriented master extraction using MASTERID at page+4.

Approach (codex-discovered):
  - Manager.1800 is 512-byte pages.
  - Each page has u32 LE MASTERID at offset +4.
  - Consecutive pages with the same MASTERID form ONE master record.
  - Within a record's pages, we walk TLVs (02 10 ...) to extract fields.
  - GUID = `<company_guid>-<master_id:08x>` matches Tally XML directly.

Validation: match by GUID against Rosetta — no fuzzy name matching.

Output schema mirrors Rosetta: companies, groups, ledgers, stock_items,
voucher_types, godowns, cost_centres, units, plus an `unclassified_masters`
table for records whose GUID isn't in Rosetta (deleted/internal).
"""
import json
import re
import sqlite3
import struct
from collections import defaultdict
from pathlib import Path


PAGE_SIZE = 512
MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"


def normalize(s: str) -> str:
    """Codex insight: Tally names trail \\r\\n sometimes."""
    if s is None:
        return s
    return s.rstrip("\r\n").strip()


def parse_tlv_at(data: bytes, p: int):
    """Parse one TLV at offset p. Return (off, fid, type_hex, len, decoded) or None."""
    if p + 8 > len(data) or data[p:p+2] != MARKER:
        return None
    fid = struct.unpack_from("<H", data, p + 2)[0]
    tb = bytes(data[p+4:p+6])
    ln = struct.unpack_from("<H", data, p + 6)[0]
    end = p + 8 + ln
    if end > len(data):
        return None
    val = bytes(data[p+8:end])
    decoded = None
    if tb == TYPE_STRING_UTF16:
        if ln >= 2 and ln % 2 == 0 and val[-2:] == b"\x00\x00":
            try:
                text = val[:-2].decode("utf-16le")
                if not text or all(c.isprintable() or c in "\t\n " for c in text):
                    decoded = text
            except UnicodeDecodeError:
                pass
    elif tb in (b"\x00\x0d", b"\x00\x06", b"\x00\x08") and ln == 4:
        decoded = struct.unpack_from("<I", val, 0)[0]
    elif tb == b"\x00\x0a" and ln == 4:
        decoded = struct.unpack_from("<i", val, 0)[0]
    elif tb == b"\x00\x09" and ln == 1:
        decoded = val[0]
    elif tb == b"\x00\x0b" and ln == 8:
        decoded = struct.unpack_from("<d", val, 0)[0]
    return (p, fid, tb.hex(), ln, end, decoded)


def walk_tlvs_in_range(data: bytes, start: int, end: int):
    """Yield TLV tuples within [start, end). Advances by record length when valid."""
    idx = start
    while idx < end - 8:
        p = data.find(MARKER, idx)
        if p < 0 or p >= end:
            break
        rec = parse_tlv_at(data, p)
        if rec and rec[5] is not None:  # decoded successfully
            yield rec
            idx = rec[4]  # rec[4] = end offset
        else:
            idx = p + 1


def page_groups(data: bytes):
    """Yield (master_id, [page_indices]) — ALL pages with the same MASTERID anywhere
    in the file collapse into one record. Skip page 0 (root) and MASTERID=0 pages."""
    n_pages = len(data) // PAGE_SIZE
    by_id: dict[int, list[int]] = defaultdict(list)
    for pn in range(1, n_pages):
        mid = struct.unpack_from("<I", data, pn * PAGE_SIZE + 4)[0]
        if mid == 0:
            continue
        by_id[mid].append(pn)
    for mid, pages in by_id.items():
        yield mid, sorted(pages)


def extract_record(data: bytes, master_id: int, pages: list[int]):
    """Extract all TLVs from the page range belonging to one record."""
    start = pages[0] * PAGE_SIZE
    end = (pages[-1] + 1) * PAGE_SIZE
    fields = defaultdict(list)
    field_types = {}
    for tlv in walk_tlvs_in_range(data, start, end):
        off, fid, t, ln, _, dec = tlv
        if dec is not None:
            fields[fid].append(dec)
            field_types.setdefault(fid, t)
    return {
        "master_id": master_id,
        "pages": pages,
        "page_start": pages[0],
        "page_count": len(pages),
        "byte_start": start,
        "byte_end": end,
        "fields": dict(fields),
        "field_types": field_types,
    }


def best_name(fields: dict[int, list]) -> str | None:
    """Pick the most likely 'name' for a record from common name field IDs."""
    for fid in (0x0002, 0x01f7, 0x0af3, 0x0001):
        if fid in fields and fields[fid]:
            v = fields[fid][0]
            if isinstance(v, str) and v:
                return normalize(v)
    return None


def main():
    src = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Manager.1800")
    rosetta = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")
    out_path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/070525_master_v5.sqlite3")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    print(f"Reading {src.name} ({src.stat().st_size:,} bytes)...")
    data = src.read_bytes()
    n_pages = len(data) // PAGE_SIZE
    print(f"  {n_pages:,} pages of {PAGE_SIZE} bytes each")

    # Build Rosetta GUID -> (kind, name, guid, full_row_dict) lookup
    print(f"\nLoading Rosetta GUID lookup...")
    conn = sqlite3.connect(str(rosetta))
    company_guid = None
    cguid_row = conn.execute("SELECT guid FROM ledgers WHERE guid IS NOT NULL LIMIT 1").fetchone()
    if cguid_row:
        company_guid = cguid_row[0][:36]
        print(f"  Company GUID: {company_guid}")

    rosetta_by_master_id = {}  # master_id -> (kind, name, guid, full_row)
    for kind in ["ledgers", "groups", "stock_items", "voucher_types",
                 "godowns", "cost_centres", "units", "companies"]:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({kind})")]
        if "guid" not in cols:
            continue
        for row in conn.execute(f"SELECT * FROM {kind} WHERE guid IS NOT NULL"):
            rd = dict(zip(cols, row))
            guid = rd["guid"]
            if len(guid) >= 45 and guid[36] == "-":
                suffix = guid[37:45]
                try:
                    mid = int(suffix, 16)
                    if mid not in rosetta_by_master_id:
                        rosetta_by_master_id[mid] = (kind, rd.get("name"), guid, rd)
                except ValueError:
                    pass
    print(f"  {len(rosetta_by_master_id)} Rosetta records keyed by master_id")

    # Walk page groups, extract records
    print(f"\nExtracting page-grouped records...")
    records = []
    for mid, pages in page_groups(data):
        rec = extract_record(data, mid, pages)
        records.append(rec)
    print(f"  {len(records)} master records (by page-grouping)")

    # Classify by Rosetta lookup
    matched_count = 0
    unmatched_count = 0
    by_kind = defaultdict(list)
    for r in records:
        mid = r["master_id"]
        if mid in rosetta_by_master_id:
            kind, name, guid, full_row = rosetta_by_master_id[mid]
            r["kind"] = kind
            r["rosetta_name"] = name
            r["guid"] = guid
            r["rosetta_row"] = full_row
            matched_count += 1
        else:
            r["kind"] = "unclassified_masters"
            r["guid"] = (company_guid + f"-{mid:08x}") if company_guid else None
            r["rosetta_row"] = None
            unmatched_count += 1
        by_kind[r["kind"]].append(r)

    print(f"  Matched to Rosetta GUID: {matched_count}")
    print(f"  Unmatched (deleted/internal/etc): {unmatched_count}")
    print()
    for kind, recs in sorted(by_kind.items(), key=lambda x: -len(x[1])):
        print(f"  {kind:<22}: {len(recs):>6}")

    # Write SQLite
    print(f"\nWriting SQLite -> {out_path}")
    out = sqlite3.connect(str(out_path))
    out.execute("PRAGMA synchronous=OFF")
    out.execute("PRAGMA journal_mode=MEMORY")

    out.execute("""
        CREATE TABLE master_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            master_id INTEGER UNIQUE,
            guid TEXT UNIQUE,
            kind TEXT,
            page_start INTEGER,
            page_count INTEGER,
            byte_start INTEGER,
            extracted_name TEXT,
            rosetta_name TEXT,
            field_count INTEGER,
            fields_json TEXT
        )
    """)
    out.execute("CREATE INDEX idx_kind_v5 ON master_records(kind)")
    out.execute("CREATE INDEX idx_guid_v5 ON master_records(guid)")

    for kind in ("ledgers", "groups", "stock_items", "voucher_types",
                 "godowns", "cost_centres", "units", "companies",
                 "unclassified_masters"):
        out.execute(f"""
            CREATE TABLE {kind} (
                master_id INTEGER PRIMARY KEY,
                guid TEXT,
                page_start INTEGER,
                page_count INTEGER,
                extracted_name TEXT,
                rosetta_name TEXT,
                fields_json TEXT
            )
        """)

    for r in records:
        ext_name = best_name(r["fields"])
        fields_json = json.dumps(
            {f"0x{fid:04x}": (vals if len(vals) > 1 else vals[0])
             for fid, vals in r["fields"].items()},
            ensure_ascii=False, default=str
        )
        out.execute(
            "INSERT INTO master_records(master_id, guid, kind, page_start, page_count, byte_start, extracted_name, rosetta_name, field_count, fields_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["master_id"], r.get("guid"), r["kind"], r["page_start"], r["page_count"],
             r["byte_start"], ext_name, r.get("rosetta_name"),
             sum(len(v) for v in r["fields"].values()), fields_json),
        )
        out.execute(
            f"INSERT OR IGNORE INTO {r['kind']}(master_id, guid, page_start, page_count, extracted_name, rosetta_name, fields_json) VALUES (?,?,?,?,?,?,?)",
            (r["master_id"], r.get("guid"), r["page_start"], r["page_count"],
             ext_name, r.get("rosetta_name"), fields_json),
        )

    out.commit()

    # Validate vs Rosetta by GUID
    print("\n=== Validation by GUID (no name matching) ===")
    print(f"{'kind':<22} {'rosetta':>8} {'ours':>8} {'match':>8} {'%':>6}")
    grand_match = 0
    grand_total = 0
    for kind in ["ledgers", "groups", "stock_items", "voucher_types",
                 "godowns", "cost_centres", "units"]:
        ros_guids = set(r[0] for r in conn.execute(f"SELECT guid FROM {kind} WHERE guid IS NOT NULL"))
        our_guids = set(r[0] for r in out.execute(f"SELECT guid FROM {kind} WHERE guid IS NOT NULL"))
        match = len(ros_guids & our_guids)
        pct = 100 * match / max(1, len(ros_guids))
        print(f"{kind:<22} {len(ros_guids):>8} {len(our_guids):>8} {match:>8} {pct:>6.1f}")
        grand_match += match
        grand_total += len(ros_guids)
    print(f"{'TOTAL':<22} {grand_total:>8} {'':>8} {grand_match:>8} {100*grand_match/grand_total:>6.1f}")

    out.close()
    print(f"\nWrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

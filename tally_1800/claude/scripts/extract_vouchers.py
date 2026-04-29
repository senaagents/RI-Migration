"""Voucher extractor for TranMgr.1800.

Strategy (discovered through binary RE):
  - TranMgr.1800 is 512-byte pages. Pages have a 28-byte header (+0..+27) and
    a body that contains both COMPACT slot records and TLV (02 10 ...) records.
  - The compact slot at offset whatever has format <fid:u16le> <type:u16le>
    <value:6 bytes>. Each slot is 10 bytes. Slot positions in body are NOT
    fixed at 28 + 10*k; we signature-scan for known (fid, type) pairs.
  - Field 0x0bbb (compact, type bytes 00 06) on a page is the voucher
    master_id (u32 LE). 100% of Rosetta voucher master_ids appear at this
    anchor on a unique page per voucher (kind=1 page).
  - Field 0x0067 (compact, type bytes 00 0d) on the SAME anchor page is the
    voucher_date as a u32 days-since-1899-12-31 serial. 30,543 / 30,545
    Rosetta voucher dates match (99.99%); the 2 misses are giant-serial
    sentinels we filter out.
  - voucher_type is NOT stored in TranMgr — it lives in VchStatus.1800 at
    slot+0x64 (u32 voucher_type master_id). VchStatus has 30,564 non-empty
    slots (codex finding); pairing them with TranMgr vouchers grouped by
    date and ordered by master_id ascending recovers ~86 % of voucher_type
    assignments. The residual 14 % are date-clusters where VchStatus skips
    base-type-31 internal vouchers — fixing it requires an explicit
    skip-flag we haven't decoded yet.

  GUID = `<company_guid>-<master_id:08x>` matches Tally XML directly.

  The TLV records on the same page carry per-voucher fields:
    0x0003 (string)  — voucher_key (composite "<hexA>-<hexB>-<hexC>")
    0x00cb (string)  — voucher_number
    0x00cd (string)  — narration  (often truncated; full narration may be on
                                   another page — gather from any page sharing
                                   the master_id)
    0x00ce (string)  — partial address-like / sometimes party name (varies)
    0x0002 (string)  — party name (when present, often on related pages)

Validation: match by GUID against rosetta.sqlite3.
"""
import json
import re
import sqlite3
import struct
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


PAGE_SIZE = 512
HEADER_LEN = 28  # bytes 0..27 are page header
COMPACT_SLOT_LEN = 10
TLV_MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"

# Anchor field that uniquely identifies a voucher's primary page
ANCHOR_FIELD_BYTES = b"\xbb\x0b\x00\x06"  # fid=0x0bbb, type bytes "00 06"

# Companion field
PEER_FIELD_BYTES = b"\xbc\x0b\x00\x06"  # fid=0x0bbc

# Voucher date compact slot: fid=0x0067, type bytes "00 0d", value=u32 LE serial
DATE_FIELD_BYTES = b"\x67\x00\x00\x0d"

# Tally date base for the voucher_date serial (days since 1899-12-31).
DATE_EPOCH = date(1899, 12, 31)

# VchStatus.1800 layout (codex finding):
#   - 512-byte pages. Each page contains 3 fixed slots of 128 bytes each at
#     offsets 0x80, 0x100, 0x180.
#   - slot+0x28 = u32 LE voucher_date serial
#   - slot+0x64 = u32 LE voucher_type master_id (resolves via Manager.1800
#                  voucher_types table to a name).
VCHSTATUS_SLOT_OFFSETS = (0x80, 0x100, 0x180)
VCHSTATUS_SLOT_DATE_OFF = 0x28
VCHSTATUS_SLOT_VTYPE_OFF = 0x64

# Field IDs we want to extract from TLV records on voucher pages
VOUCHER_TLV_FIELDS = {
    0x0003: "voucher_key_raw",
    0x0002: "name_field_0x0002",
    0x00cb: "voucher_number",
    0x00cd: "narration",
    0x00ce: "party_or_address_0x00ce",
    0x00cf: "address_0x00cf",
    0x01f7: "currency_or_alias_0x01f7",
    0x07d3: "created_by",
    0x07d5: "voucher_number_alt",
    0x0bbc: "peer_master_id",
}


def normalize(s):
    if s is None:
        return s
    return s.rstrip("\r\n").strip()


def parse_tlv_at(data, p, end_limit):
    """Parse one TLV at offset p. Return (length_consumed, fid, type_hex, decoded)
    or None if invalid / would overflow end_limit.
    """
    if p + 8 > len(data) or data[p:p + 2] != TLV_MARKER:
        return None
    fid = struct.unpack_from("<H", data, p + 2)[0]
    tb = bytes(data[p + 4:p + 6])
    ln = struct.unpack_from("<H", data, p + 6)[0]
    rec_end = p + 8 + ln
    if rec_end > end_limit:
        return None
    val = bytes(data[p + 8:rec_end])
    decoded = None
    if tb == TYPE_STRING_UTF16:
        if ln >= 2 and ln % 2 == 0 and val[-2:] == b"\x00\x00":
            try:
                text = val[:-2].decode("utf-16le")
                if not text or all(c.isprintable() or c in "\t\n\r " for c in text):
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
    return (rec_end - p, fid, tb.hex(), ln, decoded)


def walk_tlvs_in_page(data, page_start):
    """Yield (offset_in_page, fid, type_hex, len, decoded) for valid TLVs in this
    page. Stops at first invalid record or page end.
    """
    end = page_start + PAGE_SIZE
    idx = page_start + HEADER_LEN
    while idx < end - 8:
        p = data.find(TLV_MARKER, idx, end)
        if p < 0 or p >= end:
            break
        rec = parse_tlv_at(data, p, end)
        if rec is None:
            idx = p + 2
            continue
        consumed, fid, tb, ln, dec = rec
        yield (p - page_start, fid, tb, ln, dec)
        idx = p + consumed


def parse_compact_slots(data, page_start):
    """Yield (offset_in_page, fid, type, raw_value_6) for compact slots in
    [page_start+HEADER_LEN, body limit). Body limit = first TLV marker, or
    page end if no TLV exists.
    """
    end = page_start + PAGE_SIZE
    # Find body limit = first TLV marker offset within page (or page end)
    first_tlv = data.find(TLV_MARKER, page_start + HEADER_LEN, end)
    body_end = first_tlv if first_tlv >= 0 else end
    idx = page_start + HEADER_LEN
    while idx + COMPACT_SLOT_LEN <= body_end:
        fid = struct.unpack_from("<H", data, idx + 0)[0]
        typ = struct.unpack_from("<H", data, idx + 2)[0]
        raw_val = bytes(data[idx + 4:idx + 10])
        yield (idx - page_start, fid, typ, raw_val)
        idx += COMPACT_SLOT_LEN


def find_anchor_pages(data):
    """Return dict master_id -> list of (page_no, offset_in_page) for the 0x0bbb anchor."""
    pages = {}
    idx = 0
    while True:
        p = data.find(ANCHOR_FIELD_BYTES, idx)
        if p < 0:
            break
        # value is the 4 bytes after fid+type = at p+4
        # but we want u32 starting at p+4
        val = struct.unpack_from("<I", data, p + 4)[0]
        page = p // PAGE_SIZE
        rel = p % PAGE_SIZE
        pages.setdefault(val, []).append((page, rel))
        idx = p + 1
    return pages


def find_compact_signature(data, page_start, sig):
    """Signature-scan the page body for a compact slot starting with the given
    4 bytes (fid:u16le + type:u16be-as-stored). Returns the offset of the slot
    start, or -1 if not found. Compact slots are NOT strictly aligned at
    HEADER_LEN+10*k — codex flagged this. We scan byte-by-byte in body."""
    page_end = page_start + PAGE_SIZE
    return data.find(sig, page_start + HEADER_LEN, page_end)


def decode_date_serial(serial):
    """Convert a Tally date serial (days since 1899-12-31) to ISO date string,
    or None if out of plausible range. Two TranMgr 0x0067 records have giant
    junk serials (~452M, ~2.5G) — those are date sentinels we filter."""
    if serial < 1 or serial > 100000:
        return None
    try:
        return (DATE_EPOCH + timedelta(days=serial)).isoformat()
    except OverflowError:
        return None


def extract_voucher_record(data, master_id, page_no):
    """Walk TLVs on the page and gather voucher fields. Returns dict."""
    page_start = page_no * PAGE_SIZE
    fields = defaultdict(list)
    field_types = {}
    for off, fid, tb, ln, dec in walk_tlvs_in_page(data, page_start):
        if dec is not None:
            fields[fid].append(dec)
            field_types.setdefault(fid, tb)

    # Walk compact slots and capture fields we care about (0x0bbc peer master_id).
    compact_fields = {}
    for off, fid, typ, raw_val in parse_compact_slots(data, page_start):
        if typ in (0x0006, 0x000d) and len(raw_val) >= 4:
            v = struct.unpack_from("<I", raw_val, 0)[0]
            if fid == 0x0bbc:
                compact_fields["peer_master_id"] = v
            elif fid == 0x07d4:
                compact_fields["compact_07d4"] = v
            elif fid == 0x07d3:
                compact_fields["compact_07d3"] = v
            elif fid == 0x00cb:
                compact_fields["compact_00cb"] = v

    # Voucher date — signature-scan the page body for 0x0067/000d. The compact
    # slot is at variable offset; strict 10-byte alignment from page+28 doesn't
    # work because slots can be padded.
    date_serial = None
    voucher_date = None
    dp = find_compact_signature(data, page_start, DATE_FIELD_BYTES)
    if dp >= 0:
        date_serial = struct.unpack_from("<I", data, dp + 4)[0]
        voucher_date = decode_date_serial(date_serial)

    return {
        "master_id": master_id,
        "page": page_no,
        "fields": dict(fields),
        "field_types": field_types,
        "compact": compact_fields,
        "date_serial": date_serial,
        "voucher_date": voucher_date,
    }


def parse_vchstatus_slots(vchstatus_path):
    """Parse VchStatus.1800 and return non-empty slots in file order.

    Yields (slot_index, page_no, slot_offset, date_serial, vtype_mid) where
    slot_index is 0..N over non-empty slots. A slot is non-empty when its
    date_serial != 0.
    """
    if not vchstatus_path.exists():
        return []
    data = vchstatus_path.read_bytes()
    out = []
    n_pages = len(data) // PAGE_SIZE
    idx = 0
    for pn in range(n_pages):
        for so in VCHSTATUS_SLOT_OFFSETS:
            slot_start = pn * PAGE_SIZE + so
            ds = struct.unpack_from("<I", data, slot_start + VCHSTATUS_SLOT_DATE_OFF)[0]
            if ds == 0:
                continue
            vt = struct.unpack_from("<I", data, slot_start + VCHSTATUS_SLOT_VTYPE_OFF)[0]
            out.append((idx, pn, so, ds, vt))
            idx += 1
    return out


def _reorder_within_date(recs, gap=5000):
    """Reorder voucher records within a single date so the largest contiguous
    master_id cluster comes first (sorted ascending), with isolated outlier
    mids appended at the end. Codex's note "two legacy low ids inserted into
    an Apr-30 cluster" — those legacy mids appear at the END of VchStatus's
    slot run for that date, not the beginning. Plain mid-asc puts them at
    position 0,1 and shifts the alignment of the bulk cluster by +N.

    Algorithm:
      1. Sort mids ascending.
      2. Split into clusters wherever consecutive mids differ by > gap.
      3. Concatenate: largest cluster first, smaller clusters after, each
         internally sorted ascending.

    With gap=5000, this lifts voucher_type accuracy from 99.14 % to 99.42 %.
    Larger gaps produce nearly identical results; smaller gaps over-split.
    """
    if len(recs) <= 2:
        return sorted(recs, key=lambda r: r["master_id"])
    s = sorted(recs, key=lambda r: r["master_id"])
    clusters = [[s[0]]]
    for r in s[1:]:
        if r["master_id"] - clusters[-1][-1]["master_id"] <= gap:
            clusters[-1].append(r)
        else:
            clusters.append([r])
    if len(clusters) == 1:
        return s
    # Bulk cluster = the largest; tie-break on the LATER cluster (recent edits).
    clusters.sort(key=lambda c: (len(c), c[-1]["master_id"]), reverse=True)
    return [r for cluster in clusters for r in cluster]


def pair_vouchers_with_vtypes(records, vchstatus_slots):
    """Attach voucher_type master_id to each voucher record.

    Strategy (per codex's "close to MASTERID order" hint + our verification):
      - Filter voucher records to those that have a voucher_key TLV (0x0003).
        Vouchers without voucher_key are deleted/internal/draft and are NOT
        represented in VchStatus. Filtering them out before pairing closes
        ~13 of the ~14 percentage-point gap. (Verified: 549 of 568 TranMgr
        vouchers absent from Rosetta also have voucher_key=NULL; the
        remaining 19 are codex's base-type-31 internal vouchers.)
      - Group remaining voucher records by date_serial.
      - Within each date group, reorder so the bulk-cluster mids come first
        (ascending), and isolated low-mid outliers (legacy back-dated edits)
        come at the end. See _reorder_within_date.
      - Stream VchStatus slots by date in file order.
      - Pair the i-th voucher in a date group with the i-th VchStatus slot for
        the same date.

    This recovers 99.42 % of voucher_type assignments exactly. Residual
    mismatches are within-date intra-cluster ordering quirks we don't fully
    understand yet.

    Mutates records in place: adds 'voucher_type_mid' for paired records.
    """
    voucher_by_date = defaultdict(list)
    for r in records:
        if not r.get("date_serial"):
            continue
        # Vouchers without voucher_key are NOT in VchStatus; skip them.
        if not r.get("fields", {}).get(0x0003):
            continue
        voucher_by_date[r["date_serial"]].append(r)
    for ds in voucher_by_date:
        voucher_by_date[ds] = _reorder_within_date(voucher_by_date[ds])

    slots_by_date = defaultdict(list)
    for slot in vchstatus_slots:
        slots_by_date[slot[3]].append(slot)
    # already in file order

    paired = 0
    for ds, recs in voucher_by_date.items():
        slots = slots_by_date.get(ds, [])
        n = min(len(recs), len(slots))
        for i in range(n):
            recs[i]["voucher_type_mid"] = slots[i][4]
            paired += 1
    return paired


def main():
    if len(sys.argv) > 1:
        company_dir = Path(sys.argv[1])
    else:
        company_dir = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525")
    src = company_dir / "TranMgr.1800"
    rosetta = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")
    out_path = Path(f"/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/{company_dir.name}_vouchers_v1.sqlite3")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    print(f"Reading {src} ({src.stat().st_size:,} bytes)...")
    if src.stat().st_size % PAGE_SIZE != 0:
        print(f"  WARNING: file size not page-aligned, mod={src.stat().st_size % PAGE_SIZE}")
    data = src.read_bytes()
    n_pages = len(data) // PAGE_SIZE
    print(f"  {n_pages:,} pages")

    # Determine company GUID prefix (used for unmatched voucher GUID synthesis)
    print("\nLoading Rosetta voucher GUID lookup...")
    conn = sqlite3.connect(str(rosetta))
    company_guid_prefix = None
    cguid_row = conn.execute("SELECT guid FROM vouchers WHERE guid IS NOT NULL LIMIT 1").fetchone()
    if cguid_row:
        company_guid_prefix = cguid_row[0][:36]
        print(f"  Company GUID prefix: {company_guid_prefix}")

    rosetta_voucher_by_mid = {}
    cols = [r[1] for r in conn.execute("PRAGMA table_info(vouchers)")]
    for row in conn.execute("SELECT * FROM vouchers"):
        d = dict(zip(cols, row))
        try:
            mid = int(d["master_id"])
        except (TypeError, ValueError):
            continue
        rosetta_voucher_by_mid[mid] = d
    print(f"  {len(rosetta_voucher_by_mid)} Rosetta vouchers")

    # Load voucher_type master_id -> name map from v5 master sqlite (if it
    # exists for this company). Used to render voucher_type as a human string.
    v5_path = Path(f"/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/{company_dir.name}_master_v5.sqlite3")
    vt_name = {}
    if v5_path.exists():
        v5conn = sqlite3.connect(str(v5_path))
        try:
            for r in v5conn.execute("SELECT master_id, extracted_name FROM voucher_types"):
                vt_name[r[0]] = r[1]
            print(f"  voucher_type lookup: {len(vt_name)} types from {v5_path.name}.voucher_types")
        except sqlite3.OperationalError:
            print(f"  no voucher_types table in {v5_path.name}")
        # Fallback: when voucher_types is empty (cross-company runs without rosetta
        # ground truth), the kind classifier never tagged voucher_type records as
        # 'voucher_types'. Names are still recoverable from master_records.
        if not vt_name:
            try:
                for r in v5conn.execute(
                    "SELECT master_id, extracted_name FROM master_records "
                    "WHERE extracted_name IS NOT NULL"
                ):
                    vt_name[r[0]] = r[1]
                print(f"  voucher_type fallback: {len(vt_name)} names from master_records")
            except sqlite3.OperationalError:
                pass
        v5conn.close()
    else:
        print(f"  v5 master sqlite not found ({v5_path}); voucher_type names will be NULL")

    # Find anchor pages (0x0bbb -> master_id)
    print("\nLocating voucher anchor pages (field 0x0bbb)...")
    anchor_map = find_anchor_pages(data)
    print(f"  {len(anchor_map):,} unique master_ids at anchors")

    # Build voucher records
    print("\nExtracting voucher records...")
    records = []
    for mid, locations in anchor_map.items():
        # Use first anchor location (could be on multiple pages but in practice 1)
        page_no, rel = locations[0]
        rec = extract_voucher_record(data, mid, page_no)
        records.append(rec)

    # Load VchStatus for voucher_type assignments.
    vchstatus_path = company_dir / "VchStatus.1800"
    print(f"\nReading {vchstatus_path}...")
    vchstatus_slots = parse_vchstatus_slots(vchstatus_path)
    print(f"  {len(vchstatus_slots):,} non-empty slots")

    # Pair vouchers with voucher_type by date / mid-asc heuristic.
    paired = pair_vouchers_with_vtypes(records, vchstatus_slots)
    print(f"  voucher_type paired for {paired:,} vouchers")

    # Write SQLite
    print(f"\nWriting -> {out_path}")
    out = sqlite3.connect(str(out_path))
    out.execute("PRAGMA synchronous=OFF")
    out.execute("PRAGMA journal_mode=MEMORY")

    out.execute("""
        CREATE TABLE vouchers (
            master_id INTEGER PRIMARY KEY,
            guid TEXT UNIQUE,
            page INTEGER,
            voucher_key_raw TEXT,
            voucher_number TEXT,
            narration TEXT,
            party_or_address TEXT,
            peer_master_id INTEGER,
            voucher_date TEXT,
            voucher_date_serial INTEGER,
            voucher_type_mid INTEGER,
            voucher_type TEXT,
            rosetta_match INTEGER DEFAULT 0,
            rosetta_voucher_date TEXT,
            rosetta_voucher_number TEXT,
            rosetta_voucher_type TEXT,
            rosetta_party_name TEXT,
            fields_json TEXT,
            compact_json TEXT
        )
    """)
    out.execute("CREATE INDEX idx_voucher_guid ON vouchers(guid)")
    out.execute("CREATE INDEX idx_voucher_match ON vouchers(rosetta_match)")
    out.execute("CREATE INDEX idx_voucher_date ON vouchers(voucher_date)")
    out.execute("CREATE INDEX idx_voucher_type ON vouchers(voucher_type)")

    for r in records:
        mid = r["master_id"]
        guid = (company_guid_prefix + f"-{mid:08x}") if company_guid_prefix else None
        ros = rosetta_voucher_by_mid.get(mid)
        rosetta_match = 1 if ros is not None else 0

        # Pull TLV string fields by best name
        f = r["fields"]
        def first_str(fid):
            v = f.get(fid)
            if v and isinstance(v[0], str):
                return normalize(v[0])
            return None

        voucher_key_raw = first_str(0x0003)
        voucher_number = first_str(0x00cb) or first_str(0x07d5)
        narration = first_str(0x00cd)
        party_or_address = first_str(0x00ce) or first_str(0x0002)

        fields_serializable = {f"0x{k:04x}": (v if len(v) > 1 else v[0]) for k, v in f.items()}
        fields_json = json.dumps(fields_serializable, ensure_ascii=False, default=str)
        compact_json = json.dumps(r["compact"], ensure_ascii=False)

        vtype_mid = r.get("voucher_type_mid")
        vtype_name = vt_name.get(vtype_mid) if vtype_mid is not None else None

        out.execute(
            "INSERT OR REPLACE INTO vouchers VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mid, guid, r["page"], voucher_key_raw, voucher_number,
                narration, party_or_address,
                r["compact"].get("peer_master_id"),
                r.get("voucher_date"),
                r.get("date_serial"),
                vtype_mid,
                vtype_name,
                rosetta_match,
                ros["voucher_date"] if ros else None,
                ros["voucher_number"] if ros else None,
                ros["voucher_type_name"] if ros else None,
                ros["party_name"] if ros else None,
                fields_json, compact_json,
            ),
        )

    out.commit()

    # Validation by GUID
    print("\n=== Validation by GUID ===")
    ros_guids = set(r[0] for r in conn.execute("SELECT guid FROM vouchers WHERE guid IS NOT NULL"))
    our_guids = set(r[0] for r in out.execute("SELECT guid FROM vouchers WHERE guid IS NOT NULL"))
    matched = ros_guids & our_guids
    pct = 100 * len(matched) / max(1, len(ros_guids))
    print(f"  Rosetta vouchers : {len(ros_guids):>6}")
    print(f"  Ours             : {len(our_guids):>6}")
    print(f"  GUID match       : {len(matched):>6}  ({pct:.1f}%)")
    print(f"  Extra (deleted/internal): {len(our_guids - ros_guids):>6}")

    # Field-level coverage among matched vouchers
    print("\n=== Field coverage among GUID-matched vouchers ===")
    cur = out.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN voucher_key_raw IS NOT NULL THEN 1 ELSE 0 END) as has_key,
            SUM(CASE WHEN voucher_number IS NOT NULL THEN 1 ELSE 0 END) as has_num,
            SUM(CASE WHEN narration IS NOT NULL THEN 1 ELSE 0 END) as has_narr,
            SUM(CASE WHEN party_or_address IS NOT NULL THEN 1 ELSE 0 END) as has_party,
            SUM(CASE WHEN voucher_date IS NOT NULL THEN 1 ELSE 0 END) as has_date,
            SUM(CASE WHEN voucher_type IS NOT NULL THEN 1 ELSE 0 END) as has_type
        FROM vouchers WHERE rosetta_match=1
    """)
    total, has_key, has_num, has_narr, has_party, has_date, has_type = cur.fetchone()
    total = total or 0
    has_key = has_key or 0
    has_num = has_num or 0
    has_narr = has_narr or 0
    has_party = has_party or 0
    has_date = has_date or 0
    has_type = has_type or 0
    print(f"  matched           : {total}")
    if total:
        print(f"  has voucher_key   : {has_key}  ({100*has_key/total:.1f}%)")
        print(f"  has voucher_number: {has_num}  ({100*has_num/total:.1f}%)")
        print(f"  has narration     : {has_narr}  ({100*has_narr/total:.1f}%)")
        print(f"  has party_or_addr : {has_party} ({100*has_party/total:.1f}%)")
        print(f"  has voucher_date  : {has_date}  ({100*has_date/total:.1f}%)")
        print(f"  has voucher_type  : {has_type}  ({100*has_type/total:.1f}%)")

    # voucher_date and voucher_type accuracy vs Rosetta
    print("\n=== voucher_date / voucher_type accuracy ===")
    cur = out.execute("""
        SELECT
            SUM(CASE WHEN voucher_date IS NOT NULL AND voucher_date = rosetta_voucher_date
                     THEN 1 ELSE 0 END) AS date_exact,
            SUM(CASE WHEN voucher_date IS NOT NULL AND rosetta_voucher_date IS NOT NULL
                     THEN 1 ELSE 0 END) AS date_compared,
            SUM(CASE WHEN voucher_type IS NOT NULL AND voucher_type = rosetta_voucher_type
                     THEN 1 ELSE 0 END) AS type_exact,
            SUM(CASE WHEN voucher_type IS NOT NULL AND rosetta_voucher_type IS NOT NULL
                     THEN 1 ELSE 0 END) AS type_compared
        FROM vouchers WHERE rosetta_match=1
    """)
    date_exact, date_compared, type_exact, type_compared = cur.fetchone()
    date_exact = date_exact or 0
    date_compared = date_compared or 0
    type_exact = type_exact or 0
    type_compared = type_compared or 0
    if date_compared:
        print(f"  voucher_date  : {date_exact}/{date_compared} exact "
              f"({100*date_exact/date_compared:.2f}%)")
    if type_compared:
        print(f"  voucher_type  : {type_exact}/{type_compared} exact "
              f"({100*type_exact/type_compared:.2f}%)")
    else:
        # Fallback: report on ALL extracted vouchers (no rosetta to validate against)
        cur2 = out.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN voucher_key_raw IS NOT NULL THEN 1 ELSE 0 END) as has_key,
                SUM(CASE WHEN voucher_number IS NOT NULL THEN 1 ELSE 0 END) as has_num,
                SUM(CASE WHEN narration IS NOT NULL THEN 1 ELSE 0 END) as has_narr,
                SUM(CASE WHEN party_or_address IS NOT NULL THEN 1 ELSE 0 END) as has_party
            FROM vouchers
        """)
        t, hk, hn, hna, hp = cur2.fetchone()
        t = t or 0
        if t:
            print(f"  (no rosetta match for this company, reporting on ALL extracted)")
            print(f"  total extracted   : {t}")
            print(f"  has voucher_key   : {hk}  ({100*hk/t:.1f}%)")
            print(f"  has voucher_number: {hn}  ({100*hn/t:.1f}%)")
            print(f"  has narration     : {hna}  ({100*hna/t:.1f}%)")
            print(f"  has party_or_addr : {hp}  ({100*hp/t:.1f}%)")

    out.close()
    conn.close()
    print(f"\nWrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

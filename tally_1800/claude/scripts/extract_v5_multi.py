"""Extract v5 (multi-company): page-oriented master extraction using MASTERID at page+4.

Generalised v5 that takes a corpus folder + company-name guess at the CLI:

    python3 extract_v5_multi.py <corpus_folder> <company_name_guess> [-o out.sqlite]

If the company is present in corpus/rosetta.sqlite3 (matched on `companies.name`
or `ledgers.company_name`), GUID-based validation runs against that company's
slice of Rosetta. Otherwise we report structural metrics only:

    total pages, page groups (= records), unique master_ids,
    kinds-classification distribution, distinct extracted names,
    plausible-name count.

We optionally cross-check distinct extracted names against codex's
manager_records table at /Users/aakashchid/workshop/sena/tallydatacrack-codex/out/<company>_decoded.sqlite3
when --codex is given.

Bug fix vs original v5: the validation loop crashed on tables without a `guid`
column (voucher_types, godowns, units, cost_centres). Those four are now
validated by NAME instead, scoped to the company under test.
"""
import argparse
import json
import re
import sqlite3
import struct
from collections import defaultdict
from pathlib import Path


PAGE_SIZE = 512
MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"
PAGE_HEADER_SIZE = 28  # Each page's data area starts at offset 28; bytes 0..27 are
                       # the page header (16 bytes) + slot-block marker (12 bytes).
                       # When a TLV spans a page boundary, the tail of its value
                       # continues at offset 28 of the next page, NOT offset 0.

GUID_KINDS = ("ledgers", "groups", "stock_items", "companies")
NAME_KINDS = ("voucher_types", "godowns", "cost_centres", "units")
ALL_KINDS = GUID_KINDS + NAME_KINDS


def normalize(s):
    if s is None:
        return s
    return s.rstrip("\r\n").strip()


def _decode_tlv_value(tb, ln, val):
    """Decode a TLV value given its type-byte and length. val is the raw bytes."""
    if tb == TYPE_STRING_UTF16:
        if ln >= 2 and ln % 2 == 0 and val[-2:] == b"\x00\x00":
            try:
                text = val[:-2].decode("utf-16le")
                if not text or all(c.isprintable() or c in "\t\n " for c in text):
                    return text
            except UnicodeDecodeError:
                pass
        return None
    if tb in (b"\x00\x0d", b"\x00\x06", b"\x00\x08") and ln == 4:
        return struct.unpack_from("<I", val, 0)[0]
    if tb == b"\x00\x0a" and ln == 4:
        return struct.unpack_from("<i", val, 0)[0]
    if tb == b"\x00\x09" and ln == 1:
        return val[0]
    if tb == b"\x00\x0b" and ln == 8:
        return struct.unpack_from("<d", val, 0)[0]
    return None


def _build_logical_stream(data, page_indices_run):
    """Concatenate the data-area bytes of contiguous pages in `page_indices_run`,
    skipping each page's 28-byte header. Returns (stream_bytes, page_offsets) where
    page_offsets[i] = byte offset in stream where page i's data area starts.

    This accounts for TLVs that span page boundaries: their value continues at
    offset 28 of the next page, not at offset 0 (which is the next page's header).
    """
    parts = []
    page_offsets = []
    cursor = 0
    for pn in page_indices_run:
        s = pn * PAGE_SIZE
        e = s + PAGE_SIZE
        # Skip this page's header (bytes 0..27 of the page = first 28 bytes).
        page_data_area = data[s + PAGE_HEADER_SIZE:e]
        page_offsets.append((pn, cursor))
        parts.append(page_data_area)
        cursor += len(page_data_area)
    return b"".join(parts), page_offsets


def _walk_tlvs_in_logical_stream(stream):
    """Walk a TLV stream where bytes are already filtered through the page-header
    gap. A TLV that crosses what was previously a page boundary now reads cleanly
    because we've stripped the 28-byte page headers."""
    idx = 0
    n = len(stream)
    while idx < n - 8:
        p = stream.find(MARKER, idx)
        if p < 0:
            break
        if p + 8 > n:
            break
        fid = struct.unpack_from("<H", stream, p + 2)[0]
        tb = bytes(stream[p + 4:p + 6])
        ln = struct.unpack_from("<H", stream, p + 6)[0]
        end = p + 8 + ln
        if end > n:
            # Truncated at the end of the run — skip.
            break
        val = bytes(stream[p + 8:end])
        decoded = _decode_tlv_value(tb, ln, val)
        if decoded is not None:
            yield (p, fid, tb.hex(), ln, end, decoded)
            idx = end
        else:
            idx = p + 1


def walk_tlvs_in_pages(data, page_indices):
    """Walk TLVs across the listed pages, respecting the 28-byte page header
    on each page. Splits page list into contiguous runs and concatenates the
    data areas of each run before walking — so TLVs whose value spans a page
    boundary are read correctly (the value continues at offset 28 of the next
    page, NOT offset 0)."""
    if not page_indices:
        return
    pages = sorted(page_indices)
    runs = []
    cur_start = pages[0]
    cur_end = pages[0]
    for pn in pages[1:]:
        if pn == cur_end + 1:
            cur_end = pn
        else:
            runs.append(list(range(cur_start, cur_end + 1)))
            cur_start = cur_end = pn
    runs.append(list(range(cur_start, cur_end + 1)))
    for run in runs:
        stream, _page_offsets = _build_logical_stream(data, run)
        yield from _walk_tlvs_in_logical_stream(stream)


def page_groups(data):
    n_pages = len(data) // PAGE_SIZE
    by_id = defaultdict(list)
    for pn in range(1, n_pages):
        mid = struct.unpack_from("<I", data, pn * PAGE_SIZE + 4)[0]
        if mid == 0:
            continue
        by_id[mid].append(pn)
    for mid, pages in by_id.items():
        yield mid, sorted(pages)


def extract_record(data, master_id, pages):
    fields = defaultdict(list)
    field_types = {}
    for tlv in walk_tlvs_in_pages(data, pages):
        off, fid, t, ln, _, dec = tlv
        if dec is not None:
            fields[fid].append(dec)
            field_types.setdefault(fid, t)
    return {
        "master_id": master_id,
        "pages": pages,
        "page_start": pages[0],
        "page_count": len(pages),
        "byte_start": pages[0] * PAGE_SIZE,
        "byte_end": (pages[-1] + 1) * PAGE_SIZE,
        "fields": dict(fields),
        "field_types": field_types,
    }


NAME_FIELDS = (0x0002, 0x01f7, 0x0af3, 0x0001)
NAME_FIELD_PRIORITY = {0x0002: 1000, 0x01f7: 500, 0x0af3: 300, 0x0001: 100}
NAME_NOISE_LABELS = frozenset({
    "Primary Mobile No.", "Mobile No.", "Email", "Phone No.",
    "Whatsapp No.", "Office Address", "Home Address",
})
_TALLY_PREFIX_RE = re.compile(r"^[0-9]{2}[A-Za-z][0-9A-Za-z]?\s")
_ALLOWED_HIGH_CODEPOINTS = frozenset({
    0x2010, 0x2013, 0x2014, 0x2018, 0x2019, 0x201c, 0x201d,
    0x20a8, 0x20a9, 0x20b9, 0x2122, 0x00a9, 0x00ae, 0x00b0, 0x00ba,
})


def _name_has_garbage(s):
    """Reject TLV reads where the walker overran into the next record's bytes
    (manifests as CJK / Bengali / Devanagari etc. inside a Latin master name)."""
    for c in s:
        cp = ord(c)
        if cp < 0x20 and c not in "\t\n\r":
            return True
        if 0xd800 <= cp <= 0xdfff:
            return True
        if cp == 0xfffd:
            return True
        if cp > 0x024f and cp not in _ALLOWED_HIGH_CODEPOINTS:
            return True
    return False


def _name_is_noise(s):
    """Reject template-form labels picked up via cross-master page pollution."""
    s = s.strip()
    if s in NAME_NOISE_LABELS:
        return True
    if not any(c.isalpha() or c.isdigit() for c in s):
        return True
    return False


def best_name(fields):
    """Pick the most likely 'name' for a record from common name field IDs.

    Priority: 0x0002 > 0x01f7 > 0x0af3 > 0x0001, with three caveats:
      (a) within a multi-value list, prefer the first entry (Tally writes
          the canonical name first, alias second);
      (b) reject values whose codepoints fall outside Latin-extended (signals
          a TLV walker overrun into the next record);
      (c) reject template-form labels (Primary Mobile No. etc.) that come from
          cross-master page pollution.
    A '10E2 ' / '20L1 ' prefix gives a small bonus to break ties toward
    real ledger names.
    """
    candidates = []
    for fid in NAME_FIELDS:
        if fid not in fields or not fields[fid]:
            continue
        for list_pos, val in enumerate(fields[fid]):
            if not isinstance(val, str):
                continue
            if _name_has_garbage(val):
                continue
            if _name_is_noise(val):
                continue
            stripped = normalize(val)
            if not stripped:
                continue
            score = NAME_FIELD_PRIORITY[fid] - list_pos * 30
            if _TALLY_PREFIX_RE.match(stripped):
                score += 200
            candidates.append((score, stripped))
    if not candidates:
        return None
    candidates.sort(key=lambda c: -c[0])
    return candidates[0][1]


def is_plausible_name(name):
    if not name or not isinstance(name, str):
        return False
    if len(name) <= 3:
        return False
    has_letter = any(c.isalpha() for c in name)
    return has_letter


def load_linkmgr_name_lookup(linkmgr_path):
    """Build a manager_master_id -> most-frequent-ledger-name lookup from a
    pre-extracted LinkMgr SQLite. Returns None if file doesn't exist.

    Used as a fallback for ledger records whose own pages don't carry the name.
    Caveats: LinkMgr names can be STALE (recorded at allocation creation time;
    later renames in Manager.1800 don't propagate). Use only when extracted_name
    is None to avoid replacing a fresh name with a stale one.
    """
    if not linkmgr_path or not Path(linkmgr_path).exists():
        return None
    conn = sqlite3.connect(str(linkmgr_path))
    lookup = {}
    rows = conn.execute("""
        SELECT manager_master_id, ledger_name, COUNT(*) AS n FROM (
            SELECT manager_master_id, ledger_name FROM bill_allocations
            WHERE manager_master_id IS NOT NULL
              AND ledger_name IS NOT NULL AND ledger_name != ''
            UNION ALL
            SELECT manager_master_id, ledger_name FROM bank_allocations
            WHERE manager_master_id IS NOT NULL
              AND ledger_name IS NOT NULL AND ledger_name != ''
        ) GROUP BY manager_master_id, ledger_name
        ORDER BY manager_master_id, n DESC
    """).fetchall()
    for mid, name, n in rows:
        if mid not in lookup:
            lookup[mid] = name
    conn.close()
    return lookup


def load_rosetta_company(rosetta_path, company_name):
    """Return dict with rosetta-by-master-id (GUID kinds) and rosetta-by-name (NAME kinds)
    scoped to one company, or None if company not present in rosetta."""
    if not rosetta_path.exists():
        return None
    conn = sqlite3.connect(str(rosetta_path))
    cguid_row = conn.execute(
        "SELECT guid FROM ledgers WHERE company_name=? AND guid IS NOT NULL LIMIT 1",
        (company_name,),
    ).fetchone()
    if not cguid_row:
        conn.close()
        return None
    company_guid = cguid_row[0][:36]

    by_master_id = {}
    for kind in ("ledgers", "groups", "stock_items", "companies"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({kind})")]
        if "guid" not in cols or "company_name" not in cols:
            continue
        for row in conn.execute(
            f"SELECT * FROM {kind} WHERE company_name=? AND guid IS NOT NULL",
            (company_name,),
        ):
            rd = dict(zip(cols, row))
            guid = rd["guid"]
            if len(guid) >= 45 and guid[36] == "-":
                try:
                    mid = int(guid[37:45], 16)
                    if mid not in by_master_id:
                        by_master_id[mid] = (kind, rd.get("name"), guid, rd)
                except ValueError:
                    pass

    by_name = {}
    for kind in NAME_KINDS:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({kind})")]
        if "name" not in cols or "company_name" not in cols:
            continue
        for row in conn.execute(
            f"SELECT * FROM {kind} WHERE company_name=?",
            (company_name,),
        ):
            rd = dict(zip(cols, row))
            nm = normalize(rd.get("name"))
            if nm:
                by_name[(kind, nm)] = rd

    conn.close()
    return {
        "company_guid": company_guid,
        "company_name": company_name,
        "by_master_id": by_master_id,
        "by_name": by_name,
    }


def classify_record(rec, rosetta):
    """Assign rec['kind']. Two-stage:
        1. If master_id is in Rosetta GUID lookup → use that kind.
        2. Else if the extracted name matches a Rosetta NAME_KINDS row → use that kind.
        3. Else 'unclassified_masters'.
    """
    mid = rec["master_id"]
    name = best_name(rec["fields"])
    rec["extracted_name"] = name
    if rosetta and mid in rosetta["by_master_id"]:
        kind, ros_name, guid, full_row = rosetta["by_master_id"][mid]
        rec["kind"] = kind
        rec["rosetta_name"] = ros_name
        rec["guid"] = guid
        rec["rosetta_row"] = full_row
        rec["matched_by"] = "guid"
        return
    if rosetta and name:
        for kind in NAME_KINDS:
            if (kind, name) in rosetta["by_name"]:
                rec["kind"] = kind
                rec["rosetta_name"] = name
                rec["guid"] = (rosetta["company_guid"] + f"-{mid:08x}") if rosetta["company_guid"] else None
                rec["rosetta_row"] = rosetta["by_name"][(kind, name)]
                rec["matched_by"] = "name"
                return
    rec["kind"] = "unclassified_masters"
    rec["guid"] = (rosetta["company_guid"] + f"-{mid:08x}") if rosetta and rosetta.get("company_guid") else None
    rec["rosetta_name"] = None
    rec["rosetta_row"] = None
    rec["matched_by"] = None


def write_sqlite(out_path, records):
    if out_path.exists():
        out_path.unlink()
    out = sqlite3.connect(str(out_path))
    out.execute("PRAGMA synchronous=OFF")
    out.execute("PRAGMA journal_mode=MEMORY")
    out.execute("""
        CREATE TABLE master_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            master_id INTEGER UNIQUE,
            guid TEXT,
            kind TEXT,
            page_start INTEGER,
            page_count INTEGER,
            byte_start INTEGER,
            extracted_name TEXT,
            rosetta_name TEXT,
            matched_by TEXT,
            field_count INTEGER,
            fields_json TEXT
        )
    """)
    out.execute("CREATE INDEX idx_kind_v5m ON master_records(kind)")
    out.execute("CREATE INDEX idx_guid_v5m ON master_records(guid)")
    for kind in ALL_KINDS + ("unclassified_masters",):
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
        fj = json.dumps(
            {f"0x{fid:04x}": (vals if len(vals) > 1 else vals[0])
             for fid, vals in r["fields"].items()},
            ensure_ascii=False, default=str,
        )
        out.execute(
            "INSERT INTO master_records(master_id, guid, kind, page_start, page_count, byte_start, extracted_name, rosetta_name, matched_by, field_count, fields_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (r["master_id"], r.get("guid"), r["kind"], r["page_start"], r["page_count"],
             r["byte_start"], r.get("extracted_name"), r.get("rosetta_name"), r.get("matched_by"),
             sum(len(v) for v in r["fields"].values()), fj),
        )
        out.execute(
            f"INSERT OR IGNORE INTO {r['kind']}(master_id, guid, page_start, page_count, extracted_name, rosetta_name, fields_json) VALUES (?,?,?,?,?,?,?)",
            (r["master_id"], r.get("guid"), r["page_start"], r["page_count"],
             r.get("extracted_name"), r.get("rosetta_name"), fj),
        )
    out.commit()
    return out


def validate_against_rosetta(rosetta_path, company_name, out_conn):
    """Return list of (kind, rosetta_count, ours_count, match_count, pct)."""
    rconn = sqlite3.connect(str(rosetta_path))
    results = []
    grand_match = 0
    grand_total = 0
    for kind in GUID_KINDS:
        cols = [r[1] for r in rconn.execute(f"PRAGMA table_info({kind})")]
        if "guid" not in cols or "company_name" not in cols:
            continue
        ros_guids = set(
            r[0] for r in rconn.execute(
                f"SELECT guid FROM {kind} WHERE company_name=? AND guid IS NOT NULL",
                (company_name,),
            )
        )
        if not ros_guids:
            continue
        our_guids = set(
            r[0] for r in out_conn.execute(
                f"SELECT guid FROM {kind} WHERE guid IS NOT NULL"
            )
        )
        match = len(ros_guids & our_guids)
        pct = 100 * match / max(1, len(ros_guids))
        results.append((kind, len(ros_guids), len(our_guids), match, pct))
        grand_match += match
        grand_total += len(ros_guids)
    for kind in NAME_KINDS:
        cols = [r[1] for r in rconn.execute(f"PRAGMA table_info({kind})")]
        if "name" not in cols or "company_name" not in cols:
            continue
        ros_names = set(
            normalize(r[0]) for r in rconn.execute(
                f"SELECT name FROM {kind} WHERE company_name=? AND name IS NOT NULL",
                (company_name,),
            )
        )
        if not ros_names:
            continue
        our_names = set(
            normalize(r[0]) for r in out_conn.execute(
                f"SELECT extracted_name FROM {kind} WHERE extracted_name IS NOT NULL"
            )
        )
        match = len(ros_names & our_names)
        pct = 100 * match / max(1, len(ros_names))
        results.append((kind, len(ros_names), len(our_names), match, pct))
        grand_match += match
        grand_total += len(ros_names)
    rconn.close()
    return results, grand_match, grand_total


def cross_check_codex(codex_path, our_names):
    if not codex_path.exists():
        return None
    cconn = sqlite3.connect(str(codex_path))
    codex_names = set(
        normalize(r[0]) for r in cconn.execute(
            "SELECT DISTINCT name FROM manager_records WHERE name IS NOT NULL"
        )
    )
    cconn.close()
    plausible_codex = {n for n in codex_names if is_plausible_name(n)}
    plausible_ours = {n for n in our_names if is_plausible_name(n)}
    overlap = plausible_codex & plausible_ours
    only_codex = plausible_codex - plausible_ours
    only_ours = plausible_ours - plausible_codex
    return {
        "codex_distinct": len(codex_names),
        "codex_plausible": len(plausible_codex),
        "ours_plausible": len(plausible_ours),
        "overlap": len(overlap),
        "pct_codex_in_ours": 100 * len(overlap) / max(1, len(plausible_codex)),
        "pct_ours_in_codex": 100 * len(overlap) / max(1, len(plausible_ours)),
        "only_codex_sample": sorted(only_codex)[:10],
        "only_ours_sample": sorted(only_ours)[:10],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_folder", help="Path to a corpus/<company>/ folder containing Manager.1800")
    ap.add_argument("company_name", help="Best-guess Rosetta company_name (for validation, if present)")
    ap.add_argument("-o", "--output", default=None, help="Output SQLite path")
    ap.add_argument("--rosetta", default="/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")
    ap.add_argument("--codex", default=None, help="Path to codex's _decoded.sqlite3 for cross-check")
    ap.add_argument("--linkmgr", default=None, help="Path to a <corpus>_linkmgr_v0.sqlite3 produced by extract_linkmgr.py; used to fill ledger names that aren't stored in Manager.1800")
    args = ap.parse_args()

    corpus = Path(args.corpus_folder).resolve()
    src = corpus / "Manager.1800"
    if not src.exists():
        raise SystemExit(f"Manager.1800 not found in {corpus}")
    company = corpus.name
    out_path = Path(args.output) if args.output else (
        Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out") /
        f"{company}_master_v5.sqlite3"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"=== {company} ({args.company_name}) ===")
    print(f"Reading {src} ({src.stat().st_size:,} bytes)")
    data = src.read_bytes()
    n_pages = len(data) // PAGE_SIZE
    print(f"  {n_pages:,} pages of {PAGE_SIZE} bytes each")

    rosetta_path = Path(args.rosetta)
    rosetta = load_rosetta_company(rosetta_path, args.company_name)
    if rosetta:
        print(f"  Rosetta company-slice loaded: {len(rosetta['by_master_id'])} GUID-kind rows, {len(rosetta['by_name'])} NAME-kind rows")
        print(f"  Company GUID: {rosetta['company_guid']}")
    else:
        print(f"  No Rosetta data for company_name='{args.company_name}' — structural validation only")

    print("Extracting page-grouped records...")
    records = []
    for mid, pages in page_groups(data):
        rec = extract_record(data, mid, pages)
        records.append(rec)
    print(f"  {len(records):,} master records (= unique non-zero MASTERIDs)")

    linkmgr_names = load_linkmgr_name_lookup(args.linkmgr) if args.linkmgr else None
    if linkmgr_names is not None:
        print(f"  LinkMgr name-lookup loaded: {len(linkmgr_names):,} master_id->name mappings")

    by_kind = defaultdict(list)
    linkmgr_filled = 0
    for r in records:
        classify_record(r, rosetta)
        if linkmgr_names is not None and r.get("kind") == "ledgers" and not r.get("extracted_name"):
            cand = linkmgr_names.get(r["master_id"])
            if cand:
                r["extracted_name"] = cand
                r["matched_by"] = (r.get("matched_by") or "") + "+linkmgr"
                linkmgr_filled += 1
        by_kind[r["kind"]].append(r)
    if linkmgr_names is not None:
        print(f"  LinkMgr fallback filled extracted_name for: {linkmgr_filled} ledger records")

    print()
    print("Kind distribution:")
    for kind, recs in sorted(by_kind.items(), key=lambda x: -len(x[1])):
        print(f"  {kind:<22}: {len(recs):>6}")

    distinct_names = {r.get("extracted_name") for r in records if r.get("extracted_name")}
    plausible = {n for n in distinct_names if is_plausible_name(n)}
    print()
    print(f"Distinct extracted names: {len(distinct_names):,}")
    print(f"Plausible names (alpha + len>3): {len(plausible):,}")

    print(f"\nWriting SQLite -> {out_path}")
    out = write_sqlite(out_path, records)

    if rosetta:
        print("\n=== Validation vs Rosetta ===")
        print(f"{'kind':<22} {'rosetta':>8} {'ours':>8} {'match':>8} {'%':>6}")
        results, grand_match, grand_total = validate_against_rosetta(
            rosetta_path, args.company_name, out
        )
        for kind, rc, oc, mc, pct in results:
            print(f"{kind:<22} {rc:>8} {oc:>8} {mc:>8} {pct:>6.1f}")
        if grand_total:
            print(f"{'TOTAL':<22} {grand_total:>8} {'':>8} {grand_match:>8} {100*grand_match/grand_total:>6.1f}")

    if args.codex:
        codex_path = Path(args.codex)
        cc = cross_check_codex(codex_path, distinct_names)
        if cc:
            print(f"\n=== Cross-check vs codex {codex_path.name} ===")
            print(f"  codex distinct names:    {cc['codex_distinct']:,}")
            print(f"  codex plausible names:   {cc['codex_plausible']:,}")
            print(f"  ours plausible names:    {cc['ours_plausible']:,}")
            print(f"  overlap:                 {cc['overlap']:,}")
            print(f"  codex names found in ours: {cc['pct_codex_in_ours']:.1f}%")
            print(f"  ours names found in codex: {cc['pct_ours_in_codex']:.1f}%")
            if cc['only_codex_sample']:
                print(f"  sample codex-only names: {cc['only_codex_sample']}")
            if cc['only_ours_sample']:
                print(f"  sample ours-only names: {cc['only_ours_sample']}")

    out.close()
    print(f"\nWrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

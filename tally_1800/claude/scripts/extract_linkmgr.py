"""LinkMgr.1800 extractor.

LinkMgr.1800 is a 512-byte page-oriented file (same shell as Manager.1800 / TranMgr.1800).
Each page has:
  +0   u32  hdr0 (random/checksum)
  +4   u32  MASTERID (this LinkMgr record's own id, NOT the Manager ledger id)
  +8   u32  hdr2 (kind/flags)
  +12  u32  hdr3 (sub-record id)
  +16  u32  hdr4 (= 0x0000000b = 11; constant marker?)
  +20  u32  hdr5 (= 0xffffffff sentinel)
  +24  u32  hdr6 (= 0)
  +28..  body: compact-slot block then TLV stream

Compact-slot block: 10-byte rows of `[u8 0x00][u8 0x00][u16 LE fid][u8 0x00][u8 type][u32 LE value]`.
TLV stream: `02 10 [u16 fid][u16 type][u16 len][value:len bytes]`.

Records of interest (per codex + this file's investigation):
  TLV 0x232e = ledger name (back-pointer by name + by master_id slot)
  TLV 0x2331 = bank_name
  TLV 0x2332 = branch
  TLV 0x2333 = account_number (or u32 in some pages)
  TLV 0x2346 = payment_mode (NEFT/RTGS)
  TLV 0x232b = UTR / instrument number
  TLV 0x232f = A/c Payee crossing
  TLV 0x2348 = print_status
  TLV 0x2358 = instrument number
  TLV 0x0002 = allocation GUID
  TLV 0x0003 = secondary GUID / key

  SLOT 0x232d = back-pointer to Manager.1800 ledger master_id (verified
                100% on 21/21 pages for ledger 2477; 1/1 for ledger 2649)

Output: out/<corpus>_linkmgr_v0.sqlite3 with three tables:

  ledger_name_links  (for filling 110 missing ledger names from extract_v5_multi)
  bank_allocations   (NEFT/RTGS bank-account details)
  bill_allocations   (bill-wise refs / generic allocations)

Usage:
    python3 scripts/extract_linkmgr.py <corpus_folder> [-o out.sqlite]
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

BANK_TLV_FIDS = frozenset({0x2331, 0x2332, 0x2333, 0x2346, 0x232b, 0x232f, 0x2358})
NAMED_TLV_FIDS = {
    0x0002: "allocation_guid",
    0x0003: "secondary_key",
    0x232b: "utr_or_instrument",
    0x232e: "ledger_name",
    0x232f: "ac_payee",
    0x2331: "bank_name",
    0x2332: "branch",
    0x2333: "account_number",
    0x2346: "payment_mode",
    0x2348: "print_status",
    0x2358: "instrument_number",
}
BACKPTR_SLOT_FID = 0x232d


def parse_tlvs(page):
    out = []
    idx = 0
    while idx < PAGE_SIZE - 8:
        p = page.find(MARKER, idx)
        if p < 0:
            break
        if p + 8 > PAGE_SIZE:
            break
        fid = struct.unpack_from("<H", page, p + 2)[0]
        tb = bytes(page[p+4:p+6])
        ln = struct.unpack_from("<H", page, p + 6)[0]
        end = p + 8 + ln
        if end > PAGE_SIZE:
            idx = p + 1
            continue
        val = bytes(page[p+8:end])
        decoded = None
        if tb == b"\x00\x0f":
            if ln >= 2 and ln % 2 == 0 and val[-2:] == b"\x00\x00":
                try:
                    text = val[:-2].decode("utf-16le")
                    if text and not _has_garbage(text):
                        decoded = text.rstrip("\r\n").strip()
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
        out.append((fid, tb.hex(), decoded))
        idx = end
    return out


def parse_slots(page):
    """Walk the compact-slot block at offsets 32..(first 02 10 marker).

    Format per slot (10 bytes): [u8 0x00][u8 0x00][u16 LE fid][u8 0x00][u8 type][u32 LE value]
    """
    out = []
    pos = 32
    while pos < PAGE_SIZE - 10:
        if page[pos] == 0 and page[pos+1] == 0 and page[pos+4] == 0:
            fid = struct.unpack_from("<H", page, pos + 2)[0]
            type_b = page[pos + 5]
            val = struct.unpack_from("<I", page, pos + 6)[0]
            if fid != 0:
                out.append((fid, type_b, val))
                pos += 10
                continue
        if page[pos:pos+2] == MARKER:
            break
        pos += 1
    return out


_ALLOWED_HIGH_CODEPOINTS = frozenset({
    0x2010, 0x2013, 0x2014, 0x2018, 0x2019, 0x201c, 0x201d,
    0x20a8, 0x20a9, 0x20b9, 0x2122, 0x00a9, 0x00ae, 0x00b0, 0x00ba,
})


def _has_garbage(s):
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


def classify(tlv_fids, slot_fids):
    has_bank_tlv = bool(tlv_fids & BANK_TLV_FIDS - {0x232b, 0x232f, 0x2346, 0x2358})
    has_payment_mode = 0x2346 in tlv_fids
    has_232d_slot = BACKPTR_SLOT_FID in slot_fids
    has_232e_tlv = 0x232e in tlv_fids
    has_alloc_guid = 0x0002 in tlv_fids

    if has_bank_tlv or (has_payment_mode and (0x2331 in tlv_fids or 0x2333 in tlv_fids)):
        return "bank_allocation"
    if has_payment_mode:
        return "bank_allocation"
    if has_232d_slot and has_232e_tlv and not has_alloc_guid:
        return "ledger_name_link"
    if has_alloc_guid and has_232e_tlv:
        return "bill_allocation"
    return "other"


def extract(corpus_folder, out_path):
    src = Path(corpus_folder) / "LinkMgr.1800"
    if not src.exists():
        raise SystemExit(f"LinkMgr.1800 not found in {corpus_folder}")
    print(f"Reading {src} ({src.stat().st_size:,} bytes)")
    data = src.read_bytes()
    n_pages = len(data) // PAGE_SIZE
    print(f"  {n_pages:,} pages")

    out = sqlite3.connect(str(out_path))
    out.execute("PRAGMA synchronous=OFF")
    out.execute("PRAGMA journal_mode=MEMORY")
    out.execute("""
        CREATE TABLE linkmgr_pages (
            page_no INTEGER PRIMARY KEY,
            master_id INTEGER,
            hdr2 INTEGER,
            hdr3 INTEGER,
            kind TEXT,
            backptr_master_id INTEGER,
            slots_json TEXT,
            tlvs_json TEXT
        )
    """)
    out.execute("CREATE INDEX idx_linkmgr_kind ON linkmgr_pages(kind)")
    out.execute("CREATE INDEX idx_linkmgr_backptr ON linkmgr_pages(backptr_master_id)")
    out.execute("""
        CREATE TABLE ledger_name_links (
            page_no INTEGER PRIMARY KEY,
            linkmgr_master_id INTEGER,
            manager_master_id INTEGER NOT NULL,
            ledger_name TEXT NOT NULL,
            allocation_guid TEXT
        )
    """)
    out.execute("CREATE INDEX idx_ledger_name_links ON ledger_name_links(manager_master_id)")
    out.execute("CREATE INDEX idx_ledger_name_links_name ON ledger_name_links(ledger_name)")
    out.execute("""
        CREATE TABLE bank_allocations (
            page_no INTEGER PRIMARY KEY,
            linkmgr_master_id INTEGER,
            manager_master_id INTEGER,
            ledger_name TEXT,
            bank_name TEXT,
            branch TEXT,
            account_number TEXT,
            payment_mode TEXT,
            ac_payee TEXT,
            utr_or_instrument TEXT,
            instrument_number TEXT,
            print_status TEXT,
            allocation_guid TEXT,
            tlvs_json TEXT
        )
    """)
    out.execute("CREATE INDEX idx_bank_alloc_manager ON bank_allocations(manager_master_id)")
    out.execute("CREATE INDEX idx_bank_alloc_bank ON bank_allocations(bank_name)")
    out.execute("""
        CREATE TABLE bill_allocations (
            page_no INTEGER PRIMARY KEY,
            linkmgr_master_id INTEGER,
            manager_master_id INTEGER,
            ledger_name TEXT,
            allocation_guid TEXT,
            secondary_key TEXT,
            tlvs_json TEXT
        )
    """)
    out.execute("CREATE INDEX idx_bill_alloc_manager ON bill_allocations(manager_master_id)")

    kind_counts = defaultdict(int)
    for pn in range(1, n_pages):
        page = data[pn*PAGE_SIZE:(pn+1)*PAGE_SIZE]
        master_id = struct.unpack_from("<I", page, 4)[0]
        hdr2 = struct.unpack_from("<I", page, 8)[0]
        hdr3 = struct.unpack_from("<I", page, 12)[0]
        slots = parse_slots(page)
        tlvs = parse_tlvs(page)
        slot_fids = {fid for fid, _, _ in slots}
        tlv_fids_set = {fid for fid, _, _ in tlvs}

        backptr = None
        for fid, _, val in slots:
            if fid == BACKPTR_SLOT_FID:
                backptr = val
                break

        named_tlvs = {}
        for fid, tb, dec in tlvs:
            if fid in NAMED_TLV_FIDS and dec is not None:
                col = NAMED_TLV_FIDS[fid]
                if col in named_tlvs:
                    if isinstance(named_tlvs[col], list):
                        named_tlvs[col].append(dec)
                    else:
                        named_tlvs[col] = [named_tlvs[col], dec]
                else:
                    named_tlvs[col] = dec

        kind = classify(tlv_fids_set, slot_fids)
        kind_counts[kind] += 1

        slots_json = json.dumps(
            [{"fid": f"0x{fid:04x}", "type": tb, "val": val} for fid, tb, val in slots]
        )
        tlvs_json = json.dumps(
            [{"fid": f"0x{fid:04x}", "type": tb, "val": dec} for fid, tb, dec in tlvs],
            ensure_ascii=False, default=str,
        )
        out.execute(
            "INSERT INTO linkmgr_pages VALUES (?,?,?,?,?,?,?,?)",
            (pn, master_id, hdr2, hdr3, kind, backptr, slots_json, tlvs_json),
        )

        if kind == "ledger_name_link" and backptr is not None and "ledger_name" in named_tlvs:
            ln = named_tlvs["ledger_name"]
            ln = ln[0] if isinstance(ln, list) else ln
            out.execute(
                "INSERT INTO ledger_name_links VALUES (?,?,?,?,?)",
                (pn, master_id, backptr, ln, named_tlvs.get("allocation_guid")),
            )
        elif kind == "bank_allocation":
            ln = named_tlvs.get("ledger_name")
            ln = ln[0] if isinstance(ln, list) else ln
            out.execute(
                "INSERT INTO bank_allocations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pn, master_id, backptr,
                    ln,
                    str(named_tlvs.get("bank_name") or ""),
                    str(named_tlvs.get("branch") or ""),
                    str(named_tlvs.get("account_number") or ""),
                    str(named_tlvs.get("payment_mode") or ""),
                    str(named_tlvs.get("ac_payee") or ""),
                    str(named_tlvs.get("utr_or_instrument") or ""),
                    str(named_tlvs.get("instrument_number") or ""),
                    str(named_tlvs.get("print_status") or ""),
                    str(named_tlvs.get("allocation_guid") or ""),
                    tlvs_json,
                ),
            )
        elif kind == "bill_allocation":
            ln = named_tlvs.get("ledger_name")
            ln = ln[0] if isinstance(ln, list) else ln
            out.execute(
                "INSERT INTO bill_allocations VALUES (?,?,?,?,?,?,?)",
                (
                    pn, master_id, backptr, ln,
                    str(named_tlvs.get("allocation_guid") or ""),
                    str(named_tlvs.get("secondary_key") or ""),
                    tlvs_json,
                ),
            )

    out.commit()
    print()
    print("Page kind distribution:")
    for k, n in sorted(kind_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<20}: {n:>6}")

    name_link_count = out.execute("SELECT COUNT(*) FROM ledger_name_links").fetchone()[0]
    bank_count = out.execute("SELECT COUNT(*) FROM bank_allocations").fetchone()[0]
    bill_count = out.execute("SELECT COUNT(*) FROM bill_allocations").fetchone()[0]
    distinct_backptr_namelinks = out.execute(
        "SELECT COUNT(DISTINCT manager_master_id) FROM ledger_name_links"
    ).fetchone()[0]
    distinct_backptr_bank = out.execute(
        "SELECT COUNT(DISTINCT manager_master_id) FROM bank_allocations WHERE manager_master_id IS NOT NULL"
    ).fetchone()[0]
    print(f"\nledger_name_links: {name_link_count} rows ({distinct_backptr_namelinks} distinct manager master_ids)")
    print(f"bank_allocations: {bank_count} rows ({distinct_backptr_bank} distinct manager master_ids)")
    print(f"bill_allocations: {bill_count} rows")
    out.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_folder", help="Path to a corpus/<company>/ folder containing LinkMgr.1800")
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args()

    corpus = Path(args.corpus_folder).resolve()
    company = corpus.name
    out_path = Path(args.output) if args.output else (
        Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out") /
        f"{company}_linkmgr_v0.sqlite3"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    print(f"=== {company} LinkMgr.1800 ===")
    extract(corpus, out_path)
    print(f"\nWrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

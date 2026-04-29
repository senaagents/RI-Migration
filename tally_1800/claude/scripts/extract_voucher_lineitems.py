"""Voucher line item extractor for TranMgr.1800 — v1.

Final result on 070525 (matched against rosetta.sqlite3):

  voucher_inventory_entries: 113,592 / 115,505 multiset match = 98.3%
  voucher_ledger_entries:     40,286 /  40,781 multiset match = 98.8%

(Multiset = composite key (voucher_master_id, ref_master_id) with occurrence
counts. Both metrics exceed the 95% target.)

Approach:

1. Scan 0x0bbb compact-slot anchors to find each voucher's master_id and tree_id.
   Apply codex's exportable-voucher filter:
     - skip voucher anchors with no same-page TLV 0x0003 (truly deleted)
     - skip voucher anchors with base_type=31 ('Stat Only Outwards' internal)
   On 070525 this yields exactly 30,545 trees (== rosetta voucher count),
   excluding 549 deleted + 19 internal of the 31,113 raw 0x0bbb anchors.

2. For each kind=2 page in an exportable voucher's tree, scan 2-byte-aligned
   offsets for slot pattern `<fid:u16le> 00 03 <u32 LE master_id>` with
   fid=0x0002. Resolve the u32 via /out/070525_master_v5.sqlite3. The kind
   tells us what it is:
     stock_items -> voucher_inventory_entries row
     ledgers     -> voucher_ledger_entries row
     other kinds (godowns/voucher_types/groups/cost_centres/units) -> ignore
       (those refs exist but are header/parent metadata, not line items)

3. Multiset count: each occurrence of a (voucher_master_id, ref_master_id)
   pair becomes a row. A voucher with the same item on three line items
   produces 3 rows.

Why kind=2 only:
- kind=1 has the voucher header but not line-item refs.
- kind=2 has the actual line-item data.
- kind=ff is index/B-tree node metadata.
- After applying the codex base_type filter, the duplicate-page issue
  (same data appearing on rec_type=5 and rec_type=66 pages) is essentially
  gone — the duplicate bursts were concentrated in non-exportable trees.

Caveats / NOT done:
- Per-line amount/qty/rate pairing not implemented (rows have NULL amount/
  quantity/rate columns). Codex finding: amounts are scaled i64 x 100000
  under type bytes that vary by fid (0x0008, 0x0009, 0x000c). Pairing
  amount-to-line requires walking sub-records (00 50 <id> 00 10 <len>) and
  pairing within each — left for v2.
- godown / cost_centre / unit assignment to specific inventory lines not done.
- bill_allocations / bank_allocations live in LinkMgr.1800 (separate task).
- Cross-company runs structurally OK on 100001/100025/300925 but no rosetta
  truth for those companies.
"""
import json
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


PAGE_SIZE = 512
HEADER_LEN = 28
ANCHOR_BYTES = b"\xbb\x0b\x00\x06"


def parse_anchor_base_type(data, page_no):
    """Read the same-page TLV 0x0003 string '<date_hex>-<base_type_hex>-<key_hex>'
    and return base_type as int, or None if the TLV is missing/malformed.

    Codex finding: base_type==31 marks internal/non-exportable vouchers
    ('Stat Only Outwards'); missing 0x0003 marks truly-deleted vouchers.
    """
    page_off = page_no * PAGE_SIZE
    chunk = data[page_off:page_off + PAGE_SIZE]
    marker = b"\x02\x10\x03\x00\x00\x0f"  # fid=0x0003 type=string
    pos = chunk.find(marker)
    if pos < 0 or pos + 8 >= PAGE_SIZE:
        return None
    ln = struct.unpack_from("<H", chunk, pos + 6)[0]
    if pos + 8 + ln > PAGE_SIZE or ln < 4:
        return None
    try:
        s = chunk[pos + 8:pos + 8 + ln - 2].decode("utf-16le")
    except UnicodeDecodeError:
        return None
    parts = s.split("-")
    if len(parts) != 3 or len(parts[1]) != 8:
        return None
    try:
        return int(parts[1], 16)
    except ValueError:
        return None


def build_voucher_tree_map(data, exclude_base_type=(31,), exclude_missing_0x0003=True):
    """Return dict master_id -> (tree_id, anchor_page_no, base_type).

    Filters: codex's exportable-voucher filter — drop vouchers where the
    same-page 0x0003 TLV is missing (truly deleted) and where base_type ∈
    exclude_base_type (internal/Stat Only).
    """
    out = {}
    excluded_missing = 0
    excluded_internal = 0
    idx = 0
    while True:
        p = data.find(ANCHOR_BYTES, idx)
        if p < 0:
            break
        master_id = struct.unpack_from("<I", data, p + 4)[0]
        page_no = p // PAGE_SIZE
        tree_id = struct.unpack_from("<I", data, page_no * PAGE_SIZE + 4)[0]
        base_type = parse_anchor_base_type(data, page_no)
        idx = p + 1

        if base_type is None and exclude_missing_0x0003:
            excluded_missing += 1
            continue
        if base_type in exclude_base_type:
            excluded_internal += 1
            continue
        out[master_id] = (tree_id, page_no, base_type)
    return out, excluded_missing, excluded_internal


def scan_tree_refs(data, master_id_to_kind, exportable_tree_ids):
    """Scan kind=2 pages, return dict tree_id -> Counter[(kind, ref_master_id)].

    Pages limited to exportable voucher trees (codex base_type filter applied
    by caller). With non-exportable trees excluded, kind=2 alone gives clean
    multiset coverage; rec_type-discriminator filtering not needed.
    """
    n_pages = len(data) // PAGE_SIZE
    all_master_ids = set(master_id_to_kind)
    out = defaultdict(Counter)
    for pn in range(1, n_pages):
        page_off = pn * PAGE_SIZE
        if struct.unpack_from("<I", data, page_off + 20)[0] != 2:
            continue
        tree_id = struct.unpack_from("<I", data, page_off + 4)[0]
        if tree_id not in exportable_tree_ids:
            continue
        chunk = data[page_off:page_off + PAGE_SIZE]
        for i in range(HEADER_LEN, PAGE_SIZE - 8):
            if chunk[i + 2:i + 4] != b"\x00\x03":
                continue
            fid = struct.unpack_from("<H", chunk, i)[0]
            if fid != 0x0002:
                continue
            v = struct.unpack_from("<I", chunk, i + 4)[0]
            if v not in all_master_ids:
                continue
            mkind = master_id_to_kind[v]
            out[tree_id][(mkind, v)] += 1
    return out


def main():
    if len(sys.argv) > 1:
        company_dir = Path(sys.argv[1])
    else:
        company_dir = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525")
    src = company_dir / "TranMgr.1800"
    rosetta = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")
    masters = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/070525_master_v5.sqlite3")
    out_path = Path(f"/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/{company_dir.name}_lineitems_v1.sqlite3")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    print(f"Reading {src} ({src.stat().st_size:,} bytes)...")
    data = src.read_bytes()
    n_pages = len(data) // PAGE_SIZE
    print(f"  {n_pages:,} pages")

    if not masters.exists():
        print(f"ERROR: master extract not found: {masters}", file=sys.stderr)
        return 1
    print(f"\nLoading master id -> kind/name lookup from {masters}...")
    mconn = sqlite3.connect(str(masters))
    master_id_to_kind = {}
    master_id_to_name = {}
    for r in mconn.execute("SELECT master_id, kind, COALESCE(rosetta_name, extracted_name) FROM master_records"):
        master_id_to_kind[r[0]] = r[1]
        master_id_to_name[r[0]] = r[2]
    mconn.close()
    print(f"  loaded {len(master_id_to_kind):,} master records")

    print("\nBuilding voucher master_id -> tree_id map (0x0bbb anchor scan)...")
    print("  applying codex filter: drop vouchers with missing 0x0003 TLV or base_type=31")
    mid_to_tree_anchor, excl_missing, excl_internal = build_voucher_tree_map(data)
    print(f"  {len(mid_to_tree_anchor):,} exportable voucher trees")
    print(f"  excluded {excl_missing} (missing 0x0003 TLV — truly deleted) + {excl_internal} (base_type=31 internal)")

    exportable_tree_ids = set(t for (t, _, _) in mid_to_tree_anchor.values())
    print("\nScanning kind=2 pages for line-item master_id refs (fid=0x0002 type=03)...")
    tree_refs = scan_tree_refs(data, master_id_to_kind, exportable_tree_ids)
    print(f"  refs found in {len(tree_refs):,} trees")

    company_guid_prefix = None
    if rosetta.exists():
        rconn = sqlite3.connect(str(rosetta))
        cguid = rconn.execute("SELECT guid FROM vouchers WHERE guid IS NOT NULL LIMIT 1").fetchone()
        if cguid:
            company_guid_prefix = cguid[0][:36]
        rconn.close()

    out = sqlite3.connect(str(out_path))
    out.execute("PRAGMA synchronous=OFF")
    out.execute("PRAGMA journal_mode=MEMORY")

    out.execute("""
        CREATE TABLE voucher_ledger_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_master_id INTEGER NOT NULL,
            voucher_guid TEXT,
            ledger_master_id INTEGER NOT NULL,
            ledger_name TEXT,
            amount REAL,
            dr_cr TEXT
        )
    """)
    out.execute("CREATE INDEX idx_led_vmid ON voucher_ledger_entries(voucher_master_id)")
    out.execute("CREATE INDEX idx_led_lid ON voucher_ledger_entries(ledger_master_id)")

    out.execute("""
        CREATE TABLE voucher_inventory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_master_id INTEGER NOT NULL,
            voucher_guid TEXT,
            stock_item_master_id INTEGER NOT NULL,
            item_name TEXT,
            quantity REAL,
            rate REAL,
            amount REAL,
            godown_master_id INTEGER,
            unit_master_id INTEGER
        )
    """)
    out.execute("CREATE INDEX idx_inv_vmid ON voucher_inventory_entries(voucher_master_id)")
    out.execute("CREATE INDEX idx_inv_sid ON voucher_inventory_entries(stock_item_master_id)")

    led_rows = []
    inv_rows = []
    for vmid, (tree_id, _anchor_page, _base_type) in mid_to_tree_anchor.items():
        guid = (company_guid_prefix + f"-{vmid:08x}") if company_guid_prefix else None
        refs = tree_refs.get(tree_id, Counter())
        for (kind, ref_mid), occurrence_count in refs.items():
            name = master_id_to_name.get(ref_mid)
            if kind == "ledgers":
                for _ in range(occurrence_count):
                    led_rows.append((vmid, guid, ref_mid, name, None, None))
            elif kind == "stock_items":
                for _ in range(occurrence_count):
                    inv_rows.append((vmid, guid, ref_mid, name, None, None, None, None, None))

    print(f"\nInserting {len(led_rows):,} ledger rows + {len(inv_rows):,} inventory rows...")
    out.executemany(
        "INSERT INTO voucher_ledger_entries "
        "(voucher_master_id, voucher_guid, ledger_master_id, ledger_name, amount, dr_cr) "
        "VALUES (?,?,?,?,?,?)",
        led_rows,
    )
    out.executemany(
        "INSERT INTO voucher_inventory_entries "
        "(voucher_master_id, voucher_guid, stock_item_master_id, item_name, "
        "quantity, rate, amount, godown_master_id, unit_master_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        inv_rows,
    )
    out.commit()

    if not rosetta.exists():
        print(f"\n(no rosetta - skipping validation)")
        out.close()
        return 0

    print("\n=== Validation against Rosetta ===")
    rconn = sqlite3.connect(str(rosetta))
    name_to_id = defaultdict(dict)
    for mid, name in master_id_to_name.items():
        if name:
            kind = master_id_to_kind[mid]
            name_to_id[kind][name.strip()] = mid

    truth_inv = Counter()
    for r in rconn.execute(
        "SELECT v.master_id, i.item_name FROM voucher_inventory_entries i "
        "JOIN vouchers v ON v.id=i.voucher_id WHERE i.item_name IS NOT NULL"
    ):
        sid = name_to_id["stock_items"].get(r[1].strip())
        if sid:
            truth_inv[(int(r[0]), sid)] += 1
    truth_led = Counter()
    for r in rconn.execute(
        "SELECT v.master_id, l.ledger_name FROM voucher_ledger_entries l "
        "JOIN vouchers v ON v.id=l.voucher_id WHERE l.ledger_name IS NOT NULL"
    ):
        lid = name_to_id["ledgers"].get(r[1].strip())
        if lid:
            truth_led[(int(r[0]), lid)] += 1

    our_inv = Counter()
    for r in out.execute("SELECT voucher_master_id, stock_item_master_id FROM voucher_inventory_entries"):
        our_inv[(r[0], r[1])] += 1
    our_led = Counter()
    for r in out.execute("SELECT voucher_master_id, ledger_master_id FROM voucher_ledger_entries"):
        our_led[(r[0], r[1])] += 1

    inv_match = sum(min(truth_inv[k], our_inv[k]) for k in truth_inv)
    inv_over = sum(max(0, our_inv[k] - truth_inv.get(k, 0)) for k in our_inv)
    inv_under = sum(max(0, truth_inv[k] - our_inv.get(k, 0)) for k in truth_inv)

    led_match = sum(min(truth_led[k], our_led[k]) for k in truth_led)
    led_over = sum(max(0, our_led[k] - truth_led.get(k, 0)) for k in our_led)
    led_under = sum(max(0, truth_led[k] - our_led.get(k, 0)) for k in truth_led)

    truth_inv_total = sum(truth_inv.values())
    truth_led_total = sum(truth_led.values())
    rconn_inv = rconn.execute("SELECT COUNT(*) FROM voucher_inventory_entries").fetchone()[0]
    rconn_led = rconn.execute("SELECT COUNT(*) FROM voucher_ledger_entries").fetchone()[0]

    # Compute: rows our v1 emits restricted to rosetta-known vouchers
    ros_inv_vmids = set(k[0] for k in truth_inv)
    ros_led_vmids = set(k[0] for k in truth_led)
    our_inv_known = sum(c for k, c in our_inv.items() if k[0] in ros_inv_vmids)
    our_led_known = sum(c for k, c in our_led.items() if k[0] in ros_led_vmids)
    our_inv_extra_vouchers = len(set(k[0] for k in our_inv) - ros_inv_vmids)
    our_led_extra_vouchers = len(set(k[0] for k in our_led) - ros_led_vmids)

    print(f"  Inventory entries:")
    print(f"    Rosetta total           : {rconn_inv:>7}")
    print(f"    Rosetta resolved truth  : {truth_inv_total:>7}  (some item_name failed master-id resolution)")
    print(f"    Our extracted rows      : {sum(our_inv.values()):>7}  (across {len(set(k[0] for k in our_inv))} voucher trees)")
    print(f"    Our rows for rosetta-known vouchers: {our_inv_known:>7}  (ratio: {our_inv_known/max(1,rconn_inv):.2f}x of rosetta total)")
    print(f"    Our rows for non-rosetta vouchers  : {sum(our_inv.values())-our_inv_known:>7}  (in {our_inv_extra_vouchers} 'extra' trees — likely vouchers without rosetta-rendered inventory)")
    print(f"    Multiset match (truth keys only)   : {inv_match:>7} ({100*inv_match/max(1,truth_inv_total):.1f}% of resolved truth)")
    print(f"    Undercount on truth keys           : {inv_under:>7}")
    print(f"  Ledger entries:")
    print(f"    Rosetta total           : {rconn_led:>7}")
    print(f"    Rosetta resolved truth  : {truth_led_total:>7}")
    print(f"    Our extracted rows      : {sum(our_led.values()):>7}  (across {len(set(k[0] for k in our_led))} voucher trees)")
    print(f"    Our rows for rosetta-known vouchers: {our_led_known:>7}  (ratio: {our_led_known/max(1,rconn_led):.2f}x of rosetta total)")
    print(f"    Our rows for non-rosetta vouchers  : {sum(our_led.values())-our_led_known:>7}  (in {our_led_extra_vouchers} 'extra' trees)")
    print(f"    Multiset match (truth keys only)   : {led_match:>7} ({100*led_match/max(1,truth_led_total):.1f}% of resolved truth)")
    print(f"    Undercount on truth keys           : {led_under:>7}")

    rconn.close()
    out.close()
    print(f"\nWrote {out_path} ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

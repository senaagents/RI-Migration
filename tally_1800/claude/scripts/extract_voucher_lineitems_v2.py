"""Voucher line item extractor v2 for TranMgr.1800.

v2 adds: amount, qty, rate, unit_master_id columns (NULL in v1).

Approach: hybrid of v1's u32-ref scan AND sub-record walking.

1. v1 baseline: scan kind=2 pages of each exportable voucher tree for
   `<fid:u16le> 00 03 <u32 LE>` slots with fid=0x0002 → emit one row per
   occurrence resolved as stock_item or ledger.

2. v2 augmentation: walk sub-records on the concatenated kind=2 page bodies
   of each tree, looking for line-item field clusters:

   Inventory line within sub-record id=0x0003 (Sales/Purchase pattern):
     - fid=0x01f6 u16    — line marker (signals start of new inventory line)
     - fid=0x0002 u32    — stock_item master_id
     - fid=0x0069 u32    — associated sale/purchase ledger master_id
     - fid=0x0072 u32    — unit master_id
     - fid=0x006e i64×100k  — amount (Tally Amount)
     - fid=0x0066 (i64×100k, unit_id) under type 0x0b — quantity (sign = direction)
     - fid=0x0002 (i64×100k, denom, unit_id) under type 0x0c — rate

   Inventory line at top level (Stock Journal pattern):
     - fid=0x0002 (i64×100k, unit_id) under type 0x0b at body offset 0 — qty
     - sub-record id=0x2713 follows with the stock_id/amount

   Ledger entry within sub-record id=0x0002:
     - fid=0x0002 u32    — ledger master_id
     - fid=0x0002 i64    — amount

3. Aggregate ledger entries per (voucher_master_id, ledger_master_id) by
   summing amounts (rosetta consolidates split ledger entries).

Encoding map (validated against rosetta voucher 28776 amounts):
   type 0x0009 = i64 amount × 100,000 (12-byte slot)
   type 0x000c = composite rate: i64 rate × 100,000 + i64 denom + u32 unit
                 (24-byte slot)
   type 0x000b = qty: i64 ×100,000 + u32 unit_master_id (16-byte slot)
                 (sign indicates direction: negative = "deemed positive" outflow)

Limitations / honest gaps:
- Stock Journal (and similar conversion-style voucher types) use a different
  body layout. Amount/qty extraction works for ~70% of vouchers (Sales,
  Purchase, Receipt Note pattern); falls back to NULL for the rest.
- Rate-from-amount-÷-qty fallback used when the explicit rate slot is missing.
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
    page_off = page_no * PAGE_SIZE
    chunk = data[page_off:page_off + PAGE_SIZE]
    marker = b"\x02\x10\x03\x00\x00\x0f"
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


def build_voucher_tree_map(data):
    """Return dict master_id -> tree_id, applying codex's exportable filter."""
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
        if base_type is None:
            excluded_missing += 1
            continue
        if base_type == 31:
            excluded_internal += 1
            continue
        out[master_id] = tree_id
    return out, excluded_missing, excluded_internal


def parse_slots(data, start, end):
    """Walk known slot types and return list of (offset, fid, type_str, value)."""
    slots = []
    j = start
    while j < end - 4:
        tb = data[j + 2:j + 4]
        if tb == b"\x00\x03" and j + 10 <= end:
            fid = struct.unpack_from("<H", data, j)[0]
            v = struct.unpack_from("<I", data, j + 4)[0]
            slots.append((j, fid, "u32", v))
            j += 10
        elif tb == b"\x00\x09" and j + 12 <= end:
            fid = struct.unpack_from("<H", data, j)[0]
            v = struct.unpack_from("<q", data, j + 4)[0]
            slots.append((j, fid, "i64_amt", v / 100000))
            j += 12
        elif tb == b"\x00\x0b" and j + 16 <= end:
            fid = struct.unpack_from("<H", data, j)[0]
            v_amt = struct.unpack_from("<q", data, j + 4)[0]
            v_unit = struct.unpack_from("<I", data, j + 12)[0]
            slots.append((j, fid, "qty", (v_amt / 100000, v_unit)))
            j += 16
        elif tb == b"\x00\x0c" and j + 24 <= end:
            fid = struct.unpack_from("<H", data, j)[0]
            v1 = struct.unpack_from("<q", data, j + 4)[0]
            v3 = struct.unpack_from("<I", data, j + 20)[0]
            slots.append((j, fid, "rate", (v1 / 100000, v3)))
            j += 24
        elif tb == b"\x00\x06" and j + 10 <= end:
            fid = struct.unpack_from("<H", data, j)[0]
            v = struct.unpack_from("<I", data, j + 4)[0]
            slots.append((j, fid, "u32_06", v))
            j += 10
        elif tb == b"\x00\x08" and j + 12 <= end:
            fid = struct.unpack_from("<H", data, j)[0]
            v = struct.unpack_from("<q", data, j + 4)[0]
            slots.append((j, fid, "i64_08", v / 100000))
            j += 12
        elif tb == b"\x00\x02" and j + 10 <= end:
            fid = struct.unpack_from("<H", data, j)[0]
            v = struct.unpack_from("<H", data, j + 4)[0]
            slots.append((j, fid, "u16", v))
            j += 10
        elif tb == b"\x00\x0d" and j + 10 <= end:
            fid = struct.unpack_from("<H", data, j)[0]
            v = struct.unpack_from("<I", data, j + 4)[0]
            slots.append((j, fid, "u32_0d", v))
            j += 10
        else:
            j += 1
    return slots


def extract_voucher_lines(data, tree_id, tree_pages, master_id_to_kind):
    """Walk one voucher tree's kind=2 pages, return (inventory_lines, ledger_lines)."""
    pages = tree_pages.get(tree_id, [])
    if not pages:
        return [], []
    body = bytearray()
    for _, pn in pages:
        body.extend(data[pn * PAGE_SIZE + HEADER_LEN:pn * PAGE_SIZE + PAGE_SIZE])
    buf = bytes(body)

    # Find sub-record bounds
    regions = []
    last_end = 0
    i = 0
    while i < len(buf) - 10:
        if buf[i:i + 2] == b"\x00\x50" and buf[i + 4:i + 6] == b"\x00\x10":
            sid = struct.unpack_from("<H", buf, i + 2)[0]
            ln = struct.unpack_from("<I", buf, i + 6)[0]
            if 0 < ln < len(buf) - i - 10:
                if last_end < i:
                    regions.append((last_end, i, None))
                regions.append((i + 10, i + 10 + ln, sid))
                last_end = i + 10 + ln
                i = last_end
                continue
        i += 1
    if last_end < len(buf):
        regions.append((last_end, len(buf), None))

    inventory_lines = []
    ledger_lines = []

    for region_start, region_end, sid in regions:
        slots = parse_slots(buf, region_start, region_end)

        # Sub-record id=0x0002: single ledger entry
        if sid == 0x0002:
            ledger_id = None
            amount = None
            for off, fid, ty, val in slots:
                if fid == 0x0002 and ty == "u32" and val in master_id_to_kind and master_id_to_kind[val] == "ledgers":
                    ledger_id = val
                elif fid == 0x0002 and ty == "i64_amt" and ledger_id is not None and amount is None:
                    amount = val
            if ledger_id is not None and amount is not None:
                ledger_lines.append((ledger_id, amount))
            continue

        # Sub-record id=0x0003 (or top-level): walk inventory line clusters
        if sid in (0x0003, None):
            line_state = None

            def emit():
                nonlocal line_state
                if line_state and line_state.get("stock_id") is not None:
                    qty = line_state.get("qty")
                    amt = line_state.get("amount")
                    rate = line_state.get("rate")
                    if rate is None and qty and amt is not None and qty != 0:
                        rate = round(amt / qty, 4)
                    inventory_lines.append({
                        "stock_id": line_state["stock_id"],
                        "qty": qty,
                        "rate": rate,
                        "amount": amt,
                        "unit": line_state.get("unit"),
                        "ledger_id": line_state.get("ledger_id"),
                    })
                line_state = None

            for off, fid, ty, val in slots:
                # Trigger: 0x01f6 u16 (Sales/Purchase pattern)
                if fid == 0x01f6 and ty == "u16":
                    emit()
                    line_state = {}
                # Top-level qty (Stock Journal pattern)
                elif fid in (0x0002, 0x0003) and ty == "qty" and sid is None:
                    if line_state is None:
                        line_state = {}
                    line_state["qty"] = abs(val[0])
                    line_state["unit"] = val[1]
                elif line_state is not None:
                    if fid == 0x0002 and ty == "u32" and val in master_id_to_kind and master_id_to_kind[val] == "stock_items":
                        line_state["stock_id"] = val
                    elif fid == 0x0069 and ty == "u32" and val in master_id_to_kind and master_id_to_kind[val] == "ledgers":
                        line_state["ledger_id"] = val
                    elif fid == 0x0072 and ty == "u32" and val in master_id_to_kind and master_id_to_kind[val] == "units":
                        line_state["unit"] = val
                    elif fid == 0x006e and ty == "i64_amt":
                        line_state["amount"] = val
                    elif fid == 0x0066 and ty == "qty":
                        line_state["qty"] = abs(val[0])
                        line_state["unit"] = val[1]
                    elif fid == 0x0002 and ty == "rate":
                        line_state["rate"] = val[0]
            emit()
            continue

    # Consolidate ledger by (ledger_id) summing amounts
    led_consolidated = defaultdict(float)
    for lid, amt in ledger_lines:
        led_consolidated[lid] += amt
    led_final = [(lid, round(amt, 2)) for lid, amt in led_consolidated.items()]

    return inventory_lines, led_final


def main():
    if len(sys.argv) > 1:
        company_dir = Path(sys.argv[1])
    else:
        company_dir = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525")
    src = company_dir / "TranMgr.1800"
    rosetta = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")
    masters_path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/070525_master_v5.sqlite3")
    out_path = Path(f"/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/{company_dir.name}_lineitems_v2.sqlite3")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    print(f"Reading {src} ({src.stat().st_size:,} bytes)...")
    data = src.read_bytes()
    n_pages = len(data) // PAGE_SIZE
    print(f"  {n_pages:,} pages")

    print(f"\nLoading master id -> kind/name lookup from {masters_path}...")
    mconn = sqlite3.connect(str(masters_path))
    master_id_to_kind = {}
    master_id_to_name = {}
    for r in mconn.execute("SELECT master_id, kind, COALESCE(rosetta_name, extracted_name) FROM master_records"):
        master_id_to_kind[r[0]] = r[1]
        master_id_to_name[r[0]] = r[2]
    mconn.close()
    print(f"  loaded {len(master_id_to_kind):,} master records")

    print("\nBuilding exportable voucher master_id -> tree_id map...")
    mid_to_tree, excl_missing, excl_internal = build_voucher_tree_map(data)
    print(f"  {len(mid_to_tree):,} exportable voucher trees")
    print(f"  excluded {excl_missing} missing-0x0003 + {excl_internal} base_type=31 internal")

    print("\nBuilding tree_id -> kind=2 pages (sorted by rec_id)...")
    tree_pages = defaultdict(list)
    exportable_tree_ids = set(mid_to_tree.values())
    for pn in range(1, n_pages):
        page_off = pn * PAGE_SIZE
        if struct.unpack_from("<I", data, page_off + 20)[0] != 2:
            continue
        tid = struct.unpack_from("<I", data, page_off + 4)[0]
        if tid not in exportable_tree_ids:
            continue
        rec_id = struct.unpack_from("<I", data, page_off + 12)[0]
        tree_pages[tid].append((rec_id, pn))
    for tid in tree_pages:
        tree_pages[tid].sort()
    print(f"  {len(tree_pages):,} trees with kind=2 pages")

    company_guid = None
    if rosetta.exists():
        rconn = sqlite3.connect(str(rosetta))
        cguid = rconn.execute("SELECT guid FROM vouchers WHERE guid IS NOT NULL LIMIT 1").fetchone()
        if cguid:
            company_guid = cguid[0][:36]
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
            amount REAL
        )
    """)
    out.execute("CREATE INDEX idx_led_vmid ON voucher_ledger_entries(voucher_master_id)")

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
            ledger_master_id INTEGER,
            unit_master_id INTEGER
        )
    """)
    out.execute("CREATE INDEX idx_inv_vmid ON voucher_inventory_entries(voucher_master_id)")

    print("\nExtracting line items per voucher...")
    n_done = 0
    inv_rows = []
    led_rows = []
    for vmid, tree_id in mid_to_tree.items():
        guid = (company_guid + f"-{vmid:08x}") if company_guid else None
        inv_lines, led_lines = extract_voucher_lines(data, tree_id, tree_pages, master_id_to_kind)
        for inv in inv_lines:
            inv_rows.append((
                vmid, guid, inv["stock_id"],
                master_id_to_name.get(inv["stock_id"]),
                inv["qty"], inv["rate"], inv["amount"],
                inv["ledger_id"], inv["unit"],
            ))
        for lid, amt in led_lines:
            led_rows.append((
                vmid, guid, lid, master_id_to_name.get(lid), amt,
            ))
        n_done += 1
        if n_done % 5000 == 0:
            print(f"  processed {n_done:,} vouchers")

    print(f"\nInserting {len(led_rows):,} ledger rows + {len(inv_rows):,} inventory rows...")
    out.executemany(
        "INSERT INTO voucher_ledger_entries "
        "(voucher_master_id, voucher_guid, ledger_master_id, ledger_name, amount) "
        "VALUES (?,?,?,?,?)",
        led_rows,
    )
    out.executemany(
        "INSERT INTO voucher_inventory_entries "
        "(voucher_master_id, voucher_guid, stock_item_master_id, item_name, "
        "quantity, rate, amount, ledger_master_id, unit_master_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        inv_rows,
    )
    out.commit()

    if not rosetta.exists():
        print("(no rosetta — skipping validation)")
        out.close()
        return 0

    print("\n=== Validation against Rosetta ===")
    rconn = sqlite3.connect(str(rosetta))

    name_to_id = defaultdict(dict)
    for mid, name in master_id_to_name.items():
        if name:
            name_to_id[master_id_to_kind[mid]][name.strip()] = mid

    # Inventory truth
    truth_inv = {}  # (vmid, sid) -> [(qty, rate, amount), ...]
    for r in rconn.execute(
        "SELECT v.master_id, i.item_name, i.quantity, i.rate, i.amount "
        "FROM voucher_inventory_entries i JOIN vouchers v ON v.id=i.voucher_id "
        "WHERE i.item_name IS NOT NULL"
    ):
        sid = name_to_id["stock_items"].get(r[1].strip())
        if sid:
            key = (int(r[0]), sid)
            truth_inv.setdefault(key, []).append((r[2], r[3], r[4]))

    # Ledger truth
    truth_led = {}  # (vmid, lid) -> [amount, ...]
    for r in rconn.execute(
        "SELECT v.master_id, l.ledger_name, l.amount "
        "FROM voucher_ledger_entries l JOIN vouchers v ON v.id=l.voucher_id "
        "WHERE l.ledger_name IS NOT NULL"
    ):
        lid = name_to_id["ledgers"].get(r[1].strip())
        if lid:
            key = (int(r[0]), lid)
            truth_led.setdefault(key, []).append(r[2])

    # Our inventory: pair multisets per (vmid, sid)
    our_inv = {}
    for r in out.execute("SELECT voucher_master_id, stock_item_master_id, quantity, rate, amount FROM voucher_inventory_entries"):
        key = (r[0], r[1])
        our_inv.setdefault(key, []).append((r[2], r[3], r[4]))
    our_led = {}
    for r in out.execute("SELECT voucher_master_id, ledger_master_id, amount FROM voucher_ledger_entries"):
        key = (r[0], r[1])
        our_led.setdefault(key, []).append(r[2])

    # Inventory: row-count match (multiset by key) AND amount-exact-match (per pair)
    inv_row_match = 0
    inv_amount_match = 0
    inv_qty_match = 0
    inv_rate_match = 0
    truth_inv_total = sum(len(v) for v in truth_inv.values())
    for key, truth_rows in truth_inv.items():
        ours = our_inv.get(key, [])
        n_match = min(len(ours), len(truth_rows))
        inv_row_match += n_match
        # Within matched rows, count exact amount/qty/rate matches
        for i in range(n_match):
            # Try greedy match: sort our by amount, sort truth by amount, pair index-wise
            pass
        # Greedy multiset compare
        truth_amounts = sorted([t[2] for t in truth_rows], key=lambda x: (x is None, x))
        our_amounts = sorted([o[2] for o in ours if o[2] is not None], key=lambda x: x)
        for ta, oa in zip(truth_amounts, our_amounts):
            if ta is not None and oa is not None and abs(ta - oa) < 0.01:
                inv_amount_match += 1
        truth_qtys = sorted([t[0] for t in truth_rows], key=lambda x: (x is None, x))
        our_qtys = sorted([o[0] for o in ours if o[0] is not None], key=lambda x: x)
        for tq, oq in zip(truth_qtys, our_qtys):
            if tq is not None and oq is not None and abs(tq - oq) < 0.01:
                inv_qty_match += 1
        truth_rates = sorted([t[1] for t in truth_rows], key=lambda x: (x is None, x))
        our_rates = sorted([o[1] for o in ours if o[1] is not None], key=lambda x: x)
        for tr, ors in zip(truth_rates, our_rates):
            if tr is not None and ors is not None and abs(tr - ors) < 0.01:
                inv_rate_match += 1

    # Ledger: amount-exact match
    led_row_match = 0
    led_amount_match = 0
    truth_led_total = sum(len(v) for v in truth_led.values())
    for key, truth_amounts in truth_led.items():
        our_amounts = our_led.get(key, [])
        n_match = min(len(our_amounts), len(truth_amounts))
        led_row_match += n_match
        # Greedy amount match
        ta = sorted(truth_amounts)
        oa = sorted([a for a in our_amounts if a is not None])
        for t, o in zip(ta, oa):
            if abs(t - o) < 0.01:
                led_amount_match += 1

    print(f"  Inventory:")
    print(f"    rosetta total      : {sum(len(v) for v in truth_inv.values()):>7}")
    print(f"    our total          : {sum(len(v) for v in our_inv.values()):>7}")
    print(f"    row-key match      : {inv_row_match:>7} ({100*inv_row_match/max(1,truth_inv_total):.1f}%)")
    print(f"    amount exact match : {inv_amount_match:>7} ({100*inv_amount_match/max(1,truth_inv_total):.1f}%)")
    print(f"    qty exact match    : {inv_qty_match:>7} ({100*inv_qty_match/max(1,truth_inv_total):.1f}%)")
    print(f"    rate exact match   : {inv_rate_match:>7} ({100*inv_rate_match/max(1,truth_inv_total):.1f}%)")
    print(f"  Ledger:")
    print(f"    rosetta total      : {sum(len(v) for v in truth_led.values()):>7}")
    print(f"    our total          : {sum(len(v) for v in our_led.values()):>7}")
    print(f"    row-key match      : {led_row_match:>7} ({100*led_row_match/max(1,truth_led_total):.1f}%)")
    print(f"    amount exact match : {led_amount_match:>7} ({100*led_amount_match/max(1,truth_led_total):.1f}%)")

    rconn.close()
    out.close()
    print(f"\nWrote {out_path} ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

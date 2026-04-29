#!/usr/bin/env python3
"""For top 5 long-tail voucher types not yet covered by codex, check which
of codex's existing byte patterns fire and at what density."""
from __future__ import annotations

import sqlite3
import struct
import shutil
from collections import defaultdict, Counter
from pathlib import Path

PAGE_SIZE = 512
ROOT = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude")
TRANMGR = ROOT / "corpus/070525/TranMgr.1800"
ROSETTA = ROOT / "corpus/rosetta.sqlite3"
MASTER_V5 = ROOT / "out/070525_master_v5.sqlite3"
DECODED_HEADERS = Path("/Users/aakashchid/workshop/sena/tallydatacrack-codex/out/070525_decoded_headers.sqlite3")
DECODED_INV = Path("/Users/aakashchid/workshop/sena/tallydatacrack-codex/out/070525_decoded_inventory.sqlite3")

LONG_TAIL = [
    "Store Movement Stk Jrl",
    "Purchase Order",
    "Purchase Order -Others",
    "Delivery Challan - Stock Alignment",
    "Job Order",
]

PATTERNS = {
    "subrec_f6_01":     b"\x00\x50\xf6\x01\x00\x10",      # F6 (codex post_rate_f6)
    "subrec_f6_44":     b"\xf6\x01\x44\x00",              # columnar amt-side
    "subrec_f6_46":     b"\xf6\x01\x46\x00",              # columnar qty-side
    "subrec_01fe":      b"\x00\x50\xfe\x01\x00\x10",      # 01fe GST class
    "subrec_0006":      b"\x00\x50\x06\x00\x00\x10",      # ledger 0x0006
    "subrec_0000":      b"\x00\x50\x00\x00\x00\x10",      # 0000 (no_sibling target)
    "subrec_01ff":      b"\x00\x50\xff\x01\x00\x10",      # inv-aggregate ledger
    "anchor_52d3":      b"\x04\xd3\x52\x00\x09",          # 0x52d3/0009 summary
    "marker_69_03":     b"\x69\x00\x00\x03",              # variant B inline
    "marker_6e_03":     b"\x6e\x00\x00\x03",              # variant B inline
    "marker_68_03":     b"\x68\x00\x00\x03",              # variant A stock-repeat
    "marker_6d_03":     b"\x6d\x00\x00\x03",              # variant A stock-repeat
    "marker_02_03":     b"\x02\x00\x00\x03",              # generic ref
    "marker_02_09":     b"\x02\x00\x00\x09",              # generic amount
    "marker_02_0b":     b"\x02\x00\x00\x0b",              # generic qty
    "marker_02_0c":     b"\x02\x00\x00\x0c",              # generic rate
    "marker_80_amt":    b"\x80\x00\x02\x00\x00\x09",      # neg-amount fallback
    "kind_0042":        b"\x01\x42\x00",                   # 300925 Material kind=0x0042 (heuristic body)
    "marker_70_09":     b"\x70\x00\x00\x09",              # CGST per row
}


def main():
    rosetta = sqlite3.connect(ROSETTA)
    company = rosetta.execute("select distinct company_name from vouchers limit 1").fetchone()[0]

    v5 = sqlite3.connect(MASTER_V5)
    n2m_stock = {n: m for m, n in v5.execute("select master_id, rosetta_name from stock_items where rosetta_name is not null")}
    n2m_ledger = {n: m for m, n in v5.execute("select master_id, rosetta_name from ledgers where rosetta_name is not null")}

    # Voucher header → group_id
    shutil.copy(DECODED_HEADERS, "/tmp/cdx_h.sqlite3")
    vmid_to_group = {int(m): int(g) for m, g in sqlite3.connect("/tmp/cdx_h.sqlite3").execute(
        "select master_id, page_group_id from voucher_header_candidates "
        "where page_group_id is not null and key_raw is not null and base_type_code != 31"
    )}

    print("loading TranMgr...")
    data = TRANMGR.read_bytes()
    pages_by_group = defaultdict(list)
    for p in range(1, len(data) // PAGE_SIZE):
        gid = struct.unpack_from("<I", data, p * PAGE_SIZE + 4)[0]
        pages_by_group[gid].append(p)

    # Codex output for these voucher types
    shutil.copy(DECODED_INV, "/tmp/cdx_i.sqlite3")
    codex = sqlite3.connect("/tmp/cdx_i.sqlite3")

    def voucher_stream(vmid):
        gid = vmid_to_group.get(vmid)
        if gid is None: return None
        out = bytearray()
        for p in sorted(pages_by_group.get(gid, [])):
            out.extend(data[p * PAGE_SIZE + 28:(p + 1) * PAGE_SIZE])
        return bytes(out)

    for vt in LONG_TAIL:
        print(f"\n========== {vt} ==========")
        # Truth: vouchers + rows
        truth_vouchers = []  # vmid
        truth_rows = []  # (vmid, sid, qty_scaled, rate_scaled, amount_scaled, ledger_mid)
        rows = list(rosetta.execute(f"""
            select cast(v.master_id as int), vie.item_name, vie.quantity, vie.rate, vie.amount, vie.ledger_name
            from vouchers v
            join voucher_inventory_entries vie on vie.voucher_id=v.id
            where v.voucher_type_name=? and v.company_name=?
        """, (vt, company)))
        seen_vmids = set()
        for vmid, item, q, r, a, ln in rows:
            if vmid is None: continue
            sid = n2m_stock.get(item)
            lmid = n2m_ledger.get(ln) if ln else None
            truth_rows.append((vmid, sid, int(round(q*100000)), int(round(r*100000)), int(round(a*100000)), lmid))
            if vmid not in seen_vmids:
                seen_vmids.add(vmid)
                truth_vouchers.append(vmid)
        print(f"vouchers={len(truth_vouchers)} truth_rows={len(truth_rows)}")
        # Sign distribution
        signs = Counter(("neg" if t[4]<0 else "pos" if t[4]>0 else "zer") for t in truth_rows)
        print(f"  sign distribution: {dict(signs)}")
        # codex coverage
        cnt_codex = codex.execute(
            "select count(*) from voucher_inventory_entries_1800 where voucher_type_name=?", (vt,)
        ).fetchone()[0]
        print(f"  codex output rows for this type: {cnt_codex}")
        codex_keys = {(v,s,q,r,a) for v,s,q,r,a in codex.execute(
            "select voucher_master_id, stock_item_master_id, quantity_scaled, rate_scaled, amount_scaled "
            "from voucher_inventory_entries_1800 where voucher_type_name=?", (vt,))}
        matched_truth = sum(1 for t in truth_rows if (t[0], t[1], t[2], t[3], t[4]) in codex_keys)
        print(f"  rosetta truth rows matched by codex: {matched_truth}/{len(truth_rows)} = {matched_truth/max(1,len(truth_rows)):.4%}")

        # Sample 5 vouchers, check pattern density
        sample = truth_vouchers[:5]
        for vmid in sample:
            stream = voucher_stream(vmid)
            if not stream:
                print(f"  vmid={vmid}: NO PAGES")
                continue
            counts = {name: stream.count(pat) for name, pat in PATTERNS.items()}
            print(f"  vmid={vmid} stream_len={len(stream)}: " + " ".join(f"{n}={c}" for n,c in counts.items() if c>0))

        # Aggregate density per voucher (avg per truth voucher)
        agg = Counter()
        for vmid in truth_vouchers[:200]:  # sample 200
            stream = voucher_stream(vmid)
            if not stream: continue
            for name, pat in PATTERNS.items():
                agg[name] += stream.count(pat)
        nv = len([v for v in truth_vouchers[:200] if voucher_stream(v) is not None])
        if nv > 0:
            print(f"  per-voucher avg pattern density (n={nv}):")
            for name, total in sorted(agg.items(), key=lambda x: -x[1]):
                if total > 0:
                    print(f"    {name}: {total/nv:.2f}/voucher (total={total})")


if __name__ == "__main__":
    main()

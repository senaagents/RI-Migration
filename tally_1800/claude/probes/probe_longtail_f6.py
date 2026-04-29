#!/usr/bin/env python3
"""For long-tail voucher types, simulate codex's post_rate_f6 rule and
measure how many truth (vmid, sid, qty, amount) tuples it covers."""
from __future__ import annotations

import sqlite3
import struct
import shutil
from collections import defaultdict
from pathlib import Path

PAGE_SIZE = 512
ROOT = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude")
TRANMGR = ROOT / "corpus/070525/TranMgr.1800"
ROSETTA = ROOT / "corpus/rosetta.sqlite3"
MASTER_V5 = ROOT / "out/070525_master_v5.sqlite3"
DECODED_HEADERS = Path("/Users/aakashchid/workshop/sena/tallydatacrack-codex/out/070525_decoded_headers.sqlite3")

LONG_TAIL = [
    "Store Movement Stk Jrl",
    "Purchase Order",
    "Purchase Order -Others",
    "Delivery Challan - Stock Alignment",
    "Job Order",
]

SUBREC_F6 = b"\x00\x50\xf6\x01\x00\x10"
STOCK_FIELD = b"\x02\x00\x00\x03"
AMT_MARK = b"\x02\x00\x00\x09"
QTY_MARK = b"\x02\x00\x00\x0b"
EXT_AMT = b"\x80\x00\x02\x00\x00\x09"
EXT_QTY = b"\x80\x00\x02\x00\x00\x0b"
RATE_MARK = b"\x02\x00\x00\x0c"


def main():
    rosetta = sqlite3.connect(ROSETTA)
    company = rosetta.execute("select distinct company_name from vouchers limit 1").fetchone()[0]

    v5 = sqlite3.connect(MASTER_V5)
    n2m_stock = {n: m for m, n in v5.execute("select master_id, rosetta_name from stock_items where rosetta_name is not null")}
    stock_ids = set(n2m_stock.values())

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

    def voucher_stream(vmid):
        gid = vmid_to_group.get(vmid)
        if gid is None: return None
        out = bytearray()
        for p in sorted(pages_by_group.get(gid, [])):
            out.extend(data[p * PAGE_SIZE + 28:(p + 1) * PAGE_SIZE])
        return bytes(out)

    def find_preceding_stock(stream, before_pos, lookback=400):
        start = max(0, before_pos - lookback)
        best_pos, best_sid = -1, None
        pos = start
        while True:
            pos = stream.find(STOCK_FIELD, pos, before_pos)
            if pos < 0: break
            if pos + 8 > len(stream): break
            sid = struct.unpack_from("<I", stream, pos + 4)[0]
            if sid in stock_ids:
                if pos > best_pos:
                    best_pos, best_sid = pos, sid
            pos += 1
        return best_sid

    def extract_f6_rows(stream):
        """Return list of (sid_guess, amt, qty)."""
        out = []
        n = len(stream)
        pos = 0
        while True:
            pos = stream.find(SUBREC_F6, pos)
            if pos < 0: break
            if pos + 10 > n: break
            body_len = struct.unpack_from("<I", stream, pos + 6)[0]
            body_start = pos + 10
            body_end = min(n, body_start + min(body_len, 200))  # bound
            sid = find_preceding_stock(stream, pos)
            # Amount inside body
            amt = qty = None
            ap = stream.find(AMT_MARK, body_start, body_end)
            if ap >= 0 and ap + 12 <= n:
                amt = struct.unpack_from("<q", stream, ap + 4)[0]
            qp = stream.find(QTY_MARK, body_start, body_end)
            if qp >= 0 and qp + 12 <= n:
                qty = struct.unpack_from("<q", stream, qp + 4)[0]
            if amt is not None and qty is not None and sid is not None:
                out.append((sid, qty, amt))
            pos = body_end
        # Negative-amount fallback (codex doesn't have this yet)
        pos = 0
        while True:
            pos = stream.find(EXT_AMT, pos)
            if pos < 0: break
            if pos + 14 > n: break
            amt = struct.unpack_from("<q", stream, pos + 6)[0]
            sid = find_preceding_stock(stream, pos, lookback=600)
            # find nearest qty (signed)
            qp_neg = stream.find(EXT_QTY, max(0, pos - 200), pos + 200)
            qty = None
            if qp_neg >= 0 and qp_neg + 14 <= n:
                qty = struct.unpack_from("<q", stream, qp_neg + 6)[0]
            else:
                qp = stream.find(QTY_MARK, max(0, pos - 200), pos + 200)
                if qp >= 0 and qp + 12 <= n:
                    qty = struct.unpack_from("<q", stream, qp + 4)[0]
            if amt is not None and qty is not None and sid is not None:
                out.append((sid, qty, amt))
            pos += 1
        return out

    for vt in LONG_TAIL:
        truth_rows = list(rosetta.execute(f"""
            select cast(v.master_id as int), vie.item_name, vie.quantity, vie.amount
            from vouchers v
            join voucher_inventory_entries vie on vie.voucher_id=v.id
            where v.voucher_type_name=? and v.company_name=?
        """, (vt, company)))
        truth_keys = set()
        truth_qa_keys = set()  # (vmid, sid, amt) ignoring qty
        for vmid, item, q, a in truth_rows:
            sid = n2m_stock.get(item)
            if sid is None or vmid is None: continue
            truth_keys.add((vmid, sid, int(round(q*100000)), int(round(a*100000))))
            truth_qa_keys.add((vmid, sid, int(round(a*100000))))

        # Extract from each voucher
        vmids = {vmid for vmid, *_ in truth_rows if vmid is not None}
        extracted_keys = set()
        extracted_qa = set()
        for vmid in vmids:
            stream = voucher_stream(vmid)
            if not stream: continue
            for sid, qty, amt in extract_f6_rows(stream):
                extracted_keys.add((vmid, sid, qty, amt))
                extracted_qa.add((vmid, sid, amt))
        match_full = len(truth_keys & extracted_keys)
        match_qa = len(truth_qa_keys & extracted_qa)
        print(f"\n{vt}:")
        print(f"  truth (vmid,sid,qty,amt): {len(truth_keys)}; matched by F6+ext: {match_full} = {match_full/max(1,len(truth_keys)):.2%}")
        print(f"  truth (vmid,sid,amt):     {len(truth_qa_keys)}; matched: {match_qa} = {match_qa/max(1,len(truth_qa_keys)):.2%}")


if __name__ == "__main__":
    main()

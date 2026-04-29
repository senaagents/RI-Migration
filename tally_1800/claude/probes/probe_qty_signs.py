#!/usr/bin/env python3
"""Validate `\\x80\\x00\\x02\\x00\\x00\\x0b <i64>` as the negative-qty marker
on Store Movement Stk Jrl truth rows."""
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

EXT_QTY = b"\x80\x00\x02\x00\x00\x0b"
BARE_QTY = b"\x00\x00\x02\x00\x00\x0b"


def main():
    rosetta = sqlite3.connect(ROSETTA)
    company = rosetta.execute("select distinct company_name from vouchers limit 1").fetchone()[0]

    v5 = sqlite3.connect(MASTER_V5)
    n2m = {n: m for m, n in v5.execute("select master_id, rosetta_name from stock_items where rosetta_name is not null")}

    # Truth Store Movement rows
    truth = []
    for vmid, item, q, a in rosetta.execute(
        "select cast(v.master_id as int), vie.item_name, vie.quantity, vie.amount "
        "from vouchers v join voucher_inventory_entries vie on vie.voucher_id=v.id "
        "where v.voucher_type_name='Store Movement Stk Jrl' and v.company_name=?", (company,)):
        sid = n2m.get(item)
        if sid is None or vmid is None: continue
        truth.append((vmid, sid, int(round(q*100000)), int(round(a*100000))))
    print(f"Store Movement truth rows: {len(truth)}")
    neg_q = [t for t in truth if t[2] < 0]
    pos_q = [t for t in truth if t[2] > 0]
    zero_q = [t for t in truth if t[2] == 0]
    print(f"  qty signs: neg={len(neg_q)} pos={len(pos_q)} zero={len(zero_q)}")

    print("loading TranMgr...")
    data = TRANMGR.read_bytes()
    shutil.copy(DECODED_HEADERS, "/tmp/h.sqlite3")
    vmid_to_group = {int(m): int(g) for m, g in sqlite3.connect("/tmp/h.sqlite3").execute(
        "select master_id, page_group_id from voucher_header_candidates "
        "where page_group_id is not null and key_raw is not null and base_type_code != 31"
    )}
    pages_by_group = defaultdict(list)
    for p in range(1, len(data) // PAGE_SIZE):
        gid = struct.unpack_from("<I", data, p * PAGE_SIZE + 4)[0]
        pages_by_group[gid].append(p)

    def vstream(vmid):
        gid = vmid_to_group.get(vmid)
        if gid is None: return None
        out = bytearray()
        for p in sorted(pages_by_group.get(gid, [])):
            out.extend(data[p * PAGE_SIZE + 28:(p + 1) * PAGE_SIZE])
        return bytes(out)

    # Test: for each negative-qty truth row, does `\x80\x00\x02\x00\x00\x0b <qty_i64>` appear in tree?
    rec_neg = 0
    for vmid, sid, q, a in neg_q:
        stream = vstream(vmid)
        if not stream: continue
        target = EXT_QTY + struct.pack("<q", q)
        if target in stream:
            rec_neg += 1
    print(f"\nNegative-qty recovery via `\\x80\\x00\\x02\\x00\\x00\\x0b + i64`: {rec_neg}/{len(neg_q)} = {rec_neg/max(1,len(neg_q)):.4%}")

    # And: for positive-qty rows, does `\x00\x00\x02\x00\x00\x0b <qty>` appear?
    rec_pos = 0
    for vmid, sid, q, a in pos_q:
        stream = vstream(vmid)
        if not stream: continue
        target = BARE_QTY + struct.pack("<q", q)
        if target in stream:
            rec_pos += 1
    print(f"Positive-qty recovery via `\\x00\\x00\\x02\\x00\\x00\\x0b + i64`: {rec_pos}/{len(pos_q)} = {rec_pos/max(1,len(pos_q)):.4%}")

    # And: does the bare 4-byte qty marker `\x02\x00\x00\x0b` find positives (existing F6 path)?
    rec_pos_bare = 0
    for vmid, sid, q, a in pos_q:
        stream = vstream(vmid)
        if not stream: continue
        target = b"\x02\x00\x00\x0b" + struct.pack("<q", q)
        if target in stream:
            rec_pos_bare += 1
    print(f"Positive-qty recovery via bare `\\x02\\x00\\x00\\x0b + i64`: {rec_pos_bare}/{len(pos_q)} = {rec_pos_bare/max(1,len(pos_q)):.4%}")

    # Cross-check: same rule on Stock Journal + Conversion Stock Journal negative-qty truth
    print("\n--- Cross-check on Stock Journal + Conversion Stock Journal ---")
    truth_sj = []
    for vmid, item, q, a in rosetta.execute(
        "select cast(v.master_id as int), vie.item_name, vie.quantity, vie.amount "
        "from vouchers v join voucher_inventory_entries vie on vie.voucher_id=v.id "
        "where v.voucher_type_name in ('Stock Journal','Conversion Stock Journal') and v.company_name=?", (company,)):
        sid = n2m.get(item)
        if sid is None or vmid is None: continue
        truth_sj.append((vmid, sid, int(round(q*100000)), int(round(a*100000))))
    sj_neg = [t for t in truth_sj if t[2] < 0]
    sj_pos = [t for t in truth_sj if t[2] > 0]
    print(f"  SJ+CSJ truth: total={len(truth_sj)} neg_qty={len(sj_neg)} pos_qty={len(sj_pos)}")
    rec_sj_neg = 0
    for vmid, sid, q, a in sj_neg:
        stream = vstream(vmid)
        if not stream: continue
        if EXT_QTY + struct.pack("<q", q) in stream:
            rec_sj_neg += 1
    print(f"  SJ+CSJ neg-qty recovery via 80-qty: {rec_sj_neg}/{len(sj_neg)} = {rec_sj_neg/max(1,len(sj_neg)):.4%}")


if __name__ == "__main__":
    main()

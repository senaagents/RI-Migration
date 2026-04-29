#!/usr/bin/env python3
"""Investigate the 91 zero-amount no-sibling misses: genuine all-zero F6 rows
or non-zero rows rounding to zero?"""
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

JOURNAL_TYPES = ("Stock Journal", "Conversion Stock Journal")

SUBREC_F6 = b"\x00\x50\xf6\x01\x00\x10"
STOCK_FIELD = b"\x02\x00\x00\x03"


def main():
    rosetta = sqlite3.connect(ROSETTA)
    company = rosetta.execute("select distinct company_name from vouchers limit 1").fetchone()[0]
    v5 = sqlite3.connect(MASTER_V5)
    n2m = {n: m for m, n in v5.execute("select master_id, rosetta_name from stock_items where rosetta_name is not null")}
    stock_ids = set(n2m.values())

    # Truth + raw rosetta values (preserve raw quantity/amount for sub-rupee detection)
    truth = []
    for vmid, item, q, r, a in rosetta.execute(
        "select cast(v.master_id as int), vie.item_name, vie.quantity, vie.rate, vie.amount "
        "from vouchers v join voucher_inventory_entries vie on vie.voucher_id=v.id "
        "where v.voucher_type_name in (?, ?) and v.company_name=?", (*JOURNAL_TYPES, company)):
        sid = n2m.get(item)
        if sid is None or vmid is None: continue
        truth.append((vmid, sid, int(round(q*100000)), int(round(r*100000)), int(round(a*100000)), q, r, a, item))

    # Codex output keys
    shutil.copy(DECODED_INV, "/tmp/cdx.sqlite3")
    codex = sqlite3.connect("/tmp/cdx.sqlite3")
    codex_keys = {(v,s,q,r,a) for v,s,q,r,a in codex.execute(
        "select voucher_master_id, stock_item_master_id, quantity_scaled, rate_scaled, amount_scaled "
        "from voucher_inventory_entries_1800 where voucher_type_name in (?,?)", JOURNAL_TYPES)}

    no_sibling = [t for t in truth if (t[0],t[1],t[2],t[3],t[4]) not in codex_keys]
    zero = [t for t in no_sibling if t[4] == 0]
    print(f"no_sibling={len(no_sibling)} zero-amount={len(zero)}")

    # Inspect raw values for zero-amount truth
    print("\nRaw rosetta values for first 20 zero-amount misses:")
    for t in zero[:20]:
        vmid, sid, q_s, r_s, a_s, q, r, a, item = t
        print(f"  vmid={vmid} sid={sid} q={q!r} r={r!r} a={a!r} item={item!r}")

    raw_zero_q = sum(1 for t in zero if t[5] == 0)
    raw_zero_r = sum(1 for t in zero if t[6] == 0)
    raw_zero_a = sum(1 for t in zero if t[7] == 0)
    raw_zero_all = sum(1 for t in zero if t[5]==0 and t[6]==0 and t[7]==0)
    print(f"\nzero-amount truth raw zero-counts: qty=0:{raw_zero_q}/{len(zero)} rate=0:{raw_zero_r} amt=0:{raw_zero_a} all_three=0:{raw_zero_all}")

    # Voucher tree
    print("loading TranMgr...")
    data = TRANMGR.read_bytes()
    vmid_to_group = {int(m): int(g) for m, g in sqlite3.connect(DECODED_HEADERS).execute(
        "select master_id, page_group_id from voucher_header_candidates "
        "where page_group_id is not null and key_raw is not null and base_type_code != 31"
    )}
    pg = defaultdict(list)
    for p in range(1, len(data)//PAGE_SIZE):
        g = struct.unpack_from("<I", data, p*PAGE_SIZE+4)[0]
        pg[g].append(p)
    def vstream(v):
        g = vmid_to_group.get(v)
        if not g: return None
        out = bytearray()
        for p in sorted(pg[g]): out.extend(data[p*PAGE_SIZE+28:(p+1)*PAGE_SIZE])
        return bytes(out)

    # For each zero-truth row, find F6 sub-records in the voucher's tree, classify by content
    # of body: AMT_ONLY / QTY_ONLY / NEITHER / BOTH
    NEITHER_F6_per_voucher = defaultdict(int)
    candidates_per_zero = []  # list of (vmid, sid, count_neither_f6, count_neither_with_preceding_sid_match)
    for t in zero:
        vmid, sid, *_ = t
        stream = vstream(vmid)
        if not stream: continue
        # Walk all F6 sub-records
        neither_f6_count = 0
        matching = 0
        pos = 0
        while True:
            pos = stream.find(SUBREC_F6, pos)
            if pos < 0: break
            if pos + 10 > len(stream): break
            blen = struct.unpack_from("<I", stream, pos+6)[0]
            body_start = pos + 10
            body_end = min(len(stream), body_start + min(blen, 200))
            body = stream[body_start:body_end]
            has_amt = b"\x02\x00\x00\x09" in body
            has_qty = b"\x02\x00\x00\x0b" in body
            if not has_amt and not has_qty:
                neither_f6_count += 1
                # Check preceding stock anchor
                start = max(0, pos - 400)
                # find last stock anchor before pos
                last_stock = None
                p2 = start
                while True:
                    p2 = stream.find(STOCK_FIELD, p2, pos)
                    if p2 < 0: break
                    if p2 + 8 > len(stream): break
                    s_id = struct.unpack_from("<I", stream, p2+4)[0]
                    if s_id in stock_ids:
                        last_stock = s_id
                    p2 += 1
                if last_stock == sid:
                    matching += 1
            pos = body_end
        NEITHER_F6_per_voucher[vmid] = neither_f6_count
        candidates_per_zero.append((vmid, sid, neither_f6_count, matching))

    # Summary
    n_zero = len(zero)
    n_with_neither_f6 = sum(1 for *_, c, _ in candidates_per_zero if c > 0)
    n_with_match = sum(1 for *_, _, m in candidates_per_zero if m > 0)
    print(f"\nZero-amount misses with at least 1 NEITHER-F6 sub-record in voucher: {n_with_neither_f6}/{n_zero}")
    print(f"Zero-amount misses with at least 1 NEITHER-F6 whose preceding stock matches truth sid: {n_with_match}/{n_zero}")

    # Distribution of NEITHER-F6 counts vs zero-truth count per voucher
    by_vmid_zero = Counter(t[0] for t in zero)
    by_vmid_neither = Counter()
    for vmid, _, c, _ in candidates_per_zero:
        by_vmid_neither[vmid] = max(by_vmid_neither[vmid], c)
    matching_per_voucher = sum(1 for v, n in by_vmid_zero.items() if by_vmid_neither[v] == n)
    print(f"\nVouchers where NEITHER-F6 count == zero-truth-row count: {matching_per_voucher}/{len(by_vmid_zero)}")

    # Sample dumps for first 3 zero misses
    print("\n--- Hex dumps around the truth voucher's NEITHER-F6 sub-records (first 3 zero misses) ---")
    shown = 0
    for t in zero:
        if shown >= 3: break
        vmid, sid, *_ = t
        stream = vstream(vmid)
        if not stream: continue
        # Find first NEITHER-F6
        pos = 0
        found = False
        while True:
            pos = stream.find(SUBREC_F6, pos)
            if pos < 0: break
            if pos+10 > len(stream): break
            blen = struct.unpack_from("<I", stream, pos+6)[0]
            body_start = pos+10
            body_end = min(len(stream), body_start+min(blen,200))
            body = stream[body_start:body_end]
            if b"\x02\x00\x00\x09" not in body and b"\x02\x00\x00\x0b" not in body:
                ctx = stream[max(0,pos-16):min(len(stream),pos+blen+10)]
                print(f"  vmid={vmid} truth_sid={sid} pos={pos} body_len={blen}")
                print(f"    body_hex: {body.hex()}")
                shown += 1
                found = True
                break
            pos = body_end
        if not found:
            print(f"  vmid={vmid} truth_sid={sid}: NO neither-F6 found in voucher")
            shown += 1


if __name__ == "__main__":
    main()

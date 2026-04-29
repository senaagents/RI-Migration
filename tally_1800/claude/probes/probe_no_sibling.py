#!/usr/bin/env python3
"""Validate the 0x80-sign-prefix rule on all no-sibling Journal misses.

A "no-sibling" miss = a rosetta truth row in (Stock Journal | Conversion Stock
Journal) for which:
  1. codex's voucher_inventory_entries_1800 does NOT contain a matching
     (vmid, stock_id, qty, rate, amount) tuple, AND
  2. flipping the amount sign (and therefore qty/rate sign) does NOT match a
     codex row either (this is the previous teammate's "sibling-projection"
     classification — sibling=other-leg-of-pair).

Rule under test: scan voucher tree pages for `80 00 02 00 00 09 <i64>` inside
sub-records whose id != 0xf6 (the codex F6 walker already covers 0xf6). Match
i64 == truth amount_scaled (negative). Validate by exact equality."""
from __future__ import annotations

import sqlite3
import struct
from collections import defaultdict
from pathlib import Path

PAGE_SIZE = 512
ROOT = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude")
TRANMGR = ROOT / "corpus/070525/TranMgr.1800"
ROSETTA = ROOT / "corpus/rosetta.sqlite3"
MASTER_V5 = ROOT / "out/070525_master_v5.sqlite3"
DECODED_HEADERS = Path("/Users/aakashchid/workshop/sena/tallydatacrack-codex/out/070525_decoded_headers.sqlite3")
DECODED_INV = Path("/Users/aakashchid/workshop/sena/tallydatacrack-codex/out/070525_decoded_inventory.sqlite3")

SCALE = 100000

JOURNAL_TYPES = ("Stock Journal", "Conversion Stock Journal")

EXTENDED_AMT = b"\x80\x00\x02\x00\x00\x09"


def main():
    rosetta = sqlite3.connect(ROSETTA)
    company = rosetta.execute("select distinct company_name from vouchers where voucher_type_name='Stock Journal' limit 1").fetchone()[0]

    v5 = sqlite3.connect(MASTER_V5)
    name_to_mid = {n: m for m, n in v5.execute("select master_id, rosetta_name from stock_items where rosetta_name is not null")}

    # Load truth rows for journal types
    truth_rows = []  # (vmid, stock_id, qty_scaled, rate_scaled, amount_scaled)
    rows = rosetta.execute(f"""
        select cast(v.master_id as integer), vie.item_name, vie.quantity, vie.rate, vie.amount
        from vouchers v
        join voucher_inventory_entries vie on vie.voucher_id=v.id
        where v.voucher_type_name in (?, ?)
          and v.company_name=?
    """, (*JOURNAL_TYPES, company))
    for vmid, item, qty, rate, amount in rows:
        sid = name_to_mid.get(item)
        if sid is None or vmid is None: continue
        truth_rows.append((vmid, sid, int(round(qty * SCALE)), int(round(rate * SCALE)), int(round(amount * SCALE))))
    print(f"truth journal rows: {len(truth_rows)}")

    # Load codex output keys
    # Copy codex DB to /tmp to avoid locks from concurrent teammates
    import shutil
    tmp_codex = Path("/tmp/codex_inv_copy.sqlite3")
    shutil.copy(DECODED_INV, tmp_codex)
    codex = sqlite3.connect(tmp_codex)
    codex_keys = set()
    for vmid, sid, q, r, a in codex.execute(
        "select voucher_master_id, stock_item_master_id, quantity_scaled, rate_scaled, amount_scaled "
        "from voucher_inventory_entries_1800 where voucher_type_name in (?, ?)", JOURNAL_TYPES):
        codex_keys.add((vmid, sid, q, r, a))
    print(f"codex journal output rows: {len(codex_keys)}")

    # Classify misses
    no_sibling = []
    paired = []
    matched = 0
    for tr in truth_rows:
        if tr in codex_keys:
            matched += 1
            continue
        vmid, sid, q, r, a = tr
        # sibling = same |qty|, |rate|, |amount| with flipped signs (paired stock journal)
        flipped = (vmid, sid, -q, r, -a)  # qty/amount flipped
        if flipped in codex_keys:
            paired.append(tr)
        else:
            no_sibling.append(tr)
    print(f"matched={matched} paired_missing={len(paired)} no_sibling={len(no_sibling)}")

    # Voucher tree mapping
    print("loading TranMgr...")
    data = TRANMGR.read_bytes()
    vmid_to_group = {int(m): int(g) for m, g in sqlite3.connect(DECODED_HEADERS).execute(
        "select master_id, page_group_id from voucher_header_candidates "
        "where page_group_id is not null and key_raw is not null and base_type_code != 31"
    )}
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

    # For each no_sibling miss, search for amount-i64 inside `80 00 02 00 00 09 <i64>` patterns
    recovered = 0
    no_pages = 0
    no_match = 0
    examples_hit = []
    examples_miss = []
    for vmid, sid, q, r, a in no_sibling:
        stream = voucher_stream(vmid)
        if not stream:
            no_pages += 1
            continue
        # Find all 80 00 02 00 00 09 <i64> hits and check if any equals a (negative)
        target = struct.pack("<q", a)
        found = False
        pos = 0
        while True:
            pos = stream.find(EXTENDED_AMT, pos)
            if pos < 0: break
            if pos + 14 > len(stream): break
            val = stream[pos + 6:pos + 14]
            if val == target:
                found = True
                if len(examples_hit) < 5:
                    examples_hit.append((vmid, sid, a, pos))
                break
            pos += 1
        if found:
            recovered += 1
        else:
            no_match += 1
            if len(examples_miss) < 5:
                examples_miss.append((vmid, sid, a))

    print(f"\n=== 0x80-sign rule on no_sibling misses ===")
    print(f"no_sibling total: {len(no_sibling)}")
    print(f"  recovered (exact amount match in 80 00 02 00 00 09 i64): {recovered}/{len(no_sibling)} = {recovered/len(no_sibling):.4%}")
    print(f"  no_pages: {no_pages}")
    print(f"  no_match: {no_match}")

    # Breakdown by sign
    pos_miss = [t for t in no_sibling if t[4] > 0]
    neg_miss = [t for t in no_sibling if t[4] < 0]
    zer_miss = [t for t in no_sibling if t[4] == 0]
    print(f"\n  no_sibling sign breakdown: neg={len(neg_miss)} pos={len(pos_miss)} zero={len(zer_miss)}")
    # Recovery on negs
    rec_neg = 0
    for vmid, sid, q, r, a in neg_miss:
        stream = voucher_stream(vmid)
        if stream and struct.pack("<q", a) in stream:
            # Confirm via 80 00 prefix
            target = EXTENDED_AMT + struct.pack("<q", a)
            if target in stream:
                rec_neg += 1
    print(f"  negative-amount recovery via 80 00 02 00 00 09 + i64 prefix: {rec_neg}/{len(neg_miss)} = {rec_neg/max(1,len(neg_miss)):.4%}")

    # False positive check: does this pattern ever fire on rows that ARE matched?
    # Sample 1000 already-matched truth rows and check if their amount appears as 80 00 ... i64 in unrelated voucher
    # Skip - the actual concern is whether this pattern can be safely applied as fallback only
    # Better: count how many no_sibling have MULTIPLE 80 00 ... i64 hits
    multi_hits = 0
    for vmid, sid, q, r, a in no_sibling[:200]:
        stream = voucher_stream(vmid)
        if not stream: continue
        hits = 0
        pos = 0
        while True:
            pos = stream.find(EXTENDED_AMT, pos)
            if pos < 0: break
            hits += 1
            pos += 1
        if hits > 1:
            multi_hits += 1
    print(f"\n  no_sibling vouchers with >1 `80 00 02 00 00 09` hits (first 200 sampled): {multi_hits}")

    print("\nHit examples:")
    for v, s, a, p in examples_hit:
        print(f"  vmid={v} sid={s} amt_scaled={a} stream_off={p}")
    print("Miss examples:")
    for v, s, a in examples_miss:
        print(f"  vmid={v} sid={s} amt_scaled={a}")


if __name__ == "__main__":
    main()

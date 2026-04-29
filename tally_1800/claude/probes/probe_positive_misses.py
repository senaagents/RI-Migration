#!/usr/bin/env python3
"""For the 286 positive+zero no-sibling misses, find the byte pattern carrying
their amount. Test candidates: bare `\\x00\\x00\\x02\\x00\\x00\\x09 <i64>`, raw
i64 anywhere, F6 sub-record extension, etc."""
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


def main():
    rosetta = sqlite3.connect(ROSETTA)
    company = rosetta.execute("select distinct company_name from vouchers limit 1").fetchone()[0]

    v5 = sqlite3.connect(MASTER_V5)
    n2m = {n: m for m, n in v5.execute("select master_id, rosetta_name from stock_items where rosetta_name is not null")}

    # Truth
    truth = []
    for vmid, item, q, r, a in rosetta.execute(
        "select cast(v.master_id as int), vie.item_name, vie.quantity, vie.rate, vie.amount "
        "from vouchers v join voucher_inventory_entries vie on vie.voucher_id=v.id "
        "where v.voucher_type_name in (?, ?) and v.company_name=?", (*JOURNAL_TYPES, company)):
        sid = n2m.get(item)
        if sid is None or vmid is None: continue
        truth.append((vmid, sid, int(round(q*100000)), int(round(r*100000)), int(round(a*100000))))

    # Codex output
    shutil.copy(DECODED_INV, "/tmp/cdx_i.sqlite3")
    codex_keys = set()
    for v, s, q, r, a in sqlite3.connect("/tmp/cdx_i.sqlite3").execute(
        "select voucher_master_id, stock_item_master_id, quantity_scaled, rate_scaled, amount_scaled "
        "from voucher_inventory_entries_1800 where voucher_type_name in (?, ?)", JOURNAL_TYPES):
        codex_keys.add((v,s,q,r,a))

    no_sibling = [t for t in truth if t not in codex_keys]
    pos_zero = [t for t in no_sibling if t[4] >= 0]
    print(f"no_sibling={len(no_sibling)} positive+zero misses={len(pos_zero)}")

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

    def vstream(vmid):
        gid = vmid_to_group.get(vmid)
        if gid is None: return None
        out = bytearray()
        for p in sorted(pages_by_group.get(gid, [])):
            out.extend(data[p * PAGE_SIZE + 28:(p + 1) * PAGE_SIZE])
        return bytes(out)

    # Test patterns - looking for the truth amount as i64 LE inside the voucher tree,
    # then study what 6-byte prefix immediately precedes it
    pos_misses = [t for t in pos_zero if t[4] > 0]
    zero_misses = [t for t in pos_zero if t[4] == 0]
    print(f"  positive amount misses: {len(pos_misses)}")
    print(f"  zero amount misses: {len(zero_misses)}")

    prefix_counts_pos = Counter()
    found_pos = 0
    no_amt_pos = 0
    for vmid, sid, q, r, a in pos_misses:
        stream = vstream(vmid)
        if not stream: continue
        target = struct.pack("<q", a)
        positions = []
        p = 0
        while True:
            p = stream.find(target, p)
            if p < 0: break
            positions.append(p)
            p += 1
        if not positions:
            no_amt_pos += 1
            continue
        found_pos += 1
        # For each position, capture the 6 bytes immediately preceding
        for pos in positions:
            if pos >= 6:
                pre = stream[pos-6:pos]
                prefix_counts_pos[pre] += 1
    print(f"\nPositive misses where target i64 found anywhere in tree: {found_pos}/{len(pos_misses)}")
    print(f"  no_amt_pos (target value not in stream at all): {no_amt_pos}")
    print("Top 15 6-byte prefixes immediately before the i64:")
    for pre, c in prefix_counts_pos.most_common(15):
        print(f"  {pre.hex()} count={c}")

    # Now narrow: only look for prefixes that ARE the marker form `?? ?? 02 00 00 09`
    # and check what the leading 2 bytes are
    print("\nLeading-2-byte distribution where bytes 2..6 are '02 00 00 09':")
    lead2 = Counter()
    for pre, c in prefix_counts_pos.items():
        if pre[2:6] == b"\x02\x00\x00\x09":
            lead2[pre[:2]] += c
    for ld, c in lead2.most_common(20):
        print(f"  lead={ld.hex()} count={c}")

    # Try the validated rule: find `<lead2> 02 00 00 09 <i64>` where i64 == target
    # for various leading byte values; measure recovery
    candidates = [b"\x00\x00", b"\x80\x00", b"\x80\x80", b"\xff\xff", b"\x40\x00", b"\x04\x00", b"\x0c\x00", b"\x44\x00"]
    print("\nRecovery by leading-2-byte hypothesis (target == i64 immediately after `<lead> 02 00 00 09`):")
    for lead in candidates:
        marker = lead + b"\x02\x00\x00\x09"
        rec = 0
        for vmid, sid, q, r, a in pos_misses:
            stream = vstream(vmid)
            if not stream: continue
            target = struct.pack("<q", a)
            full = marker + target
            if full in stream:
                rec += 1
        print(f"  lead={lead.hex()} recovery={rec}/{len(pos_misses)} = {rec/max(1,len(pos_misses)):.2%}")

    # Bigger window: any 4-byte pattern ending in `02 00 00 09` then target i64
    # (already covered by lead2 distribution above)

    # For zero amount: the i64 is 0x0000000000000000 which is too generic; try
    # a different signal — qty alone or "neither marker" F6 sub-records
    print(f"\nZero-amount misses: {len(zero_misses)}; (cannot use byte-equal-amount; needs different rule)")
    # Per previous teammate, zero rows correspond to F6 sub-records with NEITHER
    # \x02\x00\x00\x09 nor \x02\x00\x00\x0b inside the body.
    # Just count: for these vmids, do they have F6 sub-records?
    zero_vmids = {t[0] for t in zero_misses}
    print(f"  distinct zero-miss vmids: {len(zero_vmids)}")

    # Check positive misses: where IS the amount stored? Look at 6-byte windows before/after.
    print("\nFor first 5 positive misses, dump 32 bytes around each i64 occurrence:")
    n_dumps = 0
    for vmid, sid, q, r, a in pos_misses:
        if n_dumps >= 5: break
        stream = vstream(vmid)
        if not stream: continue
        target = struct.pack("<q", a)
        p = stream.find(target)
        if p < 0: continue
        n_dumps += 1
        s = max(0, p-16)
        e = min(len(stream), p+16)
        ctx = stream[s:e]
        rel = p - s
        print(f"  vmid={vmid} sid={sid} amt={a} stream_off={p}")
        print(f"    {ctx[:rel].hex()} [{ctx[rel:rel+8].hex()}] {ctx[rel+8:].hex()}")


if __name__ == "__main__":
    main()

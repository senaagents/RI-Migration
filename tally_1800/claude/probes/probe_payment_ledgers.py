#!/usr/bin/env python3
"""For mid-volume voucher types (Journal/Payment/Receipt/Contra), test codex's
existing voucher_ledger_entries byte rules: 0x0006 sub-record direct-accounting
+ 0x52d3 summary anchor."""
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

VOUCHER_TYPES = [
    "11 Journal",
    "51 Journal",
    "12 Payt Bank",
    "32 Payt Bank",
    "21 Journal",
    "62 Receipts Bank",
    "92 Payment Bank",
    "72 Payt Bank",
    "12 Payt Cash",
    "72 Contra",
]

SUBREC_0006 = b"\x00\x50\x06\x00\x00\x10"
SUBREC_01FF = b"\x00\x50\xff\x01\x00\x10"
ANCHOR_52D3 = b"\x04\xd3\x52\x00\x09"
LED_REF = b"\x02\x00\x00\x03"
AMT_MARKER = b"\x02\x00\x00\x09"


def main():
    rosetta = sqlite3.connect(ROSETTA)
    company = rosetta.execute("select distinct company_name from vouchers limit 1").fetchone()[0]
    v5 = sqlite3.connect(MASTER_V5)
    name_to_mid = {n: m for m, n in v5.execute("select master_id, rosetta_name from ledgers where rosetta_name is not null")}
    ledger_ids = set(name_to_mid.values())

    print("loading TranMgr...")
    data = TRANMGR.read_bytes()
    shutil.copy(DECODED_HEADERS, "/tmp/h.sqlite3")
    vmid_to_group = {int(m): int(g) for m, g in sqlite3.connect("/tmp/h.sqlite3").execute(
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

    def extract_0006_rows(stream):
        """Yield (ledger_mid, amt_scaled, sign_byte) for each 0x0006 sub-record."""
        out = []
        n = len(stream)
        pos = 0
        while True:
            pos = stream.find(SUBREC_0006, pos)
            if pos < 0: break
            if pos + 10 > n: break
            blen = struct.unpack_from("<I", stream, pos+6)[0]
            body_start = pos + 10
            body_end = min(n, body_start + blen)
            body = stream[body_start:body_end]
            # First 02 00 00 03 <ledger_mid>
            led_pos = body.find(LED_REF)
            if led_pos < 0 or led_pos+8 > len(body):
                pos = body_end; continue
            led = struct.unpack_from("<I", body, led_pos+4)[0]
            if led not in ledger_ids:
                pos = body_end; continue
            # First 02 00 00 09 <amt_i64> after ledger
            amt_pos = body.find(AMT_MARKER, led_pos+8)
            if amt_pos < 0 or amt_pos+12 > len(body):
                pos = body_end; continue
            amt = struct.unpack_from("<q", body, amt_pos+4)[0]
            # sign byte at body[amt_pos - 2]
            sign = body[amt_pos-2] if amt_pos >= 2 else 0
            out.append((led, amt, sign))
            pos = body_end
        return out

    def extract_0002_rows(stream):
        """Yield (ledger_mid, amt_scaled, sign_byte) by walking 0x0002 sub-records."""
        out = []
        n = len(stream)
        p = 0
        while True:
            p = stream.find(b"\x00\x50\x02\x00\x00\x10", p)
            if p < 0 or p + 10 > n: break
            body = stream[p+10:min(n, p+10+800)]
            idx = 0
            while idx < len(body):
                lp = body.find(LED_REF, idx)
                if lp < 0 or lp+8 > len(body): break
                led = struct.unpack_from("<I", body, lp+4)[0]
                if led not in ledger_ids:
                    idx = lp+1; continue
                ap = body.find(AMT_MARKER, lp+8)
                if ap < 0 or ap+12 > len(body):
                    idx = lp+1; continue
                mid_led = body.find(LED_REF, lp+8, ap)
                if mid_led >= 0:
                    idx = mid_led; continue
                amt = struct.unpack_from("<q", body, ap+4)[0]
                sign = body[ap-2] if ap>=2 else 0
                out.append((led, amt, sign))
                idx = ap+12
            p += 10
        return out

    def extract_52d3_rows(stream):
        """Yield (ledger_mid, amt_scaled, sign_byte) for each 0x52d3/0009 anchor."""
        # Pattern: <sign:u8> 04 d3 52 00 09 <ledger:u32> <amt:i64>
        out = []
        n = len(stream)
        pos = 0
        while True:
            pos = stream.find(ANCHOR_52D3, pos)
            if pos < 0: break
            # sign at pos-1
            if pos < 1 or pos + 5 + 4 + 8 > n:
                pos += 1; continue
            sign = stream[pos-1]
            if sign not in (0x00, 0x80):
                pos += 1; continue
            led = struct.unpack_from("<I", stream, pos+5)[0]
            if led not in ledger_ids:
                pos += 1; continue
            amt = struct.unpack_from("<q", stream, pos+5+4)[0]
            out.append((led, amt, sign))
            pos += 1
        return out

    print(f"\n{'voucher_type':30s} {'truth':>7s} {'r_0006':>7s} {'r_52d3':>7s} {'r_0002':>7s} {'union3':>7s} {'pct':>6s}")
    for vt in VOUCHER_TYPES:
        truth_rows = list(rosetta.execute(
            "select cast(v.master_id as int), vle.ledger_name, vle.amount, vle.is_deemed_positive "
            "from vouchers v join voucher_ledger_entries vle on vle.voucher_id=v.id "
            "where v.voucher_type_name=? and v.company_name=?", (vt, company)))
        truth_set_amt = set()  # (vmid, lmid, amt_scaled)
        truth_set_amt_sign = set()  # (vmid, lmid, amt_scaled, dp)
        for vmid, ln, a, dp in truth_rows:
            lmid = name_to_mid.get(ln)
            if lmid is None or vmid is None: continue
            truth_set_amt.add((vmid, lmid, int(round(a*100000))))
            truth_set_amt_sign.add((vmid, lmid, int(round(a*100000)), int(dp)))
        n_truth = len(truth_set_amt)

        # Extract from voucher trees
        vmids = {vmid for vmid, *_ in truth_rows if vmid is not None}
        ext_0006 = set()
        ext_52d3 = set()
        ext_0002 = set()
        for vmid in vmids:
            stream = vstream(vmid)
            if not stream: continue
            for led, amt, sign in extract_0006_rows(stream):
                ext_0006.add((vmid, led, amt))
            for led, amt, sign in extract_52d3_rows(stream):
                ext_52d3.add((vmid, led, amt))
            for led, amt, sign in extract_0002_rows(stream):
                ext_0002.add((vmid, led, amt))
        match_0006 = len(truth_set_amt & ext_0006)
        match_52d3 = len(truth_set_amt & ext_52d3)
        match_0002 = len(truth_set_amt & ext_0002)
        match_union = len(truth_set_amt & (ext_0006 | ext_52d3 | ext_0002))
        pct = match_union / max(1, n_truth) * 100
        print(f"{vt:30s} {n_truth:>7d} {match_0006:>7d} {match_52d3:>7d} {match_0002:>7d} {match_union:>7d} {pct:>5.1f}%")


if __name__ == "__main__":
    main()

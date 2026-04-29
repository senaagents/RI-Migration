#!/usr/bin/env python3
"""Cross-company structural validation of 0x0002 sub-record framing for
voucher_ledger_entries. Without rosetta truth on 100001/100025/300925,
verify that the same byte-pattern shape (header + field-coded body)
appears at consistent density across companies."""
from __future__ import annotations

import struct
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude")
COMPANIES = ["070525", "100001", "100025", "300925"]
PAGE_SIZE = 512

SR_0002 = b"\x00\x50\x02\x00\x00\x10"
SR_0006 = b"\x00\x50\x06\x00\x00\x10"
SR_F6   = b"\x00\x50\xf6\x01\x00\x10"
SR_FE   = b"\x00\x50\xfe\x01\x00\x10"
SR_FF   = b"\x00\x50\xff\x01\x00\x10"
ANC_52D3 = b"\x04\xd3\x52\x00\x09"
LED_REF  = b"\x02\x00\x00\x03"
AMT_MARK = b"\x02\x00\x00\x09"


def scan_subrec_bodies(data: bytes, header: bytes, max_window: int = 800):
    """Yield (pos, body_bytes_window) for each sub-record header occurrence."""
    n = len(data)
    pos = 0
    while True:
        pos = data.find(header, pos)
        if pos < 0: return
        if pos + 10 > n: return
        # take a fixed-size scan window because body_len u32 can be malformed
        body = data[pos+10 : min(n, pos+10+max_window)]
        # Stop at next sub-record header inside window
        stop = body.find(b"\x00\x50")
        if 0 <= stop < len(body):
            body = body[:stop]
        yield pos, body
        pos += 10


def body_has_field_pattern(body: bytes) -> tuple[bool, int, int]:
    """Returns (has_pattern, ledger_u32, amount_i64) for the FIRST
    field-coded `02 00 00 03 <u32> ... <sign> 00 02 00 00 09 <i64>` pair."""
    lp = body.find(LED_REF)
    if lp < 0 or lp+8 > len(body): return False, 0, 0
    led = struct.unpack_from("<I", body, lp+4)[0]
    ap = body.find(AMT_MARK, lp+8)
    if ap < 0 or ap+12 > len(body): return False, 0, 0
    amt = struct.unpack_from("<q", body, ap+4)[0]
    return True, led, amt


def main():
    print(f"{'company':>8s} {'tranmgr_MB':>11s} {'sr_0002':>9s} {'sr_0006':>9s} {'sr_f6':>9s} {'sr_fe':>9s} {'sr_ff':>9s} {'anc_52d3':>10s}")
    summaries = {}
    for c in COMPANIES:
        f = ROOT / f"corpus/{c}/TranMgr.1800"
        data = f.read_bytes()
        cnt_0002 = data.count(SR_0002)
        cnt_0006 = data.count(SR_0006)
        cnt_f6 = data.count(SR_F6)
        cnt_fe = data.count(SR_FE)
        cnt_ff = data.count(SR_FF)
        cnt_52d3 = data.count(ANC_52D3)
        summaries[c] = (data, cnt_0002, cnt_0006, cnt_f6, cnt_fe, cnt_ff, cnt_52d3)
        print(f"{c:>8s} {len(data)/1e6:>10.1f}M {cnt_0002:>9d} {cnt_0006:>9d} {cnt_f6:>9d} {cnt_fe:>9d} {cnt_ff:>9d} {cnt_52d3:>10d}")

    # For each company, sample N=5 0x0002 sub-records and check whether the body
    # carries the field-coded ledger+amount pattern with a sign byte
    print(f"\n{'-'*60}\n0x0002 body-content shape audit (sampling first 5 well-formed):")
    for c in COMPANIES:
        data = summaries[c][0]
        print(f"\n=== {c} ===")
        examples_with_pattern = 0
        examples_total = 0
        sign_distribution = Counter()
        examples_shown = 0
        amt_marker_positions = []
        for pos, body in scan_subrec_bodies(data, SR_0002):
            examples_total += 1
            if examples_total > 200: break  # cap for speed
            has, led, amt = body_has_field_pattern(body)
            if has:
                examples_with_pattern += 1
                # Find the sign byte at body[ap-2] for the amt
                ap = body.find(AMT_MARK)
                if ap >= 2:
                    sign_distribution[body[ap-2]] += 1
                if examples_shown < 5:
                    examples_shown += 1
                    sign = body[ap-2] if ap>=2 else 0
                    print(f"  pos={pos} body_first_60={body[:60].hex()}")
                    print(f"      led_u32={led} amt_i64={amt} sign_byte=0x{sign:02x}")
        n = min(examples_total, 200)
        print(f"  field-coded-pair-bearing 0x0002 sub-records (sampled): {examples_with_pattern}/{n} = {examples_with_pattern/max(1,n):.1%}")
        print(f"  sign-byte distribution among bearing samples: {dict(sign_distribution)}")

    # Cross-company: compare relative density of 0x0002 vs 0x0006 vs 0x52d3
    print(f"\n{'-'*60}\nRelative carrier density (per MB of TranMgr):")
    print(f"{'company':>8s} {'0x0002/MB':>10s} {'0x0006/MB':>10s} {'0x52d3/MB':>10s}")
    for c in COMPANIES:
        data, c0002, c0006, *_, c52d3 = summaries[c]
        mb = len(data) / 1e6
        print(f"{c:>8s} {c0002/mb:>10.1f} {c0006/mb:>10.1f} {c52d3/mb:>10.1f}")


if __name__ == "__main__":
    main()

"""Find a known voucher in TranMgr.1800 and dump all TLVs around it.

Pick voucher #1 from Rosetta:
  GUID:          8739d473-fcf3-43ab-8c58-9bf307c70b03-00007065
  master_id:     28773 (= 0x00007065 LE u32 = bytes 65 70 00 00)
  voucher_type:  '61 Sales'
  voucher_no:    '1'
  date:          2025-04-05
  party:         '60A2 Micheal Traders'
  narration:     'Being Sale of Damaged Stove tO Ram die Mr karthi'

Anchor on the unique narration string, walk backwards to find voucher start,
walk forwards to find voucher end (next voucher's anchor).
"""
import struct
import sys
from pathlib import Path


MARKER = b"\x02\x10"


def parse_tlv(data, p):
    if p + 8 > len(data):
        return None
    if data[p:p+2] != MARKER:
        return None
    fid = struct.unpack_from("<H", data, p + 2)[0]
    tb = bytes(data[p+4:p+6])
    ln = struct.unpack_from("<H", data, p + 6)[0]
    end = p + 8 + ln
    if end > len(data):
        return None
    return {"off": p, "fid": fid, "type": tb.hex(), "len": ln, "end": end, "value": data[p+8:end]}


def decode_value(rec):
    v = rec["value"]
    t = rec["type"]
    if t == "000f":
        if len(v) >= 2 and v[-2:] == b"\x00\x00":
            try:
                return v[:-2].decode("utf-16le")
            except UnicodeDecodeError:
                return f"<bad-utf16:{v.hex()[:40]}>"
    elif t in ("000d", "0006", "0008") and rec["len"] == 4:
        return f"u32:{struct.unpack_from('<I', v, 0)[0]}"
    elif t == "000a" and rec["len"] == 4:
        return f"i32:{struct.unpack_from('<i', v, 0)[0]}"
    elif t == "0009" and rec["len"] == 1:
        return f"u8:{v[0]}"
    elif t == "000b" and rec["len"] == 8:
        return f"f64:{struct.unpack_from('<d', v, 0)[0]:.4f}"
    return f"<{t}:{v.hex()[:40]}>"


def main():
    path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/TranMgr.1800")
    print(f"Mmap loading {path}...")
    with path.open("rb") as fh:
        data = fh.read()
    print(f"  {len(data):,} bytes")

    narration = "Being Sale of Damaged Stove tO Ram die Mr karthi"
    enc = narration.encode("utf-16le") + b"\x00\x00"
    pos = data.find(enc)
    print(f"\nNarration {narration!r}: pos=0x{pos:x}")

    if pos < 0:
        return

    # Read 8 bytes before narration to confirm TLV header
    if pos < 8:
        return
    hdr_off = pos - 8
    hdr_rec = parse_tlv(data, hdr_off)
    print(f"Narration TLV: fid=0x{hdr_rec['fid']:04x} type={hdr_rec['type']} len={hdr_rec['len']}")

    # Walk BACKWARDS from narration TLV to find voucher start.
    # We walk back ~2KB and find every preceding TLV by scanning for `02 10` markers
    # then forwards, ensuring lengths line up.
    print("\n--- Walking backwards from narration to find voucher start ---")

    # Method: scan for 02 10 markers in the 4KB window before narration; for each,
    # try to parse a TLV and see if its `end` lands inside the window. The earliest
    # consistent TLV chain is our voucher start candidate.
    window_start = max(0, hdr_off - 6000)
    candidate_starts = []
    p = window_start
    while p < hdr_off:
        if data[p:p+2] == MARKER:
            candidate_starts.append(p)
        p += 1

    # Walk forwards from each candidate and see if we land exactly on the narration TLV
    valid_chains = []
    for cs in candidate_starts:
        cur = cs
        chain = []
        while cur < hdr_off + 1000:
            r = parse_tlv(data, cur)
            if not r:
                break
            chain.append(r)
            cur = r["end"]
            if cur == hdr_off + 8 + hdr_rec["len"]:
                break
        else:
            continue
        if cur >= hdr_off and len(chain) > 5:
            valid_chains.append((cs, chain))

    if valid_chains:
        # Pick the longest chain
        best = max(valid_chains, key=lambda x: len(x[1]))
        cs, chain = best
        print(f"\nLongest valid TLV chain reaching narration starts at 0x{cs:x} ({len(chain)} TLVs):")
        for r in chain:
            v = decode_value(r)
            v_show = (v[:60] + "...") if isinstance(v, str) and len(v) > 60 else v
            print(f"  0x{r['off']:08x}  fid=0x{r['fid']:04x}  type={r['type']}  len={r['len']:>4}  val={v_show!r}")

    # Also: find the very first TLV after narration that looks like a "next voucher start"
    print("\n--- Walking forwards from narration to find voucher end / next voucher start ---")
    cur = hdr_off + 8 + hdr_rec["len"]
    forward = []
    while cur < len(data) and len(forward) < 80:
        r = parse_tlv(data, cur)
        if not r:
            cur += 1
            continue
        forward.append(r)
        cur = r["end"]
    for r in forward[:60]:
        v = decode_value(r)
        v_show = (v[:60] + "...") if isinstance(v, str) and len(v) > 60 else v
        print(f"  0x{r['off']:08x}  fid=0x{r['fid']:04x}  type={r['type']}  len={r['len']:>4}  val={v_show!r}")


if __name__ == "__main__":
    main()

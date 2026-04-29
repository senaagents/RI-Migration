"""Walk back further from voucher #1's narration to find the voucher's true start.

Strategy: scan ~10KB before the narration, parsing every TLV. Find the
spot where a TLV chain starts cleanly (gap before, no TLV before that gap).
That's the voucher record start.
"""
import struct
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
    return (p, fid, tb.hex(), ln, end, data[p+8:end])


def decode(rec):
    p, fid, t, ln, end, v = rec
    if t == "000f" and len(v) >= 2 and v[-2:] == b"\x00\x00":
        try:
            return v[:-2].decode("utf-16le", errors="replace")
        except UnicodeDecodeError:
            return None
    if t in ("000d", "0006", "0008") and ln == 4:
        return f"u32:{struct.unpack_from('<I', v, 0)[0]}"
    if t == "000a" and ln == 4:
        return f"i32:{struct.unpack_from('<i', v, 0)[0]}"
    if t == "0009" and ln == 1:
        return f"u8:{v[0]}"
    if t == "000b" and ln == 8:
        return f"f64:{struct.unpack_from('<d', v, 0)[0]:.4f}"
    return None


def main():
    path = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/TranMgr.1800")
    with path.open("rb") as fh:
        # voucher #1 narration is at 0x75d9e - load up to a generous window
        fh.seek(0)
        data = fh.read(0x100000)  # first 1 MB; voucher #1 is at 0x75d9e

    narration_off = 0x75d9e - 8  # narration TLV header start
    # Build a TLV chain backwards: scan the 16KB before, find the *latest* TLV whose
    # `end` is exactly the next TLV's start. Walk that chain back.
    window_start = max(0, narration_off - 0x4000)
    print(f"Scanning {window_start:08x}..{narration_off+200:08x}")

    # Build all TLV starts in this window (only the ones whose .end lands on another valid TLV start)
    all_records = []
    p = window_start
    while p < narration_off + 200:
        r = parse_tlv(data, p)
        if r:
            all_records.append(r)
        p += 1

    # Build forward-chain: for each record, can we get to narration_off by following .end?
    by_start = {}
    for r in all_records:
        by_start.setdefault(r[0], r)
    by_end = {}
    for r in all_records:
        by_end.setdefault(r[4], r)  # records that end at given offset

    # For narration TLV, walk back: who ends at narration_off?
    chain = []
    if narration_off in by_end:
        cursor = by_end[narration_off]
        for _ in range(200):
            chain.append(cursor)
            if cursor[0] in by_end:
                cursor = by_end[cursor[0]]
            else:
                break
    chain.reverse()

    # Add the narration TLV itself
    nr = parse_tlv(data, narration_off)
    if nr:
        chain.append(nr)

    print(f"\nReverse-chained {len(chain)} TLVs ending at narration:")
    for r in chain:
        v = decode(r)
        v_show = (v[:60] + "...") if isinstance(v, str) and len(v) > 60 else v
        print(f"  0x{r[0]:08x}  fid=0x{r[1]:04x}  type={r[2]}  len={r[3]:>4}  val={v_show!r}")

    # Look at bytes right before chain[0] to see if there's a NON-tlv record-prologue
    if chain:
        first = chain[0]
        print(f"\n32 bytes before first chained TLV (0x{first[0]:08x}):")
        s = max(0, first[0] - 32)
        chunk = data[s:first[0]]
        for i in range(0, len(chunk), 16):
            row = chunk[i:i+16]
            h = " ".join(f"{b:02x}" for b in row)
            a = "".join(chr(b) if 0x20 <= b < 0x7f else "." for b in row)
            print(f"  0x{s+i:08x}  {h:<47}  |{a}|")


if __name__ == "__main__":
    main()

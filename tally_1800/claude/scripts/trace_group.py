"""Find a known group like 'Capital Account' in Manager.1800 and dump 64 bytes before/after."""
import sys
from pathlib import Path

data = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Manager.1800").read_bytes()

for name in ["Capital Account", "Sundry Debtors", "Sundry Creditors", "Bank Accounts", "Cash-in-Hand"]:
    enc = name.encode("utf-16le") + b"\x00\x00"
    pos = 0
    found = []
    while True:
        p = data.find(enc, pos)
        if p < 0:
            break
        found.append(p)
        pos = p + 1
        if len(found) > 5:
            break
    print(f"\n=== '{name}' found at {len(found)} positions ===")
    for off in found[:3]:
        s = max(0, off - 64)
        e = min(len(data), off + len(enc) + 8)
        chunk = data[s:e]
        print(f"\n  @ 0x{off:08x}:")
        for i in range(0, len(chunk), 16):
            row = chunk[i : i + 16]
            hexrow = " ".join(f"{b:02x}" for b in row)
            ascrow = "".join(chr(b) if 0x20 <= b < 0x7f else "." for b in row)
            mark = "  <-- name starts here" if (s + i) <= off < (s + i + 16) else ""
            print(f"    0x{s+i:08x}  {hexrow:<47}  |{ascrow}|{mark}")

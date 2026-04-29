"""Dissect Company.1800 around known anchors.

Goal: figure out record / field framing. We know company name is at 0x544
in UTF-16LE. Look at the bytes immediately before and after to spot:
  - length prefix (u16/u32 LE that equals the string byte-length)
  - type tag (small int / 4-byte tag)
  - field separators / record boundaries
"""
import struct
import sys
from pathlib import Path


def hexdump(data: bytes, start: int, length: int = 64, label: str = "") -> str:
    end = min(start + length, len(data))
    chunk = data[start:end]
    lines = [f"{label}  offset=0x{start:08x} len={length}"]
    for i in range(0, len(chunk), 16):
        row = chunk[i : i + 16]
        hexpart = " ".join(f"{b:02x}" for b in row)
        ascpart = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in row)
        lines.append(f"  0x{start + i:08x}  {hexpart:<47}  |{ascpart}|")
    return "\n".join(lines)


def find_all(data: bytes, needle: bytes) -> list[int]:
    out = []
    idx = 0
    while True:
        p = data.find(needle, idx)
        if p < 0:
            break
        out.append(p)
        idx = p + 1
    return out


def context_around(data: bytes, off: int, before: int = 32, after: int = 64) -> str:
    s = max(0, off - before)
    e = min(len(data), off + after)
    return hexdump(data, s, e - s, f"context @ 0x{off:08x}")


def analyze_string_anchor(data: bytes, text: str, encoding: str = "utf-16le"):
    enc = text.encode(encoding)
    hits = find_all(data, enc)
    print(f"\n>>> STRING ANCHOR: '{text}' [{encoding}] len_bytes={len(enc)} -> {len(hits)} hits")
    for off in hits:
        print()
        print(context_around(data, off, before=16, after=len(enc) + 16))
        # Try to read length prefix
        for prefix_offset in range(2, 9):
            if off - prefix_offset < 0:
                continue
            for fmt, sz in [("<H", 2), ("<I", 4), ("<Q", 8)]:
                if off - prefix_offset < 0 or off - prefix_offset + sz > len(data):
                    continue
                if prefix_offset != sz:
                    continue
                val = struct.unpack(fmt, data[off - prefix_offset : off - prefix_offset + sz])[0]
                if val in (len(text), len(enc)):
                    print(f"    >>> length-prefix candidate: at off-{prefix_offset} fmt={fmt} value={val} ({'char-count' if val == len(text) else 'byte-count'})")


def main(path: Path):
    data = path.read_bytes()
    print(f"== {path} ({len(data)} bytes) ==")

    # Header dump
    print()
    print(hexdump(data, 0, 256, "FILE HEAD"))

    # Last 256 bytes
    print()
    print(hexdump(data, max(0, len(data) - 256), 256, "FILE TAIL"))

    # Anchors we already know exist
    for text in [
        "Avinash Industries - Chennai Unit - 2025-26",
        "ashwaonline98@gmail.com",
        "Tamil Nadu",
        "603202",
        "Chennai",
    ]:
        analyze_string_anchor(data, text, "utf-16le")


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Company.1800"
    )
    main(p)

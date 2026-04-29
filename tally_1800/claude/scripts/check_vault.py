"""Quick vault-status check.

Vaulted .1800 files (per Elcomsoft .900 research, applied as hypothesis to .1800):
  - whole-file entropy >= ~7.85 (near-uniform random)
  - no recoverable plaintext anywhere
Non-vaulted files:
  - lower entropy (binary structure visible)
  - known strings (company name) findable in UTF-16LE / ASCII
"""
import math
import sys
from collections import Counter
from pathlib import Path


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    n = len(data)
    counts = Counter(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def search_string(data: bytes, needle: str) -> list[tuple[int, str]]:
    hits = []
    encs = [
        ("ascii", needle.encode("ascii", errors="ignore")),
        ("utf-16le", needle.encode("utf-16le")),
        ("utf-16be", needle.encode("utf-16be")),
        ("utf-8", needle.encode("utf-8")),
    ]
    for label, enc in encs:
        if not enc:
            continue
        idx = 0
        while True:
            pos = data.find(enc, idx)
            if pos < 0:
                break
            hits.append((pos, label))
            idx = pos + 1
    return hits


def main(folder: Path):
    files = sorted([p for p in folder.iterdir() if p.suffix.lower() == ".1800"])
    print(f"== {folder} ==")
    print(f"{'file':<22} {'size':>12}  {'entropy':>8}  vault-likely?")
    print("-" * 70)
    for f in files:
        data = f.read_bytes()
        e = entropy(data)
        # Vaulted heuristic: very high entropy + bigger than a header file
        vault_likely = e >= 7.80 and len(data) > 4096
        print(f"{f.name:<22} {len(data):>12}  {e:>8.4f}  {'YES' if vault_likely else 'no'}")

    # Known-plaintext probes against Company.1800
    company_file = folder / "Company.1800"
    if not company_file.exists():
        return
    print()
    print(f"-- Known-plaintext probes against {company_file.name} --")
    needles = [
        "Avinash",
        "Avinash Industries",
        "Avinash Industries - Chennai Unit - 2025-26",
        "Chennai",
        "ashwaonline98@gmail.com",
        "Tamil Nadu",
        "603202",
    ]
    data = company_file.read_bytes()
    any_found = False
    for n in needles:
        hits = search_string(data, n)
        if hits:
            any_found = True
            for off, enc in hits[:3]:
                print(f"  FOUND '{n}'  encoding={enc}  offset=0x{off:08x}")
        else:
            print(f"  miss  '{n}'")
    print()
    print("Verdict:")
    if any_found:
        print("  Plaintext anchors visible -> *NOT* vaulted (only schema-obfuscation).")
    else:
        print("  No plaintext anchors -> probably vaulted OR strong obfuscation.")


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "/Users/aakashchid/workshop/sena/office/_workspace-admin/data-dumps/avinash-tally-live-2026-04-21/070525"
    )
    main(folder)

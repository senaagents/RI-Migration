"""Verify codex's claim: Manager.1800 is 512-byte pages with MASTERID at page+4.

Test:
  1. Confirm file size is multiple of 512.
  2. For each page, read u32 LE at offset +4. That's the MASTERID candidate.
  3. From Rosetta, for each master row with a guid, parse the 8-hex-digit suffix
     into a u32. Build a set.
  4. Cross-tabulate: how many MASTERIDs in pages match Rosetta GUIDs?
"""
import sqlite3
import struct
from collections import Counter
from pathlib import Path


PAGE_SIZE = 512


def main():
    mgr = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/070525/Manager.1800")
    rosetta = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")

    data = mgr.read_bytes()
    print(f"Manager.1800: {len(data):,} bytes")
    print(f"Multiple of 512?  {len(data) % PAGE_SIZE == 0}  ({len(data) // PAGE_SIZE} pages)")
    print()

    # Pull u32 at offset +4 from every page
    page_count = len(data) // PAGE_SIZE
    page_master_ids = []
    for pn in range(page_count):
        u32 = struct.unpack_from("<I", data, pn * PAGE_SIZE + 4)[0]
        page_master_ids.append(u32)
    page_id_set = set(page_master_ids)
    print(f"Distinct u32 values at page+4 across {page_count} pages: {len(page_id_set)}")
    print(f"Top 10 most common values at page+4:")
    cc = Counter(page_master_ids)
    for v, c in cc.most_common(10):
        print(f"  0x{v:08x} ({v:>10}) count={c}")

    # Pull MASTERIDs from Rosetta GUIDs
    print("\n--- Rosetta MASTERID extraction ---")
    conn = sqlite3.connect(str(rosetta))
    rosetta_master_ids = {}  # master_id_int -> (kind, name, guid)
    for kind in ["ledgers", "groups", "stock_items", "voucher_types",
                 "godowns", "cost_centres", "units"]:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({kind})")]
        if "guid" not in cols:
            continue
        for row in conn.execute(f"SELECT guid, name FROM {kind} WHERE guid IS NOT NULL"):
            guid, name = row
            # GUID format: <36-char-uuid>-<8-char-hex>
            if len(guid) >= 45 and guid[36] == "-":
                suffix = guid[37:]
                if len(suffix) == 8:
                    try:
                        master_id = int(suffix, 16)
                        rosetta_master_ids.setdefault(master_id, (kind, name, guid))
                    except ValueError:
                        pass
    print(f"Rosetta master GUIDs parsed: {len(rosetta_master_ids)}")

    # Intersect
    overlap = page_id_set & set(rosetta_master_ids.keys())
    only_pages = page_id_set - set(rosetta_master_ids.keys())
    only_rosetta = set(rosetta_master_ids.keys()) - page_id_set
    print(f"\nIntersection size: {len(overlap)}")
    print(f"  In both (page+4 ∈ Rosetta MASTERIDs): {len(overlap)}")
    print(f"  Only in pages: {len(only_pages)}")
    print(f"  Only in Rosetta: {len(only_rosetta)}")

    print(f"\nPercentage of Rosetta master records found via page MASTERID: "
          f"{100 * len(overlap) / max(1, len(rosetta_master_ids)):.1f}%")

    # Spot-check: for 5 random Rosetta masters, find their page
    print("\n--- 5 random Rosetta masters, located by MASTERID ---")
    sample = list(rosetta_master_ids.items())[:5]
    for mid, (kind, name, guid) in sample:
        try:
            page_no = page_master_ids.index(mid)
            print(f"  {kind:<14} '{name[:50]}'")
            print(f"    rosetta_guid={guid}  master_id={mid} (0x{mid:08x})")
            print(f"    found at page {page_no} (offset 0x{page_no*PAGE_SIZE:x})")
        except ValueError:
            print(f"  {kind:<14} '{name[:50]}'  master_id=0x{mid:08x}  NOT FOUND in pages")


if __name__ == "__main__":
    main()

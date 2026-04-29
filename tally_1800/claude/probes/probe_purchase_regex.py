#!/usr/bin/env python3
"""Validate name-regex selection of Purchase-class root group(s) for Variant A
inventory-ledger rule. Compare against the rosetta-truth-derived classification."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

ROOT = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude")
ROSETTA = ROOT / "corpus/rosetta.sqlite3"

# Regex candidates to test
REGEXES = {
    "purchase_accounts_only": re.compile(r"\bpurchase\s*accounts?\b", re.I),
    "purchases_or_purchase_accounts": re.compile(r"\bpurchas[ei](?:s|\s*accounts?)?\b", re.I),
    "purchase_or_direct_or_indirect_exp": re.compile(
        r"\b(purchase\s*accounts?|direct\s*expenses?|indirect\s*expenses?)\b", re.I),
    "any_purchase_substring": re.compile(r"purchas", re.I),
    "tally_reserved_purchase": re.compile(
        r"^(?:[A-Z]?\d*[-.\s]*)?(purchase\s*accounts?|direct\s*expenses?|"
        r"indirect\s*expenses?)\s*$", re.I),
}

def main():
    conn = sqlite3.connect(ROSETTA)
    company = conn.execute("select distinct company_name from vouchers limit 1").fetchone()[0]

    # Top-level groups (parent='Primary')
    top = list(conn.execute(
        "select name, is_revenue, is_deemed_positive, affects_gross_profit "
        "from groups where parent='Primary' and company_name=?", (company,)))
    print(f"Top-level groups in {company}: {len(top)}")

    # "Truth" purchase root: is_revenue=1 AND affects_gross_profit=1 AND is_deemed_positive=1
    truth = {n for n, r, dp, gp in top if r and dp and gp}
    print(f"Truth-flag purchase root(s) (is_revenue & affects_GP & dp): {truth}")

    print("\nName-regex matches per candidate:")
    for label, rx in REGEXES.items():
        hits = [n for n, *_ in top if rx.search(n)]
        print(f"  {label:42s} -> {hits}")

    # Now validate downstream: do the regex-selected ledger sets give the same
    # Variant-A row recall as the truth-flag-selected set?
    # For each candidate regex, build PC ledger mid set, run minimal Variant A check.
    import struct
    from collections import Counter, defaultdict
    PAGE_SIZE = 512
    TRANMGR = ROOT / "corpus/070525/TranMgr.1800"
    DECODED_HEADERS = Path("/Users/aakashchid/workshop/sena/tallydatacrack-codex/out/070525_decoded_headers.sqlite3")
    MASTER_V5 = ROOT / "out/070525_master_v5.sqlite3"

    v5 = sqlite3.connect(MASTER_V5)
    name_to_mid = {n: m for m, n in v5.execute("select master_id, rosetta_name from ledgers where rosetta_name is not null")}

    # Build group lineage root-of-each-group map
    parent_of = dict(conn.execute("select name, parent from groups where company_name=?", (company,)))
    def root_of(g):
        cur, seen = g, set()
        while cur in parent_of and parent_of[cur] not in (None, "Primary", cur) and cur not in seen:
            seen.add(cur); cur = parent_of[cur]
        return cur
    led_root = {}  # ledger name -> root group
    for name, group in conn.execute("select name, parent from ledgers where company_name=?", (company,)):
        led_root[name] = root_of(group)

    # Truth: vouchers + inventory-ledger
    truth_v = {}
    for vmid, ln in conn.execute(
        "select cast(v.master_id as int), vie.ledger_name from vouchers v "
        "join voucher_inventory_entries vie on vie.voucher_id=v.id "
        "where v.voucher_type_name='31-Puchase(Monthly)' and v.company_name=?", (company,)):
        if vmid is None: continue
        lmid = name_to_mid.get(ln)
        if lmid: truth_v[vmid] = lmid

    # Variant A discriminator: voucher tree has no 0x69/0x6e markers
    print("\nLoading TranMgr...")
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

    # Cache streams + Variant A subset
    streams = {}
    variant_a = []
    for vmid in truth_v:
        s = vstream(vmid)
        if not s: continue
        streams[vmid] = s
        if s.count(b"\x69\x00\x00\x03") == 0 and s.count(b"\x6e\x00\x00\x03") == 0:
            variant_a.append(vmid)
    print(f"Variant A vouchers cached: {len(variant_a)}")

    all_ledger_mids = set(name_to_mid.values())

    def evaluate_pc_set(pc_root_groups):
        """Given a set of root-group names, build PC ledger mid set and run rule with stop-list."""
        pc_mids = {name_to_mid[ln] for ln, root in led_root.items()
                   if root in pc_root_groups and ln in name_to_mid}
        # Build stop-list (>40% prevalence)
        from collections import Counter
        gcount = Counter()
        for vmid in variant_a:
            seen = set()
            stream = streams[vmid]
            for marker in (b"\x02\x00\x00\x03", b"\x03\x00\x00\x03"):
                pos = 0
                while True:
                    pos = stream.find(marker, pos)
                    if pos < 0 or pos+8 > len(stream): break
                    v = struct.unpack_from("<I", stream, pos+4)[0]
                    if v in pc_mids and v not in seen:
                        seen.add(v); gcount[v] += 1
                    pos += 1
        n = len(variant_a)
        stop = {k for k,c in gcount.items() if c/n > 0.40}
        # Score
        correct = 0
        for vmid in variant_a:
            stream = streams[vmid]
            counts = Counter()
            for marker in (b"\x02\x00\x00\x03", b"\x03\x00\x00\x03"):
                pos = 0
                while True:
                    pos = stream.find(marker, pos)
                    if pos < 0 or pos+8 > len(stream): break
                    v = struct.unpack_from("<I", stream, pos+4)[0]
                    if v in pc_mids:
                        counts[v] += 1
                    pos += 1
            filtered = Counter({k:v for k,v in counts.items() if k not in stop})
            tgt = filtered if filtered else counts
            if tgt and tgt.most_common(1)[0][0] == truth_v[vmid]:
                correct += 1
        return pc_mids, stop, correct

    print("\n=== Per-regex Variant A voucher precision ===")
    for label, rx in REGEXES.items():
        groups_matched = {n for n, *_ in top if rx.search(n)}
        pc_mids, stop, correct = evaluate_pc_set(groups_matched)
        print(f"  {label:42s} groups={len(groups_matched)} pc_mids={len(pc_mids)} stop={stop} correct={correct}/{len(variant_a)} = {correct/max(1,len(variant_a)):.4%}")

    # Truth-flag baseline
    pc_mids, stop, correct = evaluate_pc_set(truth)
    print(f"  {'TRUTH-FLAG (rev+dp+gp)':42s} groups={truth} pc_mids={len(pc_mids)} stop={stop} correct={correct}/{len(variant_a)} = {correct/max(1,len(variant_a)):.4%}")


if __name__ == "__main__":
    main()

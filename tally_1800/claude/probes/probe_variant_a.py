#!/usr/bin/env python3
"""Validate Variant A 31-Puchase(Monthly) inventory-ledger rule:
   most-frequent ledger ref (02/03 fid 0x0003) inside the voucher tree
   that classifies under Purchase-class group lineage."""
from __future__ import annotations

import sqlite3
import struct
from collections import Counter, defaultdict
from pathlib import Path

PAGE_SIZE = 512
ROOT = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude")
TRANMGR = ROOT / "corpus/070525/TranMgr.1800"
ROSETTA = ROOT / "corpus/rosetta.sqlite3"
MASTER_V5 = ROOT / "out/070525_master_v5.sqlite3"
DECODED_HEADERS = Path("/Users/aakashchid/workshop/sena/tallydatacrack-codex/out/070525_decoded_headers.sqlite3")

PURCHASE_ROOTS = {
    "E30-Purchase Accounts",
    "L-Direct Expenses",
    "L-Indirect Expenses",
    "L-Sales Accounts",  # observed but unlikely; included for completeness
}


def load_company_name(conn):
    row = conn.execute("select distinct company_name from vouchers where voucher_type_name='31-Puchase(Monthly)' limit 1").fetchone()
    return row[0]


def load_group_lineage(rosetta: sqlite3.Connection, company: str) -> dict[str, str]:
    """For each group name, return its top-level ancestor (root)."""
    rows = list(rosetta.execute("select name, parent from groups where company_name=?", (company,)))
    parent = {n: p for n, p in rows}
    root_of = {}
    for n in parent:
        cur = n
        seen = set()
        while cur in parent and parent[cur] and parent[cur] != "Primary" and cur not in seen:
            seen.add(cur)
            nxt = parent[cur]
            if nxt is None or nxt == "Primary" or nxt == cur:
                break
            cur = nxt
        root_of[n] = cur
    return root_of


def classify_ledgers(rosetta, company, root_of):
    """Returns master_id -> root_group, by joining ledger.parent in rosetta and v5 master_id."""
    v5 = sqlite3.connect(MASTER_V5)
    name_to_mid = {n: m for m, n in v5.execute("select master_id, rosetta_name from ledgers where rosetta_name is not null")}
    # ledger -> group_name
    led_parent = dict(rosetta.execute("select name, parent from ledgers where company_name=?", (company,)))
    out = {}
    for name, group in led_parent.items():
        mid = name_to_mid.get(name)
        if mid is None:
            continue
        # Find root: walk up groups
        cur = group
        seen = set()
        while cur and cur != "Primary" and cur not in seen:
            seen.add(cur)
            r = root_of.get(cur)
            if r is None or r == cur:
                break
            cur = r
        out[mid] = cur
    return out, name_to_mid


def load_pages_by_mid(data: bytes, vmid_to_group: dict[int, int]) -> dict[int, list[int]]:
    """Returns voucher master_id -> list of page numbers (page+4 == group_id)."""
    pages_by_group = defaultdict(list)
    for p in range(1, len(data) // PAGE_SIZE):
        gid = struct.unpack_from("<I", data, p * PAGE_SIZE + 4)[0]
        pages_by_group[gid].append(p)
    out = {}
    for vmid, gid in vmid_to_group.items():
        if gid in pages_by_group:
            out[vmid] = pages_by_group[gid]
    return out


def load_vmid_to_group():
    conn = sqlite3.connect(DECODED_HEADERS)
    return {int(m): int(g) for m, g in conn.execute(
        "select master_id, page_group_id from voucher_header_candidates "
        "where page_group_id is not null and key_raw is not null and base_type_code != 31"
    )}


def voucher_streams(data: bytes, pages: list[int]) -> bytes:
    """Concatenate kind=2 page bodies, stripping 28-byte page headers, ordered."""
    out = bytearray()
    for p in sorted(pages):
        kind = data[p * PAGE_SIZE + 12]  # heuristic
        # Just take all pages - the markers are 4-byte patterns rare enough
        body = data[p * PAGE_SIZE + 28 : (p + 1) * PAGE_SIZE]
        out.extend(body)
    return bytes(out)


def count_ledger_refs(stream: bytes, ledger_ids: set[int]) -> Counter:
    """Count u32 ledger IDs after `02 00 00 03` and `03 00 00 03` markers."""
    c = Counter()
    for marker in (b"\x02\x00\x00\x03", b"\x03\x00\x00\x03"):
        pos = 0
        n = len(stream)
        m = len(marker)
        while True:
            pos = stream.find(marker, pos)
            if pos < 0 or pos + m + 4 > n:
                break
            v = struct.unpack_from("<I", stream, pos + m)[0]
            if v in ledger_ids:
                c[v] += 1
            pos += 1
    return c


def count_variant_b_markers(stream: bytes) -> int:
    return stream.count(b"\x69\x00\x00\x03") + stream.count(b"\x6e\x00\x00\x03")


def main():
    print("loading rosetta...")
    rosetta = sqlite3.connect(ROSETTA)
    company = load_company_name(rosetta)
    root_of = load_group_lineage(rosetta, company)
    led_root, name_to_mid = classify_ledgers(rosetta, company, root_of)

    purchase_class_mids = {mid for mid, root in led_root.items() if root in PURCHASE_ROOTS}
    print(f"purchase-class ledger master_ids: {len(purchase_class_mids)}")
    all_ledger_mids = set(name_to_mid.values())

    # Truth: voucher master_id -> truth ledger master_id
    truth = {}
    rows = rosetta.execute("""
        select cast(v.master_id as integer), vie.ledger_name
        from vouchers v
        join voucher_inventory_entries vie on vie.voucher_id=v.id
        where v.voucher_type_name='31-Puchase(Monthly)'
          and v.company_name=?
    """, (company,))
    for vmid, lname in rows:
        if vmid is None: continue
        lmid = name_to_mid.get(lname)
        if lmid is None: continue
        truth.setdefault(vmid, set()).add(lmid)

    print(f"vouchers with truth: {len(truth)}")
    # All should have exactly 1 ledger
    multi = sum(1 for s in truth.values() if len(s) > 1)
    print(f"  vouchers with >1 truth ledger: {multi}")

    print("loading TranMgr.1800 + voucher header candidates...")
    data = TRANMGR.read_bytes()
    vmid_to_group = load_vmid_to_group()
    print(f"vmid_to_group entries: {len(vmid_to_group)}")
    pages_by_mid = load_pages_by_mid(data, vmid_to_group)

    variant_a = []  # vmids with NO 0x69/0x6e markers
    variant_b = []  # vmids with 0x69/0x6e markers
    no_pages = 0
    for vmid in truth:
        pages = pages_by_mid.get(vmid)
        if not pages:
            no_pages += 1
            continue
        stream = voucher_streams(data, pages)
        if count_variant_b_markers(stream) > 0:
            variant_b.append(vmid)
        else:
            variant_a.append(vmid)
    print(f"variant_a={len(variant_a)} variant_b={len(variant_b)} no_pages={no_pages}")

    # First pass: gather global frequency of each PC ledger across Variant A
    global_pc_count = Counter()
    cached_streams = {}
    for vmid in variant_a:
        stream = voucher_streams(data, pages_by_mid[vmid])
        cached_streams[vmid] = stream
        c_all = count_ledger_refs(stream, all_ledger_mids)
        for k in c_all:
            if k in purchase_class_mids:
                global_pc_count[k] += 1
    print(f"\nGlobal PC ledger frequency across Variant A:")
    for k, v in global_pc_count.most_common(10):
        print(f"  mid={k} freq={v}/{len(variant_a)} ({v/len(variant_a):.1%})")
    # Stop-list: ledgers appearing in >40% of all vouchers
    stop_list = {k for k, v in global_pc_count.items() if v / len(variant_a) > 0.40}
    print(f"  stop_list (>40% prevalence): {stop_list}")

    correct_pc = 0
    correct_pc_filtered = 0
    correct_any = 0  # most-frequent of all ledger refs
    no_candidate_pc = 0
    no_candidate_any = 0
    wrong_pc_examples = []
    wrong_any_examples = []
    correct_pc_examples = []
    for vmid in variant_a:
        truth_mid = next(iter(truth[vmid]))
        stream = cached_streams[vmid]
        c_all = count_ledger_refs(stream, all_ledger_mids)
        c_pc = Counter({k: v for k, v in c_all.items() if k in purchase_class_mids})
        c_pc_filtered = Counter({k: v for k, v in c_pc.items() if k not in stop_list})

        if c_pc_filtered:
            pred_pc_f = c_pc_filtered.most_common(1)[0][0]
            if pred_pc_f == truth_mid:
                correct_pc_filtered += 1
        elif c_pc:
            # all candidates were in stop list - fall back to original
            pred_pc_f = c_pc.most_common(1)[0][0]
            if pred_pc_f == truth_mid:
                correct_pc_filtered += 1

        if c_pc:
            pred_pc = c_pc.most_common(1)[0][0]
            if pred_pc == truth_mid:
                correct_pc += 1
                if len(correct_pc_examples) < 3:
                    correct_pc_examples.append((vmid, pred_pc, c_pc.most_common(3)))
            else:
                if len(wrong_pc_examples) < 5:
                    wrong_pc_examples.append((vmid, truth_mid, pred_pc, c_pc.most_common(3)))
        else:
            no_candidate_pc += 1

        if c_all:
            pred_any = c_all.most_common(1)[0][0]
            if pred_any == truth_mid:
                correct_any += 1
            else:
                if len(wrong_any_examples) < 3:
                    wrong_any_examples.append((vmid, truth_mid, pred_any, c_all.most_common(3)))
        else:
            no_candidate_any += 1

    n = len(variant_a)
    print(f"\n=== VARIANT A ({n} vouchers) ===")
    print(f"Rule: most-frequent (02|03)/0003 ledger ref filtered to Purchase-class")
    print(f"  correct: {correct_pc}/{n} = {correct_pc/n:.4%}")
    print(f"  no_candidate (no purchase-class ref found): {no_candidate_pc}")
    print(f"  wrong: {n - correct_pc - no_candidate_pc}")
    print()
    print(f"Rule: most-frequent PC ledger MINUS stop-list of >40% prevalent defaults")
    print(f"  correct: {correct_pc_filtered}/{n} = {correct_pc_filtered/n:.4%}")
    print()
    print(f"Rule: most-frequent (02|03)/0003 ledger ref UNFILTERED (any ledger)")
    print(f"  correct: {correct_any}/{n} = {correct_any/n:.4%}")
    print(f"  no_candidate: {no_candidate_any}")
    print()
    print("Correct-PC examples:")
    for v, pred, top in correct_pc_examples:
        print(f"  vmid={v} pred={pred} top3={top}")
    print("Wrong-PC examples:")
    for v, tr, pr, top in wrong_pc_examples:
        print(f"  vmid={v} truth={tr} pred={pr} top3={top}")
    print("Wrong-ANY examples:")
    for v, tr, pr, top in wrong_any_examples:
        print(f"  vmid={v} truth={tr} pred={pr} top3={top}")

    # Per-row precision: assume rule fires once per voucher, applied to all rows
    # Since each Variant A voucher has all rows with same ledger, voucher accuracy = row accuracy
    truth_rows_a = sum(len([1 for _ in rosetta.execute(
        "select 1 from voucher_inventory_entries vie join vouchers v on vie.voucher_id=v.id "
        "where v.voucher_type_name='31-Puchase(Monthly)' and cast(v.master_id as integer)=?", (vmid,)
    )]) for vmid in variant_a[:0])  # skip detailed count

    # Compute total rows for variant A
    if variant_a:
        placeholders = ",".join("?" * len(variant_a))
        total_a_rows = rosetta.execute(
            f"select count(*) from voucher_inventory_entries vie join vouchers v on vie.voucher_id=v.id "
            f"where v.voucher_type_name='31-Puchase(Monthly)' and cast(v.master_id as integer) in ({placeholders})",
            variant_a
        ).fetchone()[0]
        print(f"\nTotal Variant A inventory rows: {total_a_rows}")
        print(f"  Recovered with PC rule: {correct_pc} vouchers covered")
        # Need to compute actual rows for correct vouchers
        # For now report voucher-level precision; row count below
        correct_vmids = []
        for vmid in variant_a:
            truth_mid = next(iter(truth[vmid]))
            stream = voucher_streams(data, pages_by_mid[vmid])
            c_all = count_ledger_refs(stream, all_ledger_mids)
            c_pc = Counter({k: v for k, v in c_all.items() if k in purchase_class_mids})
            if c_pc and c_pc.most_common(1)[0][0] == truth_mid:
                correct_vmids.append(vmid)
        if correct_vmids:
            ph = ",".join("?" * len(correct_vmids))
            correct_rows = rosetta.execute(
                f"select count(*) from voucher_inventory_entries vie join vouchers v on vie.voucher_id=v.id "
                f"where v.voucher_type_name='31-Puchase(Monthly)' and cast(v.master_id as integer) in ({ph})",
                correct_vmids
            ).fetchone()[0]
            print(f"  Correct row recall (PC rule): {correct_rows}/{total_a_rows} = {correct_rows/total_a_rows:.4%}")

        # Stop-list filtered correct vmids
        correct_vmids_f = []
        for vmid in variant_a:
            truth_mid = next(iter(truth[vmid]))
            stream = cached_streams[vmid]
            c_all = count_ledger_refs(stream, all_ledger_mids)
            c_pc = Counter({k: v for k, v in c_all.items() if k in purchase_class_mids})
            c_pc_filtered = Counter({k: v for k, v in c_pc.items() if k not in stop_list})
            target = c_pc_filtered if c_pc_filtered else c_pc
            if target and target.most_common(1)[0][0] == truth_mid:
                correct_vmids_f.append(vmid)
        if correct_vmids_f:
            ph = ",".join("?" * len(correct_vmids_f))
            correct_rows_f = rosetta.execute(
                f"select count(*) from voucher_inventory_entries vie join vouchers v on vie.voucher_id=v.id "
                f"where v.voucher_type_name='31-Puchase(Monthly)' and cast(v.master_id as integer) in ({ph})",
                correct_vmids_f
            ).fetchone()[0]
            print(f"  Correct row recall (PC+stop-list rule): {correct_rows_f}/{total_a_rows} = {correct_rows_f/total_a_rows:.4%}")


if __name__ == "__main__":
    main()

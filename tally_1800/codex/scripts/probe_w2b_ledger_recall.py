#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


PAGE_SIZE = 512
PAGE_HEADER_SIZE = 28
SUBREC_0006 = b"\x00\x50\x06\x00\x00\x10"
LEDGER_MARKER = b"\x02\x00\x00\x03"
AMOUNT_MARKER = b"\x02\x00\x00\x09"
AMOUNT_SCALE = 100000

ROOT = Path("/Users/aakashchid/workshop/sena")
DEFAULT_DECODED = ROOT / "tallydatacrack-codex/out/070525_decoded_laneA_sales_logical.sqlite3"
DEFAULT_ROSETTA = ROOT / "tallydatacrack-claude/corpus/rosetta.sqlite3"
DEFAULT_MASTER = ROOT / "tallydatacrack-claude/out/070525_master_v5.sqlite3"


@dataclass(frozen=True)
class TruthRow:
    voucher_master_id: int
    voucher_type_name: str
    ledger_master_id: int
    ledger_name: str
    amount_scaled: int
    is_party_ledger: int
    tax_rate: float
    has_bill_alloc: bool
    has_bank_alloc: bool

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.voucher_master_id, self.ledger_master_id, self.amount_scaled)

    @property
    def pair_key(self) -> tuple[int, int]:
        return (self.voucher_master_id, self.ledger_master_id)


@dataclass(frozen=True)
class CandidateTuple:
    voucher_master_id: int
    voucher_type_name: str | None
    ledger_master_id: int
    ledger_name: str | None
    amount_scaled: int
    sources: frozenset[str]
    evidence_count: int
    first_page_no: int | None
    first_page_offset: int | None

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.voucher_master_id, self.ledger_master_id, self.amount_scaled)

    @property
    def source_set(self) -> str:
        return "+".join(sorted(self.sources))


def open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def amount_text(amount_scaled: int) -> str:
    return f"{amount_scaled / AMOUNT_SCALE:.2f}"


def is_nonempty_json(raw: str | None) -> bool:
    return bool(raw and raw not in {"[]", ""})


def accounting_type(voucher_type_name: str | None) -> bool:
    name = (voucher_type_name or "").lower()
    if any(blocked in name for blocked in (
        "stock journal",
        "receipt note",
        "purchase order",
        "job order",
        "delivery challan",
    )):
        return False
    return any(token in name for token in (
        "payt",
        "payment",
        "receipt",
        "receipts",
        "journal",
        "contra",
    ))


def ledger_group(ledger_name: str | None, is_party_ledger: int = 0, tax_rate: float = 0.0) -> str:
    name = (ledger_name or "").upper()
    if tax_rate or any(token in name for token in (" CGST", " SGST", " IGST", "GST ", "TDS", "TCS", "VAT")):
        return "tax"
    if is_party_ledger:
        return "party"
    if "ROUND OFF" in name or name.strip() == "ROUND OFF":
        return "round_off"
    if "CASH" in name:
        return "cash"
    if any(token in name for token in ("BANK", " CUB ", "CCOD", "OD-", "A/C", "ACCOUNT")):
        return "bank"
    if any(token in name for token in ("SALES", "PURCHASE")):
        return "sales_purchase"
    return "other"


def amount_bucket(amount_scaled: int) -> str:
    value = abs(amount_scaled) / AMOUNT_SCALE
    if value == 0:
        return "zero"
    if value < 1:
        return "lt_1"
    if value < 100:
        return "1_to_100"
    if value < 10000:
        return "100_to_10k"
    if value < 1000000:
        return "10k_to_1m"
    return "gte_1m"


def load_name_to_mid(master_path: Path) -> dict[str, int]:
    conn = open_ro(master_path)
    try:
        return {
            str(name): int(master_id)
            for master_id, name in conn.execute(
                "select master_id, rosetta_name from ledgers where rosetta_name is not null"
            )
        }
    finally:
        conn.close()


def load_truth(rosetta_path: Path, name_to_mid: dict[str, int]) -> list[TruthRow]:
    conn = open_ro(rosetta_path)
    rows: list[TruthRow] = []
    try:
        for row in conn.execute(
            """
            select cast(v.master_id as int), v.voucher_type_name, e.ledger_name,
                   e.amount, e.is_party_ledger, e.tax_rate,
                   e.bill_allocations_json, e.bank_allocations_json
            from voucher_ledger_entries e
            join vouchers v on v.id = e.voucher_id
            where v.master_id is not null
            """
        ):
            vmid, voucher_type, ledger_name, amount, is_party, tax_rate, bill_json, bank_json = row
            ledger_id = name_to_mid.get(str(ledger_name))
            if ledger_id is None:
                continue
            rows.append(
                TruthRow(
                    voucher_master_id=int(vmid),
                    voucher_type_name=str(voucher_type),
                    ledger_master_id=ledger_id,
                    ledger_name=str(ledger_name),
                    amount_scaled=int(round(float(amount) * AMOUNT_SCALE)),
                    is_party_ledger=int(is_party or 0),
                    tax_rate=float(tax_rate or 0.0),
                    has_bill_alloc=is_nonempty_json(bill_json),
                    has_bank_alloc=is_nonempty_json(bank_json),
                )
            )
    finally:
        conn.close()
    return rows


def load_final(conn: sqlite3.Connection) -> set[tuple[int, int, int]]:
    return {
        (int(v), int(l), int(a))
        for v, l, a in conn.execute(
            "select voucher_master_id, ledger_master_id, amount_scaled from voucher_ledger_entries_1800"
        )
    }


def load_candidates(conn: sqlite3.Connection) -> dict[tuple[int, int, int], CandidateTuple]:
    rows: dict[tuple[int, int, int], CandidateTuple] = {}
    for row in conn.execute(
        """
        select voucher_master_id, min(voucher_type_name), ledger_master_id,
               min(ledger_name), amount_scaled,
               group_concat(distinct source), count(*), min(page_no), min(page_offset)
        from voucher_ledger_entry_candidates
        group by voucher_master_id, ledger_master_id, amount_scaled
        """
    ):
        vmid, vtype, ledger_id, ledger_name, amount, sources, evidence_count, page_no, page_offset = row
        key = (int(vmid), int(ledger_id), int(amount))
        rows[key] = CandidateTuple(
            voucher_master_id=key[0],
            voucher_type_name=vtype,
            ledger_master_id=key[1],
            ledger_name=ledger_name,
            amount_scaled=key[2],
            sources=frozenset(str(sources or "").split(",")) if sources else frozenset(),
            evidence_count=int(evidence_count or 0),
            first_page_no=int(page_no) if page_no is not None else None,
            first_page_offset=int(page_offset) if page_offset is not None else None,
        )
    return rows


def load_voucher_meta(conn: sqlite3.Connection) -> dict[int, tuple[int, str | None, int | None]]:
    return {
        int(master_id): (
            int(page_group_id),
            voucher_type_name,
            int(voucher_type_master_id) if voucher_type_master_id is not None else None,
        )
        for master_id, page_group_id, voucher_type_name, voucher_type_master_id in conn.execute(
            """
            select v.master_id, v.page_group_id, m.name, v.paired_voucher_type_master_id
            from voucher_header_candidates v
            left join manager_master_records m on m.master_id = v.paired_voucher_type_master_id
            where v.page_group_id is not null
              and v.key_raw is not null
              and v.base_type_code != 31
            """
        )
    }


def source_file_path(conn: sqlite3.Connection, file_name: str) -> Path | None:
    row = conn.execute("select path from source_files where file_name = ?", (file_name,)).fetchone()
    return Path(row[0]) if row and row[0] else None


def print_counter(title: str, counter: Counter, limit: int = 25) -> None:
    print(f"\n## {title}")
    for key, count in counter.most_common(limit):
        if isinstance(key, tuple):
            text = "\t".join(str(part) for part in key)
        else:
            text = str(key)
        print(f"{text}\t{count}")


def missing_atlas(
    truth_rows: list[TruthRow],
    final: set[tuple[int, int, int]],
    candidates: dict[tuple[int, int, int], CandidateTuple],
    linkmgr_pairs: set[tuple[int, int]],
    linkmgr_vouchers: set[int],
) -> list[TruthRow]:
    truth_by_key: dict[tuple[int, int, int], TruthRow] = {row.key: row for row in truth_rows}
    missing = [truth_by_key[key] for key in sorted(set(truth_by_key) - final)]
    candidates_by_pair: dict[tuple[int, int], list[CandidateTuple]] = defaultdict(list)
    candidates_by_voucher_amount: dict[tuple[int, int], list[CandidateTuple]] = defaultdict(list)
    for cand in candidates.values():
        candidates_by_pair[(cand.voucher_master_id, cand.ledger_master_id)].append(cand)
        candidates_by_voucher_amount[(cand.voucher_master_id, cand.amount_scaled)].append(cand)

    by_vtype: Counter = Counter()
    by_group: Counter = Counter()
    by_availability: Counter = Counter()
    by_source_exact: Counter = Counter()
    by_amount: Counter = Counter()
    examples: list[str] = []

    for row in missing:
        group = ledger_group(row.ledger_name, row.is_party_ledger, row.tax_rate)
        by_vtype[row.voucher_type_name] += 1
        by_group[group] += 1
        by_amount[amount_bucket(row.amount_scaled)] += 1
        exact = candidates.get(row.key)
        if exact:
            availability = "exact_candidate_not_final"
            by_source_exact[exact.source_set] += 1
        elif candidates_by_pair.get(row.pair_key):
            availability = "same_voucher_ledger_wrong_amount"
        elif candidates_by_voucher_amount.get((row.voucher_master_id, row.amount_scaled)):
            availability = "same_voucher_amount_wrong_ledger"
        elif row.pair_key in linkmgr_pairs:
            availability = "linkmgr_voucher_ledger_only"
        elif row.voucher_master_id in linkmgr_vouchers:
            availability = "linkmgr_voucher_only"
        else:
            availability = "no_direct_candidate"
        by_availability[(availability, group)] += 1
        if len(examples) < 30:
            examples.append(
                "\t".join(
                    [
                        str(row.voucher_master_id),
                        row.voucher_type_name,
                        str(row.ledger_master_id),
                        row.ledger_name,
                        amount_text(row.amount_scaled),
                        group,
                        availability,
                        exact.source_set if exact else "",
                    ]
                )
            )

    print("# W2B Ledger Missing Atlas")
    print(f"truth_tuples\t{len(set(row.key for row in truth_rows))}")
    print(f"final_tuples\t{len(final)}")
    print(f"missing_tuples\t{len(missing)}")
    print_counter("missing_by_voucher_type", by_vtype)
    print_counter("missing_by_ledger_group", by_group)
    print_counter("missing_by_amount_bucket", by_amount)
    print_counter("missing_by_availability_and_ledger_group", by_availability, 40)
    print_counter("missing_exact_candidate_source_sets", by_source_exact, 40)
    print("\n## missing_examples")
    print("voucher\tvoucher_type\tledger_id\tledger_name\tamount\tledger_group\tavailability\texact_sources")
    for line in examples:
        print(line)
    return missing


def score_candidate_experiments(
    truth_rows: list[TruthRow],
    final: set[tuple[int, int, int]],
    candidates: dict[tuple[int, int, int], CandidateTuple],
) -> dict[str, tuple[int, int, int]]:
    truth = {row.key for row in truth_rows}
    truth_meta = {row.key: row for row in truth_rows}
    by_voucher_amount_source: Counter[tuple[int, int, str]] = Counter()
    for cand in candidates.values():
        by_voucher_amount_source[(cand.voucher_master_id, cand.amount_scaled, cand.source_set)] += 1

    experiments: dict[str, list[CandidateTuple]] = defaultdict(list)
    for cand in candidates.values():
        if cand.key in final:
            continue
        meta = truth_meta.get(cand.key)
        group = (
            ledger_group(meta.ledger_name, meta.is_party_ledger, meta.tax_rate)
            if meta
            else ledger_group(cand.ledger_name)
        )
        is_accounting = accounting_type(cand.voucher_type_name)
        no_sibling = by_voucher_amount_source[(cand.voucher_master_id, cand.amount_scaled, cand.source_set)] == 1
        if is_accounting and no_sibling:
            experiments["accounting_no_sibling_any_source"].append(cand)
        if is_accounting and no_sibling and "subrecord_0002_wide" in cand.sources:
            experiments["accounting_no_sibling_has_0002_wide"].append(cand)
        if is_accounting and no_sibling and cand.sources == frozenset({"subrecord_0002_wide"}):
            experiments["accounting_no_sibling_0002_wide_only"].append(cand)
        if is_accounting and no_sibling and group != "tax":
            experiments["accounting_no_sibling_non_tax"].append(cand)
        if group == "tax":
            experiments["tax_candidates_not_final"].append(cand)

    print("\n# W2B Candidate Promotion Experiments")
    scored: dict[str, tuple[int, int, int]] = {}
    for name, rows in sorted(experiments.items()):
        keys = {row.key for row in rows}
        true_count = len(keys & truth)
        false_count = len(keys - truth)
        scored[name] = (len(keys), true_count, false_count)
        print(f"{name}\temit={len(keys)}\ttrue_gain={true_count}\textras={false_count}")

    print("\n## non_final_candidate_source_sets")
    source_stats: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for cand in candidates.values():
        if cand.key in final:
            continue
        bucket = ("accounting" if accounting_type(cand.voucher_type_name) else "non_accounting", cand.source_set)
        source_stats[bucket][0] += 1
        if cand.key in truth:
            source_stats[bucket][1] += 1
        else:
            source_stats[bucket][2] += 1
    for (kind, source_set), (total, true_count, false_count) in sorted(
        source_stats.items(),
        key=lambda item: (-item[1][1], item[1][2], item[0]),
    )[:40]:
        print(f"{kind}\t{source_set}\temit={total}\ttrue={true_count}\tfalse={false_count}")
    return scored


def tax_duplicate_analysis(truth_rows: list[TruthRow], final: set[tuple[int, int, int]], candidates: dict[tuple[int, int, int], CandidateTuple]) -> None:
    truth = {row.key for row in truth_rows}
    by_voucher_amount: dict[tuple[int, int], list[TruthRow]] = defaultdict(list)
    for row in truth_rows:
        if ledger_group(row.ledger_name, row.is_party_ledger, row.tax_rate) == "tax":
            by_voucher_amount[(row.voucher_master_id, row.amount_scaled)].append(row)

    duplicate_tax_keys = {
        row.key
        for rows in by_voucher_amount.values()
        if len({row.ledger_master_id for row in rows}) > 1
        for row in rows
    }
    missing_tax = duplicate_tax_keys - final
    candidate_tax_false = {
        cand.key
        for cand in candidates.values()
        if cand.key not in final
        and ledger_group(cand.ledger_name) == "tax"
        and cand.key not in truth
    }
    print("\n# W2B GST/Tax Duplicate Collision Analysis")
    print(f"duplicate_tax_truth_tuples\t{len(duplicate_tax_keys)}")
    print(f"duplicate_tax_missing_tuples\t{len(missing_tax)}")
    print(f"non_final_tax_candidate_false_tuples\t{len(candidate_tax_false)}")
    print("## duplicate_tax_missing_examples")
    shown = 0
    row_by_key = {row.key: row for row in truth_rows}
    for key in sorted(missing_tax):
        row = row_by_key[key]
        exact = candidates.get(key)
        print(
            f"{row.voucher_master_id}\t{row.voucher_type_name}\t{row.ledger_master_id}\t"
            f"{row.ledger_name}\t{amount_text(row.amount_scaled)}\t"
            f"{exact.source_set if exact else ''}"
        )
        shown += 1
        if shown >= 20:
            break


def linkmgr_missing_analysis(truth_rows: list[TruthRow], final: set[tuple[int, int, int]], linkmgr_pairs: set[tuple[int, int]], linkmgr_vouchers: set[int]) -> None:
    missing = [row for row in truth_rows if row.key not in final]
    party_missing = [row for row in missing if row.is_party_ledger]
    bill_bank_missing = [row for row in missing if row.has_bill_alloc or row.has_bank_alloc]
    print("\n# W2B LinkMgr Missing Party Analysis")
    for name, rows in (
        ("party_missing", party_missing),
        ("bill_or_bank_missing", bill_bank_missing),
    ):
        pair_hits = sum(1 for row in rows if row.pair_key in linkmgr_pairs)
        voucher_hits = sum(1 for row in rows if row.voucher_master_id in linkmgr_vouchers)
        print(f"{name}\trows={len(rows)}\tlinkmgr_pair_hits={pair_hits}\tlinkmgr_voucher_hits={voucher_hits}")
    print("## linkmgr_party_missing_examples")
    shown = 0
    for row in party_missing:
        if row.pair_key not in linkmgr_pairs and row.voucher_master_id not in linkmgr_vouchers:
            continue
        print(
            f"{row.voucher_master_id}\t{row.voucher_type_name}\t{row.ledger_master_id}\t"
            f"{row.ledger_name}\t{amount_text(row.amount_scaled)}\t"
            f"pair={int(row.pair_key in linkmgr_pairs)}\tvoucher={int(row.voucher_master_id in linkmgr_vouchers)}"
        )
        shown += 1
        if shown >= 20:
            break


def parse_0006_body_candidates(
    decoded_conn: sqlite3.Connection,
    truth_rows: list[TruthRow],
    ledger_ids: set[int],
    tranmgr_path: Path,
) -> None:
    voucher_meta = load_voucher_meta(decoded_conn)
    groups_needed = {
        voucher_meta[row.voucher_master_id][0]
        for row in truth_rows
        if row.voucher_master_id in voucher_meta
    }
    data = tranmgr_path.read_bytes()
    pages_by_group: dict[int, list[int]] = defaultdict(list)
    for page_no in range(1, len(data) // PAGE_SIZE):
        group_id = struct.unpack_from("<I", data, page_no * PAGE_SIZE + 4)[0]
        if group_id in groups_needed:
            pages_by_group[group_id].append(page_no)

    def stream_and_origins(vmid: int) -> tuple[bytes, list[tuple[int, int]]]:
        group_id = voucher_meta.get(vmid, (None, None, None))[0]
        if group_id is None:
            return b"", []
        chunks: list[bytes] = []
        origins: list[tuple[int, int]] = []
        for page_no in sorted(pages_by_group.get(group_id, [])):
            start = page_no * PAGE_SIZE + PAGE_HEADER_SIZE
            body = data[start : (page_no + 1) * PAGE_SIZE]
            chunks.append(body)
            origins.extend((page_no, PAGE_HEADER_SIZE + idx) for idx in range(len(body)))
        return b"".join(chunks), origins

    emitted_bodies: dict[tuple[int, int], list[tuple[int, str, int, int, int, int]]] = defaultdict(list)
    for vmid in sorted({row.voucher_master_id for row in truth_rows}):
        stream, origins = stream_and_origins(vmid)
        if not stream:
            continue
        pos = 0
        while True:
            pos = stream.find(SUBREC_0006, pos)
            if pos < 0 or pos + 10 > len(stream):
                break
            body_len = struct.unpack_from("<I", stream, pos + 6)[0]
            if body_len <= 0 or body_len > 2000:
                pos += 1
                continue
            body_start = pos + 10
            body = stream[body_start : min(len(stream), body_start + body_len)]
            ledger_pos = body.find(LEDGER_MARKER)
            if ledger_pos < 0 or ledger_pos + 8 > len(body):
                pos += 10
                continue
            ledger_id = struct.unpack_from("<I", body, ledger_pos + 4)[0]
            if ledger_id not in ledger_ids:
                pos += 10
                continue
            amount_pos = body.find(AMOUNT_MARKER, ledger_pos + 8)
            if amount_pos < 0 or amount_pos + 12 > len(body):
                pos += 10
                continue
            amount = struct.unpack_from("<q", body, amount_pos + 4)[0]
            if amount == 0 or 1000 <= abs(amount) < 10**16:
                body_hash = hashlib.sha1(body[: min(len(body), 128)]).hexdigest()
                page_no, page_off = origins[pos] if pos < len(origins) else (-1, -1)
                amount_logical_pos = body_start + amount_pos + 4
                amount_page, amount_page_off = origins[amount_logical_pos] if amount_logical_pos < len(origins) else (-1, -1)
                emitted_bodies[(vmid, ledger_id)].append(
                    (amount, body_hash, body_len, page_no, page_off, amount_page * PAGE_SIZE + amount_page_off)
                )
            pos += 10

    truth_pair_sum: Counter[tuple[int, int]] = Counter()
    truth_tuple_set = {row.key for row in truth_rows}
    for row in truth_rows:
        truth_pair_sum[row.pair_key] += row.amount_scaled

    def emitted_for_mode(mode: str, threshold: float = 0.05) -> dict[tuple[int, int], int]:
        emitted: dict[tuple[int, int], int] = {}
        for key, rows in emitted_bodies.items():
            if mode == "sum_all":
                emitted[key] = sum(amount for amount, *_rest in rows)
                continue
            by_hash: dict[str, int] = {}
            for amount, body_hash, *_rest in rows:
                by_hash.setdefault(body_hash, amount)
            values = list(by_hash.values())
            if mode == "hash_dedup":
                emitted[key] = sum(values)
            elif mode == "close_amount_tiebreak":
                if len(values) > 1:
                    same_sign = all(value >= 0 for value in values) or all(value <= 0 for value in values)
                    nonzero = [abs(value) for value in values if value]
                    close = bool(nonzero) and (max(nonzero) - min(nonzero)) / max(nonzero) <= threshold
                    emitted[key] = max(values, key=abs) if same_sign and close else sum(values)
                elif values:
                    emitted[key] = values[0]
            else:
                raise ValueError(mode)
        return emitted

    print("\n# W2B 0006 Body-Hash Fallback Retest")
    print(f"truth_voucher_ledger_pairs\t{len(truth_pair_sum)}")
    final = load_final(decoded_conn)
    for mode in ("sum_all", "hash_dedup", "close_amount_tiebreak"):
        emitted = emitted_for_mode(mode)
        correct = sum(1 for key, amount in emitted.items() if truth_pair_sum.get(key) == amount)
        wrong = sum(1 for key, amount in emitted.items() if key in truth_pair_sum and truth_pair_sum[key] != amount)
        false_pos = sum(1 for key in emitted if key not in truth_pair_sum)
        tuple_rows = {(vmid, ledger_id, amount) for (vmid, ledger_id), amount in emitted.items()}
        additive = tuple_rows - final
        true_gain = len(additive & truth_tuple_set)
        extras = len(additive - truth_tuple_set)
        print(
            f"{mode}\temit={len(emitted)}\tpair_correct={correct}\tpair_wrong={wrong}\t"
            f"pair_false={false_pos}\tadditive_true_gain={true_gain}\tadditive_extras={extras}"
        )

    emitted = emitted_for_mode("close_amount_tiebreak")
    print("## 0006_close_amount_additive_extra_examples")
    shown = 0
    row_meta = {(row.voucher_master_id, row.ledger_master_id): row for row in truth_rows}
    for (vmid, ledger_id), amount in sorted(emitted.items()):
        key = (vmid, ledger_id, amount)
        if key in final or key in truth_tuple_set:
            continue
        row = row_meta.get((vmid, ledger_id))
        rows = emitted_bodies[(vmid, ledger_id)]
        print(
            f"{vmid}\t{ledger_id}\t{row.voucher_type_name if row else ''}\t"
            f"{row.ledger_name if row else ''}\t{amount_text(amount)}\t"
            f"bodies={len(rows)}\thashes={len({body_hash for _amount, body_hash, *_ in rows})}\t"
            f"amounts={[amount_text(item[0]) for item in rows[:6]]}"
        )
        shown += 1
        if shown >= 20:
            break


def main() -> int:
    parser = argparse.ArgumentParser(description="Wave-2 Lane B ledger recall probes.")
    parser.add_argument("--decoded", type=Path, default=DEFAULT_DECODED)
    parser.add_argument("--rosetta", type=Path, default=DEFAULT_ROSETTA)
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--tranmgr", type=Path, default=None)
    args = parser.parse_args()

    name_to_mid = load_name_to_mid(args.master)
    truth_rows = load_truth(args.rosetta, name_to_mid)
    truth_set = {row.key for row in truth_rows}
    decoded = open_ro(args.decoded)
    try:
        final = load_final(decoded)
        candidates = load_candidates(decoded)
        linkmgr_pairs = {
            (int(v), int(l))
            for v, l in decoded.execute(
                """
                select voucher_master_id, ledger_master_id
                from linkmgr_allocation_pages
                where voucher_master_id is not null
                  and ledger_master_id is not null
                """
            )
        }
        linkmgr_vouchers = {
            int(v)
            for (v,) in decoded.execute(
                """
                select distinct voucher_master_id
                from linkmgr_allocation_pages
                where voucher_master_id is not null
                """
            )
        }
        missing_atlas(truth_rows, final, candidates, linkmgr_pairs, linkmgr_vouchers)
        score_candidate_experiments(truth_rows, final, candidates)
        tax_duplicate_analysis(truth_rows, final, candidates)
        linkmgr_missing_analysis(truth_rows, final, linkmgr_pairs, linkmgr_vouchers)
        tranmgr_path = args.tranmgr or source_file_path(decoded, "TranMgr.1800")
        if tranmgr_path and tranmgr_path.exists():
            parse_0006_body_candidates(decoded, truth_rows, set(name_to_mid.values()), tranmgr_path)
        else:
            print(f"\n# W2B 0006 Body-Hash Fallback Retest\nmissing_tranmgr\t{tranmgr_path}")
        print("\n# W2B Summary Scalars")
        print(f"truth_tuples\t{len(truth_set)}")
        print(f"final_tuples\t{len(final)}")
        print(f"final_true\t{len(final & truth_set)}")
        print(f"final_extras\t{len(final - truth_set)}")
        print(f"missing_tuples\t{len(truth_set - final)}")
    finally:
        decoded.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

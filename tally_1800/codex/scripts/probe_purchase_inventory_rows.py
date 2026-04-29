from __future__ import annotations

import argparse
import sqlite3
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from probe_journal_inventory_rows import (
    div_round_half_up,
    iter_body_compact_i64_values,
    iter_logical_f6_blocks,
    load_pages_by_mid,
    parse_columnar_pairs,
    parse_post_rate_subrecords,
)
from probe_sales_inventory_runs import (
    SCALE,
    find_nearby_ledger_for_amount,
    find_rate_i64,
    iter_streams_for_pages,
    load_group_to_master_id,
    load_rosetta_ids,
    parse_sales_runs,
    repair_split_i64,
    resolve_compact_master_id,
    scaled,
)


TYPE3_CELL_SIZE = 10


@dataclass(frozen=True)
class PurchaseRun:
    voucher_master_id: int
    stock_item_id: int
    ledger_id: int
    quantity_scaled: int
    rate_scaled: int
    amount_scaled: int
    source: str
    page_no: int
    page_offset: int


def _find_stock_row_candidates(
    stream: bytes,
    origins: list[tuple[int, int]],
    subrec_pos: int,
    stock_ids: set[int],
    ledger_ids: set[int],
    amount_scaled: int,
    *,
    lookback: int = 520,
) -> list[tuple[int, int, int, int, str]]:
    """Return (stock_pos, stock_id, rate, ledger_id, source)."""
    candidates: list[tuple[int, int, int, int, str]] = []
    start = max(0, subrec_pos - lookback)
    pos = start
    while True:
        pos = stream.find(b"\x02\x00\x00\x03", pos, subrec_pos)
        if pos < 0:
            break
        if pos + 8 > len(stream):
            pos += 1
            continue
        raw_stock = struct.unpack_from("<I", stream, pos + 4)[0]
        stock_id = resolve_compact_master_id(raw_stock, stock_ids)
        if stock_id is None:
            pos += 1
            continue

        rate_hit = find_rate_i64(stream, pos + 8, subrec_pos)
        if rate_hit is None:
            pos += 1
            continue
        rate_pos, raw_rate = rate_hit
        rate_scaled = abs(
            repair_split_i64(
                stream,
                rate_pos,
                raw_rate,
                origins,
                signed_low32=False,
            )
        )
        if rate_scaled == 0:
            pos += 1
            continue

        second_field = stream[pos + TYPE3_CELL_SIZE : pos + TYPE3_CELL_SIZE + 4]
        ledger_id = None
        source = ""
        if second_field == b"\x69\x00\x00\x03":
            ledger_value = struct.unpack_from("<I", stream, pos + TYPE3_CELL_SIZE + 4)[0]
            maybe_ledger = resolve_compact_master_id(ledger_value, ledger_ids)
            repeat_pos = stream.find(
                b"\x6e\x00\x00\x03",
                pos + TYPE3_CELL_SIZE + 8,
                min(subrec_pos, pos + 80),
            )
            if repeat_pos >= 0 and maybe_ledger is not None:
                repeat_value = struct.unpack_from("<I", stream, repeat_pos + 4)[0]
                if resolve_compact_master_id(repeat_value, {maybe_ledger}) == maybe_ledger:
                    ledger_id = maybe_ledger
                    source = "inline_69_6e"
        elif second_field == b"\x68\x00\x00\x03":
            repeat_stock = struct.unpack_from("<I", stream, pos + TYPE3_CELL_SIZE + 4)[0]
            if resolve_compact_master_id(repeat_stock, {stock_id}) == stock_id:
                stock_repeat_pos = stream.find(
                    b"\x6d\x00\x00\x03",
                    pos + TYPE3_CELL_SIZE + 8,
                    min(subrec_pos, pos + 100),
                )
                if stock_repeat_pos >= 0:
                    stock_repeat = struct.unpack_from("<I", stream, stock_repeat_pos + 4)[0]
                    if resolve_compact_master_id(stock_repeat, {stock_id}) == stock_id:
                        ledger_id = find_nearby_ledger_for_amount(
                            stream,
                            pos,
                            amount_scaled,
                            ledger_ids,
                            search_bytes=1200,
                        )
                        if ledger_id is None:
                            ledger_id = find_nearby_ledger_for_amount(
                                stream,
                                pos,
                                amount_scaled,
                                ledger_ids,
                            )
                        source = "stock_repeat_68_6d"

        if ledger_id is not None:
            candidates.append((pos, stock_id, rate_scaled, ledger_id, source))
        pos += 1

    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates


def parse_purchase_runs(
    data: bytes,
    group_to_mid: dict[int, int],
    stock_ids: set[int],
    ledger_ids: set[int],
    target_mids: set[int] | None = None,
) -> list[PurchaseRun]:
    pages_by_mid = load_pages_by_mid(data, group_to_mid)
    if target_mids is not None:
        pages_by_mid = {mid: pages for mid, pages in pages_by_mid.items() if mid in target_mids}

    runs: list[PurchaseRun] = []
    seen: set[tuple[int, int, int, int, int, int, str, int, int]] = set()
    for mid, pages in pages_by_mid.items():
        for stream, origins in iter_streams_for_pages(data, sorted(pages)):
            for block in iter_logical_f6_blocks(stream, origins):
                amounts = iter_body_compact_i64_values(block.body, b"\x02\x00\x00\x09")
                quantities = iter_body_compact_i64_values(block.body, b"\x02\x00\x00\x0b")
                if not amounts or not quantities:
                    continue
                _amount_pos, amount_scaled = amounts[0]
                _qty_pos, quantity_scaled = quantities[0]
                quantity_scaled = abs(quantity_scaled)
                if amount_scaled == 0 or quantity_scaled == 0:
                    continue
                derived_rate = div_round_half_up(abs(amount_scaled) * int(SCALE), quantity_scaled)
                for _stock_pos, stock_id, rate_scaled, ledger_id, source in _find_stock_row_candidates(
                    stream,
                    origins,
                    block.stream_pos,
                    stock_ids,
                    ledger_ids,
                    amount_scaled,
                )[:4]:
                    candidate_rates = [rate_scaled]
                    if derived_rate not in candidate_rates:
                        candidate_rates.append(derived_rate)
                    for candidate_rate in candidate_rates:
                        key = (
                            mid,
                            stock_id,
                            ledger_id,
                            quantity_scaled,
                            candidate_rate,
                            amount_scaled,
                            source,
                            block.page_no,
                            block.page_offset,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        runs.append(
                            PurchaseRun(
                                voucher_master_id=mid,
                                stock_item_id=stock_id,
                                ledger_id=ledger_id,
                                quantity_scaled=quantity_scaled,
                                rate_scaled=candidate_rate,
                                amount_scaled=amount_scaled,
                                source=source,
                                page_no=block.page_no,
                                page_offset=block.page_offset,
                            )
                        )

    for sales_run in parse_sales_runs(data, group_to_mid, stock_ids, ledger_ids):
        if target_mids is not None and sales_run.voucher_master_id not in target_mids:
            continue
        key = (
            sales_run.voucher_master_id,
            sales_run.stock_item_id,
            sales_run.ledger_id,
            abs(sales_run.quantity_scaled),
            sales_run.rate_scaled,
            sales_run.amount_scaled,
            "sales_compact_family",
            sales_run.page_no,
            sales_run.page_offset,
        )
        if key in seen:
            continue
        seen.add(key)
        runs.append(
            PurchaseRun(
                voucher_master_id=sales_run.voucher_master_id,
                stock_item_id=sales_run.stock_item_id,
                ledger_id=sales_run.ledger_id,
                quantity_scaled=abs(sales_run.quantity_scaled),
                rate_scaled=sales_run.rate_scaled,
                amount_scaled=sales_run.amount_scaled,
                source="sales_compact_family",
                page_no=sales_run.page_no,
                page_offset=sales_run.page_offset,
            )
        )

    ledgers_by_mid: dict[int, set[int]] = defaultdict(set)
    for run in runs:
        ledgers_by_mid[run.voucher_master_id].add(run.ledger_id)
    unique_ledger_by_mid = {
        mid: next(iter(ledger_ids))
        for mid, ledger_ids in ledgers_by_mid.items()
        if len(ledger_ids) == 1
    }
    if unique_ledger_by_mid:
        value_runs = [
            *parse_post_rate_subrecords(data, pages_by_mid, stock_ids),
            *parse_columnar_pairs(data, pages_by_mid, stock_ids),
        ]
        for value_run in value_runs:
            ledger_id = unique_ledger_by_mid.get(value_run.voucher_master_id)
            if ledger_id is None:
                continue
            key = (
                value_run.voucher_master_id,
                value_run.stock_item_id,
                ledger_id,
                value_run.quantity_scaled,
                value_run.rate_scaled,
                value_run.amount_scaled,
                "voucher_unique_ledger",
                value_run.page_no,
                value_run.page_offset,
            )
            if key in seen:
                continue
            seen.add(key)
            runs.append(
                PurchaseRun(
                    voucher_master_id=value_run.voucher_master_id,
                    stock_item_id=value_run.stock_item_id,
                    ledger_id=ledger_id,
                    quantity_scaled=value_run.quantity_scaled,
                    rate_scaled=value_run.rate_scaled,
                    amount_scaled=value_run.amount_scaled,
                    source="voucher_unique_ledger",
                    page_no=value_run.page_no,
                    page_offset=value_run.page_offset,
                )
            )
    return runs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_sqlite", type=Path)
    parser.add_argument("--tranmgr", type=Path, required=True)
    parser.add_argument("--rosetta", type=Path, required=True)
    parser.add_argument("--voucher-type", default="31-Puchase(Monthly)")
    parser.add_argument("--examples", type=int, default=10)
    args = parser.parse_args()

    decoded = sqlite3.connect(args.decoded_sqlite)
    rosetta = sqlite3.connect(args.rosetta)
    stock_name_to_id = load_rosetta_ids(rosetta, "stock_items")
    ledger_name_to_id = load_rosetta_ids(rosetta, "ledgers")

    truth = []
    for row in rosetta.execute(
        """
        select cast(v.master_id as integer), e.item_name, e.ledger_name,
               e.quantity, e.rate, e.amount
        from voucher_inventory_entries e
        join vouchers v on v.id = e.voucher_id
        where v.voucher_type_name = ?
        """,
        (args.voucher_type,),
    ):
        mid, item_name, ledger_name, quantity, rate, amount = row
        stock_id = stock_name_to_id.get(item_name)
        ledger_id = ledger_name_to_id.get(ledger_name)
        if stock_id is None or ledger_id is None:
            continue
        truth.append(
            (
                int(mid),
                stock_id,
                ledger_id,
                scaled(quantity),
                scaled(rate),
                scaled(amount),
                str(item_name),
                str(ledger_name),
            )
        )

    runs = parse_purchase_runs(
        args.tranmgr.read_bytes(),
        load_group_to_master_id(decoded),
        set(stock_name_to_id.values()),
        set(ledger_name_to_id.values()),
        target_mids={row[0] for row in truth},
    )

    run_keys = Counter(
        (
            r.voucher_master_id,
            r.stock_item_id,
            r.ledger_id,
            r.quantity_scaled,
            r.rate_scaled,
            r.amount_scaled,
        )
        for r in runs
    )
    item_qty_amount_keys = Counter(
        (r.voucher_master_id, r.stock_item_id, r.quantity_scaled, r.amount_scaled)
        for r in runs
    )
    source_counts = Counter(r.source for r in runs)

    exact = 0
    qty_amount_only = 0
    missing = []
    for mid, stock_id, ledger_id, quantity, rate, amount, item_name, ledger_name in truth:
        if run_keys[(mid, stock_id, ledger_id, quantity, rate, amount)]:
            exact += 1
        elif item_qty_amount_keys[(mid, stock_id, quantity, amount)]:
            qty_amount_only += 1
        elif len(missing) < args.examples:
            missing.append((mid, item_name, ledger_name, quantity, rate, amount))

    print(f"voucher_type={args.voucher_type}")
    print(f"truth_rows={len(truth)}")
    print(f"purchase_candidate_rows={len(runs)}")
    print(f"source_counts={dict(source_counts)}")
    print(f"exact_qty_rate_amount_ledger_matches={exact}/{len(truth)}")
    print(f"item_qty_amount_matches_without_ledger_or_rate={qty_amount_only}/{len(truth)}")
    print("missing_examples=")
    for row in missing:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import sqlite3
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from probe_sales_inventory_runs import (
    PAGE_SIZE,
    SCALE,
    find_nearby_ledger_for_amount,
    iter_streams_for_pages,
    load_group_to_master_id,
    load_rosetta_ids,
    repair_split_i64,
    scaled,
)


@dataclass(frozen=True)
class MatrixValue:
    voucher_master_id: int
    stock_item_id: int
    page_no: int
    page_offset: int
    stream_pos: int
    scaled_value: int


@dataclass(frozen=True)
class MatrixRun:
    voucher_master_id: int
    stock_item_id: int
    ledger_id: int
    amount_scaled: int
    quantity_scaled: int
    rate_scaled: int


def load_pages_by_mid(data: bytes, group_to_mid: dict[int, int]) -> dict[int, list[int]]:
    pages_by_mid: dict[int, list[int]] = defaultdict(list)
    for page_no in range(1, len(data) // PAGE_SIZE):
        group_id = struct.unpack_from("<I", data, page_no * PAGE_SIZE + 4)[0]
        mid = group_to_mid.get(group_id)
        if mid is not None:
            pages_by_mid[mid].append(page_no)
    return pages_by_mid


def iter_matrix_values(
    data: bytes,
    pages_by_mid: dict[int, list[int]],
    stock_ids: set[int],
    kind: int,
    target_keys: set[tuple[int, int, int]] | None = None,
) -> list[MatrixValue]:
    values: list[MatrixValue] = []
    for mid, pages in pages_by_mid.items():
        pages.sort()
        for stream, origins in iter_streams_for_pages(data, pages):
            pos = 0
            while True:
                pos = stream.find(b"\xf6\x01" + bytes([kind, 0x00]), pos)
                if pos < 2:
                    break
                marker_pos = pos - 2
                if marker_pos + 56 > len(stream):
                    pos += 1
                    continue
                variant = stream[marker_pos + 1]
                if variant not in (0x04, 0x0C):
                    pos += 1
                    continue
                stock_id = struct.unpack_from("<I", stream, marker_pos + 6)[0]
                if stock_id not in stock_ids:
                    pos += 1
                    continue

                if kind == 0x44:
                    offsets = (34,) if variant == 0x04 else (38,)
                    signed_low32 = False
                else:
                    offsets = (26, 42) if variant == 0x04 else (30, 46)
                    signed_low32 = True

                page_no, page_offset = origins[marker_pos]
                for value_offset in offsets:
                    raw = struct.unpack_from("<q", stream, marker_pos + value_offset)[0]
                    value = repair_split_i64(
                        stream,
                        marker_pos + value_offset - 4,
                        raw,
                        origins,
                        signed_low32=signed_low32,
                    )
                    if value == 0:
                        continue
                    normalized = abs(value) if kind == 0x46 else value
                    if target_keys is not None and (mid, stock_id, normalized) not in target_keys:
                        continue
                    values.append(
                        MatrixValue(
                            voucher_master_id=mid,
                            stock_item_id=stock_id,
                            page_no=page_no,
                            page_offset=page_offset,
                            stream_pos=marker_pos,
                            scaled_value=value,
                        )
                    )
                pos += 1
    return values


def parse_matrix_runs(
    data: bytes,
    group_to_mid: dict[int, int],
    stock_ids: set[int],
    ledger_ids: set[int],
    target_mids: set[int] | None = None,
    target_amount_keys: set[tuple[int, int, int]] | None = None,
    target_quantity_keys: set[tuple[int, int, int]] | None = None,
) -> list[MatrixRun]:
    pages_by_mid = load_pages_by_mid(data, group_to_mid)
    if target_mids is not None:
        pages_by_mid = {
            mid: pages for mid, pages in pages_by_mid.items() if mid in target_mids
        }
    amounts = iter_matrix_values(data, pages_by_mid, stock_ids, 0x44, target_amount_keys)
    quantities = iter_matrix_values(data, pages_by_mid, stock_ids, 0x46, target_quantity_keys)

    quantities_by_key: dict[tuple[int, int], list[MatrixValue]] = defaultdict(list)
    for q in quantities:
        quantities_by_key[(q.voucher_master_id, q.stock_item_id)].append(q)

    streams_by_mid = {
        mid: iter_streams_for_pages(data, sorted(pages))[0][0]
        for mid, pages in pages_by_mid.items()
    }

    runs: list[MatrixRun] = []
    seen: set[tuple[int, int, int, int, int, int]] = set()
    for amount in amounts:
        stream = streams_by_mid[amount.voucher_master_id]
        ledger_id = find_nearby_ledger_for_amount(
            stream,
            amount.stream_pos,
            amount.scaled_value,
            ledger_ids,
        )
        if ledger_id is None:
            continue
        for quantity in quantities_by_key[(amount.voucher_master_id, amount.stock_item_id)]:
            qty = abs(quantity.scaled_value)
            if qty == 0:
                continue
            numerator = amount.scaled_value * int(SCALE)
            if numerator % qty != 0:
                continue
            rate = numerator // qty
            key = (
                amount.voucher_master_id,
                amount.stock_item_id,
                ledger_id,
                qty,
                rate,
                amount.scaled_value,
            )
            if key in seen:
                continue
            seen.add(key)
            runs.append(
                MatrixRun(
                    voucher_master_id=amount.voucher_master_id,
                    stock_item_id=amount.stock_item_id,
                    ledger_id=ledger_id,
                    amount_scaled=amount.scaled_value,
                    quantity_scaled=qty,
                    rate_scaled=rate,
                )
            )
    return runs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_sqlite", type=Path)
    parser.add_argument("--tranmgr", type=Path, required=True)
    parser.add_argument("--rosetta", type=Path, required=True)
    parser.add_argument("--voucher-type", default="61 Sales")
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
                item_name,
                ledger_name,
            )
        )

    target_mids = {row[0] for row in truth}
    target_amount_keys = {(mid, stock_id, amount) for mid, stock_id, _ledger_id, _quantity, _rate, amount, _item_name, _ledger_name in truth}
    target_quantity_keys = {(mid, stock_id, quantity) for mid, stock_id, _ledger_id, quantity, _rate, _amount, _item_name, _ledger_name in truth}

    runs = parse_matrix_runs(
        args.tranmgr.read_bytes(),
        load_group_to_master_id(decoded),
        set(stock_name_to_id.values()),
        set(ledger_name_to_id.values()),
        target_mids=target_mids,
        target_amount_keys=target_amount_keys,
        target_quantity_keys=target_quantity_keys,
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
    item_amount_keys = Counter(
        (r.voucher_master_id, r.stock_item_id, r.amount_scaled) for r in runs
    )

    exact = 0
    item_amount = 0
    missing_examples = []
    for mid, stock_id, ledger_id, quantity, rate, amount, item_name, ledger_name in truth:
        key = (mid, stock_id, ledger_id, quantity, rate, amount)
        if run_keys[key] > 0:
            exact += 1
        elif item_amount_keys[(mid, stock_id, amount)] > 0:
            item_amount += 1
        elif len(missing_examples) < args.examples:
            missing_examples.append((mid, item_name, ledger_name, quantity, rate, amount))

    runs_for_type_mids = {
        int(row[0])
        for row in rosetta.execute(
            "select cast(master_id as integer) from vouchers where voucher_type_name = ?",
            (args.voucher_type,),
        )
    }
    type_runs = [r for r in runs if r.voucher_master_id in runs_for_type_mids]

    print(f"voucher_type={args.voucher_type}")
    print(f"truth_rows={len(truth)}")
    print(f"all_matrix_runs={len(runs)}")
    print(f"type_matrix_runs={len(type_runs)}")
    print(f"exact_qty_rate_amount_ledger_matches={exact}/{len(truth)}")
    print(f"item_amount_matches_without_full_tuple={item_amount}/{len(truth)}")
    print("missing_examples=")
    for example in missing_examples:
        print(example)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

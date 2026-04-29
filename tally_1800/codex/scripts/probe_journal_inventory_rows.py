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
    find_compact_i64,
    find_rate_i64,
    iter_streams_for_pages,
    load_group_to_master_id,
    load_rosetta_ids,
    read_logical_bytes_with_end,
    repair_split_i64,
    resolve_compact_master_id,
    scaled,
)


SUBREC_F6 = b"\x00\x50\xf6\x01\x00\x10"
STOCK_FIELD = b"\x02\x00\x00\x03"


@dataclass(frozen=True)
class JournalRun:
    voucher_master_id: int
    stock_item_id: int
    quantity_scaled: int
    rate_scaled: int
    amount_scaled: int
    source: str
    page_no: int
    page_offset: int


@dataclass(frozen=True)
class ColumnRow:
    voucher_master_id: int
    stock_item_id: int
    stream_pos: int
    page_no: int
    page_offset: int
    value_a_scaled: int
    value_b_scaled: int


@dataclass(frozen=True)
class LogicalF6Block:
    stream_pos: int
    page_no: int
    page_offset: int
    body: bytes


def div_round_half_up(numerator: int, denominator: int) -> int:
    if denominator == 0:
        return 0
    return int(
        (Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def load_pages_by_mid(data: bytes, group_to_mid: dict[int, int]) -> dict[int, list[int]]:
    pages_by_mid: dict[int, list[int]] = defaultdict(list)
    for page_no in range(1, len(data) // PAGE_SIZE):
        group_id = struct.unpack_from("<I", data, page_no * PAGE_SIZE + 4)[0]
        mid = group_to_mid.get(group_id)
        if mid is not None:
            pages_by_mid[mid].append(page_no)
    return pages_by_mid


def iter_compact_i64_values(
    stream: bytes,
    origins: list[tuple[int, int]],
    marker: bytes,
    start: int,
    end: int,
    *,
    signed_low32: bool,
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    pos = start
    end = min(end, len(stream))
    while True:
        hit = find_compact_i64(stream, marker, pos, end)
        if hit is None:
            return out
        value_pos, raw_value = hit
        value = repair_split_i64(
            stream,
            value_pos,
            raw_value,
            origins,
            signed_low32=signed_low32,
        )
        if value:
            out.append((value_pos, value))
        pos = value_pos + 1


def find_preceding_stock_rates(
    stream: bytes,
    origins: list[tuple[int, int]],
    subrec_pos: int,
    stock_ids: set[int],
    *,
    lookback: int = 360,
) -> list[tuple[int, int, int]]:
    """Return nearby preceding (stock_pos, stock_id, rate) candidates."""
    start = max(0, subrec_pos - lookback)
    candidates: list[tuple[int, int, int]] = []
    pos = start
    while True:
        pos = stream.find(STOCK_FIELD, pos, subrec_pos)
        if pos < 0:
            break
        if pos + 8 > len(stream):
            pos += 1
            continue
        raw_stock_id = struct.unpack_from("<I", stream, pos + 4)[0]
        stock_id = resolve_compact_master_id(raw_stock_id, stock_ids)
        if stock_id is None:
            pos += 1
            continue
        rate_hit = find_rate_i64(stream, pos + 8, subrec_pos)
        if rate_hit is not None:
            rate_pos, raw_rate = rate_hit
            rate = repair_split_i64(
                stream,
                rate_pos,
                raw_rate,
                origins,
                signed_low32=False,
            )
            if rate:
                candidates.append((pos, stock_id, abs(rate)))
        else:
            candidates.append((pos, stock_id, 0))
        pos += 1
    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates


def iter_logical_f6_blocks(
    stream: bytes,
    origins: list[tuple[int, int]],
) -> list[LogicalF6Block]:
    positions: set[int] = set()
    pos = 0
    while True:
        pos = stream.find(SUBREC_F6, pos)
        if pos < 0:
            break
        positions.add(pos)
        pos += 1

    # Catch the rarer case where the F6 marker itself crosses a page boundary.
    for idx, (_page, offset) in enumerate(origins):
        if offset < PAGE_SIZE - len(SUBREC_F6) + 1:
            continue
        if stream[idx] != SUBREC_F6[0]:
            continue
        result = read_logical_bytes_with_end(stream, origins, idx, len(SUBREC_F6))
        if result is not None and result[0] == SUBREC_F6:
            positions.add(idx)

    blocks: list[LogicalF6Block] = []
    for subrec_pos in sorted(positions):
        header = read_logical_bytes_with_end(stream, origins, subrec_pos, 10)
        if header is None:
            continue
        header_bytes, body_start, last_origin = header
        if header_bytes[: len(SUBREC_F6)] != SUBREC_F6:
            continue
        subrec_len = struct.unpack_from("<I", header_bytes, 6)[0]
        if not 0 < subrec_len < 4000:
            continue
        body = read_logical_bytes_with_end(
            stream,
            origins,
            body_start,
            subrec_len,
            previous_origin=last_origin,
        )
        if body is None:
            continue
        page_no, page_offset = origins[subrec_pos]
        blocks.append(
            LogicalF6Block(
                stream_pos=subrec_pos,
                page_no=page_no,
                page_offset=page_offset,
                body=body[0],
            )
        )
    return blocks


def iter_body_compact_i64_values(body: bytes, marker: bytes) -> list[tuple[int, int]]:
    values: list[tuple[int, int]] = []
    pos = 0
    while True:
        pos = body.find(marker, pos)
        if pos < 0:
            return values
        if pos + 12 <= len(body):
            value = struct.unpack_from("<q", body, pos + 4)[0]
            if value:
                values.append((pos, value))
        pos += 1


def parse_post_rate_subrecords(
    data: bytes,
    pages_by_mid: dict[int, list[int]],
    stock_ids: set[int],
) -> list[JournalRun]:
    runs: list[JournalRun] = []
    seen: set[tuple[int, int, int, int, int, str, int, int]] = set()

    for mid, pages in pages_by_mid.items():
        for stream, origins in iter_streams_for_pages(data, sorted(pages)):
            for block in iter_logical_f6_blocks(stream, origins):
                subrec_pos = block.stream_pos
                amounts = iter_body_compact_i64_values(block.body, b"\x02\x00\x00\x09")
                quantities = iter_body_compact_i64_values(block.body, b"\x02\x00\x00\x0b")
                if not amounts or not quantities:
                    continue

                _amount_pos, amount = amounts[0]
                _qty_pos, quantity = quantities[0]
                quantity = abs(quantity)
                if quantity == 0 or amount == 0:
                    continue

                page_no, page_offset = block.page_no, block.page_offset
                derived_rate = div_round_half_up(abs(amount) * int(SCALE), quantity)
                for _stock_pos, stock_id, rate in find_preceding_stock_rates(
                    stream, origins, subrec_pos, stock_ids
                )[:3]:
                    rates = [rate] if rate else []
                    if derived_rate and derived_rate not in rates:
                        rates.append(derived_rate)
                    for candidate_rate in rates:
                        key = (
                            mid,
                            stock_id,
                            quantity,
                            candidate_rate,
                            amount,
                            "post_rate_f6",
                            page_no,
                            page_offset,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        runs.append(
                            JournalRun(
                                voucher_master_id=mid,
                                stock_item_id=stock_id,
                                quantity_scaled=quantity,
                                rate_scaled=candidate_rate,
                                amount_scaled=amount,
                                source="post_rate_f6",
                                page_no=page_no,
                                page_offset=page_offset,
                            )
                        )
    return runs


def scan_column_rows(
    stream: bytes,
    origins: list[tuple[int, int]],
    mid: int,
    stock_ids: set[int],
    kind: int,
) -> list[ColumnRow]:
    rows: list[ColumnRow] = []
    needle = b"\xf6\x01" + bytes([kind, 0x00])
    pos = 0
    while True:
        pos = stream.find(needle, pos)
        if pos < 2:
            break
        if pos + 40 > len(stream):
            pos += 1
            continue
        flag_variant = stream[pos - 1]
        if flag_variant != 0x04:
            pos += 1
            continue
        stock_id = struct.unpack_from("<I", stream, pos + 4)[0]
        if stock_id not in stock_ids:
            pos += 1
            continue

        first_value_pos = pos + 24
        second_value_pos = pos + 32
        if second_value_pos + 8 > len(stream):
            pos += 1
            continue
        raw_first = struct.unpack_from("<q", stream, first_value_pos)[0]
        raw_second = struct.unpack_from("<q", stream, second_value_pos)[0]
        first = repair_split_i64(
            stream,
            first_value_pos - 4,
            raw_first,
            origins,
            signed_low32=True,
        )
        second = repair_split_i64(
            stream,
            second_value_pos - 4,
            raw_second,
            origins,
            signed_low32=True,
        )
        if first == 0 and second == 0:
            pos += 1
            continue
        page_no, page_offset = origins[pos - 2]
        rows.append(
            ColumnRow(
                voucher_master_id=mid,
                stock_item_id=stock_id,
                stream_pos=pos - 2,
                page_no=page_no,
                page_offset=page_offset,
                value_a_scaled=first,
                value_b_scaled=second,
            )
        )
        pos += 1
    return rows


def parse_columnar_pairs(
    data: bytes,
    pages_by_mid: dict[int, list[int]],
    stock_ids: set[int],
) -> list[JournalRun]:
    runs: list[JournalRun] = []
    seen: set[tuple[int, int, int, int, int, str, int, int]] = set()
    for mid, pages in pages_by_mid.items():
        for stream, origins in iter_streams_for_pages(data, sorted(pages)):
            amount_rows = scan_column_rows(stream, origins, mid, stock_ids, 0x44)
            quantity_rows = scan_column_rows(stream, origins, mid, stock_ids, 0x46)
            amounts_by_stock: dict[int, list[ColumnRow]] = defaultdict(list)
            quantities_by_stock: dict[int, list[ColumnRow]] = defaultdict(list)
            for row in amount_rows:
                amounts_by_stock[row.stock_item_id].append(row)
            for row in quantity_rows:
                quantities_by_stock[row.stock_item_id].append(row)

            for stock_id, amount_list in amounts_by_stock.items():
                quantity_list = quantities_by_stock.get(stock_id)
                if not quantity_list:
                    continue
                amount_list.sort(key=lambda row: row.stream_pos)
                quantity_list.sort(key=lambda row: row.stream_pos)
                for amount_row, quantity_row in zip(amount_list, quantity_list):
                    pairs = (
                        (amount_row.value_a_scaled, quantity_row.value_b_scaled),
                        (amount_row.value_b_scaled, quantity_row.value_a_scaled),
                    )
                    for amount, quantity in pairs:
                        quantity = abs(quantity)
                        if amount == 0 or quantity == 0:
                            continue
                        rate = div_round_half_up(abs(amount) * int(SCALE), quantity)
                        key = (
                            mid,
                            stock_id,
                            quantity,
                            rate,
                            amount,
                            "columnar_f6_44_46",
                            amount_row.page_no,
                            amount_row.page_offset,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        runs.append(
                            JournalRun(
                                voucher_master_id=mid,
                                stock_item_id=stock_id,
                                quantity_scaled=quantity,
                                rate_scaled=rate,
                                amount_scaled=amount,
                                source="columnar_f6_44_46",
                                page_no=amount_row.page_no,
                                page_offset=amount_row.page_offset,
                            )
                        )
    return runs


def load_truth(
    rosetta: sqlite3.Connection,
    stock_name_to_id: dict[str, int],
    voucher_types: list[str],
) -> list[tuple[int, str, int, int, int, int, str]]:
    placeholders = ",".join("?" for _ in voucher_types)
    truth = []
    for row in rosetta.execute(
        f"""
        select cast(v.master_id as integer), v.voucher_type_name,
               e.item_name, e.quantity, e.rate, e.amount
        from voucher_inventory_entries e
        join vouchers v on v.id = e.voucher_id
        where v.voucher_type_name in ({placeholders})
        """,
        voucher_types,
    ):
        mid, voucher_type, item_name, quantity, rate, amount = row
        stock_id = stock_name_to_id.get(item_name)
        if stock_id is None:
            continue
        truth.append(
            (
                int(mid),
                str(voucher_type),
                stock_id,
                scaled(quantity),
                scaled(rate),
                scaled(amount),
                str(item_name),
            )
        )
    return truth


def summarize(
    label: str,
    runs: list[JournalRun],
    truth: list[tuple[int, str, int, int, int, int, str]],
    voucher_types: list[str],
    examples: int,
) -> None:
    run_keys = Counter(
        (
            r.voucher_master_id,
            r.stock_item_id,
            r.quantity_scaled,
            r.rate_scaled,
            r.amount_scaled,
        )
        for r in runs
    )
    item_amount_keys = Counter(
        (r.voucher_master_id, r.stock_item_id, r.amount_scaled) for r in runs
    )
    item_qty_amount_keys = Counter(
        (r.voucher_master_id, r.stock_item_id, r.quantity_scaled, r.amount_scaled)
        for r in runs
    )

    print(f"\n[{label}] candidate_rows={len(runs)}")
    for voucher_type in voucher_types:
        vt_truth = [row for row in truth if row[1] == voucher_type]
        exact = 0
        item_amount = 0
        qty_amount = 0
        missing: list[tuple[int, str, int, int, int]] = []
        for mid, _vt, stock_id, quantity, rate, amount, item_name in vt_truth:
            if run_keys[(mid, stock_id, quantity, rate, amount)] > 0:
                exact += 1
            elif item_qty_amount_keys[(mid, stock_id, quantity, amount)] > 0:
                qty_amount += 1
            elif item_amount_keys[(mid, stock_id, amount)] > 0:
                item_amount += 1
            elif len(missing) < examples:
                missing.append((mid, item_name, quantity, rate, amount))

        denominator = len(vt_truth)
        pct = (100.0 * exact / denominator) if denominator else 0.0
        print(
            f"{voucher_type}: exact={exact}/{denominator} ({pct:.2f}%) "
            f"qty_amount_only={qty_amount} item_amount_only={item_amount}"
        )
        if missing:
            print("missing_examples=")
            for example in missing:
                print(example)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_sqlite", type=Path)
    parser.add_argument("--tranmgr", type=Path, required=True)
    parser.add_argument("--rosetta", type=Path, required=True)
    parser.add_argument(
        "--voucher-type",
        action="append",
        default=None,
        help="Voucher type to validate. Repeatable.",
    )
    parser.add_argument("--examples", type=int, default=8)
    args = parser.parse_args()

    voucher_types = args.voucher_type or ["Conversion Stock Journal", "Stock Journal"]

    decoded = sqlite3.connect(args.decoded_sqlite)
    rosetta = sqlite3.connect(args.rosetta)
    stock_name_to_id = load_rosetta_ids(rosetta, "stock_items")
    truth = load_truth(rosetta, stock_name_to_id, voucher_types)
    target_mids = {row[0] for row in truth}

    data = args.tranmgr.read_bytes()
    pages_by_mid = load_pages_by_mid(data, load_group_to_master_id(decoded))
    pages_by_mid = {mid: pages for mid, pages in pages_by_mid.items() if mid in target_mids}

    print(f"voucher_types={', '.join(voucher_types)}")
    print(f"truth_rows={len(truth)}")
    print(f"target_vouchers={len(target_mids)}")

    stock_ids = set(stock_name_to_id.values())
    post_rate_runs = parse_post_rate_subrecords(data, pages_by_mid, stock_ids)
    columnar_runs = parse_columnar_pairs(data, pages_by_mid, stock_ids)
    union_runs = list({run: None for run in [*post_rate_runs, *columnar_runs]}.keys())

    summarize("post_rate_f6", post_rate_runs, truth, voucher_types, args.examples)
    summarize("columnar_f6_44_46", columnar_runs, truth, voucher_types, args.examples)
    summarize("union", union_runs, truth, voucher_types, args.examples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

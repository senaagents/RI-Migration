from __future__ import annotations

import argparse
import sqlite3
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


PAGE_SIZE = 512
SCALE = Decimal(100000)
TYPE3_CELL_SIZE = 10
PAGE_HEADER_SIZE = 0x18
SPLIT_CONTINUATION_OFFSETS = (0x1C, PAGE_HEADER_SIZE, 0)


@dataclass(frozen=True)
class SalesRun:
    voucher_master_id: int
    page_no: int
    page_offset: int
    stock_item_id: int
    ledger_id: int
    unit_id: int
    amount_scaled: int
    quantity_scaled: int
    rate_scaled: int


def scaled(value: object) -> int:
    return int((Decimal(str(value)) * SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def guid_suffix_to_id(guid: str | None) -> int | None:
    if not guid:
        return None
    suffix = guid.rsplit("-", 1)[-1]
    try:
        return int(suffix, 16)
    except ValueError:
        return None


def load_rosetta_ids(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, guid in conn.execute(f"select name, guid from {table} where guid is not null"):
        master_id = guid_suffix_to_id(guid)
        if master_id is not None:
            out[name] = master_id
    return out


def load_group_to_master_id(decoded: sqlite3.Connection) -> dict[int, int]:
    return {
        int(group_id): int(master_id)
        for master_id, group_id in decoded.execute(
            """
            select master_id, page_group_id
            from voucher_header_candidates
            where page_group_id is not null
              and key_raw is not null
              and base_type_code != 31
            """
        )
    }


def find_nearby_ledger_for_amount(
    stream: bytes,
    start: int,
    amount_scaled: int,
    ledger_ids: set[int],
    search_bytes: int | None = None,
) -> int | None:
    end = len(stream) if search_bytes is None else min(len(stream), start + search_bytes)
    pos = 0 if search_bytes is None else start
    amount_bytes = struct.pack("<q", amount_scaled)
    candidates: list[tuple[int, int]] = []
    while True:
        marker = stream.find(b"\x00\x50\xff\x01\x00\x10", pos, end)
        if marker < 0:
            break
        block_end = min(end, marker + 160)
        amount_pos = stream.find(amount_bytes, marker, block_end)
        if amount_pos >= 0:
            ref_pos = stream.find(b"\x02\x00\x00\x03", marker, block_end)
            if ref_pos >= 0 and ref_pos + 8 <= len(stream):
                ledger_id = struct.unpack_from("<I", stream, ref_pos + 4)[0]
                if ledger_id in ledger_ids:
                    candidates.append((abs(amount_pos - start), ledger_id))
        pos = marker + 1

    # Some row/link sub-record headers are interrupted by 512-byte page headers.
    # The field-coded pair itself is still reliable:
    #   0x0002/0003 <ledger> ... 0x0002/0009 <amount>
    #
    # Do not scan raw u32s near the amount; arbitrary quantity/rate bytes can
    # have low16 values that collide with ledger master ids.
    window_start = 0 if search_bytes is None else max(0, start - 700)
    window_end = len(stream) if search_bytes is None else min(len(stream), start + search_bytes + 400)
    amount_marker = b"\x02\x00\x00\x09" + amount_bytes
    amount_marker_pos = window_start
    while True:
        amount_marker_pos = stream.find(amount_marker, amount_marker_pos, window_end)
        if amount_marker_pos < 0:
            break
        ref_pos = max(window_start, amount_marker_pos - 120)
        while True:
            ref_pos = stream.find(b"\x02\x00\x00\x03", ref_pos, amount_marker_pos)
            if ref_pos < 0:
                break
            if ref_pos + 8 <= len(stream):
                ledger_value = struct.unpack_from("<I", stream, ref_pos + 4)[0]
                ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
                if ledger_id is not None:
                    candidates.append((abs(amount_marker_pos - start), ledger_id))
            ref_pos += 1

        amount_marker_pos += 1

    d3_marker = b"\x00\x04\xd3\x52\x00\x09"
    d3_pos = window_start
    while True:
        d3_pos = stream.find(d3_marker, d3_pos, window_end)
        if d3_pos < 0:
            break
        if d3_pos + 18 <= len(stream) and stream[d3_pos + 10 : d3_pos + 18] == amount_bytes:
            ledger_value = struct.unpack_from("<I", stream, d3_pos + 6)[0]
            ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
            if ledger_id is not None:
                candidates.append((abs(d3_pos - start), ledger_id))
        d3_pos += 1

    if candidates:
        ledger_set = {ledger_id for _distance, ledger_id in candidates}
        if len(ledger_set) == 1:
            return next(iter(ledger_set))
        return None


def master_id_matches_packed_value(value: int, master_id: int) -> bool:
    """Compact fields sometimes pack flags/payload above a 16-bit master id."""
    if value == master_id:
        return True
    return master_id <= 0xFFFF and (value & 0xFFFF) == master_id


def resolve_compact_master_id(value: int, known_ids: set[int]) -> int | None:
    if value in known_ids:
        return value
    low16 = value & 0xFFFF
    if low16 in known_ids:
        return low16
    return None


def find_compact_u32(
    stream: bytes,
    marker: bytes,
    start: int,
    end: int,
    expected_master_id: int | None = None,
    allow_packed_low16: bool = False,
) -> tuple[int, int] | None:
    pos = start
    end = min(end, len(stream))
    while True:
        pos = stream.find(marker, pos, end)
        if pos < 0:
            return None
        if pos + 8 <= len(stream):
            value = struct.unpack_from("<I", stream, pos + 4)[0]
            if expected_master_id is None:
                return pos, value
            if value == expected_master_id or (
                allow_packed_low16
                and master_id_matches_packed_value(value, expected_master_id)
            ):
                return pos, value
        pos += 1


def find_compact_i64(
    stream: bytes,
    marker: bytes,
    start: int,
    end: int,
) -> tuple[int, int] | None:
    pos = stream.find(marker, start, min(end, len(stream)))
    if pos < 0 or pos + 12 > len(stream):
        return None
    return pos, struct.unpack_from("<q", stream, pos + 4)[0]


def find_rate_i64(
    stream: bytes,
    start: int,
    end: int,
) -> tuple[int, int] | None:
    exact = find_compact_i64(stream, b"\x02\x00\x00\x0c", start, end)
    if exact is not None:
        return exact
    pos = start
    end = min(end, len(stream))
    while True:
        pos = stream.find(b"\x00\x0c", pos, end)
        if pos < 0 or pos + 10 > len(stream):
            return None
        scale_pos = pos + 10
        if (
            scale_pos + 8 <= len(stream)
            and stream[scale_pos : scale_pos + 8] == b"\xa0\x86\x01\x00\x00\x00\x00\x00"
            and stream.find(b"\x02\x00", max(start, pos - 40), pos + 2) >= 0
        ):
            return pos, struct.unpack_from("<q", stream, pos + 2)[0]
        pos += 1


def _same_page_contiguous(origins: list[tuple[int, int]]) -> bool:
    if not origins:
        return False
    page = origins[0][0]
    offsets = [offset for origin_page, offset in origins if origin_page == page]
    return len(offsets) == len(origins) and offsets == list(range(offsets[0], offsets[0] + len(offsets)))


def read_logical_bytes_with_end(
    stream: bytes,
    origins: list[tuple[int, int]],
    start: int,
    length: int,
    *,
    previous_origin: tuple[int, int] | None = None,
) -> tuple[bytes, int, tuple[int, int]] | None:
    if start >= len(stream) or start >= len(origins):
        return None
    out = bytearray()
    idx = start
    last_page: int | None
    last_offset: int | None
    if previous_origin is None:
        last_page = None
        last_offset = None
    else:
        last_page, last_offset = previous_origin
    last_consumed_idx = start - 1
    search_end = min(len(stream), start + length + 96)
    while idx < search_end and len(out) < length:
        page, offset = origins[idx]
        use_byte = False
        if last_page is None:
            use_byte = True
        elif page == last_page and last_offset is not None and offset == last_offset + 1:
            use_byte = True
        elif page != last_page and last_offset == PAGE_SIZE - 1:
            use_byte = offset >= 0x1C

        if use_byte:
            out.append(stream[idx])
            last_page = page
            last_offset = offset
            last_consumed_idx = idx
        idx += 1
    if len(out) == length and last_page is not None and last_offset is not None:
        return bytes(out), last_consumed_idx + 1, (last_page, last_offset)
    return None


def _read_logical_split_bytes(
    stream: bytes,
    origins: list[tuple[int, int]],
    start: int,
    length: int,
) -> bytes | None:
    result = read_logical_bytes_with_end(stream, origins, start, length)
    if result is None:
        return None
    logical, _end, _origin = result
    return logical


def repair_split_i64(
    stream: bytes,
    field_pos: int,
    value: int,
    origins: list[tuple[int, int]],
    *,
    signed_low32: bool,
) -> int:
    value_start = field_pos + 4
    if value_start + 7 >= len(origins):
        return value

    marker_origins = origins[field_pos:value_start] if field_pos >= 0 else []
    if (
        len(marker_origins) == 4
        and marker_origins[-1][1] == PAGE_SIZE - 1
        and origins[value_start][0] != marker_origins[-1][0]
    ):
        # The 4-byte compact field marker can end exactly at the page edge.
        # In that layout the next page still exposes continuation/control bytes
        # before the value. The actual i64 resumes at offset 0x1c.
        marker_page = marker_origins[-1][0]
        search_end = min(len(origins) - 7, value_start + 64)
        for preferred_offset in SPLIT_CONTINUATION_OFFSETS:
            for idx in range(value_start, search_end):
                page, offset = origins[idx]
                if page == marker_page or offset != preferred_offset:
                    continue
                candidate_origins = origins[idx : idx + 8]
                if candidate_origins == [
                    (page, offset + delta) for delta in range(8)
                ]:
                    return struct.unpack("<q", stream[idx : idx + 8])[0]

    value_origins = origins[value_start : value_start + 8]
    if _same_page_contiguous(value_origins):
        return value
    logical = _read_logical_split_bytes(stream, origins, value_start, 8)
    if logical is not None:
        return struct.unpack("<q", logical)[0]

    low_origins = origins[value_start : value_start + 4]
    high_origins = origins[value_start + 4 : value_start + 8]
    if len({page for page, _offset in low_origins}) != 1:
        return value
    low_offsets = [offset for _page, offset in low_origins]
    if low_offsets != list(range(low_offsets[0], low_offsets[0] + 4)):
        return value
    if low_origins[-1][1] != PAGE_SIZE - 1:
        return value
    if high_origins[0][0] == low_origins[0][0]:
        return value

    low_page = low_origins[0][0]
    high_start = value_start + 4
    continuation_pos = None
    search_end = min(len(origins) - 3, high_start + 48)
    for preferred_offset in SPLIT_CONTINUATION_OFFSETS:
        for idx in range(high_start, search_end):
            page, offset = origins[idx]
            if page == low_page or offset != preferred_offset:
                continue
            candidate_origins = origins[idx : idx + 4]
            if candidate_origins == [
                (page, offset + delta) for delta in range(4)
            ]:
                continuation_pos = idx
                break
        if continuation_pos is not None:
            break
    if continuation_pos is not None and continuation_pos + 4 <= len(stream):
        raw = stream[value_start : value_start + 4] + stream[continuation_pos : continuation_pos + 4]
        return struct.unpack("<q", raw)[0]

    low_bytes = stream[value_start : value_start + 4]
    if signed_low32:
        return struct.unpack("<i", low_bytes)[0]
    return struct.unpack("<I", low_bytes)[0]


def iter_streams_for_pages(data: bytes, pages: list[int]) -> list[tuple[bytes, list[tuple[int, int]]]]:
    full_chunks = []
    full_origins: list[tuple[int, int]] = []
    body_chunks = []
    body_origins: list[tuple[int, int]] = []
    for page_no in pages:
        page = data[page_no * PAGE_SIZE : (page_no + 1) * PAGE_SIZE]
        full_chunks.append(page)
        full_origins.extend((page_no, page_offset) for page_offset in range(len(page)))
        body = page[PAGE_HEADER_SIZE:]
        body_chunks.append(body)
        body_origins.extend(
            (page_no, page_offset)
            for page_offset in range(PAGE_HEADER_SIZE, PAGE_HEADER_SIZE + len(body))
        )
    return [
        (b"".join(full_chunks), full_origins),
        (b"".join(body_chunks), body_origins),
    ]


def parse_sales_runs(
    data: bytes,
    group_to_mid: dict[int, int],
    stock_ids: set[int],
    ledger_ids: set[int],
) -> list[SalesRun]:
    runs: list[SalesRun] = []
    pages_by_mid: dict[int, list[int]] = defaultdict(list)
    for page_no in range(1, len(data) // PAGE_SIZE):
        page_start = page_no * PAGE_SIZE
        group_id = struct.unpack_from("<I", data, page_start + 4)[0]
        mid = group_to_mid.get(group_id)
        if mid is None:
            continue
        pages_by_mid[mid].append(page_no)

    for mid, pages in pages_by_mid.items():
        pages.sort()
        for stream, origins in iter_streams_for_pages(data, pages):
            pos = 0
            while True:
                pos = stream.find(b"\x02\x00\x00\x03", pos)
                if pos < 0:
                    break
                if pos + 120 > len(stream):
                    pos += 1
                    continue
                stock_id = struct.unpack_from("<I", stream, pos + 4)[0]
                if stock_id not in stock_ids:
                    pos += 1
                    continue

                second_field = stream[pos + TYPE3_CELL_SIZE : pos + TYPE3_CELL_SIZE + 4]
                if second_field not in (
                    b"\x69\x00\x00\x03",
                    b"\x68\x00\x00\x03",
                    b"\x6d\x00\x00\x03",
                ):
                    pos += 1
                    continue

                stock_repeat_search_start = pos + (
                    TYPE3_CELL_SIZE if second_field == b"\x6d\x00\x00\x03" else (TYPE3_CELL_SIZE * 2) - 2
                )
                stock_repeat_hit = find_compact_u32(
                    stream,
                    b"\x6d\x00\x00\x03",
                    stock_repeat_search_start,
                    pos + 100,
                    expected_master_id=stock_id,
                    allow_packed_low16=True,
                )
                if stock_repeat_hit is None and second_field not in (
                    b"\x69\x00\x00\x03",
                    b"\x68\x00\x00\x03",
                ):
                    pos += 1
                    continue
                if stock_repeat_hit is not None:
                    unit_search_start = stock_repeat_hit[0] + 8
                elif second_field == b"\x68\x00\x00\x03":
                    unit_search_start = pos + (TYPE3_CELL_SIZE * 2)
                else:
                    unit_search_start = pos + (TYPE3_CELL_SIZE * 2)

                unit_hit = find_compact_u32(
                    stream,
                    b"\x72\x00\x00\x03",
                    unit_search_start,
                    pos + 180,
                )
                if unit_hit is None:
                    pos += 1
                    continue
                unit_pos, unit_id = unit_hit

                amount_hit = find_compact_i64(
                    stream,
                    b"\x6e\x00\x00\x09",
                    unit_pos + TYPE3_CELL_SIZE,
                    unit_pos + 90,
                )
                if amount_hit is None:
                    pos += 1
                    continue
                amount_pos, amount_scaled = amount_hit
                amount_scaled = repair_split_i64(
                    stream,
                    amount_pos,
                    amount_scaled,
                    origins,
                    signed_low32=False,
                )

                quantity_hit = find_compact_i64(
                    stream,
                    b"\x66\x00\x00\x0b",
                    amount_pos + 12,
                    amount_pos + 120,
                )
                if quantity_hit is None:
                    pos += 1
                    continue
                qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = repair_split_i64(
                    stream,
                    qty_pos,
                    quantity_scaled,
                    origins,
                    signed_low32=True,
                )

                rate_hit = find_rate_i64(
                    stream,
                    qty_pos + 12,
                    qty_pos + 140,
                )
                if rate_hit is None:
                    pos += 1
                    continue
                _rate_pos, rate_scaled = rate_hit
                rate_scaled = repair_split_i64(
                    stream,
                    _rate_pos,
                    rate_scaled,
                    origins,
                    signed_low32=False,
                )

                if second_field == b"\x69\x00\x00\x03":
                    ledger_value = struct.unpack_from("<I", stream, pos + 14)[0]
                    ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
                    if ledger_id is None:
                        pos += 1
                        continue
                elif second_field in (b"\x68\x00\x00\x03", b"\x6d\x00\x00\x03"):
                    ledger_id = find_nearby_ledger_for_amount(stream, pos, amount_scaled, ledger_ids)
                    if ledger_id is None:
                        pos += 1
                        continue
                else:
                    pos += 1
                    continue
                page_no, page_offset = origins[pos]
                runs.append(
                    SalesRun(
                        voucher_master_id=mid,
                        page_no=page_no,
                        page_offset=page_offset,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=unit_id,
                        amount_scaled=amount_scaled,
                        quantity_scaled=quantity_scaled,
                        rate_scaled=rate_scaled,
                    )
                )
                pos += 1

            pos = 0
            while True:
                stock_repeat = stream.find(b"\x6d\x00\x00\x03", pos)
                if stock_repeat < 0:
                    break
                if stock_repeat + 120 > len(stream):
                    pos = stock_repeat + 1
                    continue
                stock_value = struct.unpack_from("<I", stream, stock_repeat + 4)[0]
                stock_id = resolve_compact_master_id(stock_value, stock_ids)
                if stock_id is None:
                    pos = stock_repeat + 1
                    continue

                unit_hit = find_compact_u32(
                    stream,
                    b"\x72\x00\x00\x03",
                    stock_repeat + 8,
                    stock_repeat + 160,
                )
                if unit_hit is None:
                    pos = stock_repeat + 1
                    continue
                unit_pos, unit_id = unit_hit
                amount_hit = find_compact_i64(
                    stream,
                    b"\x6e\x00\x00\x09",
                    unit_pos + TYPE3_CELL_SIZE,
                    unit_pos + 90,
                )
                if amount_hit is None:
                    pos = stock_repeat + 1
                    continue
                amount_pos, amount_scaled = amount_hit
                amount_scaled = repair_split_i64(
                    stream,
                    amount_pos,
                    amount_scaled,
                    origins,
                    signed_low32=False,
                )
                quantity_hit = find_compact_i64(
                    stream,
                    b"\x66\x00\x00\x0b",
                    amount_pos + 12,
                    amount_pos + 120,
                )
                if quantity_hit is None:
                    pos = stock_repeat + 1
                    continue
                qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = repair_split_i64(
                    stream,
                    qty_pos,
                    quantity_scaled,
                    origins,
                    signed_low32=True,
                )
                rate_hit = find_rate_i64(
                    stream,
                    qty_pos + 12,
                    qty_pos + 140,
                )
                if rate_hit is None:
                    pos = stock_repeat + 1
                    continue
                _rate_pos, rate_scaled = rate_hit
                rate_scaled = repair_split_i64(
                    stream,
                    _rate_pos,
                    rate_scaled,
                    origins,
                    signed_low32=False,
                )

                ledger_id = None
                lookback_start = max(0, stock_repeat - 140)
                scan = lookback_start
                while True:
                    ledger_pos = stream.find(b"\x69\x00\x00\x03", scan, stock_repeat)
                    if ledger_pos < 0:
                        break
                    ledger_value = struct.unpack_from("<I", stream, ledger_pos + 4)[0]
                    maybe_ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
                    if maybe_ledger_id is not None:
                        ledger_id = maybe_ledger_id
                    scan = ledger_pos + 1
                if ledger_id is None:
                    ledger_id = find_nearby_ledger_for_amount(
                        stream,
                        stock_repeat,
                        amount_scaled,
                        ledger_ids,
                    )
                if ledger_id is None:
                    pos = stock_repeat + 1
                    continue

                page_no, page_offset = origins[stock_repeat]
                runs.append(
                    SalesRun(
                        voucher_master_id=mid,
                        page_no=page_no,
                        page_offset=page_offset,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=unit_id,
                        amount_scaled=amount_scaled,
                        quantity_scaled=quantity_scaled,
                        rate_scaled=rate_scaled,
                    )
                )
                pos = stock_repeat + 1

            # Late-February sales rows can split the row into:
            #   stock + ledger + stock-repeat + rate
            # followed by a sub-record:
            #   00 50 f6 01 00 10 <len> ... 0002/0009 amount ... 0002/000b qty
            #
            # This is a narrower parser than the matrix scan: it requires the
            # row-local ledger id and exact qty * rate == amount consistency.
            pos = 0
            while True:
                pos = stream.find(b"\x02\x00\x00\x03", pos)
                if pos < 0:
                    break
                if pos + 80 > len(stream):
                    pos += 1
                    continue
                stock_id = struct.unpack_from("<I", stream, pos + 4)[0]
                if stock_id not in stock_ids:
                    pos += 1
                    continue
                second_field = stream[pos + TYPE3_CELL_SIZE : pos + TYPE3_CELL_SIZE + 4]
                if second_field not in (b"\x69\x00\x00\x03", b"\x68\x00\x00\x03"):
                    pos += 1
                    continue
                ledger_id = None
                if second_field == b"\x69\x00\x00\x03":
                    ledger_value = struct.unpack_from("<I", stream, pos + TYPE3_CELL_SIZE + 4)[0]
                    ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
                    if ledger_id is None:
                        pos += 1
                        continue
                else:
                    second_value = struct.unpack_from("<I", stream, pos + TYPE3_CELL_SIZE + 4)[0]
                    if resolve_compact_master_id(second_value, {stock_id}) != stock_id:
                        pos += 1
                        continue

                stock_repeat_hit = find_compact_u32(
                    stream,
                    b"\x6d\x00\x00\x03",
                    pos + (TYPE3_CELL_SIZE * 2),
                    pos + 60,
                    expected_master_id=stock_id,
                    allow_packed_low16=True,
                )
                if stock_repeat_hit is None:
                    pos += 1
                    continue

                rate_hit = find_rate_i64(
                    stream,
                    stock_repeat_hit[0] + TYPE3_CELL_SIZE,
                    stock_repeat_hit[0] + 100,
                )
                if rate_hit is None:
                    pos += 1
                    continue
                rate_pos, rate_scaled = rate_hit
                rate_scaled = repair_split_i64(
                    stream,
                    rate_pos,
                    rate_scaled,
                    origins,
                    signed_low32=False,
                )

                subrec_pos = stream.find(
                    b"\x00\x50\xf6\x01\x00\x10",
                    rate_pos + 12,
                    min(len(stream), rate_pos + 700),
                )
                if subrec_pos < 0 or subrec_pos + 10 > len(stream):
                    pos += 1
                    continue
                subrec_len = struct.unpack_from("<I", stream, subrec_pos + 6)[0]
                if not 0 < subrec_len < 2000:
                    pos += 1
                    continue
                subrec_end = min(len(stream), subrec_pos + 10 + subrec_len)

                amount_hit = find_compact_i64(
                    stream,
                    b"\x02\x00\x00\x09",
                    subrec_pos + 10,
                    subrec_end,
                )
                quantity_hit = find_compact_i64(
                    stream,
                    b"\x02\x00\x00\x0b",
                    subrec_pos + 10,
                    subrec_end,
                )
                if amount_hit is None or quantity_hit is None:
                    pos += 1
                    continue

                amount_pos, amount_scaled = amount_hit
                amount_scaled = repair_split_i64(
                    stream,
                    amount_pos,
                    amount_scaled,
                    origins,
                    signed_low32=False,
                )
                qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = abs(
                    repair_split_i64(
                        stream,
                        qty_pos,
                        quantity_scaled,
                        origins,
                        signed_low32=True,
                    )
                )
                if quantity_scaled == 0 or rate_scaled == 0:
                    pos += 1
                    continue
                if quantity_scaled * rate_scaled != amount_scaled * int(SCALE):
                    pos += 1
                    continue
                if ledger_id is None:
                    ledger_id = find_nearby_ledger_for_amount(
                        stream,
                        pos,
                        amount_scaled,
                        ledger_ids,
                    )
                    if ledger_id is None:
                        pos += 1
                        continue

                page_no, page_offset = origins[pos]
                runs.append(
                    SalesRun(
                        voucher_master_id=mid,
                        page_no=page_no,
                        page_offset=page_offset,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=0,
                        amount_scaled=amount_scaled,
                        quantity_scaled=quantity_scaled,
                        rate_scaled=rate_scaled,
                    )
                )
                pos += 1
    return runs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_sqlite", type=Path)
    parser.add_argument("--tranmgr", type=Path, required=True)
    parser.add_argument("--rosetta", type=Path, required=True)
    parser.add_argument("--voucher-type", default="61 Sales")
    parser.add_argument("--examples", type=int, default=8)
    args = parser.parse_args()

    decoded = sqlite3.connect(args.decoded_sqlite)
    rosetta = sqlite3.connect(args.rosetta)
    group_to_mid = load_group_to_master_id(decoded)
    stock_name_to_id = load_rosetta_ids(rosetta, "stock_items")
    ledger_name_to_id = load_rosetta_ids(rosetta, "ledgers")

    data = args.tranmgr.read_bytes()
    runs = parse_sales_runs(
        data,
        group_to_mid,
        set(stock_name_to_id.values()),
        set(ledger_name_to_id.values()),
    )

    run_keys = Counter(
        (
            r.voucher_master_id,
            r.stock_item_id,
            r.ledger_id,
            abs(r.quantity_scaled),
            r.rate_scaled,
            r.amount_scaled,
        )
        for r in runs
    )
    run_item_amount_keys = Counter(
        (r.voucher_master_id, r.stock_item_id, r.amount_scaled) for r in runs
    )

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

    exact = 0
    item_amount = 0
    missing_examples = []
    for mid, stock_id, ledger_id, quantity, rate, amount, item_name, ledger_name in truth:
        key = (mid, stock_id, ledger_id, quantity, rate, amount)
        if run_keys[key] > 0:
            exact += 1
        elif run_item_amount_keys[(mid, stock_id, amount)] > 0:
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
    print(f"all_sales_pattern_runs={len(runs)}")
    print(f"type_sales_pattern_runs={len(type_runs)}")
    print(f"exact_qty_rate_amount_ledger_matches={exact}/{len(truth)}")
    print(f"item_amount_matches_without_full_tuple={item_amount}/{len(truth)}")
    print("missing_examples=")
    for example in missing_examples:
        print(example)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

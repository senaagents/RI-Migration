from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


PAGE_SIZE = 512
PAGE_HEADER_SIZE = 0x18
SCALE = Decimal(100000)
TYPE3_CELL_SIZE = 10
SPLIT_CONTINUATION_OFFSETS = (0x1C, PAGE_HEADER_SIZE, 0)
SUBREC_F6 = b"\x00\x50\xf6\x01\x00\x10"
STOCK_FIELD = b"\x02\x00\x00\x03"
SQLITE_INT64_MIN = -(1 << 63)
SQLITE_INT64_MAX = (1 << 63) - 1


@dataclass(frozen=True)
class InventoryRun:
    voucher_master_id: int
    stock_item_id: int
    ledger_id: int | None
    unit_id: int | None
    quantity_scaled: int
    rate_scaled: int
    amount_scaled: int
    source: str
    confidence: str
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


def scaled(value: object) -> int:
    return int((Decimal(str(value)) * SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fits_sqlite_int64(value: int) -> bool:
    return SQLITE_INT64_MIN <= value <= SQLITE_INT64_MAX


def valid_scaled_fields(row: InventoryRun) -> bool:
    return (
        fits_sqlite_int64(row.quantity_scaled)
        and fits_sqlite_int64(row.rate_scaled)
        and fits_sqlite_int64(row.amount_scaled)
    )


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
        marker_page = marker_origins[-1][0]
        search_end = min(len(origins) - 7, value_start + 64)
        for preferred_offset in SPLIT_CONTINUATION_OFFSETS:
            for idx in range(value_start, search_end):
                page, offset = origins[idx]
                if page == marker_page or offset != preferred_offset:
                    continue
                candidate_origins = origins[idx : idx + 8]
                if candidate_origins == [(page, offset + delta) for delta in range(8)]:
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
            if candidate_origins == [(page, offset + delta) for delta in range(4)]:
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


def master_id_matches_packed_value(value: int, master_id: int) -> bool:
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
                allow_packed_low16 and master_id_matches_packed_value(value, expected_master_id)
            ):
                return pos, value
        pos += 1


def find_compact_i64(stream: bytes, marker: bytes, start: int, end: int) -> tuple[int, int] | None:
    pos = stream.find(marker, start, min(end, len(stream)))
    if pos < 0 or pos + 12 > len(stream):
        return None
    return pos, struct.unpack_from("<q", stream, pos + 4)[0]


def find_rate_i64(stream: bytes, start: int, end: int) -> tuple[int, int] | None:
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


def find_preceding_ledger_for_amount(
    stream: bytes,
    row_pos: int,
    amount_scaled: int,
    ledger_ids: set[int],
    *,
    lookback: int = 900,
) -> int | None:
    """Find the closest preceding ledger allocation carrying this positive amount."""
    amount_bytes = struct.pack("<q", amount_scaled)
    start = max(0, row_pos - lookback)
    end = min(len(stream), row_pos)
    candidates: list[tuple[int, int]] = []

    amount_marker = b"\x02\x00\x00\x09" + amount_bytes
    amount_pos = start
    while True:
        amount_pos = stream.find(amount_marker, amount_pos, end)
        if amount_pos < 0:
            break
        ref_pos = max(start, amount_pos - 140)
        closest_ref: tuple[int, int] | None = None
        while True:
            ref_pos = stream.find(STOCK_FIELD, ref_pos, amount_pos)
            if ref_pos < 0:
                break
            if ref_pos + 8 <= len(stream):
                ledger_value = struct.unpack_from("<I", stream, ref_pos + 4)[0]
                ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
                if ledger_id is not None:
                    closest_ref = (ref_pos, ledger_id)
            ref_pos += 1
        if closest_ref is not None:
            ref_distance_pos, ledger_id = closest_ref
            candidates.append((row_pos - ref_distance_pos, ledger_id))
        amount_pos += 1

    d3_pos = start
    d3_marker = b"\x00\x04\xd3\x52\x00\x09"
    while True:
        d3_pos = stream.find(d3_marker, d3_pos, end)
        if d3_pos < 0:
            break
        if d3_pos + 18 <= len(stream) and stream[d3_pos + 10 : d3_pos + 18] == amount_bytes:
            ledger_value = struct.unpack_from("<I", stream, d3_pos + 6)[0]
            ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
            if ledger_id is not None:
                candidates.append((row_pos - d3_pos, ledger_id))
        d3_pos += 1

    if not candidates:
        return None
    candidates.sort()
    nearest_distance = candidates[0][0]
    nearest_ledgers = {ledger_id for distance, ledger_id in candidates if distance == nearest_distance}
    if len(nearest_ledgers) == 1:
        return next(iter(nearest_ledgers))
    return None


def parse_sales_logical_compact_rows(
    data: bytes,
    pages_by_mid: dict[int, list[int]],
    stock_ids: set[int],
    ledger_ids: set[int],
    unit_ids: set[int] | None = None,
) -> list[InventoryRun]:
    """Recover Sales compact rows whose marker/value run crosses a page header."""
    runs: list[InventoryRun] = []
    seen: set[tuple[int, int, int, int, int, int, int | None, int, int]] = set()

    for mid, pages in pages_by_mid.items():
        for stream, origins in iter_streams_for_pages(data, sorted(pages)):
            positions: set[int] = set()
            pos = 0
            while True:
                pos = stream.find(STOCK_FIELD, pos)
                if pos < 0:
                    break
                if pos + 8 <= len(stream):
                    stock_id = resolve_compact_master_id(
                        struct.unpack_from("<I", stream, pos + 4)[0],
                        stock_ids,
                    )
                    if stock_id is not None:
                        positions.add(pos)
                pos += 1

            # If the 4-byte marker or following u32 value reaches the page edge,
            # raw stream reads see page-header bytes. Logical reads rejoin the
            # same compact row after the next page header.
            for idx, (_page, offset) in enumerate(origins):
                if offset < PAGE_SIZE - 6:
                    continue
                logical = read_logical_bytes_with_end(stream, origins, idx, 8)
                if logical is None or logical[0][:4] != STOCK_FIELD:
                    continue
                stock_id = resolve_compact_master_id(
                    struct.unpack_from("<I", logical[0], 4)[0],
                    stock_ids,
                )
                if stock_id is not None:
                    positions.add(idx)

            for pos in sorted(positions):
                logical = read_logical_bytes_with_end(stream, origins, pos, 260)
                if logical is None:
                    continue
                row, end_idx, _last_origin = logical
                if len({page for page, _offset in origins[pos:min(end_idx, len(origins))]}) < 2:
                    continue
                if row[:4] != STOCK_FIELD:
                    continue
                stock_id = resolve_compact_master_id(
                    struct.unpack_from("<I", row, 4)[0],
                    stock_ids,
                )
                if stock_id is None:
                    continue

                second_field = row[TYPE3_CELL_SIZE : TYPE3_CELL_SIZE + 4]
                if second_field not in (
                    b"\x69\x00\x00\x03",
                    b"\x68\x00\x00\x03",
                    b"\x6d\x00\x00\x03",
                ):
                    continue
                if second_field == b"\x68\x00\x00\x03":
                    second_value = struct.unpack_from("<I", row, TYPE3_CELL_SIZE + 4)[0]
                    if resolve_compact_master_id(second_value, {stock_id}) != stock_id:
                        continue

                stock_repeat_search_start = (
                    TYPE3_CELL_SIZE
                    if second_field == b"\x6d\x00\x00\x03"
                    else (TYPE3_CELL_SIZE * 2) - 2
                )
                stock_repeat_pos = row.find(
                    b"\x6d\x00\x00\x03" + struct.pack("<I", stock_id),
                    stock_repeat_search_start,
                    100,
                )
                if (
                    stock_repeat_pos < 0
                    and second_field not in (b"\x69\x00\x00\x03", b"\x68\x00\x00\x03")
                ):
                    continue

                unit_search_start = (
                    stock_repeat_pos + 8
                    if stock_repeat_pos >= 0
                    else TYPE3_CELL_SIZE * 2
                )
                unit_pos = row.find(b"\x72\x00\x00\x03", unit_search_start, 180)
                if unit_pos < 0 or unit_pos + 8 > len(row):
                    continue
                unit_id = struct.unpack_from("<I", row, unit_pos + 4)[0]
                if unit_ids is not None and unit_id not in unit_ids:
                    continue

                amount_hit = find_compact_i64(
                    row,
                    b"\x6e\x00\x00\x09",
                    unit_pos + TYPE3_CELL_SIZE,
                    unit_pos + 90,
                )
                if amount_hit is None:
                    continue
                amount_pos, amount_scaled = amount_hit
                if amount_scaled <= 0:
                    continue

                quantity_hit = find_compact_i64(
                    row,
                    b"\x66\x00\x00\x0b",
                    amount_pos + 12,
                    amount_pos + 120,
                )
                if quantity_hit is None:
                    continue
                qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = abs(quantity_scaled)
                if quantity_scaled == 0:
                    continue

                rate_hit = find_rate_i64(row, qty_pos + 12, qty_pos + 140)
                if rate_hit is None:
                    continue
                _rate_pos, rate_scaled = rate_hit
                rate_scaled = abs(rate_scaled)
                if rate_scaled == 0:
                    continue

                if second_field == b"\x69\x00\x00\x03":
                    ledger_value = struct.unpack_from("<I", row, TYPE3_CELL_SIZE + 4)[0]
                    ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
                else:
                    ledger_id = find_nearby_ledger_for_amount(
                        stream,
                        pos,
                        amount_scaled,
                        ledger_ids,
                    )
                    if ledger_id is None:
                        ledger_id = find_preceding_ledger_for_amount(
                            stream,
                            pos,
                            amount_scaled,
                            ledger_ids,
                        )
                if ledger_id is None:
                    continue

                page_no, page_offset = origins[pos]
                key = (
                    mid,
                    stock_id,
                    ledger_id,
                    quantity_scaled,
                    rate_scaled,
                    amount_scaled,
                    unit_id,
                    page_no,
                    page_offset,
                )
                if key in seen:
                    continue
                seen.add(key)
                runs.append(
                    InventoryRun(
                        voucher_master_id=mid,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=unit_id,
                        quantity_scaled=quantity_scaled,
                        rate_scaled=rate_scaled,
                        amount_scaled=amount_scaled,
                        source="sales_logical_compact",
                        confidence="high",
                        page_no=page_no,
                        page_offset=page_offset,
                    )
                )
    return runs


def parse_sales_logical_post_rate_f6_rows(
    data: bytes,
    pages_by_mid: dict[int, list[int]],
    stock_ids: set[int],
    ledger_ids: set[int],
    unit_ids: set[int] | None = None,
) -> list[InventoryRun]:
    """Recover page-edge Sales rows where the stock marker is followed by an F6 value subrecord."""
    runs: list[InventoryRun] = []
    seen: set[tuple[int, int, int, int, int, int, int | None, int, int]] = set()

    for mid, pages in pages_by_mid.items():
        for stream, origins in iter_streams_for_pages(data, sorted(pages)):
            positions: set[int] = set()
            for idx, (_page, offset) in enumerate(origins):
                if offset < PAGE_SIZE - len(STOCK_FIELD):
                    continue
                logical = read_logical_bytes_with_end(stream, origins, idx, len(STOCK_FIELD) + 4)
                if logical is None or logical[0][:4] != STOCK_FIELD:
                    continue
                stock_id = resolve_compact_master_id(
                    struct.unpack_from("<I", logical[0], 4)[0],
                    stock_ids,
                )
                if stock_id is not None:
                    positions.add(idx)

            for pos in sorted(positions):
                logical = read_logical_bytes_with_end(stream, origins, pos, 900)
                if logical is None:
                    continue
                row = logical[0]
                if row[:4] != STOCK_FIELD:
                    continue
                stock_id = resolve_compact_master_id(
                    struct.unpack_from("<I", row, 4)[0],
                    stock_ids,
                )
                if stock_id is None:
                    continue

                second_field = row[TYPE3_CELL_SIZE : TYPE3_CELL_SIZE + 4]
                if second_field not in (b"\x69\x00\x00\x03", b"\x68\x00\x00\x03"):
                    continue
                ledger_id = None
                if second_field == b"\x69\x00\x00\x03":
                    ledger_value = struct.unpack_from("<I", row, TYPE3_CELL_SIZE + 4)[0]
                    ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
                    if ledger_id is None:
                        continue
                else:
                    second_value = struct.unpack_from("<I", row, TYPE3_CELL_SIZE + 4)[0]
                    if resolve_compact_master_id(second_value, {stock_id}) != stock_id:
                        continue

                stock_repeat_pos = row.find(
                    b"\x6d\x00\x00\x03" + struct.pack("<I", stock_id),
                    TYPE3_CELL_SIZE + 8,
                    90,
                )
                if stock_repeat_pos < 0:
                    continue

                rate_hit = find_rate_i64(row, stock_repeat_pos + TYPE3_CELL_SIZE, stock_repeat_pos + 120)
                if rate_hit is None:
                    continue
                rate_pos, rate_scaled = rate_hit
                rate_scaled = abs(rate_scaled)
                if rate_scaled == 0:
                    continue

                if rate_pos + 28 > len(row):
                    continue
                unit_id = struct.unpack_from("<I", row, rate_pos + 20)[0]
                if unit_ids is not None and unit_id not in unit_ids:
                    continue

                subrec_pos = row.find(SUBREC_F6, rate_pos + 12, min(len(row), rate_pos + 760))
                if subrec_pos < 0 or subrec_pos + 10 > len(row):
                    continue
                subrec_len = struct.unpack_from("<I", row, subrec_pos + 6)[0]
                if not 0 < subrec_len < 2000:
                    continue
                subrec_end = min(len(row), subrec_pos + 10 + subrec_len)
                amount_hit = find_compact_i64(row, b"\x02\x00\x00\x09", subrec_pos + 10, subrec_end)
                quantity_hit = find_compact_i64(row, b"\x02\x00\x00\x0b", subrec_pos + 10, subrec_end)
                if amount_hit is None or quantity_hit is None:
                    continue

                _amount_pos, amount_scaled = amount_hit
                if amount_scaled <= 0:
                    continue
                _qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = abs(quantity_scaled)
                if quantity_scaled == 0:
                    continue
                if quantity_scaled * rate_scaled != amount_scaled * int(SCALE):
                    continue

                if ledger_id is None:
                    ledger_id = find_nearby_ledger_for_amount(stream, pos, amount_scaled, ledger_ids)
                    if ledger_id is None:
                        ledger_id = find_preceding_ledger_for_amount(stream, pos, amount_scaled, ledger_ids)
                if ledger_id is None:
                    continue

                page_no, page_offset = origins[pos]
                key = (
                    mid,
                    stock_id,
                    ledger_id,
                    quantity_scaled,
                    rate_scaled,
                    amount_scaled,
                    unit_id,
                    page_no,
                    page_offset,
                )
                if key in seen:
                    continue
                seen.add(key)
                runs.append(
                    InventoryRun(
                        voucher_master_id=mid,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=unit_id,
                        quantity_scaled=quantity_scaled,
                        rate_scaled=rate_scaled,
                        amount_scaled=amount_scaled,
                        source="sales_logical_post_rate_f6",
                        confidence="high",
                        page_no=page_no,
                        page_offset=page_offset,
                    )
                )
    return runs


def parse_sales_rows(
    data: bytes,
    group_to_mid: dict[int, int],
    stock_ids: set[int],
    ledger_ids: set[int],
    target_mids: set[int] | None = None,
    unit_ids: set[int] | None = None,
    include_logical_post_rate_f6: bool = True,
) -> list[InventoryRun]:
    runs: list[InventoryRun] = []
    pages_by_mid = load_pages_by_mid(data, group_to_mid)
    if target_mids is not None:
        pages_by_mid = {mid: pages for mid, pages in pages_by_mid.items() if mid in target_mids}

    for mid, pages in pages_by_mid.items():
        pages.sort()
        for stream, origins in iter_streams_for_pages(data, pages):
            pos = 0
            while True:
                pos = stream.find(STOCK_FIELD, pos)
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
                if second_field not in (b"\x69\x00\x00\x03", b"\x68\x00\x00\x03", b"\x6d\x00\x00\x03"):
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
                if stock_repeat_hit is None and second_field not in (b"\x69\x00\x00\x03", b"\x68\x00\x00\x03"):
                    pos += 1
                    continue
                unit_search_start = (
                    stock_repeat_hit[0] + 8 if stock_repeat_hit is not None else pos + (TYPE3_CELL_SIZE * 2)
                )

                unit_hit = find_compact_u32(stream, b"\x72\x00\x00\x03", unit_search_start, pos + 180)
                if unit_hit is None:
                    pos += 1
                    continue
                unit_pos, unit_id = unit_hit

                amount_hit = find_compact_i64(stream, b"\x6e\x00\x00\x09", unit_pos + TYPE3_CELL_SIZE, unit_pos + 90)
                if amount_hit is None:
                    pos += 1
                    continue
                amount_pos, amount_scaled = amount_hit
                amount_scaled = repair_split_i64(stream, amount_pos, amount_scaled, origins, signed_low32=False)

                quantity_hit = find_compact_i64(stream, b"\x66\x00\x00\x0b", amount_pos + 12, amount_pos + 120)
                if quantity_hit is None:
                    pos += 1
                    continue
                qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = repair_split_i64(stream, qty_pos, quantity_scaled, origins, signed_low32=True)

                rate_hit = find_rate_i64(stream, qty_pos + 12, qty_pos + 140)
                if rate_hit is None:
                    pos += 1
                    continue
                rate_pos, rate_scaled = rate_hit
                rate_scaled = repair_split_i64(stream, rate_pos, rate_scaled, origins, signed_low32=False)

                if second_field == b"\x69\x00\x00\x03":
                    ledger_value = struct.unpack_from("<I", stream, pos + 14)[0]
                    ledger_id = resolve_compact_master_id(ledger_value, ledger_ids)
                else:
                    ledger_id = find_nearby_ledger_for_amount(stream, pos, amount_scaled, ledger_ids)
                if ledger_id is None:
                    pos += 1
                    continue

                page_no, page_offset = origins[pos]
                runs.append(
                    InventoryRun(
                        voucher_master_id=mid,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=unit_id,
                        quantity_scaled=abs(quantity_scaled),
                        rate_scaled=rate_scaled,
                        amount_scaled=amount_scaled,
                        source="sales_compact",
                        confidence="high",
                        page_no=page_no,
                        page_offset=page_offset,
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

                unit_hit = find_compact_u32(stream, b"\x72\x00\x00\x03", stock_repeat + 8, stock_repeat + 160)
                if unit_hit is None:
                    pos = stock_repeat + 1
                    continue
                unit_pos, unit_id = unit_hit
                amount_hit = find_compact_i64(stream, b"\x6e\x00\x00\x09", unit_pos + TYPE3_CELL_SIZE, unit_pos + 90)
                if amount_hit is None:
                    pos = stock_repeat + 1
                    continue
                amount_pos, amount_scaled = amount_hit
                amount_scaled = repair_split_i64(stream, amount_pos, amount_scaled, origins, signed_low32=False)
                quantity_hit = find_compact_i64(stream, b"\x66\x00\x00\x0b", amount_pos + 12, amount_pos + 120)
                if quantity_hit is None:
                    pos = stock_repeat + 1
                    continue
                qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = repair_split_i64(stream, qty_pos, quantity_scaled, origins, signed_low32=True)
                rate_hit = find_rate_i64(stream, qty_pos + 12, qty_pos + 140)
                if rate_hit is None:
                    pos = stock_repeat + 1
                    continue
                rate_pos, rate_scaled = rate_hit
                rate_scaled = repair_split_i64(stream, rate_pos, rate_scaled, origins, signed_low32=False)

                ledger_id = None
                scan = max(0, stock_repeat - 140)
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
                    ledger_id = find_nearby_ledger_for_amount(stream, stock_repeat, amount_scaled, ledger_ids)
                if ledger_id is None:
                    pos = stock_repeat + 1
                    continue

                page_no, page_offset = origins[stock_repeat]
                runs.append(
                    InventoryRun(
                        voucher_master_id=mid,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=unit_id,
                        quantity_scaled=abs(quantity_scaled),
                        rate_scaled=rate_scaled,
                        amount_scaled=amount_scaled,
                        source="sales_compact_repeat",
                        confidence="high",
                        page_no=page_no,
                        page_offset=page_offset,
                    )
                )
                pos = stock_repeat + 1

            pos = 0
            while True:
                pos = stream.find(STOCK_FIELD, pos)
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

                rate_hit = find_rate_i64(stream, stock_repeat_hit[0] + TYPE3_CELL_SIZE, stock_repeat_hit[0] + 100)
                if rate_hit is None:
                    pos += 1
                    continue
                rate_pos, rate_scaled = rate_hit
                rate_scaled = repair_split_i64(stream, rate_pos, rate_scaled, origins, signed_low32=False)

                subrec_pos = stream.find(SUBREC_F6, rate_pos + 12, min(len(stream), rate_pos + 700))
                if subrec_pos < 0 or subrec_pos + 10 > len(stream):
                    pos += 1
                    continue
                subrec_len = struct.unpack_from("<I", stream, subrec_pos + 6)[0]
                if not 0 < subrec_len < 2000:
                    pos += 1
                    continue
                subrec_end = min(len(stream), subrec_pos + 10 + subrec_len)

                amount_hit = find_compact_i64(stream, b"\x02\x00\x00\x09", subrec_pos + 10, subrec_end)
                quantity_hit = find_compact_i64(stream, b"\x02\x00\x00\x0b", subrec_pos + 10, subrec_end)
                if amount_hit is None or quantity_hit is None:
                    pos += 1
                    continue

                amount_pos, amount_scaled = amount_hit
                amount_scaled = repair_split_i64(stream, amount_pos, amount_scaled, origins, signed_low32=False)
                qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = abs(repair_split_i64(stream, qty_pos, quantity_scaled, origins, signed_low32=True))
                if quantity_scaled == 0 or rate_scaled == 0:
                    pos += 1
                    continue
                if quantity_scaled * rate_scaled != amount_scaled * int(SCALE):
                    pos += 1
                    continue
                if ledger_id is None:
                    ledger_id = find_nearby_ledger_for_amount(stream, pos, amount_scaled, ledger_ids)
                    if ledger_id is None:
                        pos += 1
                        continue

                page_no, page_offset = origins[pos]
                runs.append(
                    InventoryRun(
                        voucher_master_id=mid,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=None,
                        quantity_scaled=quantity_scaled,
                        rate_scaled=rate_scaled,
                        amount_scaled=amount_scaled,
                        source="sales_post_rate_f6",
                        confidence="high",
                        page_no=page_no,
                        page_offset=page_offset,
                    )
                )
                pos += 1

            pos = 0
            while True:
                pos = stream.find(STOCK_FIELD, pos)
                if pos < 0:
                    break
                if pos + 120 > len(stream):
                    pos += 1
                    continue
                stock_id = struct.unpack_from("<I", stream, pos + 4)[0]
                if stock_id not in stock_ids:
                    pos += 1
                    continue

                rate_marker_pos = pos + TYPE3_CELL_SIZE
                if stream[rate_marker_pos : rate_marker_pos + 4] != b"\x02\x00\x00\x0c":
                    pos += 1
                    continue
                if rate_marker_pos + 28 > len(stream):
                    pos += 1
                    continue
                rate_scaled = struct.unpack_from("<q", stream, rate_marker_pos + 4)[0]
                rate_scaled = repair_split_i64(stream, rate_marker_pos, rate_scaled, origins, signed_low32=False)
                if rate_scaled == 0:
                    pos += 1
                    continue

                unit_id = struct.unpack_from("<I", stream, rate_marker_pos + 20)[0]
                if unit_ids is not None and unit_id not in unit_ids:
                    pos += 1
                    continue

                subrec_pos = stream.find(SUBREC_F6, rate_marker_pos + 12, min(len(stream), rate_marker_pos + 320))
                if subrec_pos < 0 or subrec_pos + 10 > len(stream):
                    pos += 1
                    continue
                subrec_len = struct.unpack_from("<I", stream, subrec_pos + 6)[0]
                if not 0 < subrec_len < 2000:
                    pos += 1
                    continue
                subrec_end = min(len(stream), subrec_pos + 10 + subrec_len)
                amount_hit = find_compact_i64(stream, b"\x02\x00\x00\x09", subrec_pos + 10, subrec_end)
                quantity_hit = find_compact_i64(stream, b"\x02\x00\x00\x0b", subrec_pos + 10, subrec_end)
                if amount_hit is None or quantity_hit is None:
                    pos += 1
                    continue
                amount_pos, amount_scaled = amount_hit
                amount_scaled = repair_split_i64(stream, amount_pos, amount_scaled, origins, signed_low32=False)
                qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = abs(repair_split_i64(stream, qty_pos, quantity_scaled, origins, signed_low32=True))
                if quantity_scaled == 0:
                    pos += 1
                    continue
                if quantity_scaled * rate_scaled != amount_scaled * int(SCALE):
                    pos += 1
                    continue

                ledger_id = find_preceding_ledger_for_amount(stream, pos, amount_scaled, ledger_ids)
                if ledger_id is None:
                    pos += 1
                    continue

                page_no, page_offset = origins[pos]
                runs.append(
                    InventoryRun(
                        voucher_master_id=mid,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=unit_id,
                        quantity_scaled=quantity_scaled,
                        rate_scaled=rate_scaled,
                        amount_scaled=amount_scaled,
                        source="sales_stock_rate_f6",
                        confidence="high",
                        page_no=page_no,
                        page_offset=page_offset,
                    )
                )
                pos += 1
    runs.extend(
        parse_sales_logical_compact_rows(
            data,
            pages_by_mid,
            stock_ids,
            ledger_ids,
            unit_ids,
        )
    )
    if include_logical_post_rate_f6:
        runs.extend(
            parse_sales_logical_post_rate_f6_rows(
                data,
                pages_by_mid,
                stock_ids,
                ledger_ids,
                unit_ids,
            )
        )
    return runs


def iter_logical_f6_blocks(stream: bytes, origins: list[tuple[int, int]]) -> list[LogicalF6Block]:
    positions: set[int] = set()
    pos = 0
    while True:
        pos = stream.find(SUBREC_F6, pos)
        if pos < 0:
            break
        positions.add(pos)
        pos += 1

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
        body = read_logical_bytes_with_end(stream, origins, body_start, subrec_len, previous_origin=last_origin)
        if body is None:
            continue
        page_no, page_offset = origins[subrec_pos]
        blocks.append(LogicalF6Block(stream_pos=subrec_pos, page_no=page_no, page_offset=page_offset, body=body[0]))
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


def find_preceding_stock_rates(
    stream: bytes,
    origins: list[tuple[int, int]],
    subrec_pos: int,
    stock_ids: set[int],
    *,
    lookback: int = 360,
) -> list[tuple[int, int, int]]:
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
            rate = repair_split_i64(stream, rate_pos, raw_rate, origins, signed_low32=False)
            if rate:
                candidates.append((pos, stock_id, abs(rate)))
        else:
            candidates.append((pos, stock_id, 0))
        pos += 1
    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates


def parse_post_rate_subrecords(
    data: bytes,
    pages_by_mid: dict[int, list[int]],
    stock_ids: set[int],
) -> list[InventoryRun]:
    runs: list[InventoryRun] = []
    seen: set[tuple[int, int, int, int, int, str, int, int]] = set()

    for mid, pages in pages_by_mid.items():
        for stream, origins in iter_streams_for_pages(data, sorted(pages)):
            for block in iter_logical_f6_blocks(stream, origins):
                amounts = iter_body_compact_i64_values(block.body, b"\x02\x00\x00\x09")
                quantities = iter_body_compact_i64_values(block.body, b"\x02\x00\x00\x0b")
                if not amounts or not quantities:
                    continue
                _amount_pos, amount = amounts[0]
                _qty_pos, quantity = quantities[0]
                quantity = abs(quantity)
                if quantity == 0 or amount == 0:
                    continue

                # Production export uses only the nearest preceding stock row
                # and its own rate. Wider top-N/derived-rate probes recover a
                # few rows but create far more false candidates.
                for _stock_pos, stock_id, rate in find_preceding_stock_rates(stream, origins, block.stream_pos, stock_ids)[:1]:
                    if not rate:
                        continue
                    key = (
                        mid,
                        stock_id,
                        quantity,
                        rate,
                        amount,
                        "post_rate_f6_rowrate",
                        block.page_no,
                        block.page_offset,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    runs.append(
                        InventoryRun(
                            voucher_master_id=mid,
                            stock_item_id=stock_id,
                            ledger_id=None,
                            unit_id=None,
                            quantity_scaled=quantity,
                            rate_scaled=rate,
                            amount_scaled=amount,
                            source="post_rate_f6_rowrate",
                            confidence="high",
                            page_no=block.page_no,
                            page_offset=block.page_offset,
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
        first = repair_split_i64(stream, first_value_pos - 4, raw_first, origins, signed_low32=True)
        second = repair_split_i64(stream, second_value_pos - 4, raw_second, origins, signed_low32=True)
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
) -> list[InventoryRun]:
    runs: list[InventoryRun] = []
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
                            InventoryRun(
                                voucher_master_id=mid,
                                stock_item_id=stock_id,
                                ledger_id=None,
                                unit_id=None,
                                quantity_scaled=quantity,
                                rate_scaled=rate,
                                amount_scaled=amount,
                                source="columnar_f6_44_46",
                                confidence="medium",
                                page_no=amount_row.page_no,
                                page_offset=amount_row.page_offset,
                            )
                        )
    return runs


def _find_purchase_stock_row_candidates(
    stream: bytes,
    origins: list[tuple[int, int]],
    subrec_pos: int,
    stock_ids: set[int],
    ledger_ids: set[int],
    amount_scaled: int,
    *,
    lookback: int = 520,
) -> list[tuple[int, int, int, int, str]]:
    candidates: list[tuple[int, int, int, int, str]] = []
    start = max(0, subrec_pos - lookback)
    pos = start
    while True:
        pos = stream.find(STOCK_FIELD, pos, subrec_pos)
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
        rate_scaled = abs(repair_split_i64(stream, rate_pos, raw_rate, origins, signed_low32=False))
        if rate_scaled == 0:
            pos += 1
            continue

        second_field = stream[pos + TYPE3_CELL_SIZE : pos + TYPE3_CELL_SIZE + 4]
        ledger_id = None
        source = ""
        if second_field == b"\x69\x00\x00\x03":
            ledger_value = struct.unpack_from("<I", stream, pos + TYPE3_CELL_SIZE + 4)[0]
            maybe_ledger = resolve_compact_master_id(ledger_value, ledger_ids)
            repeat_pos = stream.find(b"\x6e\x00\x00\x03", pos + TYPE3_CELL_SIZE + 8, min(subrec_pos, pos + 80))
            if repeat_pos >= 0 and maybe_ledger is not None:
                repeat_value = struct.unpack_from("<I", stream, repeat_pos + 4)[0]
                if resolve_compact_master_id(repeat_value, {maybe_ledger}) == maybe_ledger:
                    ledger_id = maybe_ledger
                    source = "purchase_inline_69_6e"
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
                            ledger_id = find_nearby_ledger_for_amount(stream, pos, amount_scaled, ledger_ids)
                        source = "purchase_stock_repeat_68_6d"

        if ledger_id is not None:
            candidates.append((pos, stock_id, rate_scaled, ledger_id, source))
        pos += 1

    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates


def parse_purchase_rows(
    data: bytes,
    group_to_mid: dict[int, int],
    stock_ids: set[int],
    ledger_ids: set[int],
    target_mids: set[int],
    unit_ids: set[int] | None = None,
) -> list[InventoryRun]:
    pages_by_mid = load_pages_by_mid(data, group_to_mid)
    pages_by_mid = {mid: pages for mid, pages in pages_by_mid.items() if mid in target_mids}

    runs: list[InventoryRun] = []
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
                for _stock_pos, stock_id, rate_scaled, ledger_id, source in _find_purchase_stock_row_candidates(
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
                            InventoryRun(
                                voucher_master_id=mid,
                                stock_item_id=stock_id,
                                ledger_id=ledger_id,
                                unit_id=None,
                                quantity_scaled=quantity_scaled,
                                rate_scaled=candidate_rate,
                                amount_scaled=amount_scaled,
                                source=source,
                                confidence="high",
                                page_no=block.page_no,
                                page_offset=block.page_offset,
                            )
                        )

            pos = 0
            while True:
                pos = stream.find(STOCK_FIELD, pos)
                if pos < 0:
                    break
                if pos + 120 > len(stream):
                    pos += 1
                    continue
                stock_id = struct.unpack_from("<I", stream, pos + 4)[0]
                if stock_id not in stock_ids:
                    pos += 1
                    continue

                rate_marker_pos = pos + TYPE3_CELL_SIZE
                if stream[rate_marker_pos : rate_marker_pos + 4] != b"\x02\x00\x00\x0c":
                    pos += 1
                    continue
                if rate_marker_pos + 28 > len(stream):
                    pos += 1
                    continue
                rate_scaled = struct.unpack_from("<q", stream, rate_marker_pos + 4)[0]
                rate_scaled = repair_split_i64(stream, rate_marker_pos, rate_scaled, origins, signed_low32=False)
                if rate_scaled == 0:
                    pos += 1
                    continue

                unit_id = struct.unpack_from("<I", stream, rate_marker_pos + 20)[0]
                if unit_ids is not None and unit_id not in unit_ids:
                    pos += 1
                    continue

                subrec_pos = stream.find(SUBREC_F6, rate_marker_pos + 12, min(len(stream), rate_marker_pos + 320))
                if subrec_pos < 0 or subrec_pos + 10 > len(stream):
                    pos += 1
                    continue
                subrec_len = struct.unpack_from("<I", stream, subrec_pos + 6)[0]
                if not 0 < subrec_len < 2000:
                    pos += 1
                    continue
                subrec_end = min(len(stream), subrec_pos + 10 + subrec_len)
                amount_hit = find_compact_i64(stream, b"\x02\x00\x00\x09", subrec_pos + 10, subrec_end)
                quantity_hit = find_compact_i64(stream, b"\x02\x00\x00\x0b", subrec_pos + 10, subrec_end)
                if amount_hit is None or quantity_hit is None:
                    pos += 1
                    continue
                amount_pos, amount_scaled = amount_hit
                amount_scaled = repair_split_i64(stream, amount_pos, amount_scaled, origins, signed_low32=False)
                qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = abs(repair_split_i64(stream, qty_pos, quantity_scaled, origins, signed_low32=True))
                if quantity_scaled == 0:
                    pos += 1
                    continue
                if quantity_scaled * abs(rate_scaled) != abs(amount_scaled) * int(SCALE):
                    pos += 1
                    continue

                ledger_id = find_preceding_ledger_for_amount(stream, pos, amount_scaled, ledger_ids)
                if ledger_id is None:
                    pos += 1
                    continue

                page_no, page_offset = origins[pos]
                key = (
                    mid,
                    stock_id,
                    ledger_id,
                    unit_id,
                    quantity_scaled,
                    abs(rate_scaled),
                    amount_scaled,
                    "purchase_stock_rate_f6",
                    page_no,
                    page_offset,
                )
                if key in seen:
                    pos += 1
                    continue
                seen.add(key)
                runs.append(
                    InventoryRun(
                        voucher_master_id=mid,
                        stock_item_id=stock_id,
                        ledger_id=ledger_id,
                        unit_id=unit_id,
                        quantity_scaled=quantity_scaled,
                        rate_scaled=abs(rate_scaled),
                        amount_scaled=amount_scaled,
                        source="purchase_stock_rate_f6",
                        confidence="high",
                        page_no=page_no,
                        page_offset=page_offset,
                    )
                )
                pos += 1

    for sales_run in parse_sales_rows(
        data,
        group_to_mid,
        stock_ids,
        ledger_ids,
        target_mids=target_mids,
        include_logical_post_rate_f6=False,
    ):
        if sales_run.voucher_master_id not in target_mids or sales_run.ledger_id is None:
            continue
        key = (
            sales_run.voucher_master_id,
            sales_run.stock_item_id,
            sales_run.ledger_id,
            sales_run.quantity_scaled,
            sales_run.rate_scaled,
            sales_run.amount_scaled,
            "purchase_sales_compact_family",
            sales_run.page_no,
            sales_run.page_offset,
        )
        if key in seen:
            continue
        seen.add(key)
        runs.append(
            InventoryRun(
                voucher_master_id=sales_run.voucher_master_id,
                stock_item_id=sales_run.stock_item_id,
                ledger_id=sales_run.ledger_id,
                unit_id=sales_run.unit_id,
                quantity_scaled=sales_run.quantity_scaled,
                rate_scaled=sales_run.rate_scaled,
                amount_scaled=sales_run.amount_scaled,
                source="purchase_sales_compact_family",
                confidence="high",
                page_no=sales_run.page_no,
                page_offset=sales_run.page_offset,
            )
        )

    ledgers_by_mid: dict[int, set[int]] = defaultdict(set)
    for run in runs:
        if run.ledger_id is not None:
            ledgers_by_mid[run.voucher_master_id].add(run.ledger_id)
    unique_ledger_by_mid = {
        mid: next(iter(ledger_set))
        for mid, ledger_set in ledgers_by_mid.items()
        if len(ledger_set) == 1
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
                "purchase_voucher_unique_ledger",
                value_run.page_no,
                value_run.page_offset,
            )
            if key in seen:
                continue
            seen.add(key)
            runs.append(
                InventoryRun(
                    voucher_master_id=value_run.voucher_master_id,
                    stock_item_id=value_run.stock_item_id,
                    ledger_id=ledger_id,
                    unit_id=None,
                    quantity_scaled=value_run.quantity_scaled,
                    rate_scaled=value_run.rate_scaled,
                    amount_scaled=value_run.amount_scaled,
                    source="purchase_voucher_unique_ledger",
                    confidence="medium",
                    page_no=value_run.page_no,
                    page_offset=value_run.page_offset,
                )
            )
    return runs


def build_inventory_candidates(
    data: bytes,
    group_to_mid: dict[int, int],
    voucher_type_by_mid: dict[int, str | None],
    stock_ids: set[int],
    ledger_ids: set[int],
    unit_ids: set[int] | None = None,
) -> list[InventoryRun]:
    rows: list[InventoryRun] = []

    sales_mids = {mid for mid, name in voucher_type_by_mid.items() if name in {"61 Sales", "Sales"}}
    if sales_mids:
        rows.extend(
            parse_sales_rows(
                data,
                group_to_mid,
                stock_ids,
                ledger_ids,
                target_mids=sales_mids,
                unit_ids=unit_ids,
            )
        )

    pages_by_mid = load_pages_by_mid(data, group_to_mid)
    journal_names = {"Conversion Stock Journal", "Stock Journal"}
    journal_mids = {mid for mid, name in voucher_type_by_mid.items() if name in journal_names}
    if journal_mids:
        journal_pages = {mid: pages for mid, pages in pages_by_mid.items() if mid in journal_mids}
        rows.extend(parse_post_rate_subrecords(data, journal_pages, stock_ids))
        rows.extend(parse_columnar_pairs(data, journal_pages, stock_ids))

    purchase_mids = {
        mid
        for mid, name in voucher_type_by_mid.items()
        if name in {"31-Puchase(Monthly)", "Purchase"}
    }
    if purchase_mids:
        rows.extend(
            parse_purchase_rows(
                data,
                group_to_mid,
                stock_ids,
                ledger_ids,
                target_mids=purchase_mids,
                unit_ids=unit_ids,
            )
        )

    deduped: dict[tuple[int, int, int | None, int | None, int, int, int, str, int, int], InventoryRun] = {}
    for row in rows:
        if not valid_scaled_fields(row):
            continue
        key = (
            row.voucher_master_id,
            row.stock_item_id,
            row.ledger_id,
            row.unit_id,
            row.quantity_scaled,
            row.rate_scaled,
            row.amount_scaled,
            row.source,
            row.page_no,
            row.page_offset,
        )
        deduped.setdefault(key, row)
    return list(deduped.values())

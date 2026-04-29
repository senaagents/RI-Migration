from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


PAGE_SIZE = 512
PAGE_HEADER_SIZE = 28
PAGE_BODY_SIZE = PAGE_SIZE - PAGE_HEADER_SIZE
AMOUNT_SCALE = 100000
ALLOWED_VIEWS = {1, 2, 0xFFFFFFFF}
MAX_SUBRECORD_WINDOW = 800


@dataclass(frozen=True)
class VoucherLedgerContext:
    voucher_master_id: int
    page_group_id: int
    voucher_type_master_id: int | None = None
    voucher_type_name: str | None = None


@dataclass(frozen=True)
class Origin:
    page_no: int
    page_offset: int

    @property
    def absolute_offset(self) -> int:
        return self.page_no * PAGE_SIZE + self.page_offset


@dataclass(frozen=True)
class LedgerEntryCandidate:
    voucher_master_id: int
    voucher_type_master_id: int | None
    voucher_type_name: str | None
    ledger_master_id: int
    ledger_name: str | None
    amount_scaled: int
    source: str
    confidence: str
    is_deemed_positive: bool | None
    sign_byte: int | None
    origin: Origin
    evidence: dict[str, object]


def amount_value(amount_scaled: int) -> float:
    return amount_scaled / AMOUNT_SCALE


def _u32_at(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _page_group_id(data: bytes, page_no: int) -> int:
    return _u32_at(data, page_no * PAGE_SIZE + 4)


def _page_view_id(data: bytes, page_no: int) -> int:
    return _u32_at(data, page_no * PAGE_SIZE + 20)


def _pages_for_voucher_groups(
    data: bytes,
    vouchers: list[VoucherLedgerContext],
) -> dict[int, list[int]]:
    group_ids = {voucher.page_group_id for voucher in vouchers}
    pages_by_group: dict[int, list[int]] = defaultdict(list)
    for page_no in range(1, len(data) // PAGE_SIZE):
        page_start = page_no * PAGE_SIZE
        if page_start + PAGE_HEADER_SIZE > len(data):
            continue
        group_id = _page_group_id(data, page_no)
        if group_id not in group_ids:
            continue
        view_id = _page_view_id(data, page_no)
        if view_id not in ALLOWED_VIEWS:
            continue
        pages_by_group[group_id].append(page_no)
    return pages_by_group


def _logical_stream_for_pages(data: bytes, pages: list[int]) -> bytes:
    chunks: list[bytes] = []
    for page_no in pages:
        page_start = page_no * PAGE_SIZE
        page_end = min(page_start + PAGE_SIZE, len(data))
        body_start = page_start + PAGE_HEADER_SIZE
        if body_start >= page_end:
            continue
        chunks.append(data[body_start:page_end])
    return b"".join(chunks)


def _origin_for_pos(pages: list[int], logical_pos: int) -> Origin:
    page_idx, body_offset = divmod(logical_pos, PAGE_BODY_SIZE)
    if page_idx < 0 or page_idx >= len(pages):
        return Origin(page_no=-1, page_offset=-1)
    return Origin(page_no=pages[page_idx], page_offset=PAGE_HEADER_SIZE + body_offset)


def _known_amount_i64(value: int, *, allow_zero: bool = False) -> bool:
    if value == 0:
        return allow_zero
    return value % 1000 == 0 and 1000 <= abs(value) < 10**16


def _subrecord_body(stream: bytes, pos: int, *, stop_at_next_header: bool = True) -> tuple[bytes, int] | None:
    if pos + 10 > len(stream):
        return None
    length = struct.unpack_from("<I", stream, pos + 6)[0]
    body_start = pos + 10
    if body_start >= len(stream):
        return None
    if 0 < length <= MAX_SUBRECORD_WINDOW:
        body_end = min(len(stream), body_start + length)
    else:
        body_end = min(len(stream), body_start + MAX_SUBRECORD_WINDOW)
    if stop_at_next_header:
        next_header = stream.find(b"\x00\x50", body_start + 1, body_end)
        if next_header > body_start:
            body_end = next_header
    if body_end <= body_start:
        return None
    return stream[body_start:body_end], body_start


def _scan_fieldcoded_subrecords(
    voucher: VoucherLedgerContext,
    stream: bytes,
    pages: list[int],
    ledger_ids: set[int],
    ledger_names: dict[int, str],
    header: bytes,
    source: str,
    confidence: str,
    *,
    stop_at_next_header: bool = True,
    ledger_first_pairing: bool = False,
    allow_zero_amount: bool = False,
) -> Iterable[LedgerEntryCandidate]:
    pos = 0
    ledger_marker = b"\x02\x00\x00\x03"
    amount_marker = b"\x02\x00\x00\x09"
    while True:
        pos = stream.find(header, pos)
        if pos < 0:
            break
        body_result = _subrecord_body(
            stream,
            pos,
            stop_at_next_header=stop_at_next_header,
        )
        if body_result is None:
            pos += 1
            continue
        body, body_start = body_result
        ledger_hits: list[tuple[int, int]] = []
        ledger_pos = 0
        while True:
            ledger_pos = body.find(ledger_marker, ledger_pos)
            if ledger_pos < 0:
                break
            if ledger_pos + 8 <= len(body):
                ledger_id = struct.unpack_from("<I", body, ledger_pos + 4)[0]
                if ledger_id in ledger_ids:
                    ledger_hits.append((ledger_pos, ledger_id))
            ledger_pos += 1

        if ledger_first_pairing:
            for idx, (ledger_body_pos, ledger_id) in enumerate(ledger_hits):
                next_ledger_pos = ledger_hits[idx + 1][0] if idx + 1 < len(ledger_hits) else len(body)
                amount_pos = body.find(amount_marker, ledger_body_pos + 8, next_ledger_pos)
                if amount_pos < 0 or amount_pos + 12 > len(body):
                    continue
                amount_scaled = struct.unpack_from("<q", body, amount_pos + 4)[0]
                if not _known_amount_i64(amount_scaled, allow_zero=allow_zero_amount):
                    continue
                sign_byte = body[amount_pos - 2] if amount_pos >= 2 else None
                origin = _origin_for_pos(pages, pos)
                amount_origin = _origin_for_pos(pages, body_start + amount_pos + 4)
                yield LedgerEntryCandidate(
                    voucher_master_id=voucher.voucher_master_id,
                    voucher_type_master_id=voucher.voucher_type_master_id,
                    voucher_type_name=voucher.voucher_type_name,
                    ledger_master_id=ledger_id,
                    ledger_name=ledger_names.get(ledger_id),
                    amount_scaled=amount_scaled,
                    source=source,
                    confidence=confidence,
                    is_deemed_positive=(sign_byte == 0x80) if sign_byte in {0x00, 0x80} else None,
                    sign_byte=sign_byte,
                    origin=origin,
                    evidence={
                        "subrecord_offset": pos,
                        "subrecord_length_used": len(body),
                        "ledger_body_offset": ledger_body_pos,
                        "amount_body_offset": amount_pos,
                        "amount_absolute_offset": amount_origin.absolute_offset,
                        "pairing": "first_amount_before_next_ledger",
                    },
                )
            pos += 1
            continue

        amount_pos = 0
        while True:
            amount_pos = body.find(amount_marker, amount_pos)
            if amount_pos < 0:
                break
            if amount_pos + 12 > len(body):
                amount_pos += 1
                continue
            amount_scaled = struct.unpack_from("<q", body, amount_pos + 4)[0]
            if not _known_amount_i64(amount_scaled, allow_zero=allow_zero_amount):
                amount_pos += 1
                continue
            preceding_ledgers = [hit for hit in ledger_hits if hit[0] < amount_pos]
            if not preceding_ledgers:
                amount_pos += 1
                continue
            ledger_body_pos, ledger_id = preceding_ledgers[-1]
            sign_byte = body[amount_pos - 2] if amount_pos >= 2 else None
            origin = _origin_for_pos(pages, pos)
            amount_origin = _origin_for_pos(pages, body_start + amount_pos + 4)
            yield LedgerEntryCandidate(
                voucher_master_id=voucher.voucher_master_id,
                voucher_type_master_id=voucher.voucher_type_master_id,
                voucher_type_name=voucher.voucher_type_name,
                ledger_master_id=ledger_id,
                ledger_name=ledger_names.get(ledger_id),
                amount_scaled=amount_scaled,
                source=source,
                confidence=confidence,
                is_deemed_positive=(sign_byte == 0x80) if sign_byte in {0x00, 0x80} else None,
                sign_byte=sign_byte,
                origin=origin,
                evidence={
                    "subrecord_offset": pos,
                    "subrecord_length_used": len(body),
                    "ledger_body_offset": ledger_body_pos,
                    "amount_body_offset": amount_pos,
                    "amount_absolute_offset": amount_origin.absolute_offset,
                },
            )
            amount_pos += 1
        pos += 1


def _scan_52d3_anchors(
    voucher: VoucherLedgerContext,
    stream: bytes,
    pages: list[int],
    ledger_ids: set[int],
    ledger_names: dict[int, str],
) -> Iterable[LedgerEntryCandidate]:
    pos = 0
    marker = b"\xd3\x52\x00\x09"
    while True:
        pos = stream.find(marker, pos)
        if pos < 2:
            break
        block_start = pos - 2
        if block_start + 18 > len(stream):
            pos += 1
            continue
        ledger_id = struct.unpack_from("<I", stream, block_start + 6)[0]
        if ledger_id not in ledger_ids:
            pos += 1
            continue
        amount_scaled = struct.unpack_from("<q", stream, block_start + 10)[0]
        if not _known_amount_i64(amount_scaled):
            pos += 1
            continue
        sign_byte = stream[pos - 1] if pos >= 1 else None
        origin = _origin_for_pos(pages, block_start)
        amount_origin = _origin_for_pos(pages, block_start + 10)
        yield LedgerEntryCandidate(
            voucher_master_id=voucher.voucher_master_id,
            voucher_type_master_id=voucher.voucher_type_master_id,
            voucher_type_name=voucher.voucher_type_name,
            ledger_master_id=ledger_id,
            ledger_name=ledger_names.get(ledger_id),
            amount_scaled=amount_scaled,
            source="52d3_anchor",
            confidence="high",
            is_deemed_positive=(sign_byte == 0x80) if sign_byte in {0x00, 0x80} else None,
            sign_byte=sign_byte,
            origin=origin,
            evidence={
                "anchor_offset": pos,
                "amount_offset": block_start + 10,
                "amount_absolute_offset": amount_origin.absolute_offset,
            },
        )
        pos += 1


def iter_ledger_candidates(
    data: bytes,
    vouchers: list[VoucherLedgerContext],
    ledger_ids: set[int],
    ledger_names: dict[int, str],
) -> Iterable[LedgerEntryCandidate]:
    pages_by_group = _pages_for_voucher_groups(data, vouchers)
    for voucher in vouchers:
        pages = pages_by_group.get(voucher.page_group_id, [])
        if not pages:
            continue
        stream = _logical_stream_for_pages(data, pages)
        if not stream:
            continue
        yield from _scan_52d3_anchors(voucher, stream, pages, ledger_ids, ledger_names)
        yield from _scan_fieldcoded_subrecords(
            voucher,
            stream,
            pages,
            ledger_ids,
            ledger_names,
            header=b"\x00\x50\x02\x00\x00\x10",
            source="subrecord_0002",
            confidence="high",
            stop_at_next_header=True,
            ledger_first_pairing=True,
            allow_zero_amount=True,
        )
        yield from _scan_fieldcoded_subrecords(
            voucher,
            stream,
            pages,
            ledger_ids,
            ledger_names,
            header=b"\x00\x50\x02\x00\x00\x10",
            source="subrecord_0002_wide",
            confidence="medium",
            stop_at_next_header=False,
            ledger_first_pairing=True,
            allow_zero_amount=True,
        )
        yield from _scan_fieldcoded_subrecords(
            voucher,
            stream,
            pages,
            ledger_ids,
            ledger_names,
            header=b"\x00\x50\x06\x00\x00\x10",
            source="subrecord_0006",
            confidence="high",
            stop_at_next_header=True,
        )
        yield from _scan_fieldcoded_subrecords(
            voucher,
            stream,
            pages,
            ledger_ids,
            ledger_names,
            header=b"\x00\x50\x13\x27\x00\x10",
            source="subrecord_2713",
            confidence="medium",
            stop_at_next_header=False,
        )


def build_ledger_candidates(
    data: bytes,
    vouchers: list[VoucherLedgerContext],
    ledger_ids: set[int],
    ledger_names: dict[int, str],
) -> list[LedgerEntryCandidate]:
    return list(iter_ledger_candidates(data, vouchers, ledger_ids, ledger_names))

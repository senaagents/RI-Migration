from __future__ import annotations

import argparse
import sqlite3
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


PAGE_SIZE = 512
AMOUNT_SCALE = Decimal(100000)

DEFAULT_DECODED = Path("out/synthetic-live/PAY002/after_decoded.sqlite3")
DEFAULT_TRANMGR = Path("out/synthetic-live/PAY002/after/TranMgr.1800")


@dataclass(frozen=True)
class VoucherGroup:
    voucher_master_id: int
    page_group_id: int
    header_page_no: int
    page_group_page_count: int | None
    narration_guess: str | None


@dataclass(frozen=True)
class Origin:
    page_no: int
    page_offset: int

    @property
    def absolute_offset(self) -> int:
        return self.page_no * PAGE_SIZE + self.page_offset


@dataclass(frozen=True)
class LedgerCandidate:
    voucher_master_id: int
    ledger_master_id: int
    ledger_name: str
    amount_scaled: int
    source: str
    origin: Origin
    evidence: str


def amount_value(amount_scaled: int) -> str:
    value = Decimal(amount_scaled) / AMOUNT_SCALE
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def load_voucher_groups(conn: sqlite3.Connection) -> list[VoucherGroup]:
    return [
        VoucherGroup(
            voucher_master_id=int(master_id),
            page_group_id=int(page_group_id),
            header_page_no=int(page_no),
            page_group_page_count=(
                int(page_group_page_count) if page_group_page_count is not None else None
            ),
            narration_guess=narration_guess,
        )
        for (
            master_id,
            page_group_id,
            page_no,
            page_group_page_count,
            narration_guess,
        ) in conn.execute(
            """
            select master_id, page_group_id, page_no, page_group_page_count, narration_guess
            from voucher_header_candidates
            where page_group_id is not null
              and key_raw is not null
            order by master_id
            """
        )
    ]


def load_manager_names(conn: sqlite3.Connection) -> dict[int, str]:
    names: dict[int, str] = {}
    table_names = {
        row[0]
        for row in conn.execute("select name from sqlite_master where type = 'table'")
    }
    if "manager_master_records" in table_names:
        for master_id, name, user_text in conn.execute(
            """
            select master_id, name, user_text
            from manager_master_records
            where master_id is not null
            """
        ):
            display = name or user_text
            if display:
                names[int(master_id)] = str(display)
    elif "manager_records" in table_names:
        for master_id, name, user_text in conn.execute(
            """
            select master_id, name, user_text
            from manager_records
            where master_id is not null
            """
        ):
            display = name or user_text
            if display and int(master_id) not in names:
                names[int(master_id)] = str(display)
    return names


def pages_for_group(data: bytes, page_group_id: int) -> list[int]:
    pages: list[int] = []
    for page_no in range(1, len(data) // PAGE_SIZE):
        page_start = page_no * PAGE_SIZE
        if page_start + 8 > len(data):
            continue
        group_id = struct.unpack_from("<I", data, page_start + 4)[0]
        if group_id == page_group_id:
            pages.append(page_no)
    return pages


def stream_for_pages(data: bytes, pages: list[int]) -> tuple[bytes, list[Origin]]:
    chunks: list[bytes] = []
    origins: list[Origin] = []
    for page_no in pages:
        page = data[page_no * PAGE_SIZE : (page_no + 1) * PAGE_SIZE]
        chunks.append(page)
        origins.extend(Origin(page_no, offset) for offset in range(len(page)))
    return b"".join(chunks), origins


def known_amount_i64(value: int) -> bool:
    return value != 0 and 1000 <= abs(value) < 10**15


def candidate_name(ledger_names: dict[int, str], ledger_id: int) -> str | None:
    return ledger_names.get(ledger_id)


def scan_f6_ledger_amounts(
    voucher: VoucherGroup,
    stream: bytes,
    origins: list[Origin],
    ledger_names: dict[int, str],
) -> list[LedgerCandidate]:
    candidates: list[LedgerCandidate] = []
    pos = 0
    marker = b"\xf6\x01\x44\x00"
    amount_offsets = (26, 34, 38)
    while True:
        pos = stream.find(marker, pos)
        if pos < 2:
            break
        block_start = pos - 2
        if block_start + 14 > len(stream):
            pos += 1
            continue
        ledger_id = struct.unpack_from("<I", stream, block_start + 6)[0]
        name = candidate_name(ledger_names, ledger_id)
        if name is None:
            pos += 1
            continue

        flags = stream[block_start : block_start + 2].hex()
        seen_values: set[int] = set()
        for amount_offset in amount_offsets:
            amount_pos = block_start + amount_offset
            if amount_pos + 8 > len(stream):
                continue
            amount_scaled = struct.unpack_from("<q", stream, amount_pos)[0]
            if not known_amount_i64(amount_scaled) or amount_scaled in seen_values:
                continue
            seen_values.add(amount_scaled)
            origin = origins[block_start]
            amount_origin = origins[amount_pos]
            candidates.append(
                LedgerCandidate(
                    voucher_master_id=voucher.voucher_master_id,
                    ledger_master_id=ledger_id,
                    ledger_name=name,
                    amount_scaled=amount_scaled,
                    source="f6_01_44",
                    origin=origin,
                    evidence=(
                        f"page={origin.page_no} off={origin.page_offset} "
                        f"abs={origin.absolute_offset} flags={flags} "
                        f"amount_off=+{amount_offset} amount_page={amount_origin.page_no} "
                        f"amount_off_page={amount_origin.page_offset}"
                    ),
                )
            )
        pos += 1
    return candidates


def scan_d3_ledger_amounts(
    voucher: VoucherGroup,
    stream: bytes,
    origins: list[Origin],
    ledger_names: dict[int, str],
) -> list[LedgerCandidate]:
    candidates: list[LedgerCandidate] = []
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
        name = candidate_name(ledger_names, ledger_id)
        if name is None:
            pos += 1
            continue
        amount_scaled = struct.unpack_from("<q", stream, block_start + 10)[0]
        if not known_amount_i64(amount_scaled):
            pos += 1
            continue
        origin = origins[block_start]
        amount_origin = origins[block_start + 10]
        candidates.append(
            LedgerCandidate(
                voucher_master_id=voucher.voucher_master_id,
                ledger_master_id=ledger_id,
                ledger_name=name,
                amount_scaled=amount_scaled,
                source="d3_52_00_09",
                origin=origin,
                evidence=(
                    f"page={origin.page_no} off={origin.page_offset} "
                    f"abs={origin.absolute_offset} flags={stream[block_start:block_start + 2].hex()} "
                    f"amount_off=+10 amount_page={amount_origin.page_no} "
                    f"amount_off_page={amount_origin.page_offset}"
                ),
            )
        )
        pos += 1
    return candidates


def scan_2713_subrecords(
    voucher: VoucherGroup,
    stream: bytes,
    origins: list[Origin],
    ledger_names: dict[int, str],
) -> list[LedgerCandidate]:
    candidates: list[LedgerCandidate] = []
    pos = 0
    # This is sub-record id 0x2713 in little-endian form. Do not confuse it
    # with the retracted 0x1327 claim (`00 50 27 13 00 10`), which was absent
    # across Claude's corpus.
    header = b"\x00\x50\x13\x27\x00\x10"
    ledger_field = b"\x02\x00\x00\x03"
    amount_field = b"\x02\x00\x00\x09"
    while True:
        pos = stream.find(header, pos)
        if pos < 0:
            break
        if pos + 10 > len(stream):
            pos += 1
            continue
        length = struct.unpack_from("<I", stream, pos + 6)[0]
        if not 0 < length < 4000:
            pos += 1
            continue
        body_start = pos + 10
        body_end = min(len(stream), body_start + length)
        body = stream[body_start:body_end]
        ledger_hits: list[tuple[int, int, str]] = []

        ledger_pos = 0
        while True:
            ledger_pos = body.find(ledger_field, ledger_pos)
            if ledger_pos < 0:
                break
            if ledger_pos + 8 <= len(body):
                ledger_id = struct.unpack_from("<I", body, ledger_pos + 4)[0]
                name = candidate_name(ledger_names, ledger_id)
                if name is not None:
                    ledger_hits.append((ledger_pos, ledger_id, name))
            ledger_pos += 1

        amount_pos = 0
        while True:
            amount_pos = body.find(amount_field, amount_pos)
            if amount_pos < 0:
                break
            if amount_pos + 12 > len(body):
                amount_pos += 1
                continue
            amount_scaled = struct.unpack_from("<q", body, amount_pos + 4)[0]
            if not known_amount_i64(amount_scaled):
                amount_pos += 1
                continue
            preceding_ledgers = [hit for hit in ledger_hits if hit[0] < amount_pos]
            if not preceding_ledgers:
                amount_pos += 1
                continue
            ledger_body_pos, ledger_id, name = preceding_ledgers[-1]
            origin = origins[pos]
            ledger_origin = origins[body_start + ledger_body_pos]
            amount_origin = origins[body_start + amount_pos + 4]
            candidates.append(
                LedgerCandidate(
                    voucher_master_id=voucher.voucher_master_id,
                    ledger_master_id=ledger_id,
                    ledger_name=name,
                    amount_scaled=amount_scaled,
                    source="subrecord_2713",
                    origin=origin,
                    evidence=(
                        f"page={origin.page_no} off={origin.page_offset} "
                        f"abs={origin.absolute_offset} len={length} "
                        f"ledger_body_off=+{ledger_body_pos} ledger_page={ledger_origin.page_no} "
                        f"ledger_off_page={ledger_origin.page_offset} "
                        f"amount_body_off=+{amount_pos} amount_page={amount_origin.page_no} "
                        f"amount_off_page={amount_origin.page_offset}"
                    ),
                )
            )
            amount_pos += 1
        pos += 1
    return candidates


def scan_candidates(
    data: bytes,
    vouchers: list[VoucherGroup],
    ledger_names: dict[int, str],
) -> tuple[list[LedgerCandidate], dict[int, list[int]]]:
    candidates: list[LedgerCandidate] = []
    pages_by_voucher: dict[int, list[int]] = {}
    for voucher in vouchers:
        pages = pages_for_group(data, voucher.page_group_id)
        pages_by_voucher[voucher.voucher_master_id] = pages
        stream, origins = stream_for_pages(data, pages)
        candidates.extend(scan_f6_ledger_amounts(voucher, stream, origins, ledger_names))
        candidates.extend(scan_d3_ledger_amounts(voucher, stream, origins, ledger_names))
        candidates.extend(scan_2713_subrecords(voucher, stream, origins, ledger_names))
    candidates.sort(
        key=lambda row: (
            row.voucher_master_id,
            row.ledger_master_id,
            row.amount_scaled,
            row.source,
            row.origin.absolute_offset,
        )
    )
    return candidates, pages_by_voucher


def print_candidates(candidates: list[LedgerCandidate]) -> None:
    print(
        "voucher_master_id\tledger_master_id\tledger_name\tamount_scaled\t"
        "amount_value\tsource\toffset_evidence"
    )
    for row in candidates:
        print(
            f"{row.voucher_master_id}\t{row.ledger_master_id}\t{row.ledger_name}\t"
            f"{row.amount_scaled}\t{amount_value(row.amount_scaled)}\t"
            f"{row.source}\t{row.evidence}"
        )


def print_summary(
    candidates: list[LedgerCandidate],
    vouchers: list[VoucherGroup],
    pages_by_voucher: dict[int, list[int]],
) -> None:
    print()
    print("voucher_groups")
    for voucher in vouchers:
        pages = ",".join(str(page) for page in pages_by_voucher[voucher.voucher_master_id])
        print(
            f"voucher={voucher.voucher_master_id} group={voucher.page_group_id} "
            f"header_page={voucher.header_page_no} pages=[{pages}] "
            f"narration={voucher.narration_guess or ''}"
        )

    counts = Counter(
        (row.voucher_master_id, row.ledger_master_id, row.ledger_name, row.amount_scaled)
        for row in candidates
    )
    by_source = Counter(row.source for row in candidates)

    print()
    print("duplicate_summary")
    for (voucher_id, ledger_id, name, scaled_amount), count in sorted(counts.items()):
        print(
            f"voucher={voucher_id} ledger={ledger_id} name={name} "
            f"amount_scaled={scaled_amount} amount_value={amount_value(scaled_amount)} "
            f"candidate_count={count}"
        )

    print()
    print("source_counts")
    for source, count in sorted(by_source.items()):
        print(f"{source}\t{count}")

    grouped: dict[int, int] = defaultdict(int)
    for row in candidates:
        grouped[row.voucher_master_id] += 1
    print()
    print("voucher_candidate_counts")
    for voucher_id, count in sorted(grouped.items()):
        print(f"voucher={voucher_id}\tcandidates={count}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only probe for PAY001/PAY002 synthetic payment ledger rows "
            "inside TranMgr.1800 voucher page groups."
        )
    )
    parser.add_argument(
        "decoded_sqlite",
        nargs="?",
        type=Path,
        default=DEFAULT_DECODED,
        help=f"decoded SQLite path (default: {DEFAULT_DECODED})",
    )
    parser.add_argument(
        "--tranmgr",
        type=Path,
        default=DEFAULT_TRANMGR,
        help=f"TranMgr.1800 path (default: {DEFAULT_TRANMGR})",
    )
    args = parser.parse_args()

    with sqlite3.connect(args.decoded_sqlite) as conn:
        vouchers = load_voucher_groups(conn)
        ledger_names = load_manager_names(conn)

    data = args.tranmgr.read_bytes()
    candidates, pages_by_voucher = scan_candidates(data, vouchers, ledger_names)
    print_candidates(candidates)
    print_summary(candidates, vouchers, pages_by_voucher)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

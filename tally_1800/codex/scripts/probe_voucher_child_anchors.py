from __future__ import annotations

import argparse
import sqlite3
import struct
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


PAGE_SIZE = 512
SCALE = Decimal(100000)


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


def find_all(data: bytes, needle: bytes):
    pos = 0
    while True:
        hit = data.find(needle, pos)
        if hit < 0:
            return
        yield hit
        pos = hit + 1


def parse_subrecord_candidates(page: bytes, mid: int, direct_ledgers: set[tuple[int, int, int]]) -> None:
    marker = b"\x00\x50\x06\x00\x00\x10"
    for pos in find_all(page, marker):
        if pos + 10 > len(page):
            continue
        length = struct.unpack_from("<I", page, pos + 6)[0]
        if not 0 < length <= PAGE_SIZE:
            continue
        block = page[pos : min(len(page), pos + 10 + length)]
        ledger_ids = []
        amounts = []
        for id_pos in find_all(block, b"\x02\x00\x00\x03"):
            if id_pos + 8 <= len(block):
                ledger_id = struct.unpack_from("<I", block, id_pos + 4)[0]
                if ledger_id:
                    ledger_ids.append(ledger_id)
        for amount_pos in find_all(block, b"\x02\x00\x00\x09"):
            if amount_pos + 12 <= len(block):
                amount = struct.unpack_from("<q", block, amount_pos + 4)[0]
                if amount:
                    amounts.append(amount)
        for ledger_id in ledger_ids:
            for amount in amounts:
                direct_ledgers.add((mid, ledger_id, amount))


def parse_summary_ledgers(page: bytes, mid: int, summary_ledgers: set[tuple[int, int, int]]) -> None:
    marker = b"\x00\x04\xd3\x52\x00\x09"
    for pos in find_all(page, marker):
        if pos + 18 > len(page):
            continue
        ledger_id = struct.unpack_from("<I", page, pos + 6)[0]
        amount = struct.unpack_from("<q", page, pos + 10)[0]
        if ledger_id and amount:
            summary_ledgers.add((mid, ledger_id, amount))


def parse_inventory_refs(
    page: bytes,
    mid: int,
    stock_ids: set[int],
    inventory_refs: dict[int, set[int]],
    inventory_values: dict[tuple[int, int], set[int]],
) -> None:
    for marker in (b"\x2e\x01\x42\x00",):
        for pos in find_all(page, marker):
            if pos + 8 <= len(page):
                stock_id = struct.unpack_from("<I", page, pos + 4)[0]
                if stock_id in stock_ids:
                    inventory_refs[mid].add(stock_id)

    for marker in (b"\xf6\x01\x44\x00", b"\xf6\x01\x46\x00", b"\xf7\x01\x44\x00", b"\xf7\x01\x46\x00"):
        for pos in find_all(page, marker):
            if pos + 8 > len(page):
                continue
            stock_id = struct.unpack_from("<I", page, pos + 4)[0]
            if stock_id not in stock_ids:
                continue
            inventory_refs[mid].add(stock_id)
            for rel in (24, 32):
                if pos + rel + 8 <= len(page):
                    value = struct.unpack_from("<q", page, pos + rel)[0]
                    if value:
                        inventory_values[(mid, stock_id)].add(value)

    # Sales invoice pages also expose a compact field-run after 0x0002/0003.
    for pos in find_all(page, b"\x02\x00\x00\x03"):
        if pos + 8 > len(page):
            continue
        stock_id = struct.unpack_from("<I", page, pos + 4)[0]
        if stock_id not in stock_ids:
            continue
        inventory_refs[mid].add(stock_id)
        window = page[pos : min(len(page), pos + 220)]
        for field_pos in range(0, max(0, len(window) - 12)):
            type_bytes = window[field_pos + 2 : field_pos + 4]
            if type_bytes not in {b"\x00\x08", b"\x00\x09", b"\x00\x0b", b"\x00\x0c"}:
                continue
            value = struct.unpack_from("<q", window, field_pos + 4)[0]
            if -10**15 < value < 10**15 and value:
                inventory_values[(mid, stock_id)].add(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_sqlite", type=Path)
    parser.add_argument("--tranmgr", type=Path, required=True)
    parser.add_argument("--rosetta", type=Path, required=True)
    args = parser.parse_args()

    decoded = sqlite3.connect(args.decoded_sqlite)
    rosetta = sqlite3.connect(args.rosetta)

    group_to_mid = {
        int(group_id): int(master_id)
        for master_id, group_id in decoded.execute(
            """
            select master_id, page_group_id
            from voucher_header_candidates
            where page_group_id is not null
            """
        )
    }

    ledger_name_to_id = load_rosetta_ids(rosetta, "ledgers")
    stock_name_to_id = load_rosetta_ids(rosetta, "stock_items")
    stock_ids = set(stock_name_to_id.values())

    direct_ledgers: set[tuple[int, int, int]] = set()
    summary_ledgers: set[tuple[int, int, int]] = set()
    inventory_refs: dict[int, set[int]] = defaultdict(set)
    inventory_values: dict[tuple[int, int], set[int]] = defaultdict(set)

    data = args.tranmgr.read_bytes()
    for page_no in range(1, len(data) // PAGE_SIZE):
        page_start = page_no * PAGE_SIZE
        group_id = struct.unpack_from("<I", data, page_start + 4)[0]
        mid = group_to_mid.get(group_id)
        if mid is None:
            continue
        page = data[page_start : page_start + PAGE_SIZE]
        parse_subrecord_candidates(page, mid, direct_ledgers)
        parse_summary_ledgers(page, mid, summary_ledgers)
        parse_inventory_refs(page, mid, stock_ids, inventory_refs, inventory_values)

    ledger_truth = []
    for mid, ledger_name, amount in rosetta.execute(
        """
        select cast(v.master_id as integer), e.ledger_name, e.amount
        from voucher_ledger_entries e
        join vouchers v on v.id = e.voucher_id
        """
    ):
        ledger_id = ledger_name_to_id.get(ledger_name)
        if ledger_id is not None:
            ledger_truth.append((int(mid), ledger_id, scaled(amount)))

    direct_hits = sum(1 for row in ledger_truth if row in direct_ledgers)
    summary_hits = sum(1 for row in ledger_truth if row in summary_ledgers)
    union_ledgers = direct_ledgers | summary_ledgers
    union_hits = sum(1 for row in ledger_truth if row in union_ledgers)

    inventory_truth = []
    for mid, item_name, quantity, rate, amount in rosetta.execute(
        """
        select cast(v.master_id as integer), e.item_name, e.quantity, e.rate, e.amount
        from voucher_inventory_entries e
        join vouchers v on v.id = e.voucher_id
        """
    ):
        stock_id = stock_name_to_id.get(item_name)
        if stock_id is not None:
            inventory_truth.append((int(mid), stock_id, scaled(quantity), scaled(rate), scaled(amount)))

    inventory_presence_hits = 0
    inventory_value_hits = 0
    for mid, stock_id, quantity, rate, amount in inventory_truth:
        if stock_id in inventory_refs.get(mid, set()):
            inventory_presence_hits += 1
        values = inventory_values.get((mid, stock_id), set())
        targets = {quantity, -quantity, rate, -rate, amount, -amount}
        if values & targets:
            inventory_value_hits += 1

    print(f"voucher_header_groups={len(group_to_mid)}")
    print(f"ledger_truth_rows={len(ledger_truth)}")
    print(f"direct_ledger_candidates={len(direct_ledgers)}")
    print(f"direct_ledger_exact={direct_hits}/{len(ledger_truth)}")
    print(f"summary_ledger_candidates={len(summary_ledgers)}")
    print(f"summary_ledger_exact={summary_hits}/{len(ledger_truth)}")
    print(f"ledger_union_candidates={len(union_ledgers)}")
    print(f"ledger_union_exact={union_hits}/{len(ledger_truth)}")
    print(f"inventory_truth_rows={len(inventory_truth)}")
    print(f"inventory_presence_exact={inventory_presence_hits}/{len(inventory_truth)}")
    print(f"inventory_any_value_exact={inventory_value_hits}/{len(inventory_truth)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

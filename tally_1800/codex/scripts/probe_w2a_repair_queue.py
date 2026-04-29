from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tally1800.inventory_decode import (  # noqa: E402
    PAGE_SIZE,
    SCALE,
    STOCK_FIELD,
    SUBREC_F6,
    TYPE3_CELL_SIZE,
    InventoryRun,
    find_compact_i64,
    find_nearby_ledger_for_amount,
    find_preceding_ledger_for_amount,
    find_rate_i64,
    iter_streams_for_pages,
    load_pages_by_mid,
    parse_columnar_pairs,
    parse_post_rate_subrecords,
    parse_purchase_rows,
    parse_sales_rows,
    read_logical_bytes_with_end,
    resolve_compact_master_id,
)


def scaled(value: object) -> int:
    return int((Decimal(str(value)) * SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def guid_suffix_to_id(guid: str | None) -> int | None:
    if not guid:
        return None
    try:
        return int(guid.rsplit("-", 1)[-1], 16)
    except ValueError:
        return None


def load_rosetta_ids(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, guid in conn.execute(f"select name, guid from {table} where guid is not null"):
        master_id = guid_suffix_to_id(guid)
        if master_id is not None:
            out[str(name)] = master_id
    return out


def load_group_to_mid(decoded: sqlite3.Connection) -> dict[int, int]:
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


def load_voucher_types(decoded: sqlite3.Connection) -> dict[int, str | None]:
    return {
        int(row["master_id"]): row["paired_voucher_type_name"]
        for row in decoded.execute(
            """
            select h.master_id, vt.name as paired_voucher_type_name
            from voucher_header_candidates h
            left join voucher_types vt on vt.master_id = h.paired_voucher_type_master_id
            """
        )
    }


def load_units(decoded: sqlite3.Connection) -> set[int]:
    return {int(row[0]) for row in decoded.execute("select master_id from units")}


def load_names(decoded: sqlite3.Connection, table: str) -> dict[int, str]:
    return {int(mid): str(name) for mid, name in decoded.execute(f"select master_id, name from {table}")}


def load_repair_rows(decoded: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        decoded.execute(
            """
            select q.*, h.page_no, h.page_group_id, h.page_group_page_count
            from xml_repair_queue q
            left join voucher_header_candidates h on h.master_id = q.voucher_master_id
            order by q.voucher_type_name, q.voucher_master_id
            """
        )
    )


def truth_rows(
    rosetta: sqlite3.Connection,
    voucher_types: set[str],
    stock_name_to_id: dict[str, int],
    ledger_name_to_id: dict[str, int],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    qmarks = ",".join("?" for _ in voucher_types)
    for row in rosetta.execute(
        f"""
        select cast(v.master_id as integer) as voucher_master_id,
               v.voucher_type_name,
               e.item_name,
               e.ledger_name,
               e.quantity,
               e.rate,
               e.amount
        from voucher_inventory_entries e
        join vouchers v on v.id = e.voucher_id
        where v.voucher_type_name in ({qmarks})
        """,
        tuple(sorted(voucher_types)),
    ):
        mid, voucher_type, item_name, ledger_name, quantity, rate, amount = row
        stock_id = stock_name_to_id.get(str(item_name))
        ledger_id = ledger_name_to_id.get(str(ledger_name)) if ledger_name is not None else None
        rows.append(
            {
                "voucher_master_id": int(mid),
                "voucher_type_name": str(voucher_type),
                "stock_item_master_id": stock_id,
                "stock_item_name": str(item_name),
                "ledger_master_id": ledger_id,
                "ledger_name": str(ledger_name) if ledger_name is not None else None,
                "quantity_scaled": scaled(quantity),
                "rate_scaled": scaled(rate),
                "amount_scaled": scaled(amount),
            }
        )
    return rows


def final_rows(decoded: sqlite3.Connection, voucher_types: set[str]) -> list[dict[str, object]]:
    qmarks = ",".join("?" for _ in voucher_types)
    return [
        dict(row)
        for row in decoded.execute(
            f"""
            select voucher_master_id, voucher_type_name,
                   stock_item_master_id, stock_item_name,
                   ledger_master_id, ledger_name,
                   quantity_scaled, rate_scaled, amount_scaled,
                   sources, confidence, first_page_no, first_page_offset
            from voucher_inventory_entries_1800
            where voucher_type_name in ({qmarks})
            """,
            tuple(sorted(voucher_types)),
        )
    ]


def candidate_rows(decoded: sqlite3.Connection, repair_mids: set[int]) -> list[dict[str, object]]:
    if not repair_mids:
        return []
    qmarks = ",".join("?" for _ in repair_mids)
    return [
        dict(row)
        for row in decoded.execute(
            f"""
            select voucher_master_id, voucher_type_name,
                   stock_item_master_id, stock_item_name,
                   ledger_master_id, ledger_name,
                   quantity_scaled, rate_scaled, amount_scaled,
                   source, confidence, page_no, page_offset
            from voucher_inventory_entry_candidates
            where voucher_master_id in ({qmarks})
            order by voucher_master_id, row_id
            """,
            tuple(sorted(repair_mids)),
        )
    ]


def inventory_key(row: dict[str, object], *, include_ledger: bool) -> tuple[object, ...] | None:
    stock_id = row.get("stock_item_master_id")
    if stock_id is None:
        return None
    base = (
        int(row["voucher_master_id"]),
        int(stock_id),
        abs(int(row["quantity_scaled"])),
        int(row["rate_scaled"]),
        int(row["amount_scaled"]),
    )
    if include_ledger:
        ledger_id = row.get("ledger_master_id")
        if ledger_id is None:
            return None
        return (base[0], base[1], int(ledger_id), base[2], base[3], base[4])
    return base


def compare_rows(
    truth: list[dict[str, object]],
    observed: list[dict[str, object]],
    *,
    include_ledger: bool,
) -> dict[str, object]:
    truth_counter: Counter[tuple[object, ...]] = Counter()
    truth_by_key: dict[tuple[object, ...], dict[str, object]] = {}
    for row in truth:
        key = inventory_key(row, include_ledger=include_ledger)
        if key is None:
            continue
        truth_counter[key] += 1
        truth_by_key.setdefault(key, row)

    observed_counter: Counter[tuple[object, ...]] = Counter()
    observed_by_key: dict[tuple[object, ...], dict[str, object]] = {}
    for row in observed:
        key = inventory_key(row, include_ledger=include_ledger)
        if key is None:
            continue
        observed_counter[key] += 1
        observed_by_key.setdefault(key, row)

    exact = sum(min(truth_counter[key], observed_counter[key]) for key in truth_counter)
    missing: list[dict[str, object]] = []
    for key, count in (truth_counter - observed_counter).items():
        row = dict(truth_by_key[key])
        row["missing_count"] = count
        missing.append(row)
    extras: list[dict[str, object]] = []
    for key, count in (observed_counter - truth_counter).items():
        row = dict(observed_by_key[key])
        row["extra_count"] = count
        extras.append(row)
    missing_counter = truth_counter - observed_counter
    extra_counter = observed_counter - truth_counter
    return {
        "truth_rows": sum(truth_counter.values()),
        "observed_rows": sum(observed_counter.values()),
        "exact_rows": exact,
        "missing_rows": sum(missing_counter.values()),
        "missing_unique_keys": len(missing),
        "extra_rows": sum(extra_counter.values()),
        "extra_unique_keys": len(extras),
        "missing_examples": sorted(missing, key=lambda r: (str(r["voucher_type_name"]), int(r["voucher_master_id"])))[:30],
        "extra_examples": sorted(extras, key=lambda r: (str(r["voucher_type_name"]), int(r["voucher_master_id"])))[:30],
    }


def run_to_dict(row: InventoryRun, stock_names: dict[int, str], ledger_names: dict[int, str]) -> dict[str, object]:
    return {
        "voucher_master_id": row.voucher_master_id,
        "voucher_type_name": None,
        "stock_item_master_id": row.stock_item_id,
        "stock_item_name": stock_names.get(row.stock_item_id),
        "ledger_master_id": row.ledger_id,
        "ledger_name": ledger_names.get(row.ledger_id) if row.ledger_id is not None else None,
        "unit_master_id": row.unit_id,
        "quantity_scaled": row.quantity_scaled,
        "rate_scaled": row.rate_scaled,
        "amount_scaled": row.amount_scaled,
        "source": row.source,
        "confidence": row.confidence,
        "page_no": row.page_no,
        "page_offset": row.page_offset,
    }


def parse_sales_logical_post_rate_f6_probe(
    data: bytes,
    pages_by_mid: dict[int, list[int]],
    stock_ids: set[int],
    ledger_ids: set[int],
    unit_ids: set[int],
) -> list[InventoryRun]:
    runs: list[InventoryRun] = []
    seen: set[tuple[int, int, int | None, int, int, int, int, int, str]] = set()
    for mid, pages in pages_by_mid.items():
        for stream, origins in iter_streams_for_pages(data, sorted(pages)):
            positions: set[int] = set()
            for idx, (_page, offset) in enumerate(origins):
                if offset < PAGE_SIZE - len(STOCK_FIELD):
                    continue
                result = read_logical_bytes_with_end(stream, origins, idx, len(STOCK_FIELD) + 4)
                if result is None or result[0][:4] != STOCK_FIELD:
                    continue
                stock_id = resolve_compact_master_id(struct.unpack_from("<I", result[0], 4)[0], stock_ids)
                if stock_id is not None:
                    positions.add(idx)

            for pos in sorted(positions):
                logical = read_logical_bytes_with_end(stream, origins, pos, 900)
                if logical is None:
                    continue
                row = logical[0]
                if row[:4] != STOCK_FIELD:
                    continue
                stock_id = resolve_compact_master_id(struct.unpack_from("<I", row, 4)[0], stock_ids)
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

                stock_repeat = row.find(b"\x6d\x00\x00\x03" + struct.pack("<I", stock_id), TYPE3_CELL_SIZE + 8, 90)
                if stock_repeat < 0:
                    continue
                rate_hit = find_rate_i64(row, stock_repeat + TYPE3_CELL_SIZE, stock_repeat + 120)
                if rate_hit is None:
                    continue
                rate_pos, rate_scaled = rate_hit
                rate_scaled = abs(rate_scaled)
                if rate_scaled == 0:
                    continue

                unit_id = None
                # Productized stock-rate rows store the unit id 20 bytes after the rate marker.
                if rate_pos + 28 <= len(row):
                    unit_id = struct.unpack_from("<I", row, rate_pos + 20)[0]
                    if unit_id not in unit_ids:
                        unit_id = None

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
                _qty_pos, quantity_scaled = quantity_hit
                quantity_scaled = abs(quantity_scaled)
                if quantity_scaled == 0:
                    continue
                if quantity_scaled * rate_scaled != abs(amount_scaled) * int(SCALE):
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
                    page_no,
                    page_offset,
                    "sales_logical_post_rate_f6_probe",
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
                        source="sales_logical_post_rate_f6_probe",
                        confidence="probe",
                        page_no=page_no,
                        page_offset=page_offset,
                    )
                )
    return runs


def filter_probe(rows: list[dict[str, object]], guard: str, repair_mids: set[int]) -> list[dict[str, object]]:
    out = []
    for row in rows:
        if guard == "all":
            out.append(row)
        elif guard == "repair_only":
            if int(row["voucher_master_id"]) in repair_mids:
                out.append(row)
        elif guard == "repair_positive_page_edge":
            if (
                int(row["voucher_master_id"]) in repair_mids
                and int(row["amount_scaled"]) > 0
                and int(row["page_offset"]) >= PAGE_SIZE - len(STOCK_FIELD)
            ):
                out.append(row)
        elif guard == "repair_positive_page_edge_unit":
            if (
                int(row["voucher_master_id"]) in repair_mids
                and int(row["amount_scaled"]) > 0
                and int(row["page_offset"]) >= PAGE_SIZE - len(STOCK_FIELD)
                and row.get("unit_master_id") is not None
            ):
                out.append(row)
        else:
            raise ValueError(guard)
    return out


def marker_hits(data: bytes, pages: list[int], markers: list[tuple[str, bytes]], *, limit: int = 3) -> dict[str, list[dict[str, object]]]:
    hits: dict[str, list[dict[str, object]]] = {name: [] for name, _marker in markers}
    chunks = bytearray()
    origins: list[tuple[int, int]] = []
    for page_no in pages:
        page = data[page_no * PAGE_SIZE : (page_no + 1) * PAGE_SIZE]
        chunks.extend(page)
        origins.extend((page_no, off) for off in range(len(page)))
    stream = bytes(chunks)
    for name, marker in markers:
        pos = 0
        while len(hits[name]) < limit:
            pos = stream.find(marker, pos)
            if pos < 0:
                break
            page_no, page_offset = origins[pos]
            start = max(0, pos - 32)
            end = min(len(stream), pos + len(marker) + 48)
            hits[name].append(
                {
                    "page_no": page_no,
                    "page_offset": page_offset,
                    "hex": stream[start:end].hex(" "),
                }
            )
            pos += 1
    return hits


def atlas(
    data: bytes,
    pages_by_mid: dict[int, list[int]],
    repair_rows: list[sqlite3.Row],
    repair_truth: list[dict[str, object]],
) -> list[dict[str, object]]:
    truth_by_mid: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in repair_truth:
        truth_by_mid[int(row["voucher_master_id"])].append(row)

    out: list[dict[str, object]] = []
    for row in repair_rows:
        mid = int(row["voucher_master_id"])
        markers: list[tuple[str, bytes]] = []
        for truth in truth_by_mid.get(mid, []):
            stock_id = truth.get("stock_item_master_id")
            ledger_id = truth.get("ledger_master_id")
            if stock_id is not None:
                markers.append((f"stock:{stock_id}", STOCK_FIELD + struct.pack("<I", int(stock_id))))
            if ledger_id is not None:
                markers.append((f"ledger:{ledger_id}", STOCK_FIELD + struct.pack("<I", int(ledger_id))))
            markers.append((f"amount:{truth['amount_scaled']}", struct.pack("<q", int(truth["amount_scaled"]))))
            markers.append((f"qty_abs:{abs(int(truth['quantity_scaled']))}", struct.pack("<q", abs(int(truth["quantity_scaled"])))))
            markers.append((f"qty_neg:{-abs(int(truth['quantity_scaled']))}", struct.pack("<q", -abs(int(truth["quantity_scaled"])))))
            markers.append((f"rate:{truth['rate_scaled']}", struct.pack("<q", int(truth["rate_scaled"]))))
        page_list = pages_by_mid.get(mid, [])
        out.append(
            {
                "voucher_master_id": mid,
                "voucher_type_name": row["voucher_type_name"],
                "page_group_id": row["page_group_id"],
                "page_count": len(page_list),
                "pages": page_list[:12],
                "truth_rows": truth_by_mid.get(mid, []),
                "byte_hits": marker_hits(data, page_list, markers) if markers else {},
            }
        )
    return out


def grouped_queue(decoded: sqlite3.Connection) -> dict[str, int]:
    return {
        str(name): int(count)
        for name, count in decoded.execute(
            """
            select voucher_type_name, count(*)
            from xml_repair_queue
            group by voucher_type_name
            order by count(*) desc
            """
        )
    }


def write_markdown(path: Path, title: str, payload: dict[str, object]) -> None:
    lines = [f"# {title}", ""]
    lines.append("```json")
    lines.append(json.dumps(payload, indent=2, sort_keys=True))
    lines.append("```")
    path.write_text("\n".join(lines) + "\n")


def json_sanitize(value: object) -> object:
    if isinstance(value, Counter):
        return [
            {"key": json_sanitize(key), "count": count}
            for key, count in value.most_common()
        ]
    if isinstance(value, dict):
        return {str(key): json_sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_sanitize(item) for item in value]
    if isinstance(value, sqlite3.Row):
        return {key: json_sanitize(value[key]) for key in value.keys()}
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decoded-070525", type=Path, default=Path("out/070525_decoded_laneA_sales_logical.sqlite3"))
    parser.add_argument("--decoded-100025", type=Path, default=Path("out/100025_decoded_current.sqlite3"))
    parser.add_argument("--tranmgr-070525", type=Path, default=Path("live-tally-data/070525/TranMgr.1800"))
    parser.add_argument("--rosetta", type=Path, default=Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3"))
    parser.add_argument("--out-dir", type=Path, default=Path("out/w2a-repair-atlas"))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    decoded = sqlite3.connect(args.decoded_070525)
    decoded.row_factory = sqlite3.Row
    decoded_100025 = sqlite3.connect(args.decoded_100025)
    decoded_100025.row_factory = sqlite3.Row
    rosetta = sqlite3.connect(args.rosetta)

    stock_name_to_id = load_rosetta_ids(rosetta, "stock_items")
    ledger_name_to_id = load_rosetta_ids(rosetta, "ledgers")
    stock_names = load_names(decoded, "stock_items")
    ledger_names = load_names(decoded, "ledgers")
    group_to_mid = load_group_to_mid(decoded)
    voucher_types = load_voucher_types(decoded)
    unit_ids = load_units(decoded)
    data = args.tranmgr_070525.read_bytes()
    pages_by_mid = load_pages_by_mid(data, group_to_mid)

    focus_types = {"61 Sales", "31-Puchase(Monthly)", "Conversion Stock Journal"}
    repairs = load_repair_rows(decoded)
    repair_mids = {int(row["voucher_master_id"]) for row in repairs}
    repair_mids_by_type: dict[str, set[int]] = defaultdict(set)
    for row in repairs:
        repair_mids_by_type[str(row["voucher_type_name"])].add(int(row["voucher_master_id"]))

    truth = truth_rows(rosetta, focus_types, stock_name_to_id, ledger_name_to_id)
    final = final_rows(decoded, focus_types)
    cand = candidate_rows(decoded, repair_mids)
    truth_by_type = defaultdict(list)
    final_by_type = defaultdict(list)
    truth_by_mid = defaultdict(list)
    for row in truth:
        truth_by_type[str(row["voucher_type_name"])].append(row)
        truth_by_mid[int(row["voucher_master_id"])].append(row)
    for row in final:
        final_by_type[str(row["voucher_type_name"])].append(row)

    repair_truth_by_queue_type: Counter[str] = Counter()
    repair_truth_actual_by_queue_type: Counter[tuple[str, str]] = Counter()
    for row in repairs:
        qtype = str(row["voucher_type_name"])
        for truth_row in truth_by_mid.get(int(row["voucher_master_id"]), []):
            repair_truth_by_queue_type[qtype] += 1
            repair_truth_actual_by_queue_type[(qtype, str(truth_row["voucher_type_name"]))] += 1

    baseline: dict[str, object] = {
        "queue_070525": grouped_queue(decoded),
        "queue_100025": grouped_queue(decoded_100025),
        "repair_candidate_source_counts_070525": Counter(
            (row["voucher_type_name"], row.get("confidence"), row.get("source")) for row in cand
        ),
        "type_metrics_070525": {},
        "repair_truth_counts_070525": Counter(
            row["voucher_type_name"] for row in truth if int(row["voucher_master_id"]) in repair_mids
        ),
        "repair_truth_counts_by_queue_type_070525": repair_truth_by_queue_type,
        "repair_truth_actual_type_by_queue_type_070525": repair_truth_actual_by_queue_type,
    }
    for voucher_type in sorted(focus_types):
        include_ledger = voucher_type != "Conversion Stock Journal"
        baseline["type_metrics_070525"][voucher_type] = compare_rows(
            truth_by_type[voucher_type],
            final_by_type[voucher_type],
            include_ledger=include_ledger,
        )

    sales_mids = {mid for mid, name in voucher_types.items() if name in {"61 Sales", "Sales"}}
    sales_repair_mids = repair_mids_by_type["61 Sales"]
    sales_truth = truth_by_type["61 Sales"]
    sales_probe_pages = {mid: pages for mid, pages in pages_by_mid.items() if mid in sales_mids}
    sales_logical_probe = [
        run_to_dict(row, stock_names, ledger_names)
        for row in parse_sales_logical_post_rate_f6_probe(
            data,
            sales_probe_pages,
            set(stock_name_to_id.values()),
            set(ledger_name_to_id.values()),
            unit_ids,
        )
    ]
    for row in sales_logical_probe:
        row["voucher_type_name"] = voucher_types.get(int(row["voucher_master_id"]))

    sales_productized_repair_runs = [
        run_to_dict(row, stock_names, ledger_names)
        for row in parse_sales_rows(
            data,
            group_to_mid,
            set(stock_name_to_id.values()),
            set(ledger_name_to_id.values()),
            target_mids=sales_repair_mids,
            unit_ids=unit_ids,
        )
    ]
    for row in sales_productized_repair_runs:
        row["voucher_type_name"] = "61 Sales"

    sales_probe_metrics = {
        "productized_parse_sales_rows_on_repair_mids": compare_rows(
            [row for row in sales_truth if int(row["voucher_master_id"]) in sales_repair_mids],
            sales_productized_repair_runs,
            include_ledger=True,
        ),
        "logical_post_rate_f6_total_candidates": len(sales_logical_probe),
        "logical_post_rate_f6_source_counts": Counter(
            (row["voucher_master_id"], row["page_no"], row["page_offset"]) for row in sales_logical_probe
        ),
        "guards": {},
    }
    for guard in ["all", "repair_only", "repair_positive_page_edge", "repair_positive_page_edge_unit"]:
        guarded = filter_probe(sales_logical_probe, guard, sales_repair_mids)
        sales_probe_metrics["guards"][guard] = {
            "rows": len(guarded),
            "comparison": compare_rows(
                [row for row in sales_truth if (guard == "all" or int(row["voucher_master_id"]) in sales_repair_mids)],
                guarded,
                include_ledger=True,
            ),
            "rows_sample": sorted(guarded, key=lambda r: (int(r["voucher_master_id"]), int(r["page_no"]), int(r["page_offset"])))[:30],
        }

    purchase_repair_mids = repair_mids_by_type["31-Puchase(Monthly)"]
    purchase_truth = truth_by_type["31-Puchase(Monthly)"]
    purchase_runs = [
        run_to_dict(row, stock_names, ledger_names)
        for row in parse_purchase_rows(
            data,
            group_to_mid,
            set(stock_name_to_id.values()),
            set(ledger_name_to_id.values()),
            target_mids=purchase_repair_mids,
            unit_ids=unit_ids,
        )
    ]
    for row in purchase_runs:
        row["voucher_type_name"] = "31-Puchase(Monthly)"
    purchase_metrics = {
        "productized_parse_purchase_rows_on_repair_mids": compare_rows(
            [row for row in purchase_truth if int(row["voucher_master_id"]) in purchase_repair_mids],
            purchase_runs,
            include_ledger=True,
        ),
        "source_counts": Counter(row["source"] for row in purchase_runs),
        "rows_sample": sorted(purchase_runs, key=lambda r: (int(r["voucher_master_id"]), str(r["source"])))[:30],
    }

    conversion_repair_mids = repair_mids_by_type["Conversion Stock Journal"]
    conversion_truth = truth_by_type["Conversion Stock Journal"]
    conversion_pages = {mid: pages for mid, pages in pages_by_mid.items() if mid in conversion_repair_mids}
    conversion_runs = [
        run_to_dict(row, stock_names, ledger_names)
        for row in [
            *parse_post_rate_subrecords(data, conversion_pages, set(stock_name_to_id.values())),
            *parse_columnar_pairs(data, conversion_pages, set(stock_name_to_id.values())),
        ]
    ]
    for row in conversion_runs:
        row["voucher_type_name"] = "Conversion Stock Journal"
    conversion_metrics = {
        "productized_value_parsers_on_repair_mids": compare_rows(
            [row for row in conversion_truth if int(row["voucher_master_id"]) in conversion_repair_mids],
            conversion_runs,
            include_ledger=False,
        ),
        "source_counts": Counter(row["source"] for row in conversion_runs),
        "rows_sample": sorted(conversion_runs, key=lambda r: (int(r["voucher_master_id"]), str(r["source"])))[:30],
    }

    repair_truth = [row for row in truth if int(row["voucher_master_id"]) in repair_mids]
    atlas_rows = atlas(data, pages_by_mid, repairs, repair_truth)

    payloads = {
        "baseline": baseline,
        "sales_probe": sales_probe_metrics,
        "purchase_probe": purchase_metrics,
        "conversion_probe": conversion_metrics,
        "atlas": {"rows": atlas_rows},
    }
    for name, payload in payloads.items():
        json_ready = json_sanitize(payload)
        (args.out_dir / f"{name}.json").write_text(json.dumps(json_ready, indent=2, sort_keys=True) + "\n")
        write_markdown(args.out_dir / f"{name}.md", f"W2A {name}", json_ready)

    summary = {
        "out_dir": str(args.out_dir),
        "files": sorted(path.name for path in args.out_dir.iterdir() if path.is_file()),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import struct
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

from .probe import (
    discover_files,
    extract_tagged_field_strings,
    iter_compact_numeric_fields,
    iter_tlvs,
)


PAGE_SIZE = 512
SCALE = Decimal(100000)


@dataclass(frozen=True)
class RawHit:
    file_name: str
    encoding: str
    needle: str
    offset: int
    page_no: int
    page_offset: int
    context_hex: str


@dataclass(frozen=True)
class NumericHit:
    file_name: str
    label: str
    value: int
    width: str
    offset: int
    page_no: int
    page_offset: int
    context_hex: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scaled_decimal(value: str) -> int:
    return int((Decimal(value) * SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tally_date_serial(value: str) -> int:
    yyyy, mm, dd = (int(part) for part in value.split("-"))
    return (date(yyyy, mm, dd) - date(1899, 12, 31)).days


def _find_all(data: bytes, needle: bytes) -> Iterable[int]:
    pos = 0
    while True:
        pos = data.find(needle, pos)
        if pos < 0:
            return
        yield pos
        pos += 1


def _context_hex(data: bytes, offset: int, length: int, radius: int = 24) -> str:
    start = max(0, offset - radius)
    end = min(len(data), offset + length + radius)
    return data[start:end].hex()


def file_map(folder: Path) -> dict[str, Path]:
    return {data_file.name: data_file.path for data_file in discover_files(folder)}


def page_hashes(data: bytes, page_size: int = PAGE_SIZE) -> list[str]:
    return [
        sha256_bytes(data[offset : offset + page_size])
        for offset in range(0, len(data), page_size)
    ]


def changed_pages(before: bytes, after: bytes, page_size: int = PAGE_SIZE) -> dict[str, object]:
    before_hashes = page_hashes(before, page_size)
    after_hashes = page_hashes(after, page_size)
    max_pages = max(len(before_hashes), len(after_hashes))
    changed = [
        idx
        for idx in range(max_pages)
        if (before_hashes[idx] if idx < len(before_hashes) else None)
        != (after_hashes[idx] if idx < len(after_hashes) else None)
    ]
    return {
        "before_pages": len(before_hashes),
        "after_pages": len(after_hashes),
        "changed_count": len(changed),
        "changed_pages_sample": changed[:80],
    }


def raw_text_hits(path: Path, texts: list[str], page_size: int = PAGE_SIZE) -> list[RawHit]:
    data = path.read_bytes()
    hits: list[RawHit] = []
    for text in texts:
        needles = [
            ("utf8", text.encode("utf-8")),
            ("utf16le", text.encode("utf-16le")),
            ("utf16le_nul", text.encode("utf-16le") + b"\x00\x00"),
        ]
        for encoding, needle in needles:
            if not needle:
                continue
            for offset in _find_all(data, needle):
                hits.append(
                    RawHit(
                        file_name=path.name,
                        encoding=encoding,
                        needle=text,
                        offset=offset,
                        page_no=offset // page_size,
                        page_offset=offset % page_size,
                        context_hex=_context_hex(data, offset, len(needle)),
                    )
                )
    return hits


def numeric_needles(numbers: list[str], dates: list[str]) -> list[tuple[str, int, list[tuple[str, bytes]]]]:
    out: list[tuple[str, int, list[tuple[str, bytes]]]] = []
    for raw in numbers:
        values = []
        try:
            integer = int(raw, 0)
            values.append((f"int:{raw}", integer))
        except ValueError:
            pass
        try:
            scaled = scaled_decimal(raw)
            values.append((f"scaled_1e5:{raw}", scaled))
        except Exception:
            pass
        for label, value in values:
            needles: list[tuple[str, bytes]] = []
            if 0 <= value <= 0xFFFFFFFF:
                needles.append(("u32le", struct.pack("<I", value)))
            if -0x80000000 <= value <= 0x7FFFFFFF:
                needles.append(("i32le", struct.pack("<i", value)))
            if -0x8000000000000000 <= value <= 0x7FFFFFFFFFFFFFFF:
                needles.append(("i64le", struct.pack("<q", value)))
            out.append((label, value, needles))
    for raw in dates:
        serial = tally_date_serial(raw)
        needles = [
            ("u32le", struct.pack("<I", serial)),
            ("i64le", struct.pack("<q", serial)),
        ]
        out.append((f"tally_date:{raw}", serial, needles))
    return out


def raw_numeric_hits(
    path: Path,
    numbers: list[str],
    dates: list[str],
    page_size: int = PAGE_SIZE,
    max_hits_per_value: int = 200,
) -> list[NumericHit]:
    data = path.read_bytes()
    hits: list[NumericHit] = []
    for label, value, needles in numeric_needles(numbers, dates):
        per_value = 0
        for width, needle in needles:
            for offset in _find_all(data, needle):
                hits.append(
                    NumericHit(
                        file_name=path.name,
                        label=label,
                        value=value,
                        width=width,
                        offset=offset,
                        page_no=offset // page_size,
                        page_offset=offset % page_size,
                        context_hex=_context_hex(data, offset, len(needle)),
                    )
                )
                per_value += 1
                if per_value >= max_hits_per_value:
                    break
            if per_value >= max_hits_per_value:
                break
    return hits


def compact_numeric_value_hits(
    path: Path,
    numbers: list[str],
    dates: list[str],
    page_size: int = PAGE_SIZE,
) -> list[dict[str, object]]:
    wanted = {value for _label, value, _needles in numeric_needles(numbers, dates) if 0 <= value <= 0xFFFFFFFF}
    if not wanted:
        return []
    rows = []
    for hit in iter_compact_numeric_fields(path, page_size=page_size):
        if hit.value in wanted:
            rows.append(
                {
                    "file_name": hit.file_name,
                    "offset": hit.offset,
                    "offset_hex": f"0x{hit.offset:x}",
                    "page_no": hit.page_no,
                    "page_offset": hit.page_offset,
                    "field_id": hit.field_id,
                    "field_hex": f"0x{hit.field_id:04x}",
                    "type_hex": hit.type_hex,
                    "value": hit.value,
                }
            )
    return rows


def tlv_value_counter(path: Path) -> Counter[tuple[int, str, str]]:
    counter: Counter[tuple[int, str, str]] = Counter()
    for hit in iter_tlvs(path):
        value = str(hit.decoded)
        counter[(hit.field_id, hit.type_hex, value)] += 1
    return counter


def field_string_counter(path: Path) -> Counter[tuple[int | None, str]]:
    counter: Counter[tuple[int | None, str]] = Counter()
    for hit in extract_tagged_field_strings(path, min_chars=1):
        counter[(hit.field_id, hit.text)] += 1
    return counter


def counter_delta(
    before: Counter[tuple[object, ...]],
    after: Counter[tuple[object, ...]],
    limit: int,
) -> dict[str, list[dict[str, object]]]:
    added = []
    removed = []
    for key, count in (after - before).most_common(limit):
        added.append({"key": list(key), "count": count})
    for key, count in (before - after).most_common(limit):
        removed.append({"key": list(key), "count": count})
    return {"added": added, "removed": removed}


def diff_windows(
    before: bytes,
    after: bytes,
    page_no: int,
    page_size: int = PAGE_SIZE,
    max_windows: int = 8,
) -> list[dict[str, object]]:
    start = page_no * page_size
    before_page = before[start : start + page_size]
    after_page = after[start : start + page_size]
    matcher = difflib.SequenceMatcher(a=before_page, b=after_page, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before_abs = start + i1
        after_abs = start + j1
        rows.append(
            {
                "tag": tag,
                "before_offset": before_abs,
                "after_offset": after_abs,
                "before_page_offset": i1,
                "after_page_offset": j1,
                "before_len": i2 - i1,
                "after_len": j2 - j1,
                "before_hex": before_page[max(0, i1 - 16) : min(len(before_page), i2 + 16)].hex(),
                "after_hex": after_page[max(0, j1 - 16) : min(len(after_page), j2 + 16)].hex(),
            }
        )
        if len(rows) >= max_windows:
            break
    return rows


def diff_snapshots(
    before_dir: Path,
    after_dir: Path,
    *,
    files: list[str] | None = None,
    sentinels: list[str] | None = None,
    numbers: list[str] | None = None,
    dates: list[str] | None = None,
    page_size: int = PAGE_SIZE,
    delta_limit: int = 30,
) -> dict[str, object]:
    sentinels = sentinels or []
    numbers = numbers or []
    dates = dates or []
    before_files = file_map(before_dir)
    after_files = file_map(after_dir)
    names = sorted(set(before_files) | set(after_files))
    if files:
        wanted = set(files)
        names = [name for name in names if name in wanted]

    report: dict[str, object] = {
        "before_dir": str(before_dir),
        "after_dir": str(after_dir),
        "sentinels": sentinels,
        "numbers": numbers,
        "dates": dates,
        "files": [],
    }

    file_reports: list[dict[str, object]] = []
    for name in names:
        before_path = before_files.get(name)
        after_path = after_files.get(name)
        row: dict[str, object] = {"file_name": name}
        if before_path is None or after_path is None:
            row["presence"] = "added" if after_path else "removed"
            file_reports.append(row)
            continue

        before_data = before_path.read_bytes()
        after_data = after_path.read_bytes()
        row.update(
            {
                "presence": "both",
                "before_size": len(before_data),
                "after_size": len(after_data),
                "size_delta": len(after_data) - len(before_data),
                "before_sha256": sha256_bytes(before_data),
                "after_sha256": sha256_bytes(after_data),
                "sha256_changed": sha256_bytes(before_data) != sha256_bytes(after_data),
                "pages": changed_pages(before_data, after_data, page_size),
            }
        )

        if row["sha256_changed"]:
            changed_sample = row["pages"]["changed_pages_sample"] if isinstance(row["pages"], dict) else []
            row["diff_windows"] = [
                {"page_no": page_no, "windows": diff_windows(before_data, after_data, int(page_no), page_size)}
                for page_no in list(changed_sample)[:3]
            ]

        if sentinels:
            row["sentinel_raw_hits_after"] = [asdict(hit) for hit in raw_text_hits(after_path, sentinels, page_size)]
            row["sentinel_field_strings_after"] = [
                {
                    "field_id": hit.field_id,
                    "field_hex": f"0x{hit.field_id:04x}" if hit.field_id is not None else None,
                    "text": hit.text,
                    "offset": hit.offset,
                    "page_no": hit.page_no,
                    "page_offset": hit.page_offset,
                }
                for hit in extract_tagged_field_strings(after_path, min_chars=1)
                if any(sentinel in hit.text for sentinel in sentinels)
            ]
            row["sentinel_tlv_hits_after"] = [
                {
                    "field_id": hit.field_id,
                    "field_hex": f"0x{hit.field_id:04x}",
                    "type_hex": hit.type_hex,
                    "decoded": hit.decoded,
                    "offset": hit.offset,
                    "page_no": hit.page_no,
                    "page_offset": hit.page_offset,
                }
                for hit in iter_tlvs(after_path)
                if isinstance(hit.decoded, str)
                and any(sentinel in hit.decoded for sentinel in sentinels)
            ]

        if numbers or dates:
            row["numeric_raw_hits_after"] = [asdict(hit) for hit in raw_numeric_hits(after_path, numbers, dates, page_size)]
            row["compact_numeric_value_hits_after"] = compact_numeric_value_hits(after_path, numbers, dates, page_size)

        if name in {"Manager.1800", "TranMgr.1800", "LinkMgr.1800", "Company.1800", "VchStatus.1800"}:
            try:
                row["tlv_delta"] = counter_delta(tlv_value_counter(before_path), tlv_value_counter(after_path), delta_limit)
            except Exception as exc:
                row["tlv_delta_error"] = str(exc)
            try:
                row["field_string_delta"] = counter_delta(
                    field_string_counter(before_path),
                    field_string_counter(after_path),
                    delta_limit,
                )
            except Exception as exc:
                row["field_string_delta_error"] = str(exc)

        file_reports.append(row)

    report["files"] = file_reports
    report["summary"] = {
        "files_compared": len(file_reports),
        "changed_files": sum(1 for row in file_reports if row.get("sha256_changed")),
        "added_files": sum(1 for row in file_reports if row.get("presence") == "added"),
        "removed_files": sum(1 for row in file_reports if row.get("presence") == "removed"),
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diff two Tally .1800 snapshots for synthetic RE experiments.")
    parser.add_argument("before_dir", type=Path)
    parser.add_argument("after_dir", type=Path)
    parser.add_argument("--file", action="append", default=None, help="specific file name to compare, repeatable")
    parser.add_argument("--sentinel", action="append", default=[], help="text value intentionally injected")
    parser.add_argument("--number", action="append", default=[], help="integer or decimal value intentionally injected")
    parser.add_argument("--date", action="append", default=[], help="YYYY-MM-DD date intentionally injected")
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    parser.add_argument("--delta-limit", type=int, default=30)
    parser.add_argument("--out", type=Path, help="write JSON report here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = diff_snapshots(
        args.before_dir.expanduser().resolve(),
        args.after_dir.expanduser().resolve(),
        files=args.file,
        sentinels=args.sentinel,
        numbers=args.number,
        dates=args.date,
        page_size=args.page_size,
        delta_limit=args.delta_limit,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import signal
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .probe import (
    PAGE_SIZES,
    discover_files,
    extract_ascii_strings,
    extract_lenprefixed_utf16le_strings,
    extract_utf16le_strings,
    iter_compact_numeric_fields,
    iter_extended_numeric_fields,
    iter_page_compact_numeric_fields,
    iter_tlvs,
    likely_binary_state,
    page_header_words,
    probe_pages,
    sample_bytes,
    sha256_file,
    suppress_broken_pipe,
    string_contexts,
    whole_file_entropy,
)
from .sqlite_export import export_sqlite
from .decoded_export import export_decoded_sqlite
from .synthetic_diff import diff_snapshots


def cmd_inspect(args: argparse.Namespace) -> int:
    company_dir = Path(args.company_dir).expanduser().resolve()
    files = discover_files(company_dir)
    result = {
        "company_dir": str(company_dir),
        "file_count": len(files),
        "files": [],
    }
    for data_file in files:
        ascii_hits = extract_ascii_strings(data_file.path, min_len=args.min_len)
        utf16_hits = extract_utf16le_strings(data_file.path, min_len=args.min_len)
        entropy = whole_file_entropy(data_file.path)
        probes = probe_pages(sample_bytes(data_file.path, max_bytes=args.probe_bytes), PAGE_SIZES)
        result["files"].append(
            {
                "name": data_file.name,
                "role": data_file.role,
                "suffix": data_file.suffix,
                "size": data_file.size,
                "sha256": sha256_file(data_file.path) if args.sha256 else None,
                "entropy_sample": round(entropy, 4),
                "ascii_strings": len(ascii_hits),
                "utf16le_strings": len(utf16_hits),
                "state": likely_binary_state(entropy, len(ascii_hits) + len(utf16_hits), data_file.size),
                "best_page_probes": [
                    {
                        "page_size": p.page_size,
                        "page_count": p.page_count,
                        "exact_pages": p.exact_pages,
                        "fixed_word4_hits": p.fixed_word4_hits,
                        "sequential_word12_hits": p.sequential_word12_hits,
                        "crc32_le_hits": p.crc32_le_hits,
                        "crc32_be_hits": p.crc32_be_hits,
                        "score": round(p.score, 4),
                    }
                    for p in probes[: args.top]
                ],
            }
        )
    print(json.dumps(result, indent=2))
    return 0


def cmd_strings(args: argparse.Namespace) -> int:
    company_dir = Path(args.company_dir).expanduser().resolve()
    for data_file in discover_files(company_dir):
        hits = extract_ascii_strings(data_file.path, args.min_len)
        if args.utf16:
            hits += extract_utf16le_strings(data_file.path, args.min_len)
        try:
            for hit in sorted(hits, key=lambda h: (h.file_name, h.offset)):
                text = hit.text
                if len(text) > args.max_text:
                    text = text[: args.max_text] + "..."
                print(f"{hit.file_name}\t{hit.offset}\t{hit.encoding}\t{text}")
        except BrokenPipeError:
            suppress_broken_pipe()
            return 0
    return 0


def cmd_sqlite(args: argparse.Namespace) -> int:
    company_dir = Path(args.company_dir).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    export_sqlite(company_dir, out_path, page_size=args.page_size)
    print(str(out_path))
    return 0


def cmd_decode_sqlite(args: argparse.Namespace) -> int:
    company_dir = Path(args.company_dir).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    export_decoded_sqlite(company_dir, out_path)
    print(str(out_path))
    return 0


def cmd_synthetic_diff(args: argparse.Namespace) -> int:
    report = diff_snapshots(
        Path(args.before_dir).expanduser().resolve(),
        Path(args.after_dir).expanduser().resolve(),
        files=args.file,
        sentinels=args.sentinel,
        numbers=args.number,
        dates=args.date,
        page_size=args.page_size,
        delta_limit=args.delta_limit,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
        print(str(out_path))
    else:
        print(text)
    return 0


def cmd_pages(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    rows = page_header_words(path, page_size=args.page_size, limit=args.limit)
    print(json.dumps({"file": str(path), "page_size": args.page_size, "pages": rows}, indent=2))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    rows = string_contexts(
        path,
        needle=args.needle,
        min_len=args.min_len,
        before=args.before,
        after=args.after,
        limit=args.limit,
    )
    print(json.dumps({"file": str(path), "contexts": rows}, indent=2))
    return 0


def cmd_lpstrings(args: argparse.Namespace) -> int:
    company_dir = Path(args.company_dir).expanduser().resolve()
    try:
        for data_file in discover_files(company_dir):
            if args.file and args.file.lower() not in data_file.name.lower():
                continue
            hits = extract_lenprefixed_utf16le_strings(
                data_file.path,
                min_chars=args.min_chars,
                max_bytes=args.max_bytes,
                min_ascii_ratio=args.min_ascii_ratio,
            )
            for hit in hits[: args.per_file_limit] if args.per_file_limit else hits:
                text = hit.text
                if len(text) > args.max_text:
                    text = text[: args.max_text] + "..."
                print(
                    f"{hit.file_name}\t{hit.offset}\t{hit.length_bytes}\t"
                    f"{hit.text_offset}\t{text}"
                )
    except BrokenPipeError:
        suppress_broken_pipe()
        return 0
    return 0


def _sample_value(value: object, max_text: int) -> object:
    if isinstance(value, str) and len(value) > max_text:
        return value[:max_text] + "..."
    return value


def cmd_tlv_hist(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    by_type: Counter[str] = Counter()
    by_field_type: Counter[tuple[int, str]] = Counter()
    samples: dict[tuple[int, str], list[object]] = defaultdict(list)
    total = 0
    for hit in iter_tlvs(
        path,
        max_bytes=args.max_bytes,
        max_string_bytes=args.max_string_bytes,
        min_printable_ratio=args.min_printable_ratio,
        page_size=args.page_size,
    ):
        total += 1
        by_type[hit.type_hex] += 1
        key = (hit.field_id, hit.type_hex)
        by_field_type[key] += 1
        if len(samples[key]) < args.samples:
            samples[key].append(_sample_value(hit.decoded, args.max_text))
    result = {
        "file": str(path),
        "bytes_scanned": path.stat().st_size if args.max_bytes <= 0 else min(args.max_bytes, path.stat().st_size),
        "valid_tlvs": total,
        "by_type": [{"type_hex": t, "count": c} for t, c in by_type.most_common(args.top)],
        "by_field_type": [
            {
                "field_id": field_id,
                "field_hex": f"0x{field_id:04x}",
                "type_hex": type_hex,
                "count": count,
                "samples": samples[(field_id, type_hex)],
            }
            for (field_id, type_hex), count in by_field_type.most_common(args.top)
        ],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _parse_int_set(values: list[str] | None) -> set[int] | None:
    if not values:
        return None
    return {int(value, 0) for value in values}


def cmd_compact_scan(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    field_ids = _parse_int_set(args.field_id)
    type_hexes = set(args.type_hex) if args.type_hex else None
    hits = []
    by_field_type: Counter[tuple[int, str]] = Counter()
    if args.extended:
        iterator = iter_extended_numeric_fields
    elif args.page_bounded:
        iterator = iter_page_compact_numeric_fields
    else:
        iterator = iter_compact_numeric_fields
    for hit in iterator(
        path,
        field_ids=field_ids,
        type_hexes=type_hexes,
        max_bytes=args.max_bytes,
        page_size=args.page_size,
    ):
        by_field_type[(hit.field_id, hit.type_hex)] += 1
        if len(hits) < args.limit:
            hits.append(
                {
                    "offset": hit.offset,
                    "offset_hex": f"0x{hit.offset:x}",
                    "page_no": hit.page_no,
                    "page_offset": hit.page_offset,
                    "field_id": hit.field_id,
                    "field_hex": f"0x{hit.field_id:04x}",
                    "type_hex": hit.type_hex,
                    "value": hit.value,
                    "value_hex": f"0x{hit.value & ((1 << 64) - 1):016x}" if args.extended else f"0x{hit.value:08x}",
                }
            )
    result = {
        "file": str(path),
        "bytes_scanned": path.stat().st_size if args.max_bytes <= 0 else min(args.max_bytes, path.stat().st_size),
        "counts": [
            {
                "field_id": field_id,
                "field_hex": f"0x{field_id:04x}",
                "type_hex": type_hex,
                "count": count,
            }
            for (field_id, type_hex), count in by_field_type.most_common(args.top)
        ],
        "samples": hits,
    }
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tally1800")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="inspect a Tally company folder")
    inspect.add_argument("company_dir")
    inspect.add_argument("--min-len", type=int, default=5)
    inspect.add_argument("--probe-bytes", type=int, default=16 * 1024 * 1024)
    inspect.add_argument("--top", type=int, default=3)
    inspect.add_argument("--sha256", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    strings = sub.add_parser("strings", help="extract visible strings from data files")
    strings.add_argument("company_dir")
    strings.add_argument("--min-len", type=int, default=5)
    strings.add_argument("--max-text", type=int, default=160)
    strings.add_argument("--utf16", action="store_true")
    strings.set_defaults(func=cmd_strings)

    sqlite_cmd = sub.add_parser("sqlite", help="export raw probe data to SQLite")
    sqlite_cmd.add_argument("company_dir")
    sqlite_cmd.add_argument("out")
    sqlite_cmd.add_argument("--page-size", type=int, default=512)
    sqlite_cmd.set_defaults(func=cmd_sqlite)

    decode_sqlite = sub.add_parser(
        "decode-sqlite",
        help="export first-pass decoded semantic tables to SQLite",
    )
    decode_sqlite.add_argument("company_dir")
    decode_sqlite.add_argument("out")
    decode_sqlite.set_defaults(func=cmd_decode_sqlite)

    synthetic_diff = sub.add_parser(
        "synthetic-diff",
        help="diff two controlled .1800 snapshots and search sentinels/numbers",
    )
    synthetic_diff.add_argument("before_dir")
    synthetic_diff.add_argument("after_dir")
    synthetic_diff.add_argument("--file", action="append", default=None)
    synthetic_diff.add_argument("--sentinel", action="append", default=[])
    synthetic_diff.add_argument("--number", action="append", default=[])
    synthetic_diff.add_argument("--date", action="append", default=[])
    synthetic_diff.add_argument("--page-size", type=int, default=512)
    synthetic_diff.add_argument("--delta-limit", type=int, default=30)
    synthetic_diff.add_argument("--out")
    synthetic_diff.set_defaults(func=cmd_synthetic_diff)

    pages = sub.add_parser("pages", help="dump page header words for one file")
    pages.add_argument("file")
    pages.add_argument("--page-size", type=int, default=512)
    pages.add_argument("--limit", type=int, default=16)
    pages.set_defaults(func=cmd_pages)

    context = sub.add_parser("context", help="dump bytes around decoded strings in one file")
    context.add_argument("file")
    context.add_argument("--needle")
    context.add_argument("--min-len", type=int, default=5)
    context.add_argument("--before", type=int, default=32)
    context.add_argument("--after", type=int, default=32)
    context.add_argument("--limit", type=int, default=20)
    context.set_defaults(func=cmd_context)

    lpstrings = sub.add_parser(
        "lpstrings",
        help="extract probable 2-byte-length-prefixed UTF-16LE strings",
    )
    lpstrings.add_argument("company_dir")
    lpstrings.add_argument("--min-chars", type=int, default=3)
    lpstrings.add_argument("--file", help="case-insensitive substring filter for file name")
    lpstrings.add_argument("--max-bytes", type=int, default=512)
    lpstrings.add_argument("--min-ascii-ratio", type=float, default=0.85)
    lpstrings.add_argument("--max-text", type=int, default=160)
    lpstrings.add_argument("--per-file-limit", type=int, default=0)
    lpstrings.set_defaults(func=cmd_lpstrings)

    tlv_hist = sub.add_parser("tlv-hist", help="scan a .1800 file for typed TLV fields")
    tlv_hist.add_argument("file")
    tlv_hist.add_argument("--max-bytes", type=int, default=0, help="0 scans the whole file")
    tlv_hist.add_argument("--page-size", type=int, default=512)
    tlv_hist.add_argument("--max-string-bytes", type=int, default=4096)
    tlv_hist.add_argument("--min-printable-ratio", type=float, default=0.85)
    tlv_hist.add_argument("--top", type=int, default=40)
    tlv_hist.add_argument("--samples", type=int, default=2)
    tlv_hist.add_argument("--max-text", type=int, default=120)
    tlv_hist.set_defaults(func=cmd_tlv_hist)

    compact = sub.add_parser("compact-scan", help="scan compact numeric fields")
    compact.add_argument("file")
    compact.add_argument("--field-id", action="append", help="integer field id, e.g. 0x0bbb")
    compact.add_argument("--type-hex", action="append", help="type bytes without 0x, e.g. 0006")
    compact.add_argument("--max-bytes", type=int, default=0, help="0 scans the whole file")
    compact.add_argument("--page-size", type=int, default=512)
    compact.add_argument(
        "--page-bounded",
        action="store_true",
        help="experimental: parse 10-byte compact slots from page +28 until first TLV marker",
    )
    compact.add_argument(
        "--extended",
        action="store_true",
        help="parse 14-byte extended slots: 80 00 <field> <type> <i64 value>",
    )
    compact.add_argument("--top", type=int, default=40)
    compact.add_argument("--limit", type=int, default=20)
    compact.set_defaults(func=cmd_compact_scan)

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)

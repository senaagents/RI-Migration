"""Command-line entry point for tally1800.

Usage:

    python3 -m tally1800 extract <company_dir> [-o out.sqlite]

Runs the canonical master / voucher / linkmgr extractors over a Tally
company folder and writes a single SQLite combining all known tables.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .extract import extract_company


def cmd_extract(args: argparse.Namespace) -> int:
    company_dir = Path(args.company_dir).expanduser().resolve()
    if not company_dir.exists():
        print(f"error: company_dir does not exist: {company_dir}", file=sys.stderr)
        return 2
    if not (company_dir / "Manager.1800").exists():
        print(f"error: {company_dir}/Manager.1800 not found", file=sys.stderr)
        return 2

    out_path = Path(args.output).expanduser().resolve() if args.output else (
        Path.cwd() / f"{company_dir.name}_tally.sqlite3"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    res = extract_company(
        company_dir=company_dir,
        out_path=out_path,
        company_name=args.company_name,
        rosetta=Path(args.rosetta) if args.rosetta else None,
        keep_intermediate=args.keep_intermediate,
    )
    print()
    print(f"Wrote {out_path}")
    print(f"  master_records: {res['master_records']:,}")
    print(f"  vouchers:       {res['vouchers']:,}")
    print(f"  linkmgr_pages:  {res['linkmgr_pages']:,}")
    print(f"  ledger_name_links: {res['ledger_name_links']:,}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tally1800")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser(
        "extract",
        help="extract masters + vouchers + linkmgr from a Tally company folder",
    )
    extract.add_argument("company_dir", help="path to corpus/<company>/")
    extract.add_argument("-o", "--output", default=None,
                         help="output SQLite path (default: ./<company>_tally.sqlite3)")
    extract.add_argument("--company-name", default=None,
                         help="best-guess Rosetta company_name (used for validation only)")
    extract.add_argument(
        "--rosetta",
        default="/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3",
        help="path to rosetta.sqlite3 (validation reference, optional)",
    )
    extract.add_argument(
        "--keep-intermediate", action="store_true",
        help="keep per-stage SQLite outputs in out/ (debugging)",
    )
    extract.set_defaults(func=cmd_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)

#!/usr/bin/env python3
"""Standalone harness for the HTTP/XML extract path.

Drives `tally_http_path.run()` directly against a running TallyPrime
without going through the Sena pairing flow — useful for end-to-end
testing of the parallel extractor against any Tally (educational mode
included; reads are unrestricted).

Usage:
  python3 run_http_extract.py \\
      --host 172.19.224.1 --ports 9000 \\
      --company "Avinash Industries - Chennai Unit - 2025-26" \\
      --from-date 2025-04-01 --to-date 2025-04-30

Output: a SQLite at extract_output_path(...). The file path is printed at
the end so callers can pipe it into sqlite3 / DB Browser for inspection.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sena_tally_bridge import BridgeConfig, log
from tally_http_path import run as run_http_extract


def parse_args(argv: list[str]) -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run the HTTP/XML Tally extract end-to-end without Sena.")
	parser.add_argument("--host", default="localhost", help="Tally HTTP host (default localhost)")
	parser.add_argument("--ports", default="9000",
	                    help="Comma-separated list of TallyPrime HTTP ports, eg 9000,9001,9002")
	parser.add_argument("--company", required=True, help="Loaded company name (used as SVCURRENTCOMPANY)")
	parser.add_argument("--from-date", required=True, help="Voucher range start, YYYY-MM-DD or YYYYMMDD")
	parser.add_argument("--to-date", required=True, help="Voucher range end, YYYY-MM-DD or YYYYMMDD")
	parser.add_argument("--company-id", default="", help="Override the slug used in the output path")
	parser.add_argument("--timeout", type=int, default=120, help="HTTP timeout per request, seconds")
	return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
	args = parse_args(argv or sys.argv[1:])
	ports = [int(p.strip()) for p in args.ports.split(",") if p.strip()]
	if not ports:
		raise SystemExit("--ports needs at least one port")

	# server="" puts heartbeat/ack into standalone mode (no Sena round-trips).
	config = BridgeConfig(
		server="",
		pairing_code="",
		tally_host=args.host,
		tally_port=ports[0],
		timeout=args.timeout,
	)
	command = {
		"id": f"standalone-{int(time.time())}",
		"type": "http_extract",
		"company_name": args.company,
		"from_date": args.from_date,
		"to_date": args.to_date,
		"tally_ports": ports,
		"host": args.host,
	}
	if args.company_id:
		command["company_id"] = args.company_id

	log(f"Standalone HTTP extract — host={args.host} ports={ports} "
	    f"company={args.company!r} range={args.from_date}..{args.to_date}")
	started = time.time()
	try:
		run_http_extract(config, "", "", command["id"], command)
	except Exception as exc:
		log(f"FAILED: {exc}")
		raise
	elapsed = time.time() - started
	print(json.dumps({"ok": True, "elapsed_seconds": round(elapsed, 1)}))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

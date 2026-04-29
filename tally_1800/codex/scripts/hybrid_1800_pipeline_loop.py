#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


DEFAULT_PIPELINE_ROOT = Path("/Users/aakashchid/workshop/tally-db-pipeline")


@dataclass(frozen=True)
class RepairTarget:
    voucher_master_id: int
    guid_guess: str | None
    voucher_date_guess: str | None
    voucher_number_guess: str | None
    voucher_type_name: str | None
    priority: int
    reason_codes: list[str]
    mode: str


def connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_names(con: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in con.execute("select name from sqlite_master where type='table'")
    }


def load_repair_targets(con: sqlite3.Connection, mode: str, limit: int | None) -> list[RepairTarget]:
    tables = table_names(con)
    targets: list[RepairTarget] = []
    if mode in {"repair", "both"}:
        if "xml_repair_queue" not in tables:
            raise RuntimeError("decoded DB has no xml_repair_queue table")
        sql = """
            select voucher_master_id, guid_guess, voucher_date_guess,
                   voucher_number_guess, voucher_type_name, repair_priority,
                   reason_codes_json
            from xml_repair_queue
            order by repair_priority desc, voucher_date_guess, voucher_master_id
        """
        for row in con.execute(sql):
            targets.append(
                RepairTarget(
                    voucher_master_id=int(row["voucher_master_id"]),
                    guid_guess=row["guid_guess"],
                    voucher_date_guess=row["voucher_date_guess"],
                    voucher_number_guess=row["voucher_number_guess"],
                    voucher_type_name=row["voucher_type_name"],
                    priority=int(row["repair_priority"]),
                    reason_codes=json_list(row["reason_codes_json"]),
                    mode="repair",
                )
            )
    if mode in {"review", "both"}:
        if "voucher_decode_audit" not in tables:
            raise RuntimeError("decoded DB has no voucher_decode_audit table")
        sql = """
            select master_id as voucher_master_id, guid_guess, voucher_date_guess,
                   voucher_number_guess, voucher_type_name, issue_codes_json
            from voucher_decode_audit
            where needs_xml_review = 1
              and needs_xml_repair = 0
            order by voucher_date_guess, master_id
        """
        for row in con.execute(sql):
            targets.append(
                RepairTarget(
                    voucher_master_id=int(row["voucher_master_id"]),
                    guid_guess=row["guid_guess"],
                    voucher_date_guess=row["voucher_date_guess"],
                    voucher_number_guess=row["voucher_number_guess"],
                    voucher_type_name=row["voucher_type_name"],
                    priority=50,
                    reason_codes=json_list(row["issue_codes_json"]),
                    mode="review",
                )
            )
    targets.sort(key=lambda item: (item.mode != "repair", -item.priority, item.voucher_date_guess or "", item.voucher_master_id))
    if limit is not None:
        return targets[:limit]
    return targets


def json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def pipeline_command(
    target: RepairTarget,
    *,
    company: str,
    pipeline_root: Path,
    host: str,
    port: int,
) -> str:
    binary = pipeline_root / ".venv" / "bin" / "tally-db-pipeline"
    executable = str(binary) if binary.exists() else "tally-db-pipeline"
    parts = [
        f"TALLY_HOST={shlex.quote(host)}",
        f"TALLY_PORT={int(port)}",
        shlex.quote(executable),
        "sync-voucher-detail-master-id",
        "--company",
        shlex.quote(company),
        "--master-id",
        str(target.voucher_master_id),
    ]
    return " ".join(parts)


def target_to_dict(
    target: RepairTarget,
    *,
    company: str,
    pipeline_root: Path,
    host: str,
    port: int,
) -> dict[str, Any]:
    return {
        "mode": target.mode,
        "voucher_master_id": target.voucher_master_id,
        "guid_guess": target.guid_guess,
        "voucher_date_guess": target.voucher_date_guess,
        "voucher_number_guess": target.voucher_number_guess,
        "voucher_type_name": target.voucher_type_name,
        "priority": target.priority,
        "reason_codes": target.reason_codes,
        "pipeline_command": pipeline_command(
            target,
            company=company,
            pipeline_root=pipeline_root,
            host=host,
            port=port,
        ),
    }


def write_plan(
    targets: list[RepairTarget],
    *,
    decoded_db: Path,
    out_dir: Path,
    company: str,
    pipeline_root: Path,
    host: str,
    port: int,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "decoded_db": str(decoded_db),
        "company": company,
        "tally_host": host,
        "tally_port": port,
        "pipeline_root": str(pipeline_root),
        "target_count": len(targets),
        "targets": [
            target_to_dict(
                target,
                company=company,
                pipeline_root=pipeline_root,
                host=host,
                port=port,
            )
            for target in targets
        ],
    }
    (out_dir / "repair-plan.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    commands = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"cd {shlex.quote(str(pipeline_root))}",
        "",
    ]
    for target in targets:
        commands.append(
            pipeline_command(
                target,
                company=company,
                pipeline_root=pipeline_root,
                host=host,
                port=port,
            )
        )
    (out_dir / "repair-commands.sh").write_text("\n".join(commands) + "\n", encoding="utf-8")
    lines = [
        "# Hybrid 1800/XML Repair Plan",
        "",
        f"- decoded DB: `{decoded_db}`",
        f"- company: `{company}`",
        f"- Tally: `{host}:{port}`",
        f"- targets: `{len(targets)}`",
        "",
        "## Targets",
        "",
        "| Mode | MASTERID | Date | Type | Priority | Reasons |",
        "| --- | ---: | --- | --- | ---: | --- |",
    ]
    for target in targets:
        lines.append(
            "| "
            + " | ".join(
                [
                    target.mode,
                    str(target.voucher_master_id),
                    target.voucher_date_guess or "",
                    target.voucher_type_name or "",
                    str(target.priority),
                    ", ".join(target.reason_codes),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Next Step",
            "",
            "Run `repair-commands.sh` only when the matching Tally company is open and the",
            "port map has been verified. Commands use narrow `MASTERID` object exports, not",
            "broad XML reports.",
        ]
    )
    (out_dir / "repair-plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_masterid_batch_request(company: str, master_ids: list[int]) -> str:
    if not master_ids:
        raise ValueError("at least one MASTERID is required")
    invalid = [mid for mid in master_ids if mid <= 0]
    if invalid:
        raise ValueError(f"invalid MASTERID values: {invalid}")
    filter_name = "CODX1800MasterIDBatchFilter"
    filter_formula = " OR ".join(f"$MasterID = {int(mid)}" for mid in master_ids)
    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>EXPORT</TALLYREQUEST>"
        "<TYPE>COLLECTION</TYPE>"
        "<ID>CODX1800VoucherRepairBatch</ID>"
        "</HEADER>"
        "<BODY><DESC>"
        "<STATICVARIABLES>"
        f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
        "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
        "</STATICVARIABLES>"
        "<TDL><TDLMESSAGE>"
        '<COLLECTION NAME="CODX1800VoucherRepairBatch" ISINITIALIZE="Yes">'
        "<TYPE>Voucher</TYPE>"
        f"<FILTER>{escape(filter_name)}</FILTER>"
        "<FETCH>*</FETCH>"
        "<FETCH>ALLLEDGERENTRIES.*</FETCH>"
        "<FETCH>ALLINVENTORYENTRIES.*</FETCH>"
        "<FETCH>BILLALLOCATIONS.*</FETCH>"
        "<FETCH>BANKALLOCATIONS.*</FETCH>"
        "<FETCH>BATCHALLOCATIONS.*</FETCH>"
        "</COLLECTION>"
        f'<SYSTEM TYPE="Formulae" NAME="{escape(filter_name)}">{escape(filter_formula)}</SYSTEM>'
        "</TDLMESSAGE></TDL>"
        "</DESC></BODY>"
        "</ENVELOPE>"
    )


def command_plan(args: argparse.Namespace) -> None:
    decoded_db = Path(args.decoded_db)
    with connect_ro(decoded_db) as con:
        targets = load_repair_targets(con, args.mode, args.limit)
    write_plan(
        targets,
        decoded_db=decoded_db,
        out_dir=Path(args.out_dir),
        company=args.company,
        pipeline_root=Path(args.pipeline_root),
        host=args.host,
        port=args.port,
    )
    print(f"wrote {len(targets)} targets to {args.out_dir}")


def command_request(args: argparse.Namespace) -> None:
    master_ids = [int(part.strip()) for part in args.master_ids.split(",") if part.strip()]
    xml = build_masterid_batch_request(args.company, master_ids)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(xml + "\n", encoding="utf-8")
        print(args.out)
    else:
        print(xml)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bridge direct .1800 decode repair queues to tally-db-pipeline XML repair."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Create a repair plan from a decoded .1800 SQLite DB.")
    plan.add_argument("--decoded-db", required=True)
    plan.add_argument("--company", required=True)
    plan.add_argument("--out-dir", required=True)
    plan.add_argument("--mode", choices=["repair", "review", "both"], default="repair")
    plan.add_argument("--limit", type=int)
    plan.add_argument("--pipeline-root", default=str(DEFAULT_PIPELINE_ROOT))
    plan.add_argument("--host", default="10.211.55.3")
    plan.add_argument("--port", type=int, default=9001)
    plan.set_defaults(func=command_plan)

    request = sub.add_parser("request", help="Build a narrow inline-TDL XML request for MASTERIDs.")
    request.add_argument("--company", required=True)
    request.add_argument("--master-ids", required=True, help="Comma-separated MASTERID list.")
    request.add_argument("--out")
    request.set_defaults(func=command_request)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

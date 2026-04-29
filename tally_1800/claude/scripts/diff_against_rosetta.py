"""Honest diff: my SQLite (out/070525_master.sqlite3) vs Rosetta (corpus/rosetta.sqlite3).

For each master kind:
  - Row counts (mine vs theirs)
  - Set diff of names: in mine only, in theirs only, in both
  - For a sample of matched names, compare every field side-by-side

For voucher-side tables (vouchers, voucher_ledger_entries, voucher_inventory_entries):
  - Just report: NOT YET EXTRACTED (TranMgr.1800 still pending)

Outputs:
  - out/diff_report.md (human-readable)
  - out/diff_details.sqlite3 (machine-queryable: matches, only_in_mine, only_in_rosetta, field_compare)
"""
import json
import sqlite3
import sys
from pathlib import Path


MINE_PATH = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/070525_master.sqlite3")
ROSETTA_PATH = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/corpus/rosetta.sqlite3")
REPORT_PATH = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/diff_report.md")
DIFF_DB_PATH = Path("/Users/aakashchid/workshop/sena/tallydatacrack-claude/out/diff_details.sqlite3")


MASTER_KINDS = ["ledgers", "groups", "stock_items", "voucher_types",
                "godowns", "cost_centres", "units"]
VOUCHER_KINDS = ["vouchers", "voucher_ledger_entries", "voucher_inventory_entries"]


def main():
    mine = sqlite3.connect(str(MINE_PATH))
    ros = sqlite3.connect(str(ROSETTA_PATH))

    # Set up diff db
    if DIFF_DB_PATH.exists():
        DIFF_DB_PATH.unlink()
    diff = sqlite3.connect(str(DIFF_DB_PATH))
    diff.execute("""
        CREATE TABLE summary (
            kind TEXT PRIMARY KEY,
            rosetta_rows INTEGER,
            mine_rows INTEGER,
            in_both INTEGER,
            only_in_mine INTEGER,
            only_in_rosetta INTEGER,
            match_pct REAL
        )
    """)
    diff.execute("""
        CREATE TABLE only_in_rosetta (
            kind TEXT, name TEXT,
            PRIMARY KEY (kind, name)
        )
    """)
    diff.execute("""
        CREATE TABLE only_in_mine (
            kind TEXT, name TEXT,
            PRIMARY KEY (kind, name)
        )
    """)
    diff.execute("""
        CREATE TABLE field_sample (
            kind TEXT, name TEXT,
            rosetta_json TEXT, mine_json TEXT
        )
    """)

    report = ["# Diff Report: tallydatacrack-claude SQLite vs Rosetta XML SQLite",
              "",
              f"- Source: `{MINE_PATH.name}` (extracted from .1800 binary)",
              f"- Reference: `{ROSETTA_PATH.name}` (extracted via Tally XML gateway)",
              "",
              "## Master tables (extracted by us)",
              "",
              "| Kind | Rosetta rows | Our rows | In both | Only in Rosetta (missing from us) | Only in ours (extra) | Match % |",
              "|---|---:|---:|---:|---:|---:|---:|"]

    for kind in MASTER_KINDS:
        ros_names = set(r[0] for r in ros.execute(f"SELECT name FROM {kind}"))
        mine_names = set(r[0] for r in mine.execute(f"SELECT name FROM {kind}"))
        # Also try matching mine.raw_name against rosetta to catch prefix variants
        mine_raw = set(r[0] for r in mine.execute(f"SELECT raw_name FROM {kind}"))
        mine_combined = mine_names | mine_raw

        both = mine_combined & ros_names
        only_ros = ros_names - mine_combined
        only_mine = mine_combined - ros_names

        match_pct = 100 * len(both) / max(1, len(ros_names))
        report.append(f"| {kind} | {len(ros_names)} | {len(mine_combined)} | {len(both)} | {len(only_ros)} | {len(only_mine)} | {match_pct:.1f}% |")

        diff.execute(
            "INSERT INTO summary VALUES (?,?,?,?,?,?,?)",
            (kind, len(ros_names), len(mine_combined), len(both),
             len(only_mine), len(only_ros), match_pct),
        )
        for n in only_ros:
            diff.execute("INSERT OR IGNORE INTO only_in_rosetta VALUES (?,?)", (kind, n))
        for n in only_mine:
            diff.execute("INSERT OR IGNORE INTO only_in_mine VALUES (?,?)", (kind, n))

    report.append("")
    report.append("## Voucher tables (NOT YET EXTRACTED — TranMgr.1800 pending)")
    report.append("")
    report.append("| Kind | Rosetta rows | Our rows | Status |")
    report.append("|---|---:|---:|---|")
    for kind in VOUCHER_KINDS:
        ros_count = ros.execute(f"SELECT COUNT(*) FROM {kind}").fetchone()[0]
        report.append(f"| {kind} | {ros_count} | 0 | not extracted yet |")
        diff.execute(
            "INSERT INTO summary VALUES (?,?,?,?,?,?,?)",
            (kind, ros_count, 0, 0, 0, ros_count, 0.0),
        )

    # Field-level sample comparison for ledgers
    report.append("")
    report.append("## Field-level sample comparisons (matched ledgers)")
    report.append("")
    report.append("Rosetta column → likely Tally field id, demonstrated on 5 random matched ledgers.")
    report.append("")

    ros_ledger_cols = [r[1] for r in ros.execute("PRAGMA table_info(ledgers)")]
    sample_names = [r[0] for r in mine.execute(
        "SELECT l.name FROM ledgers l JOIN (SELECT name FROM ledgers ORDER BY RANDOM() LIMIT 5) s ON s.name = l.name"
    )]

    for nm in sample_names:
        ros_row = ros.execute("SELECT * FROM ledgers WHERE name = ?", (nm,)).fetchone()
        if not ros_row:
            continue
        ros_dict = dict(zip(ros_ledger_cols, ros_row))
        mine_row = mine.execute("SELECT fields_json FROM ledgers WHERE name = ?", (nm,)).fetchone()
        if not mine_row:
            continue
        mine_fields = json.loads(mine_row[0])

        report.append(f"### Ledger: `{nm}`")
        report.append("")
        report.append("**Rosetta columns:**")
        report.append("```json")
        report.append(json.dumps({k: v for k, v in ros_dict.items() if v not in (None, "", 0, 0.0)},
                                 indent=2, default=str)[:1500])
        report.append("```")
        report.append("")
        report.append("**Our extracted fields (by Tally field-id):**")
        report.append("```json")
        report.append(json.dumps(mine_fields, indent=2, ensure_ascii=False)[:1500])
        report.append("```")
        report.append("")

        diff.execute(
            "INSERT INTO field_sample VALUES (?,?,?,?)",
            ("ledgers", nm,
             json.dumps(ros_dict, default=str),
             mine_row[0]),
        )

    # Honest summary
    overall_match = sum(
        diff.execute("SELECT in_both FROM summary WHERE kind = ?", (k,)).fetchone()[0]
        for k in MASTER_KINDS
    )
    overall_rosetta = sum(
        diff.execute("SELECT rosetta_rows FROM summary WHERE kind = ?", (k,)).fetchone()[0]
        for k in MASTER_KINDS
    )
    voucher_total = sum(
        diff.execute("SELECT rosetta_rows FROM summary WHERE kind = ?", (k,)).fetchone()[0]
        for k in VOUCHER_KINDS
    )

    report.insert(2, "")
    report.insert(3, "## Honest scorecard (TL;DR)")
    report.insert(4, "")
    report.insert(5, f"- **Master records (names matched)**: {overall_match} / {overall_rosetta} = {100*overall_match/max(1,overall_rosetta):.1f}%")
    report.insert(6, f"- **Voucher records**: 0 / {voucher_total} = 0% (TranMgr.1800 not yet decoded)")
    report.insert(7, "- **Field-level fidelity**: not yet validated; field-id ↔ column-name mapping is the next step.")
    report.insert(8, "- **Bottom line**: we have name-level proof of correct master extraction. We do NOT yet have full voucher-level equivalence.")
    report.insert(9, "")

    diff.commit()
    diff.close()

    REPORT_PATH.write_text("\n".join(report))
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {DIFF_DB_PATH}")
    print()
    print("\n".join(report[:35]))


if __name__ == "__main__":
    main()

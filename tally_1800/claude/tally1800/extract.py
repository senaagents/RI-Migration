"""End-to-end extraction: company folder -> single SQLite with all known tables.

Drives the canonical extractors:
  - scripts/extract_v5_multi.py    (Manager.1800 -> master records)
  - scripts/extract_linkmgr.py     (LinkMgr.1800 -> bank/bill allocations + ledger names)
  - scripts/extract_vouchers.py    (TranMgr.1800 + VchStatus.1800 -> vouchers)

Each extractor writes a per-stage SQLite to a tempdir; we then attach all
three to the output database and copy their tables in.
"""
from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _import_script(name: str) -> ModuleType:
    """Import a scripts/<name>.py file as a module."""
    path = SCRIPTS_DIR / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"missing script: {path}")
    spec = importlib.util.spec_from_file_location(f"_tally1800_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_master_extract(
    v5: ModuleType,
    company_dir: Path,
    out_path: Path,
    company_name: str | None,
    rosetta_path: Path | None,
    linkmgr_sqlite: Path | None,
) -> int:
    """Run the master extractor end-to-end, return record count."""
    src = company_dir / "Manager.1800"
    print(f"[masters] reading {src} ({src.stat().st_size:,} bytes)")
    data = src.read_bytes()

    rosetta = None
    if rosetta_path and rosetta_path.exists() and company_name:
        rosetta = v5.load_rosetta_company(rosetta_path, company_name)
        if rosetta:
            print(f"[masters] rosetta company-slice: {len(rosetta['by_master_id'])} guid rows")
        else:
            print(f"[masters] no rosetta data for {company_name!r}")
    else:
        print("[masters] no rosetta company name supplied — structural only")

    records = []
    for mid, pages in v5.page_groups(data):
        rec = v5.extract_record(data, mid, pages)
        records.append(rec)
    print(f"[masters] {len(records):,} master records")

    linkmgr_names = (
        v5.load_linkmgr_name_lookup(linkmgr_sqlite) if linkmgr_sqlite else None
    )
    if linkmgr_names is not None:
        print(f"[masters] linkmgr name lookup: {len(linkmgr_names):,} entries")

    by_kind = defaultdict(list)
    for r in records:
        v5.classify_record(r, rosetta)
        if linkmgr_names is not None and r.get("kind") == "ledgers" and not r.get("extracted_name"):
            cand = linkmgr_names.get(r["master_id"])
            if cand:
                r["extracted_name"] = cand
                r["matched_by"] = (r.get("matched_by") or "") + "+linkmgr"
        by_kind[r["kind"]].append(r)

    out = v5.write_sqlite(out_path, records)
    out.close()
    return len(records)


def _attach_and_copy(out_conn: sqlite3.Connection, src_path: Path, alias: str,
                     tables: list[str]) -> dict[str, int]:
    """Attach src_path and copy named tables into out_conn. Returns row counts."""
    out_conn.execute(f"ATTACH DATABASE ? AS {alias}", (str(src_path),))
    counts = {}
    for t in tables:
        # Drop any pre-existing table with the same name in out_conn.
        out_conn.execute(f"DROP TABLE IF EXISTS {t}")
        # Recreate the table by copying schema + data using CREATE TABLE AS.
        # CREATE TABLE AS doesn't copy indexes, so we re-add a primary-key
        # equivalent index after.
        out_conn.execute(f"CREATE TABLE {t} AS SELECT * FROM {alias}.{t}")
        n = out_conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        counts[t] = n
    out_conn.execute(f"DETACH DATABASE {alias}")
    return counts


def extract_company(
    company_dir: Path,
    out_path: Path,
    company_name: str | None = None,
    rosetta: Path | None = None,
    keep_intermediate: bool = False,
) -> dict:
    """Run all extractors on company_dir and emit a single SQLite at out_path.

    Returns a summary dict with table row counts.
    """
    company_name = company_name or company_dir.name

    # Lazy-import heavy modules so unrelated commands stay fast.
    v5 = _import_script("extract_v5_multi")
    voucher_mod = _import_script("extract_vouchers")
    linkmgr_mod = _import_script("extract_linkmgr")

    work_dir = (
        Path(tempfile.mkdtemp(prefix="tally1800_"))
        if not keep_intermediate else
        REPO_ROOT / "out"
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    linkmgr_sqlite = work_dir / f"{company_dir.name}__linkmgr.sqlite3"
    master_sqlite = work_dir / f"{company_dir.name}__master.sqlite3"
    voucher_sqlite = work_dir / f"{company_dir.name}__vouchers.sqlite3"

    for p in (linkmgr_sqlite, master_sqlite, voucher_sqlite):
        if p.exists():
            p.unlink()

    # Stage 1: LinkMgr (run first; master extractor uses it for fallback names).
    if (company_dir / "LinkMgr.1800").exists():
        print(f"\n[linkmgr] === {company_dir.name} ===")
        linkmgr_mod.extract(company_dir, linkmgr_sqlite)
        linkmgr_for_master = linkmgr_sqlite
    else:
        print(f"\n[linkmgr] no LinkMgr.1800 in {company_dir} — skipping")
        linkmgr_for_master = None

    # Stage 2: Masters (Manager.1800).
    print(f"\n[masters] === {company_dir.name} ===")
    n_master = _run_master_extract(
        v5,
        company_dir=company_dir,
        out_path=master_sqlite,
        company_name=company_name,
        rosetta_path=rosetta,
        linkmgr_sqlite=linkmgr_for_master,
    )

    # Stage 3: Vouchers (TranMgr.1800 + VchStatus.1800).
    # extract_vouchers.main() reads sys.argv; bypass it by calling main with a
    # patched argv. The simplest approach: invoke as subprocess to keep it
    # robust against import-side-effects.
    if (company_dir / "TranMgr.1800").exists():
        print(f"\n[vouchers] === {company_dir.name} ===")
        _run_voucher_extract(voucher_mod, company_dir, voucher_sqlite, master_sqlite)
        n_voucher = sqlite3.connect(str(voucher_sqlite)).execute(
            "SELECT COUNT(*) FROM vouchers").fetchone()[0]
    else:
        print(f"\n[vouchers] no TranMgr.1800 in {company_dir} — skipping")
        n_voucher = 0

    # Stage 4: Combine.
    if out_path.exists():
        out_path.unlink()
    print(f"\n[combine] writing {out_path}")
    out_conn = sqlite3.connect(str(out_path))
    out_conn.execute("PRAGMA synchronous=OFF")
    out_conn.execute("PRAGMA journal_mode=MEMORY")

    counts = {}
    counts.update(_attach_and_copy(
        out_conn, master_sqlite, "m",
        ["master_records", "ledgers", "groups", "stock_items", "voucher_types",
         "godowns", "cost_centres", "units", "companies", "unclassified_masters"],
    ))
    if voucher_sqlite.exists():
        counts.update(_attach_and_copy(
            out_conn, voucher_sqlite, "v",
            ["vouchers"],
        ))
    if linkmgr_sqlite.exists():
        counts.update(_attach_and_copy(
            out_conn, linkmgr_sqlite, "l",
            ["linkmgr_pages", "ledger_name_links", "bank_allocations", "bill_allocations"],
        ))

    # Indexes (re-create after CREATE TABLE AS, which doesn't copy them).
    _create_indexes(out_conn)

    # Provenance / metadata.
    out_conn.execute("""
        CREATE TABLE _meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    from . import __version__
    out_conn.executemany("INSERT INTO _meta(key, value) VALUES (?,?)", [
        ("tally1800_version", __version__),
        ("company_dir", str(company_dir)),
        ("company_name", company_name),
        ("master_records", str(n_master)),
        ("vouchers", str(n_voucher)),
    ])
    out_conn.commit()
    out_conn.close()

    if not keep_intermediate:
        shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "master_records": counts.get("master_records", 0),
        "vouchers": counts.get("vouchers", 0),
        "linkmgr_pages": counts.get("linkmgr_pages", 0),
        "ledger_name_links": counts.get("ledger_name_links", 0),
        "bank_allocations": counts.get("bank_allocations", 0),
        "bill_allocations": counts.get("bill_allocations", 0),
    }


def _run_voucher_extract(
    voucher_mod: ModuleType,
    company_dir: Path,
    voucher_sqlite: Path,
    master_sqlite: Path,
) -> None:
    """Drive scripts/extract_vouchers.py by faking the argv it expects.

    extract_vouchers.main() hard-codes the rosetta path and derives the output
    path from company_dir.name (out/<name>_vouchers_v1.sqlite3). To redirect to
    our voucher_sqlite path, we invoke its core helpers directly rather than
    going through main().
    """
    src = company_dir / "TranMgr.1800"
    print(f"[vouchers] reading {src} ({src.stat().st_size:,} bytes)")
    data = src.read_bytes()
    n_pages = len(data) // voucher_mod.PAGE_SIZE
    print(f"[vouchers]   {n_pages:,} pages")

    anchors = voucher_mod.find_anchor_pages(data)
    print(f"[vouchers]   {len(anchors):,} unique master_ids at 0x0bbb anchors")

    records = []
    for mid, locations in anchors.items():
        page_no, _rel = locations[0]
        rec = voucher_mod.extract_voucher_record(data, mid, page_no)
        records.append(rec)

    vchstatus_path = company_dir / "VchStatus.1800"
    slots = voucher_mod.parse_vchstatus_slots(vchstatus_path)
    print(f"[vouchers]   VchStatus slots: {len(slots):,}")
    paired = voucher_mod.pair_vouchers_with_vtypes(records, slots)
    print(f"[vouchers]   voucher_type paired for {paired:,} vouchers")

    # voucher_type name lookup: voucher_types table first, fall back to
    # master_records (cross-company case).
    vt_name = {}
    mconn = sqlite3.connect(str(master_sqlite))
    try:
        for r in mconn.execute("SELECT master_id, extracted_name FROM voucher_types"):
            vt_name[r[0]] = r[1]
    except sqlite3.OperationalError:
        pass
    if not vt_name:
        for r in mconn.execute(
            "SELECT master_id, extracted_name FROM master_records "
            "WHERE extracted_name IS NOT NULL"
        ):
            vt_name[r[0]] = r[1]
    mconn.close()

    if voucher_sqlite.exists():
        voucher_sqlite.unlink()
    out = sqlite3.connect(str(voucher_sqlite))
    out.execute("PRAGMA synchronous=OFF")
    out.execute("PRAGMA journal_mode=MEMORY")
    out.execute("""
        CREATE TABLE vouchers (
            master_id INTEGER PRIMARY KEY,
            guid TEXT,
            page INTEGER,
            voucher_key_raw TEXT,
            voucher_number TEXT,
            narration TEXT,
            party_or_address TEXT,
            peer_master_id INTEGER,
            voucher_date TEXT,
            voucher_date_serial INTEGER,
            voucher_type_mid INTEGER,
            voucher_type TEXT,
            fields_json TEXT,
            compact_json TEXT
        )
    """)

    import json
    for r in records:
        mid = r["master_id"]
        f = r["fields"]

        def first_str(fid):
            v = f.get(fid)
            if v and isinstance(v[0], str):
                return voucher_mod.normalize(v[0])
            return None

        voucher_key_raw = first_str(0x0003)
        voucher_number = first_str(0x00cb) or first_str(0x07d5)
        narration = first_str(0x00cd)
        party_or_address = first_str(0x00ce) or first_str(0x0002)

        fields_serializable = {f"0x{k:04x}": (v if len(v) > 1 else v[0])
                               for k, v in f.items()}
        fields_json = json.dumps(fields_serializable, ensure_ascii=False, default=str)
        compact_json = json.dumps(r["compact"], ensure_ascii=False)

        vt_mid = r.get("voucher_type_mid")
        vt_n = vt_name.get(vt_mid) if vt_mid is not None else None

        out.execute(
            "INSERT INTO vouchers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, None, r["page"], voucher_key_raw, voucher_number, narration,
             party_or_address, r["compact"].get("peer_master_id"),
             r.get("voucher_date"), r.get("date_serial"),
             vt_mid, vt_n, fields_json, compact_json),
        )
    out.commit()
    out.close()


def _create_indexes(conn: sqlite3.Connection) -> None:
    """Re-create indexes that CREATE TABLE AS dropped during the merge."""
    statements = [
        "CREATE INDEX IF NOT EXISTS idx_master_kind ON master_records(kind)",
        "CREATE INDEX IF NOT EXISTS idx_master_guid ON master_records(guid)",
        "CREATE INDEX IF NOT EXISTS idx_master_mid ON master_records(master_id)",
        "CREATE INDEX IF NOT EXISTS idx_voucher_date ON vouchers(voucher_date)",
        "CREATE INDEX IF NOT EXISTS idx_voucher_type ON vouchers(voucher_type)",
        "CREATE INDEX IF NOT EXISTS idx_voucher_mid ON vouchers(master_id)",
        "CREATE INDEX IF NOT EXISTS idx_linkmgr_kind ON linkmgr_pages(kind)",
        "CREATE INDEX IF NOT EXISTS idx_linkmgr_back ON linkmgr_pages(backptr_master_id)",
        "CREATE INDEX IF NOT EXISTS idx_namelinks_mid ON ledger_name_links(manager_master_id)",
        "CREATE INDEX IF NOT EXISTS idx_bank_alloc_mid ON bank_allocations(manager_master_id)",
        "CREATE INDEX IF NOT EXISTS idx_bill_alloc_mid ON bill_allocations(manager_master_id)",
    ]
    for s in statements:
        try:
            conn.execute(s)
        except sqlite3.OperationalError:
            # Table may not exist (e.g. company without LinkMgr); ignore.
            pass

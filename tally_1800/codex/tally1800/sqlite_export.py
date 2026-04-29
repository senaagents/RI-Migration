from __future__ import annotations

import sqlite3
from pathlib import Path

from .probe import (
    PAGE_SIZES,
    byte_entropy,
    discover_files,
    extract_ascii_strings,
    extract_lenprefixed_utf16le_strings,
    extract_utf16le_strings,
    probe_pages,
    sample_bytes,
    sha256_file,
    whole_file_entropy,
)


SCHEMA = """
create table if not exists raw_files (
  file_name text primary key,
  path text not null,
  role text not null,
  suffix text not null,
  size integer not null,
  sha256 text not null,
  entropy_sample real not null
);

create table if not exists page_probes (
  file_name text not null,
  page_size integer not null,
  page_count integer not null,
  exact_pages integer not null,
  fixed_word4_hits integer not null,
  sequential_word12_hits integer not null,
  crc32_le_hits integer not null,
  crc32_be_hits integer not null,
  score real not null
);

create table if not exists raw_pages (
  file_name text not null,
  page_size integer not null,
  page_no integer not null,
  offset integer not null,
  length integer not null,
  entropy real not null,
  first16_hex text not null
);

create table if not exists raw_strings (
  file_name text not null,
  offset integer not null,
  encoding text not null,
  text text not null
);

create table if not exists lenprefixed_utf16_strings (
  file_name text not null,
  offset integer not null,
  length_bytes integer not null,
  text_offset integer not null,
  text text not null
);
"""


def export_sqlite(company_dir: Path, out_path: Path, page_size: int = 512) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    files = discover_files(company_dir)
    with sqlite3.connect(out_path) as conn:
        conn.executescript(SCHEMA)
        for data_file in files:
            entropy = whole_file_entropy(data_file.path)
            conn.execute(
                """
                insert or replace into raw_files
                (file_name, path, role, suffix, size, sha256, entropy_sample)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data_file.name,
                    str(data_file.path),
                    data_file.role,
                    data_file.suffix,
                    data_file.size,
                    sha256_file(data_file.path),
                    entropy,
                ),
            )
            data = sample_bytes(data_file.path, max_bytes=16 * 1024 * 1024)
            for probe in probe_pages(data, PAGE_SIZES):
                conn.execute(
                    """
                    insert into page_probes
                    (file_name, page_size, page_count, exact_pages, fixed_word4_hits,
                     sequential_word12_hits, crc32_le_hits, crc32_be_hits, score)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data_file.name,
                        probe.page_size,
                        probe.page_count,
                        int(probe.exact_pages),
                        probe.fixed_word4_hits,
                        probe.sequential_word12_hits,
                        probe.crc32_le_hits,
                        probe.crc32_be_hits,
                        probe.score,
                    ),
                )
            for page_no, offset in enumerate(range(0, len(data), page_size)):
                page = data[offset : offset + page_size]
                if not page:
                    continue
                conn.execute(
                    """
                    insert into raw_pages
                    (file_name, page_size, page_no, offset, length, entropy, first16_hex)
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data_file.name,
                        page_size,
                        page_no,
                        offset,
                        len(page),
                        byte_entropy(page),
                        page[:16].hex(),
                    ),
                )
            for hit in extract_ascii_strings(data_file.path) + extract_utf16le_strings(data_file.path):
                conn.execute(
                    """
                    insert into raw_strings (file_name, offset, encoding, text)
                    values (?, ?, ?, ?)
                    """,
                    (hit.file_name, hit.offset, hit.encoding, hit.text),
                )
            for hit in extract_lenprefixed_utf16le_strings(data_file.path):
                conn.execute(
                    """
                    insert into lenprefixed_utf16_strings
                    (file_name, offset, length_bytes, text_offset, text)
                    values (?, ?, ?, ?, ?)
                    """,
                    (hit.file_name, hit.offset, hit.length_bytes, hit.text_offset, hit.text),
                )
        conn.commit()

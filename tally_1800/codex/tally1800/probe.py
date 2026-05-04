from __future__ import annotations

import hashlib
import math
import re
import sys
import struct
import zlib
from collections import Counter
from pathlib import Path
from typing import Iterable

from .model import (
    KNOWN_ROLES,
    DataFile,
    CompactNumericHit,
    FieldStringHit,
    LengthPrefixedStringHit,
    PageProbe,
    StringHit,
    TLVHit,
    VchStatusSlot,
)


PAGE_SIZES = (512, 1024, 2048, 4096, 8192, 16384)
ASCII_RE_TEMPLATE = rb"[\x20-\x7e]{%d,}"
TLV_MARKER = b"\x02\x10"
TYPE_STRING_UTF16 = b"\x00\x0f"
TYPE_U32_LIKE = {b"\x00\x0d", b"\x00\x06", b"\x00\x08"}
TYPE_I32 = b"\x00\x0a"
TYPE_U8 = b"\x00\x09"
TYPE_F64 = b"\x00\x0b"
COMPACT_NUMERIC_TYPES = {b"\x00\x06", b"\x00\x0d", b"\x00\x08"}
EXTENDED_NUMERIC_TYPES = {b"\x00\x09"}
EXTENDED_NUMERIC_PREFIX = b"\x80\x00"
PAGE_HEADER_SIZE = 28
PAGE_AWARE_TLV_FILES = {
    "Manager.1800",
    "TranMgr.1800",
    "LinkMgr.1800",
    "Aggr.1800",
    "CfgRept.1800",
    "ExtMngr.1800",
    "SecTran.1800",
}


def discover_files(company_dir: Path) -> list[DataFile]:
    paths = []
    for path in company_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() in {".1800", ".900", ".500", ".tsf"}:
            paths.append(path)
    files = []
    for path in sorted(paths, key=lambda p: p.name.lower()):
        stat = path.stat()
        files.append(
            DataFile(
                path=path,
                name=path.name,
                size=stat.st_size,
                role=KNOWN_ROLES.get(path.name, "unknown"),
                suffix=path.suffix.lower(),
            )
        )
    return files


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def byte_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    total = len(data)
    counts = Counter(data)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def sample_bytes(path: Path, max_bytes: int = 1024 * 1024) -> bytes:
    with path.open("rb") as fh:
        return fh.read(max_bytes)


def whole_file_entropy(path: Path, max_bytes: int = 16 * 1024 * 1024) -> float:
    return byte_entropy(sample_bytes(path, max_bytes=max_bytes))


def probe_pages(data: bytes, page_sizes: Iterable[int] = PAGE_SIZES) -> list[PageProbe]:
    probes: list[PageProbe] = []
    for page_size in page_sizes:
        if len(data) < page_size:
            continue
        page_count = len(data) // page_size
        fixed_word4_hits = 0
        sequential_word12_hits = 0
        crc32_le_hits = 0
        crc32_be_hits = 0
        for page_no in range(page_count):
            page = data[page_no * page_size : (page_no + 1) * page_size]
            if len(page) < 16:
                continue
            word4 = struct.unpack_from("<I", page, 4)[0]
            if word4 == 1:
                fixed_word4_hits += 1
            word12 = struct.unpack_from("<I", page, 12)[0]
            if word12 in {page_no, page_no + 1}:
                sequential_word12_hits += 1
            stored_le = struct.unpack_from("<I", page, 0)[0]
            stored_be = struct.unpack_from(">I", page, 0)[0]
            calc = zlib.crc32(page[4:]) & 0xFFFFFFFF
            if stored_le == calc:
                crc32_le_hits += 1
            if stored_be == calc:
                crc32_be_hits += 1
        exact_pages = len(data) % page_size == 0
        denom = max(page_count, 1)
        score = (
            (fixed_word4_hits / denom) * 0.30
            + (sequential_word12_hits / denom) * 0.20
            + (max(crc32_le_hits, crc32_be_hits) / denom) * 0.40
            + (0.10 if exact_pages else 0.0)
        )
        probes.append(
            PageProbe(
                page_size=page_size,
                page_count=page_count,
                exact_pages=exact_pages,
                fixed_word4_hits=fixed_word4_hits,
                sequential_word12_hits=sequential_word12_hits,
                crc32_le_hits=crc32_le_hits,
                crc32_be_hits=crc32_be_hits,
                score=score,
            )
        )
    return sorted(probes, key=lambda p: p.score, reverse=True)


def extract_ascii_strings(path: Path, min_len: int = 5) -> list[StringHit]:
    pattern = re.compile(ASCII_RE_TEMPLATE % min_len)
    hits: list[StringHit] = []
    data = path.read_bytes()
    for match in pattern.finditer(data):
        text = match.group(0).decode("ascii", errors="replace")
        hits.append(StringHit(path.name, match.start(), "ascii", text))
    return hits


def extract_utf16le_strings(path: Path, min_len: int = 5) -> list[StringHit]:
    data = path.read_bytes()
    hits: list[StringHit] = []
    current = bytearray()
    start = None
    idx = 0
    while idx + 1 < len(data):
        lo = data[idx]
        hi = data[idx + 1]
        if hi == 0 and 0x20 <= lo <= 0x7E:
            if start is None:
                start = idx
            current.extend((lo, hi))
        else:
            if start is not None and len(current) // 2 >= min_len:
                hits.append(
                    StringHit(
                        path.name,
                        start,
                        "utf-16le",
                        current.decode("utf-16le", errors="replace"),
                    )
                )
            current = bytearray()
            start = None
        idx += 2
    if start is not None and len(current) // 2 >= min_len:
        hits.append(
            StringHit(path.name, start, "utf-16le", current.decode("utf-16le", errors="replace"))
        )
    return hits


def extract_lenprefixed_utf16le_strings(
    path: Path,
    min_chars: int = 3,
    max_bytes: int = 512,
    min_ascii_ratio: float = 0.85,
) -> list[LengthPrefixedStringHit]:
    data = path.read_bytes()
    hits: list[LengthPrefixedStringHit] = []
    seen: set[tuple[int, int]] = set()
    for offset in range(0, max(0, len(data) - 4), 2):
        length_bytes = struct.unpack_from("<H", data, offset)[0]
        if length_bytes < (min_chars + 1) * 2:
            continue
        if length_bytes > max_bytes or length_bytes % 2:
            continue
        start = offset + 2
        end = start + length_bytes
        if end > len(data):
            continue
        payload = data[start:end]
        if len(payload) < 2 or payload[-2:] != b"\x00\x00":
            continue
        text = payload[:-2].decode("utf-16le", errors="ignore")
        if len(text) < min_chars:
            continue
        if not (32 <= ord(text[0]) <= 126):
            continue
        printable = sum(1 for ch in text if ch.isprintable() and ch not in "\x00\r\n\t")
        if printable / max(len(text), 1) < 0.90:
            continue
        asciiish = sum(1 for ch in text if 32 <= ord(ch) <= 126)
        if asciiish / max(len(text), 1) < min_ascii_ratio:
            continue
        if "\x00" in text:
            continue
        key = (offset, length_bytes)
        if key in seen:
            continue
        seen.add(key)
        hits.append(LengthPrefixedStringHit(path.name, offset, length_bytes, start, text))
    return hits


def _field_tag_before(data: bytes, offset: int) -> tuple[int | None, str | None]:
    if offset < 6:
        return None, None
    tag = data[offset - 6 : offset]
    if len(tag) == 6 and tag[0:2] == b"\x02\x10" and tag[4:6] == b"\x00\x0f":
        return struct.unpack_from("<H", tag, 2)[0], tag.hex()
    return None, tag.hex()


def extract_field_strings(
    path: Path,
    min_chars: int = 3,
    max_bytes: int = 512,
    min_ascii_ratio: float = 0.85,
    page_size: int = 512,
) -> list[FieldStringHit]:
    data = path.read_bytes()
    hits = extract_lenprefixed_utf16le_strings(
        path,
        min_chars=min_chars,
        max_bytes=max_bytes,
        min_ascii_ratio=min_ascii_ratio,
    )
    field_hits: list[FieldStringHit] = []
    for hit in hits:
        field_id, tag_hex = _field_tag_before(data, hit.offset)
        field_hits.append(
            FieldStringHit(
                file_name=hit.file_name,
                offset=hit.offset,
                length_bytes=hit.length_bytes,
                text_offset=hit.text_offset,
                text=hit.text,
                page_no=hit.offset // page_size,
                page_offset=hit.offset % page_size,
                field_id=field_id,
                tag_prefix_hex=tag_hex,
            )
        )
    return field_hits


def extract_tagged_field_strings(
    path: Path,
    min_chars: int = 1,
    max_bytes: int = 4096,
    min_ascii_ratio: float = 0.70,
    page_size: int = 512,
    data: bytes | None = None,
) -> list[FieldStringHit]:
    if data is None:
        data = path.read_bytes()
    if _should_use_page_aware_tlvs(path, data, page_size, 0):
        return _extract_tagged_field_strings_page_aware(
            path,
            data,
            min_chars=min_chars,
            max_bytes=max_bytes,
            min_ascii_ratio=min_ascii_ratio,
            page_size=page_size,
        )
    field_hits: list[FieldStringHit] = []
    pos = 0
    tag_head = b"\x02\x10"
    while True:
        tag_pos = data.find(tag_head, pos)
        if tag_pos < 0 or tag_pos + 8 > len(data):
            break
        if data[tag_pos + 4 : tag_pos + 6] != b"\x00\x0f":
            pos = tag_pos + 1
            continue
        field_id = struct.unpack_from("<H", data, tag_pos + 2)[0]
        length_offset = tag_pos + 6
        length_bytes = struct.unpack_from("<H", data, length_offset)[0]
        if length_bytes < 2 or length_bytes > max_bytes or length_bytes % 2:
            pos = tag_pos + 1
            continue
        text_offset = length_offset + 2
        end = text_offset + length_bytes
        if end > len(data):
            pos = tag_pos + 1
            continue
        payload = data[text_offset:end]
        if payload[-2:] != b"\x00\x00":
            pos = tag_pos + 1
            continue
        text = payload[:-2].decode("utf-16le", errors="ignore")
        if len(text) < min_chars or "\x00" in text:
            pos = tag_pos + 1
            continue
        if text and not text[0].isprintable():
            pos = tag_pos + 1
            continue
        printable = sum(1 for ch in text if ch.isprintable() and ch not in "\x00\r\n\t")
        if printable / max(len(text), 1) < 0.90:
            pos = tag_pos + 1
            continue
        asciiish = sum(1 for ch in text if 32 <= ord(ch) <= 126)
        if asciiish / max(len(text), 1) < min_ascii_ratio:
            pos = tag_pos + 1
            continue
        field_hits.append(
            FieldStringHit(
                file_name=path.name,
                offset=length_offset,
                length_bytes=length_bytes,
                text_offset=text_offset,
                text=text,
                page_no=length_offset // page_size,
                page_offset=length_offset % page_size,
                field_id=field_id,
                tag_prefix_hex=data[tag_pos : tag_pos + 6].hex(),
            )
        )
        pos = end
    return field_hits


def _should_use_page_aware_tlvs(path: Path, data: bytes, page_size: int, max_bytes: int) -> bool:
    if max_bytes > 0:
        return False
    return (
        path.name in PAGE_AWARE_TLV_FILES
        and page_size == 512
        and len(data) >= page_size
        and len(data) % page_size == 0
    )


def _decode_tlv_value(
    type_bytes: bytes,
    length_bytes: int,
    value: bytes,
    max_string_bytes: int,
    min_printable_ratio: float,
) -> tuple[object | None, str | None]:
    if type_bytes == TYPE_STRING_UTF16:
        if (
            2 <= length_bytes <= max_string_bytes
            and length_bytes % 2 == 0
            and value[-2:] == b"\x00\x00"
        ):
            try:
                text = value[:-2].decode("utf-16le")
            except UnicodeDecodeError:
                return None, None
            printable = sum(1 for ch in text if ch.isprintable() or ch in "\t\n\r")
            if printable / max(len(text), 1) >= min_printable_ratio:
                return text, "string_utf16le"
        return None, None
    if type_bytes in TYPE_U32_LIKE and length_bytes == 4:
        return struct.unpack_from("<I", value, 0)[0], "u32_like"
    if type_bytes == TYPE_I32 and length_bytes == 4:
        return struct.unpack_from("<i", value, 0)[0], "i32_like"
    if type_bytes == TYPE_U8 and length_bytes == 1:
        return value[0], "u8_like"
    if type_bytes == TYPE_F64 and length_bytes == 8:
        return struct.unpack_from("<d", value, 0)[0], "f64_like"
    return None, None


def _page_group_runs(data: bytes, page_size: int) -> Iterable[list[int]]:
    page_count = len(data) // page_size
    pages_by_group: dict[int, list[int]] = {}
    for page_no in range(1, page_count):
        page_start = page_no * page_size
        if page_start + 8 > len(data):
            continue
        group_id = struct.unpack_from("<I", data, page_start + 4)[0]
        if group_id == 0:
            continue
        pages_by_group.setdefault(group_id, []).append(page_no)
    for pages in pages_by_group.values():
        sorted_pages = sorted(pages)
        run = [sorted_pages[0]]
        for page_no in sorted_pages[1:]:
            if page_no == run[-1] + 1:
                run.append(page_no)
            else:
                yield run
                run = [page_no]
        yield run


def _logical_stream_for_run(
    data: bytes,
    pages: list[int],
    page_size: int,
    header_size: int = PAGE_HEADER_SIZE,
) -> tuple[bytes, list[int]]:
    chunks: list[bytes] = []
    origins: list[int] = []
    for page_no in pages:
        page_start = page_no * page_size
        body_start = page_start + header_size
        page_end = page_start + page_size
        body = data[body_start:page_end]
        chunks.append(body)
        origins.extend(range(body_start, body_start + len(body)))
    return b"".join(chunks), origins


def _iter_logical_tlvs(
    path: Path,
    data: bytes,
    max_string_bytes: int,
    min_printable_ratio: float,
    page_size: int,
) -> Iterable[TLVHit]:
    """Walk page-body TLVs with 28-byte page headers stripped between pages.

    Tally record payloads can span 512-byte pages; the continuation starts at
    page offset 28, not offset 0. A raw file scan decodes those crossings as
    mojibake. This walker keeps physical offsets for evidence while reading
    values from the logical page-body stream.
    """
    for pages in _page_group_runs(data, page_size):
        stream, origins = _logical_stream_for_run(data, pages, page_size)
        idx = 0
        while idx < len(stream) - 8:
            tag_pos = stream.find(TLV_MARKER, idx)
            if tag_pos < 0 or tag_pos + 8 > len(stream):
                break
            field_id = struct.unpack_from("<H", stream, tag_pos + 2)[0]
            type_bytes = bytes(stream[tag_pos + 4 : tag_pos + 6])
            length_bytes = struct.unpack_from("<H", stream, tag_pos + 6)[0]
            end = tag_pos + 8 + length_bytes
            if end > len(stream):
                idx = tag_pos + 1
                continue
            value = stream[tag_pos + 8 : end]
            decoded, confidence = _decode_tlv_value(
                type_bytes,
                length_bytes,
                value,
                max_string_bytes,
                min_printable_ratio,
            )
            if confidence is None:
                idx = tag_pos + 1
                continue
            physical_offset = origins[tag_pos]
            yield TLVHit(
                file_name=path.name,
                offset=physical_offset,
                page_no=physical_offset // page_size,
                page_offset=physical_offset % page_size,
                field_id=field_id,
                type_hex=type_bytes.hex(),
                length_bytes=length_bytes,
                decoded=decoded,
                confidence=confidence,
            )
            idx = end


def _extract_tagged_field_strings_page_aware(
    path: Path,
    data: bytes,
    *,
    min_chars: int,
    max_bytes: int,
    min_ascii_ratio: float,
    page_size: int,
) -> list[FieldStringHit]:
    hits: list[FieldStringHit] = []
    for pages in _page_group_runs(data, page_size):
        stream, origins = _logical_stream_for_run(data, pages, page_size)
        idx = 0
        while idx < len(stream) - 8:
            tag_pos = stream.find(TLV_MARKER, idx)
            if tag_pos < 0 or tag_pos + 8 > len(stream):
                break
            if stream[tag_pos + 4 : tag_pos + 6] != TYPE_STRING_UTF16:
                idx = tag_pos + 1
                continue
            field_id = struct.unpack_from("<H", stream, tag_pos + 2)[0]
            length_bytes = struct.unpack_from("<H", stream, tag_pos + 6)[0]
            if length_bytes < 2 or length_bytes > max_bytes or length_bytes % 2:
                idx = tag_pos + 1
                continue
            text_start = tag_pos + 8
            end = text_start + length_bytes
            if end > len(stream):
                idx = tag_pos + 1
                continue
            payload = stream[text_start:end]
            if payload[-2:] != b"\x00\x00":
                idx = tag_pos + 1
                continue
            text = payload[:-2].decode("utf-16le", errors="ignore")
            if len(text) < min_chars or "\x00" in text:
                idx = tag_pos + 1
                continue
            if text and not text[0].isprintable():
                idx = tag_pos + 1
                continue
            printable = sum(1 for ch in text if ch.isprintable() and ch not in "\x00\r\n\t")
            if printable / max(len(text), 1) < 0.90:
                idx = tag_pos + 1
                continue
            asciiish = sum(1 for ch in text if 32 <= ord(ch) <= 126)
            if asciiish / max(len(text), 1) < min_ascii_ratio:
                idx = tag_pos + 1
                continue
            length_offset = origins[tag_pos + 6]
            text_offset = origins[text_start]
            hits.append(
                FieldStringHit(
                    file_name=path.name,
                    offset=length_offset,
                    length_bytes=length_bytes,
                    text_offset=text_offset,
                    text=text,
                    page_no=length_offset // page_size,
                    page_offset=length_offset % page_size,
                    field_id=field_id,
                    tag_prefix_hex=stream[tag_pos : tag_pos + 6].hex(),
                )
            )
            idx = end
    return hits


def iter_tlvs(
    path: Path,
    max_bytes: int = 0,
    max_string_bytes: int = 4096,
    min_printable_ratio: float = 0.85,
    page_size: int = 512,
    data: bytes | None = None,
) -> Iterable[TLVHit]:
    """Yield high-confidence `02 10` TLV fields from a `.1800` file.

    Observed frame:
      02 10 <field_id:u16le> <type_code:u16> <length:u16le> <value>

    This intentionally skips invalid markers by one byte and advances by the
    TLV length only after the value passes lightweight type validation.

    If `data` is provided, it's used directly — caller has already read the
    file. Otherwise the file is read from `path`. Lets the orchestrator cache
    bytes once and feed multiple iterators without redundant I/O + Python
    bytes allocations.
    """
    if data is None:
        data = path.read_bytes() if max_bytes <= 0 else path.read_bytes()[:max_bytes]
    elif max_bytes > 0:
        data = data[:max_bytes]
    if _should_use_page_aware_tlvs(path, data, page_size, max_bytes):
        yield from _iter_logical_tlvs(
            path,
            data,
            max_string_bytes=max_string_bytes,
            min_printable_ratio=min_printable_ratio,
            page_size=page_size,
        )
        return
    idx = 0
    while idx < len(data) - 8:
        tag_pos = data.find(TLV_MARKER, idx)
        if tag_pos < 0 or tag_pos + 8 > len(data):
            break
        field_id = struct.unpack_from("<H", data, tag_pos + 2)[0]
        type_bytes = bytes(data[tag_pos + 4 : tag_pos + 6])
        length_bytes = struct.unpack_from("<H", data, tag_pos + 6)[0]
        end = tag_pos + 8 + length_bytes
        if end > len(data):
            idx = tag_pos + 1
            continue

        value = data[tag_pos + 8 : end]
        decoded, confidence = _decode_tlv_value(
            type_bytes,
            length_bytes,
            value,
            max_string_bytes,
            min_printable_ratio,
        )

        if confidence is not None:
            yield TLVHit(
                file_name=path.name,
                offset=tag_pos,
                page_no=tag_pos // page_size,
                page_offset=tag_pos % page_size,
                field_id=field_id,
                type_hex=type_bytes.hex(),
                length_bytes=length_bytes,
                decoded=decoded,
                confidence=confidence,
            )
            idx = end
        else:
            idx = tag_pos + 1


def iter_compact_numeric_fields(
    path: Path,
    field_ids: set[int] | None = None,
    type_hexes: set[str] | None = None,
    max_bytes: int = 0,
    page_size: int = 512,
    data: bytes | None = None,
) -> Iterable[CompactNumericHit]:
    """Yield compact numeric fields observed in TranMgr pages.

    Observed form, separate from the `02 10` TLV stream:
      <field_id:u16le> <type:u16> <value:u32le> 00 00

    The main proven use so far is:
      bb 0b 00 06 <voucher_master_id:u32le> 00 00

    Pass `data` to reuse already-read file bytes; otherwise reads `path`.
    """
    if data is None:
        data = path.read_bytes() if max_bytes <= 0 else path.read_bytes()[:max_bytes]
    elif max_bytes > 0:
        data = data[:max_bytes]
    type_bytes_filter = {bytes.fromhex(t) for t in type_hexes} if type_hexes else COMPACT_NUMERIC_TYPES
    if field_ids is not None and type_hexes is not None:
        seen_offsets: set[int] = set()
        for field_id in field_ids:
            for type_bytes in type_bytes_filter:
                signature = struct.pack("<H", field_id) + type_bytes
                search_from = 0
                while True:
                    offset = data.find(signature, search_from)
                    if offset < 0 or offset + 10 > len(data):
                        break
                    search_from = offset + 1
                    if offset in seen_offsets or data[offset + 8 : offset + 10] != b"\x00\x00":
                        continue
                    seen_offsets.add(offset)
                    yield CompactNumericHit(
                        file_name=path.name,
                        offset=offset,
                        page_no=offset // page_size,
                        page_offset=offset % page_size,
                        field_id=field_id,
                        type_hex=type_bytes.hex(),
                        value=struct.unpack_from("<I", data, offset + 4)[0],
                    )
        return
    for offset in range(0, max(0, len(data) - 10)):
        field_id = struct.unpack_from("<H", data, offset)[0]
        if field_ids is not None and field_id not in field_ids:
            continue
        type_bytes = bytes(data[offset + 2 : offset + 4])
        if type_bytes not in type_bytes_filter:
            continue
        if data[offset + 8 : offset + 10] != b"\x00\x00":
            continue
        yield CompactNumericHit(
            file_name=path.name,
            offset=offset,
            page_no=offset // page_size,
            page_offset=offset % page_size,
            field_id=field_id,
            type_hex=type_bytes.hex(),
            value=struct.unpack_from("<I", data, offset + 4)[0],
        )


def iter_page_compact_numeric_fields(
    path: Path,
    field_ids: set[int] | None = None,
    type_hexes: set[str] | None = None,
    max_bytes: int = 0,
    page_size: int = 512,
    header_len: int = 28,
    slot_len: int = 10,
    data: bytes | None = None,
) -> Iterable[CompactNumericHit]:
    """Yield compact numeric fields from page slot blocks.

    Observed in `TranMgr.1800`: after the 28-byte page header, fixed 10-byte
    slots run until the first `02 10` TLV marker in that page.
    """
    if data is None:
        data = path.read_bytes() if max_bytes <= 0 else path.read_bytes()[:max_bytes]
    elif max_bytes > 0:
        data = data[:max_bytes]
    type_bytes_filter = {bytes.fromhex(t) for t in type_hexes} if type_hexes else COMPACT_NUMERIC_TYPES
    page_count = len(data) // page_size
    for page_no in range(page_count):
        page_start = page_no * page_size
        page_end = min(page_start + page_size, len(data))
        body_start = page_start + header_len
        if body_start + slot_len > page_end:
            continue
        first_tlv = data.find(TLV_MARKER, body_start, page_end)
        body_end = first_tlv if first_tlv >= 0 else page_end
        offset = body_start
        while offset + slot_len <= body_end:
            field_id = struct.unpack_from("<H", data, offset)[0]
            type_bytes = bytes(data[offset + 2 : offset + 4])
            if (
                (field_ids is None or field_id in field_ids)
                and type_bytes in type_bytes_filter
                and data[offset + 8 : offset + 10] == b"\x00\x00"
            ):
                yield CompactNumericHit(
                    file_name=path.name,
                    offset=offset,
                    page_no=page_no,
                    page_offset=offset - page_start,
                    field_id=field_id,
                    type_hex=type_bytes.hex(),
                    value=struct.unpack_from("<I", data, offset + 4)[0],
                )
            offset += slot_len


def iter_extended_numeric_fields(
    path: Path,
    field_ids: set[int] | None = None,
    type_hexes: set[str] | None = None,
    max_bytes: int = 0,
    page_size: int = 512,
    data: bytes | None = None,
) -> Iterable[CompactNumericHit]:
    """Yield extended 14-byte numeric slots.

    Observed form:
      80 00 <field_id:u16le> <type:u16> <value:i64le>

    The known high-value use is `type=0009` signed/scaled accounting values
    such as bill amounts, GST bill splits, and journal no-sibling amounts.
    """
    if data is None:
        data = path.read_bytes() if max_bytes <= 0 else path.read_bytes()[:max_bytes]
    elif max_bytes > 0:
        data = data[:max_bytes]
    type_bytes_filter = {bytes.fromhex(t) for t in type_hexes} if type_hexes else EXTENDED_NUMERIC_TYPES
    search_from = 0
    while True:
        offset = data.find(EXTENDED_NUMERIC_PREFIX, search_from)
        if offset < 0 or offset + 14 > len(data):
            break
        search_from = offset + 1
        field_id = struct.unpack_from("<H", data, offset + 2)[0]
        if field_ids is not None and field_id not in field_ids:
            continue
        type_bytes = bytes(data[offset + 4 : offset + 6])
        if type_bytes not in type_bytes_filter:
            continue
        yield CompactNumericHit(
            file_name=path.name,
            offset=offset,
            page_no=offset // page_size,
            page_offset=offset % page_size,
            field_id=field_id,
            type_hex=type_bytes.hex(),
            value=struct.unpack_from("<q", data, offset + 6)[0],
        )


def iter_vch_status_slots(
    path: Path,
    max_bytes: int = 0,
    page_size: int = 512,
    slot_size: int = 128,
    data: bytes | None = None,
) -> Iterable[VchStatusSlot]:
    """Yield non-empty fixed slots from `VchStatus.1800`-style files.

    Current observed shape:
      - skip page 0
      - three 128-byte slots per 512-byte page at offsets 0x80, 0x100, 0x180
      - slot word[10] is voucher date serial
      - slot word[25] is voucher type MASTERID

    The slot-to-voucher ordering is not solved; this exposes the evidence.
    """
    if data is None:
        data = path.read_bytes() if max_bytes <= 0 else path.read_bytes()[:max_bytes]
    elif max_bytes > 0:
        data = data[:max_bytes]
    if page_size < 512 or slot_size != 128:
        slot_offsets = (page_size // 4, page_size // 2, (page_size * 3) // 4)
    else:
        slot_offsets = (0x80, 0x100, 0x180)
    slot_seq = 0
    for page_no in range(1, len(data) // page_size):
        page_start = page_no * page_size
        for page_offset in slot_offsets:
            offset = page_start + page_offset
            end = offset + slot_size
            if end > len(data):
                continue
            slot = data[offset:end]
            if not any(slot):
                continue
            words = struct.unpack_from("<32I", slot, 0)
            yield VchStatusSlot(
                file_name=path.name,
                slot_seq=slot_seq,
                offset=offset,
                page_no=page_no,
                page_offset=page_offset,
                flag_word0=words[0],
                flag_word1=words[1],
                date_serial=words[10],
                voucher_type_master_id=words[25],
                word26_state_or_flag=words[26],
                words=words,
            )
            slot_seq += 1


def likely_binary_state(entropy: float, string_count: int, size: int) -> str:
    if size == 0:
        return "empty"
    if entropy >= 7.85 and string_count == 0:
        return "high_entropy_encrypted_or_compressed"
    if entropy >= 7.4:
        return "mixed_binary_high_entropy"
    if string_count > 20:
        return "binary_with_cleartext_strings"
    return "binary_low_cleartext"


def page_header_words(path: Path, page_size: int = 512, limit: int = 16) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("rb") as fh:
        for page_no in range(limit):
            offset = page_no * page_size
            fh.seek(offset)
            page = fh.read(page_size)
            if not page:
                break
            words = []
            for word_offset in range(0, min(64, len(page)), 4):
                words.append(struct.unpack_from("<I", page, word_offset)[0])
            rows.append(
                {
                    "page_no": page_no,
                    "offset": offset,
                    "length": len(page),
                    "entropy": round(byte_entropy(page), 4),
                    "first16_hex": page[:16].hex(),
                    "u32le_0_64": words,
                }
            )
    return rows


def string_contexts(
    path: Path,
    needle: str | None = None,
    min_len: int = 5,
    before: int = 32,
    after: int = 32,
    limit: int = 20,
) -> list[dict[str, object]]:
    data = path.read_bytes()
    hits = extract_ascii_strings(path, min_len=min_len) + extract_utf16le_strings(path, min_len=min_len)
    rows = []
    for hit in sorted(hits, key=lambda h: h.offset):
        if needle and needle.lower() not in hit.text.lower():
            continue
        start = max(0, hit.offset - before)
        end = min(len(data), hit.offset + len(hit.text.encode(hit.encoding, errors="ignore")) + after)
        rows.append(
            {
                "file_name": path.name,
                "offset": hit.offset,
                "page_no_512": hit.offset // 512,
                "page_offset_512": hit.offset % 512,
                "encoding": hit.encoding,
                "text": hit.text,
                "before_hex": data[start : hit.offset].hex(),
                "text_hex": data[hit.offset : hit.offset + len(hit.text.encode(hit.encoding, errors="ignore"))].hex(),
                "after_hex": data[
                    hit.offset + len(hit.text.encode(hit.encoding, errors="ignore")) : end
                ].hex(),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def suppress_broken_pipe() -> None:
    try:
        sys.stdout.close()
    except OSError:
        pass

"""
Build a Tally internal field-id -> Rosetta column mapping.

Strategy:
  1. Join our extracted master_records (out/070525_master_v5.sqlite3) with the
     Rosetta truth tables (corpus/rosetta.sqlite3) on the 40-char GUID.
  2. For each Rosetta column with a string-ish value, count how often that
     value appears (or is contained) in the per-record fields_json under each
     field-id.
  3. A (kind, column) -> field_id mapping is "high-confidence" when one
     field-id agrees on >= MIN_CONFIDENCE of records that have a non-empty
     value for that column AND we have at least MIN_SAMPLES such records.
  4. Emit out/field_map.json and a typed sqlite (out/070525_typed_v1.sqlite3).

The field map is derived purely from extracted bytes vs. Rosetta truth — the
runtime extractor never reads any XML. raw_payloads.response_xml is consulted
only for the rare case where a Rosetta column is missing but the XML tag name
helps us label a high-evidence field-id; we keep that out of the strict
confidence-vote and surface it as "xml_hint".
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V5_DB = ROOT / "out" / "070525_master_v5.sqlite3"
V4_DB = ROOT / "out" / "070525_master_v4.sqlite3"
ROSETTA_DB = ROOT / "corpus" / "rosetta.sqlite3"
OUT_MAP = ROOT / "out" / "field_map.json"
OUT_TYPED = ROOT / "out" / "070525_typed_v1.sqlite3"

MIN_CONFIDENCE = 0.90  # hits / (samples where both Rosetta col and fid present)
MIN_SAMPLES = 20       # need at least this many such joint samples
MIN_COL_FRACTION = 0.30  # fid must appear in >= this fraction of records that
                         # have the Rosetta column populated
MIN_SAMPLES_RARE = 4     # for rarely-populated columns (email/phone) we relax

# Per-kind list of (rosetta_table, rosetta_column, normalize_fn) we try to map.
# normalize_fn is applied before comparing to fields_json values.
def _norm_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _norm_address(v):
    """Address in Rosetta is multiline ('line1\nline2'); fields_json stores
    each line as a separate field value. Return list of stripped lines."""
    if v is None:
        return None
    parts = [p.strip() for p in str(v).split("\n") if p.strip()]
    return parts or None


def _norm_amount(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # tally writes amounts as ints with implicit decimals or as strings; we
    # compare on int(f*100) and a few common stringifications.
    return f


def _norm_bool(v):
    if v is None:
        return None
    return bool(v)


KINDS = {
    "ledgers": {
        "rosetta_table": "ledgers",
        "guid_col": "guid",
        "name_col": "name",
        "xml_request_type": "ledgers",
        "xml_block_tag": "LEDGER",
        "fields": [
            ("name", "str"),
            ("parent", "str"),
            ("mailing_name", "str"),
            ("address", "addr"),
            ("state", "str"),
            ("country", "str"),
            ("pincode", "str"),
            ("email", "str"),
            ("phone", "str"),
            ("pan", "str"),
            ("gstin", "str"),
            ("gst_type", "str"),
            ("currency", "str"),
            ("opening_balance", "amount"),
            ("closing_balance", "amount"),
            ("is_bill_wise", "bool"),
            ("affects_stock", "bool"),
            ("created_by", "str"),
        ],
        # Tags from response_xml mined as additional columns. Format:
        # (label, xml_tag, kind, ros_col_alias_if_any)
        "xml_fields": [
            ("xml_currency", "CURRENCYNAME", "str", "currency"),
            ("xml_prior_state", "PRIORSTATENAME", "str", None),
            ("xml_old_pincode", "OLDPINCODE", "str", None),
            ("xml_led_state", "LEDSTATENAME", "str", None),
            ("xml_party_gstin", "PARTYGSTIN", "str", None),
            ("xml_income_tax", "INCOMETAXNUMBER", "str", None),
            ("xml_ledger_mobile", "LEDGERMOBILE", "str", None),
            ("xml_gst_reg", "GSTREGISTRATIONTYPE", "str", "gst_type"),
            ("xml_parent", "PARENT", "str", "parent"),
        ],
    },
    "groups": {
        "rosetta_table": "groups",
        "guid_col": "guid",
        "name_col": "name",
        "xml_request_type": "groups",
        "xml_block_tag": "GROUP",
        "fields": [
            ("name", "str"),
            ("parent", "str"),
            ("is_revenue", "bool"),
            ("is_deemed_positive", "bool"),
            ("affects_gross_profit", "bool"),
            ("is_subledger", "bool"),
        ],
        "xml_fields": [
            ("xml_parent", "PARENT", "str", "parent"),
        ],
    },
    "stock_items": {
        "rosetta_table": "stock_items",
        "guid_col": "guid",
        "name_col": "name",
        "xml_request_type": "stock_items",
        "xml_block_tag": "STOCKITEM",
        "fields": [
            ("name", "str"),
            ("parent", "str"),
            ("base_units", "str"),
            ("hsn_code", "str"),
            ("gst_applicable", "str"),
            ("opening_balance", "amount"),
            ("opening_quantity", "amount"),
            ("opening_rate", "amount"),
            ("closing_quantity", "amount"),
            ("closing_uom", "str"),
            ("closing_rate", "amount"),
            ("closing_value", "amount"),
        ],
        "xml_fields": [
            ("xml_parent", "PARENT", "str", "parent"),
            ("xml_gst_applicable", "GSTAPPLICABLE", "str", "gst_applicable"),
            ("xml_base_units", "BASEUNITS", "str", "base_units"),
            ("xml_opening_balance", "OPENINGBALANCE", "str", None),
            ("xml_opening_rate", "OPENINGRATE", "str", None),
        ],
    },
    "voucher_types": {
        "rosetta_table": "voucher_types",
        "guid_col": None,  # rosetta voucher_types has no guid column
        "name_col": "name",
        "xml_request_type": "voucher_types",
        "xml_block_tag": "VOUCHERTYPE",
        "fields": [
            ("name", "str"),
            ("parent", "str"),
            ("numbering_method", "str"),
        ],
        "xml_fields": [
            ("xml_parent", "PARENT", "str", "parent"),
            ("xml_numbering_method", "NUMBERINGMETHOD", "str", "numbering_method"),
        ],
    },
    "units": {
        "rosetta_table": "units",
        "guid_col": None,
        "name_col": "name",
        "xml_request_type": "units",
        "xml_block_tag": "UNIT",
        "fields": [
            ("name", "str"),
            ("original_name", "str"),
        ],
        "xml_fields": [
            ("xml_original_name", "ORIGINALNAME", "str", "original_name"),
        ],
    },
    "godowns": {
        "rosetta_table": "godowns",
        "guid_col": None,
        "name_col": "name",
        "xml_request_type": "godowns",
        "xml_block_tag": "GODOWN",
        "fields": [
            ("name", "str"),
            ("parent", "str"),
        ],
        "xml_fields": [
            ("xml_parent", "PARENT", "str", "parent"),
        ],
    },
    "cost_centres": {
        "rosetta_table": "cost_centres",
        "guid_col": None,
        "name_col": "name",
        "xml_request_type": "cost_centres",
        "xml_block_tag": "COSTCENTRE",
        "fields": [
            ("name", "str"),
            ("parent", "str"),
        ],
        "xml_fields": [
            ("xml_parent", "PARENT", "str", "parent"),
        ],
    },
}

NORMALIZERS = {
    "str": _norm_str,
    "addr": _norm_address,
    "amount": _norm_amount,
    "bool": _norm_bool,
}


def _values_match(rosetta_val, fields_json_val, kind: str) -> bool:
    """True if the Rosetta value matches one of the values we extracted under
    a field-id. fields_json_val may be a single string or a list."""
    if rosetta_val is None or fields_json_val is None:
        return False
    candidates = (
        fields_json_val if isinstance(fields_json_val, list) else [fields_json_val]
    )
    if kind == "str":
        target = str(rosetta_val).strip()
        if not target:
            return False
        for c in candidates:
            if c is None:
                continue
            if str(c).strip() == target:
                return True
        return False
    if kind == "addr":
        # rosetta_val is a list of lines; match if at least one substantial
        # line appears among the candidate values (case-insensitive prefix is
        # fine). Address extraction is noisy due to byte corruption in some
        # records, so we relax to "any line present" rather than "all".
        if not isinstance(rosetta_val, list):
            return False
        cset = [str(c).strip() for c in candidates if c is not None]
        for line in rosetta_val:
            if len(line) < 6:
                continue
            for c in cset:
                if line == c:
                    return True
                if len(line) > 8 and (line[:max(8, int(len(line) * 0.6))] in c
                                       or c[:max(8, int(len(c) * 0.6))] in line):
                    return True
        return False
    if kind == "amount":
        target = float(rosetta_val)
        for c in candidates:
            if c is None:
                continue
            try:
                f = float(str(c).replace(",", "").strip())
            except (TypeError, ValueError):
                continue
            if abs(f - target) < 1e-4:
                return True
            if abs(f - target * 100) < 1e-2:  # paise vs rupees
                return True
            if abs(f * 100 - target) < 1e-2:
                return True
        return False
    if kind == "bool":
        target = bool(rosetta_val)
        for c in candidates:
            if c is None:
                continue
            s = str(c).strip().lower()
            cb = s in ("yes", "true", "1")
            if cb == target:
                return True
        return False
    return False


def parse_xml_records(conn: sqlite3.Connection, request_type: str,
                      block_tag: str, name_attr: str = "NAME"):
    """Parse a Tally XML response into per-record dicts.

    Returns { guid_or_name -> { tag_name: value_or_list } }
    join_key is the GUID inside the block (preferred) or name_attr fallback.
    """
    cur = conn.cursor()
    row = cur.execute(
        "SELECT response_xml FROM raw_payloads WHERE request_type=? LIMIT 1",
        (request_type,),
    ).fetchone()
    if not row:
        return {}
    xml = row[0]
    # Slice into per-block chunks. Use a non-greedy regex on the block tag.
    block_re = re.compile(
        rf"<{block_tag}\s+NAME=\"([^\"]*)\"[^>]*>(.*?)</{block_tag}>", re.DOTALL
    )
    field_re = re.compile(r"<([A-Z][A-Z0-9_.]*)(?:\s+TYPE=\"[^\"]*\")?\s*>([^<]*)</\1>")
    out = {}
    for m in block_re.finditer(xml):
        name = m.group(1)
        body = m.group(2)
        rec = defaultdict(list)
        for fm in field_re.finditer(body):
            tag = fm.group(1)
            val = fm.group(2).strip()
            if val:
                rec[tag].append(val)
        # GUID is preferred join key when present
        guid = rec.get("GUID", [None])[0]
        key = guid or name
        if not key:
            continue
        # Flatten singletons
        flat = {}
        for tag, vals in rec.items():
            flat[tag] = vals[0] if len(vals) == 1 else vals
        flat["__name__"] = name
        out[key] = flat
    return out


def load_rosetta_records(conn: sqlite3.Connection, rosetta_table: str,
                         join_col: str):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {rosetta_table}")
    cols = [d[0] for d in cur.description]
    out = {}
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        key = rec.get(join_col)
        if key:
            out[key] = rec
    return out


def load_extracted_records(conn: sqlite3.Connection, kind_table: str):
    cur = conn.cursor()
    cur.execute(f"SELECT master_id, guid, fields_json FROM {kind_table}")
    out = {}
    for master_id, guid, fjson in cur.fetchall():
        if not guid or not fjson:
            continue
        try:
            fields = json.loads(fjson)
        except json.JSONDecodeError:
            continue
        out[guid] = (master_id, fields)
    return out


def load_extracted_records_v4(conn: sqlite3.Connection, kind: str):
    """v4 schema: master_records(kind, name, fields_json).
    Merge all rows with the same name into a single fields dict whose values
    are lists of every value seen for that field-id."""
    cur = conn.cursor()
    cur.execute(
        "SELECT name, fields_json FROM master_records "
        "WHERE kind=? AND fields_json IS NOT NULL AND name IS NOT NULL "
        "AND length(name) > 0",
        (kind,),
    )
    out = defaultdict(lambda: defaultdict(list))
    for name, fjson in cur.fetchall():
        try:
            fields = json.loads(fjson)
        except json.JSONDecodeError:
            continue
        for fid, val in fields.items():
            if isinstance(val, list):
                out[name][fid].extend(str(v) for v in val if v is not None)
            elif val is not None:
                out[name][fid].append(str(val))
    return {k: dict(v) for k, v in out.items()}


def build_kind_map(kind_name: str, spec: dict,
                   extracted: dict,
                   ros_conn: sqlite3.Connection,
                   join_col: str):
    """extracted: { join_key -> fields_dict_with_list_values }
    join_col: which Rosetta column to use as the join key (guid or name)."""
    rosetta = load_rosetta_records(ros_conn, spec["rosetta_table"], join_col)
    # Augment Rosetta records with values mined from response_xml.
    if spec.get("xml_request_type") and spec.get("xml_fields"):
        # XML records keyed by GUID where present, else NAME.
        xml_recs = parse_xml_records(
            ros_conn, spec["xml_request_type"], spec["xml_block_tag"]
        )
        # Re-key XML by the same join_col we use for Rosetta. parse_xml_records
        # already prefers GUID; fall back to NAME for kinds that join on name.
        if join_col == "name":
            renamed = {}
            for k, v in xml_recs.items():
                nm = v.get("__name__")
                if nm:
                    renamed[nm] = v
                else:
                    renamed[k] = v
            xml_recs = renamed
        # Inject XML-derived columns into rosetta records under the new label.
        for label, tag, _kind, _alias in spec["xml_fields"]:
            for k, ros in rosetta.items():
                xrec = xml_recs.get(k)
                if not xrec:
                    continue
                v = xrec.get(tag)
                if v is None:
                    continue
                # If the tag had multiple values, join with newline (address-like).
                ros[label] = (
                    "\n".join(v) if isinstance(v, list) else str(v).strip()
                )

    common = set(rosetta.keys()) & set(extracted.keys())
    print(f"[{kind_name}] rosetta={len(rosetta)} extracted={len(extracted)} "
          f"intersection={len(common)} join_on={join_col}")

    # Compose effective field list (Rosetta + XML-derived virtual columns).
    effective_fields = list(spec["fields"]) + [
        (label, kind) for (label, _, kind, _) in spec.get("xml_fields", [])
    ]

    # For each (column, field_id): hits, samples
    stats = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # [hits, samples]
    field_ids_seen = set()

    # Booleans: track presence-correlation between FID and column-truthy.
    # bool_present[col][fid] = (n_present_when_true, n_present_when_false)
    bool_present = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    bool_totals = defaultdict(lambda: [0, 0])  # [n_true, n_false]

    for key in common:
        ros = rosetta[key]
        fields = extracted[key]
        field_ids_seen.update(fields.keys())
        all_fids_in_record = set(fields.keys())
        for col, kind in effective_fields:
            raw = ros.get(col)
            if kind == "bool":
                if raw is None:
                    continue
                truthy = bool(raw)
                idx = 0 if truthy else 1
                bool_totals[col][idx] += 1
                for fid in all_fids_in_record:
                    bool_present[col][fid][idx] += 1
                continue
            norm = NORMALIZERS[kind](raw)
            if norm is None:
                continue
            for fid, fval in fields.items():
                stats[col][fid][1] += 1
                if _values_match(norm, fval, kind):
                    stats[col][fid][0] += 1

    # For each column, find the field_id with the highest hit rate.
    column_to_fid = {}
    audit = {}
    for col, ckind in effective_fields:
        if ckind == "bool":
            col_populated = sum(
                1 for key in common if rosetta[key].get(col) is not None
            )
        else:
            col_populated = sum(
                1 for key in common
                if NORMALIZERS[ckind](rosetta[key].get(col)) is not None
            )

        if ckind == "bool":
            # Score each fid by (P(present|true) - P(present|false)) — large
            # positive means the fid presence indicates True; large negative
            # means absence indicates True.
            n_true, n_false = bool_totals[col]
            cand = []
            for fid, (p_t, p_f) in bool_present[col].items():
                if n_true == 0 or n_false == 0:
                    continue
                rate_true = p_t / n_true
                rate_false = p_f / n_false
                # Match if the fid is overwhelmingly present-when-true and
                # absent-when-false (or vice-versa).
                signal = abs(rate_true - rate_false)
                cand.append((fid, rate_true, rate_false, signal))
            cand.sort(key=lambda x: x[3], reverse=True)
            best = None
            for fid, rt, rf, sig in cand:
                if sig >= MIN_CONFIDENCE:
                    polarity = "present_means_true" if rt > rf else "present_means_false"
                    best = (fid, polarity, rt, rf, sig)
                    break
            audit[col] = {
                "best_fid": best[0] if best else None,
                "polarity": best[1] if best else None,
                "rate_when_true": round(best[2], 4) if best else None,
                "rate_when_false": round(best[3], 4) if best else None,
                "signal": round(best[4], 4) if best else None,
                "n_true": n_true,
                "n_false": n_false,
                "top5": [
                    {"fid": fid, "rate_true": round(rt, 4),
                     "rate_false": round(rf, 4), "signal": round(sig, 4)}
                    for (fid, rt, rf, sig) in cand[:5]
                ],
            }
            if best and (best[1] == "present_means_true"):
                column_to_fid[col] = best[0]
            continue
        # rank candidate fids by rate, breaking ties by hits
        sorted_fids = sorted(
            stats[col].items(),
            key=lambda x: ((x[1][0] / x[1][1]) if x[1][1] else 0, x[1][0]),
            reverse=True,
        )
        # Pick fids: rate >= MIN_CONFIDENCE AND at least MIN_SAMPLES (or _RARE).
        # Find primary (must clear MIN_COL_FRACTION coverage). Then look for
        # fallback fids that REDUCE the residual: only accept a fallback if
        # most of its hits are on records where the primary is absent. This
        # prevents low-coverage shared slots like 0x0003 (which holds state OR
        # gstin OR pincode depending on the record) from being assigned to one
        # specific column.
        primary = None
        for fid, (hits, samples) in sorted_fids:
            if samples == 0:
                continue
            sample_floor = (
                MIN_SAMPLES if col_populated >= MIN_SAMPLES else MIN_SAMPLES_RARE
            )
            if samples < sample_floor:
                continue
            rate = hits / samples
            coverage = samples / col_populated if col_populated else 0
            if rate < MIN_CONFIDENCE:
                continue
            if coverage >= MIN_COL_FRACTION:
                primary = (fid, hits, samples, rate, coverage)
                break

        passing = [primary] if primary else []
        if primary:
            primary_fid = primary[0]
            # build the set of records where primary is present (these are
            # records the fallback shouldn't override).
            primary_records = set()
            for key in common:
                fields = extracted[key]
                if primary_fid in fields:
                    primary_records.add(key)

            # For each candidate fallback, count: agreement on records where
            # primary is ABSENT. Accept if those agreement rate >= MIN_CONFIDENCE
            # and at least MIN_SAMPLES_RARE such records exist.
            for fid, (hits, samples) in sorted_fids:
                if fid == primary_fid:
                    continue
                # Agreement on records without primary
                fid_hits_outside = 0
                fid_samples_outside = 0
                for key in common:
                    if key in primary_records:
                        continue
                    fields = extracted[key]
                    if fid not in fields:
                        continue
                    norm = NORMALIZERS[ckind](rosetta[key].get(col))
                    if norm is None:
                        continue
                    fid_samples_outside += 1
                    if _values_match(norm, fields[fid], ckind):
                        fid_hits_outside += 1
                if fid_samples_outside < MIN_SAMPLES_RARE:
                    continue
                outside_rate = fid_hits_outside / fid_samples_outside
                if outside_rate < MIN_CONFIDENCE:
                    continue
                rate = hits / samples
                coverage = samples / col_populated if col_populated else 0
                passing.append((fid, hits, samples, rate, coverage))

        best = primary
        audit[col] = {
            "best_fid": best[0] if best else None,
            "hits": best[1] if best else 0,
            "samples": best[2] if best else 0,
            "rate": round(best[3], 4) if best else 0.0,
            "coverage": round(best[4], 4) if best else 0.0,
            "col_populated": col_populated,
            "fallback_fids": [p[0] for p in passing[1:]] if best else [],
            "top5": [
                {"fid": fid, "hits": h, "samples": s,
                 "rate": round(h / s, 4) if s else 0.0,
                 "coverage": round(s / col_populated, 4) if col_populated else 0.0}
                for fid, (h, s) in sorted_fids[:5]
            ],
        }
        if best:
            column_to_fid[col] = [p[0] for p in passing]

    # column_to_fid[col] is now a list [primary, fallback1, fallback2, ...]
    # Build the inverse: primary fid -> column (used for column-name display).
    fid_to_col = defaultdict(list)
    for col, fids in column_to_fid.items():
        if fids:
            fid_to_col[fids[0]].append(col)

    spec_order = {c: i for i, (c, _) in enumerate(effective_fields)}
    flat = {}
    aliases = {}
    for fid, cols in fid_to_col.items():
        cols.sort(key=lambda c: spec_order[c])
        flat[fid] = cols[0]
        if len(cols) > 1:
            aliases[fid] = cols[1:]

    return {
        "fid_to_column": flat,
        "fid_aliases": aliases,
        "column_to_fid": column_to_fid,  # list-valued: [primary, fallback...]
        "audit": audit,
        "n_records": len(common),
        "field_ids_seen": sorted(field_ids_seen),
    }


_TYPED_ALLOWED_HIGH_CODEPOINTS = frozenset({
    0x2010, 0x2013, 0x2014, 0x2018, 0x2019, 0x201c, 0x201d,
    0x20a8, 0x20a9, 0x20b9, 0x2122, 0x00a9, 0x00ae, 0x00b0, 0x00ba,
})


def _typed_str_has_garbage(s):
    """Reject TLV strings whose codepoints fall outside Latin-extended
    (signals a TLV walker overrun across a page boundary, manifesting as
    CJK / Bengali / Devanagari mojibake)."""
    if s is None:
        return True
    for c in str(s):
        cp = ord(c)
        if cp < 0x20 and c not in "\t\n\r":
            return True
        if 0xd800 <= cp <= 0xdfff:
            return True
        if cp == 0xfffd:
            return True
        if cp > 0x024f and cp not in _TYPED_ALLOWED_HIGH_CODEPOINTS:
            return True
    return False


_TYPED_NAME_NOISE = frozenset({
    "Primary Mobile No.", "Mobile No.", "Email", "Phone No.",
    "Whatsapp No.", "Office Address", "Home Address",
})
_TYPED_TALLY_PREFIX_RE = re.compile(r"^[0-9]{2}[A-Za-z][0-9A-Za-z]?\s")

# Shape validators per column. The fid-chain mechanism may walk from a
# canonical fid (e.g. 0x0acc for state) to a generic fid (e.g. 0x0003) when the
# canonical is absent. Generic fids hold values of varied shapes — a state
# column's chain might find a GSTIN in 0x0003 if we don't shape-check.
# Validators below are loose by design: they reject obviously-wrong shapes
# (e.g. a 15-char GSTIN as a state) but accept the long tail.
_PINCODE_RE = re.compile(r"^\d{6}$")
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[A-Z\d]$")
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_PHONE_RE = re.compile(r"^[+\d\-\s()]{7,20}$")
_EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w\-]+\.[\w.\-]+$")

# Indian states/UTs published by Rosetta. Plus a few we've seen empirically.
_KNOWN_STATES = frozenset({
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Chandigarh", "Dadra and Nagar Haveli", "Daman and Diu", "Delhi",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jammu and Kashmir",
    "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "New York", "Not Applicable", "Odisha", "Puducherry",
    "Pune", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Other Territory",
})

_KNOWN_COUNTRIES = frozenset({
    "India", "China", "United States of America", "USA", "United Kingdom",
    "UK", "Singapore", "Australia", "Canada", "Germany", "France",
    "Italy", "Spain", "Japan", "Korea", "South Korea", "Vietnam",
    "Thailand", "Malaysia", "Indonesia", "Philippines", "Taiwan",
    "United Arab Emirates", "UAE", "Saudi Arabia", "Switzerland",
    "Netherlands", "Belgium", "Sweden", "Denmark", "Norway", "Finland",
    "Russia", "Turkey", "Egypt", "South Africa", "Brazil", "Mexico",
    "Argentina", "Israel", "Ireland", "Lithuania", "Poland",
    "Portugal", "Greece", "Hong Kong", "New Zealand",
})


def _value_passes_shape(value, col):
    """Loose shape validation for a typed_v1 column. Returns True if `value` is
    plausibly the right shape for `col`. Used to reject cross-fid pollution
    when the chain falls back to a generic fid like 0x0003 / 0x0004."""
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    if col == "pincode":
        return bool(_PINCODE_RE.match(s))
    if col == "gstin":
        return bool(_GSTIN_RE.match(s))
    if col == "pan":
        return bool(_PAN_RE.match(s))
    if col == "phone":
        return bool(_PHONE_RE.match(s)) and not s.startswith("+91") or bool(_PHONE_RE.match(s))
    if col == "email":
        return bool(_EMAIL_RE.match(s))
    if col == "state":
        return s in _KNOWN_STATES
    if col == "country":
        return s in _KNOWN_COUNTRIES
    # Other columns (name, mailing_name, address, created_by, currency, parent,
    # base_units, hsn_code, etc.): accept any non-empty non-mojibake string.
    return True


def _pick_typed_value(values, col=None):
    """From a list of decoded values, pick the canonical one. Skip mojibake
    and obvious template-form labels; for name-like columns, prefer the
    Tally-prefix-shaped value."""
    if not isinstance(values, list):
        if values is None:
            return None
        if isinstance(values, str) and _typed_str_has_garbage(values):
            return None
        return values
    # Filter out noise + garbage
    candidates = []
    for pos, v in enumerate(values):
        if v is None:
            continue
        if isinstance(v, str):
            if _typed_str_has_garbage(v):
                continue
            stripped = v.strip()
            if not stripped:
                continue
            if col in ("name", "mailing_name") and stripped in _TYPED_NAME_NOISE:
                continue
            candidates.append((pos, stripped))
        else:
            candidates.append((pos, v))
    if not candidates:
        return None
    if col in ("name", "mailing_name"):
        # Prefer Tally-prefix-shaped values, else first list-position.
        def score(pos, val):
            s = -pos * 30  # earlier list position wins
            if isinstance(val, str) and _TYPED_TALLY_PREFIX_RE.match(val):
                s += 200
            return s
        candidates.sort(key=lambda c: -score(*c))
    # else: first non-garbage value (already in list-position order)
    return candidates[0][1]


def build_typed_sqlite(v5_conn: sqlite3.Connection, field_map: dict, out_path: Path):
    if out_path.exists():
        out_path.unlink()
    typed = sqlite3.connect(out_path)
    tcur = typed.cursor()
    cur = v5_conn.cursor()

    for kind, kspec in KINDS.items():
        col_to_fids = field_map[kind]["column_to_fid"]  # list-valued
        # Drop xml_* virtual columns whose primary fid is the same as a real
        # Rosetta column already mapped (avoids duplicate xml_state alongside state).
        primary_to_real = {
            fids[0]: col for col, fids in col_to_fids.items()
            if not col.startswith("xml_") and fids
        }
        cleaned_columns = []
        for col, fids in col_to_fids.items():
            if not fids:
                continue
            if col.startswith("xml_"):
                # If this xml_* column's primary fid is already the primary fid
                # of a real Rosetta column, skip it.
                if fids[0] in primary_to_real and primary_to_real[fids[0]] != col:
                    continue
            cleaned_columns.append((col, fids))

        cols = ["master_id", "guid"] + [c for c, _ in cleaned_columns
                                         if c not in ("master_id", "guid")]
        try:
            cur.execute(f"SELECT master_id, guid, fields_json FROM {kind} LIMIT 1")
        except sqlite3.OperationalError:
            continue
        tcur.execute(
            f'CREATE TABLE {kind} (master_id INTEGER PRIMARY KEY, '
            f'{", ".join(f""""{c}" TEXT""" for c in cols if c not in ("master_id",))})'
        )
        cur.execute(f"SELECT master_id, guid, fields_json FROM {kind}")
        for master_id, guid, fjson in cur.fetchall():
            try:
                fields = json.loads(fjson) if fjson else {}
            except json.JSONDecodeError:
                fields = {}
            row = {"master_id": master_id, "guid": guid}
            for col, fids in cleaned_columns:
                # Walk fids in order. The primary fid (fids[0]) is
                # high-confidence by construction (>=90% rate against Rosetta),
                # so accept any non-mojibake value from it. Fallback fids are
                # generic slots (0x0003 / 0x0004) that hold many shapes —
                # shape-validate values from them to avoid pulling a GSTIN as
                # state, etc.
                value = None
                for fid_idx, fid in enumerate(fids):
                    v = fields.get(fid)
                    if v is None:
                        continue
                    is_primary = (fid_idx == 0)
                    candidate = None
                    if isinstance(v, list):
                        if col == "address":
                            clean_lines = [
                                str(x).strip() for x in v
                                if x is not None and isinstance(x, str)
                                and not _typed_str_has_garbage(x) and str(x).strip()
                            ]
                            if clean_lines:
                                candidate = "\n".join(clean_lines)
                        else:
                            for val in v:
                                if val is None:
                                    continue
                                if isinstance(val, str):
                                    if _typed_str_has_garbage(val):
                                        continue
                                    s = val.strip()
                                    if not s:
                                        continue
                                    if col in ("name", "mailing_name") and s in _TYPED_NAME_NOISE:
                                        continue
                                    if not is_primary and not _value_passes_shape(s, col):
                                        continue
                                    if col in ("name", "mailing_name"):
                                        if candidate is None or _TYPED_TALLY_PREFIX_RE.match(s):
                                            candidate = s
                                            if _TYPED_TALLY_PREFIX_RE.match(s):
                                                break
                                    else:
                                        candidate = s
                                        break
                                else:
                                    if not is_primary and not _value_passes_shape(val, col):
                                        continue
                                    candidate = str(val)
                                    break
                    elif isinstance(v, str):
                        if _typed_str_has_garbage(v):
                            continue
                        s = v.strip()
                        if s and (is_primary or _value_passes_shape(s, col)):
                            candidate = s
                    else:
                        if is_primary or _value_passes_shape(v, col):
                            candidate = str(v)
                    if candidate is not None:
                        value = candidate
                        break
                row[col] = value
            # Tally's XML gateway applies defaults for ledgers that have no
            # explicit state/country stored. Apply matching defaults when our
            # chain returned NULL.
            #   - state defaults to 'Not Applicable' (verified: 646/722 of
            #     NULL state records on 070525 are 'Not Applicable' in
            #     rosetta; the other 76 have data that lives in LinkMgr).
            #   - country defaults to 'India' (verified: 538/538 of NULL
            #     country records on 070525 are 'India' in rosetta).
            if kind == "ledgers":
                if "state" in row and row["state"] is None:
                    row["state"] = "Not Applicable"
                if "country" in row and row["country"] is None:
                    row["country"] = "India"
            placeholders = ", ".join("?" for _ in cols)
            tcur.execute(
                f'INSERT INTO {kind} ({", ".join(f""""{c}" """ for c in cols)}) '
                f'VALUES ({placeholders})',
                [row.get(c) for c in cols],
            )
    typed.commit()
    typed.close()


def _v5_usable() -> bool:
    if not V5_DB.exists() or V5_DB.stat().st_size < 1024:
        return False
    try:
        c = sqlite3.connect(V5_DB)
        cur = c.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='ledgers'")
        ok = cur.fetchone() is not None
        c.close()
        return ok
    except sqlite3.DatabaseError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-typed", action="store_true",
                    help="Skip building typed sqlite output")
    ap.add_argument("--source", choices=["auto", "v4", "v5"], default="auto")
    args = ap.parse_args()

    use_v5 = (args.source == "v5") or (args.source == "auto" and _v5_usable())
    src_path = V5_DB if use_v5 else V4_DB
    print(f"source: {'v5' if use_v5 else 'v4'} ({src_path})")

    src = sqlite3.connect(src_path)
    ros = sqlite3.connect(ROSETTA_DB)
    v4_fallback = None

    def get_v4():
        nonlocal v4_fallback
        if v4_fallback is None:
            v4_fallback = sqlite3.connect(V4_DB)
        return v4_fallback

    field_map = {}
    for kind, spec in KINDS.items():
        ext = {}
        join_col = None

        if use_v5 and spec["guid_col"] is not None:
            ec = src.cursor()
            try:
                ec.execute(f"SELECT master_id, guid, fields_json FROM {kind}")
                rows = ec.fetchall()
            except sqlite3.OperationalError:
                rows = []
            for master_id, guid, fjson in rows:
                if not guid or not fjson:
                    continue
                try:
                    fields = json.loads(fjson)
                except json.JSONDecodeError:
                    continue
                nf = {}
                for fid, val in fields.items():
                    if isinstance(val, list):
                        nf[fid] = [str(v) for v in val if v is not None]
                    elif val is not None:
                        nf[fid] = [str(val)]
                ext[guid] = nf
            join_col = "guid"

        if not ext:
            # Fall back to v4 (always join on name).
            ext = load_extracted_records_v4(get_v4(), kind)
            join_col = "name"

        field_map[kind] = build_kind_map(kind, spec, ext, ros, join_col)

    public = {
        "_meta": {
            "min_confidence": MIN_CONFIDENCE,
            "min_samples": MIN_SAMPLES,
            "source": "v5" if use_v5 else "v4",
            "source_db": str(src_path.relative_to(ROOT)),
            "source_rosetta": str(ROSETTA_DB.relative_to(ROOT)),
        },
        "by_kind": {
            kind: {
                "fid_to_column": field_map[kind]["fid_to_column"],
                "fid_aliases": field_map[kind].get("fid_aliases", {}),
                "column_to_fid": field_map[kind]["column_to_fid"],
                "n_records": field_map[kind]["n_records"],
            }
            for kind in field_map
        },
        "audit": {kind: field_map[kind]["audit"] for kind in field_map},
    }

    OUT_MAP.parent.mkdir(exist_ok=True)
    OUT_MAP.write_text(json.dumps(public, indent=2))
    print(f"wrote {OUT_MAP}")

    total_cols = sum(len(field_map[k]["column_to_fid"]) for k in field_map)
    total_fids = sum(len(field_map[k]["fid_to_column"]) for k in field_map)
    print(f"high-confidence mappings: {total_cols} columns "
          f"({total_fids} distinct field-ids)")
    for k in field_map:
        c2f = field_map[k]["column_to_fid"]
        print(f"  {k}: {len(c2f)} -> {c2f}")

    if not args.no_typed and use_v5:
        build_typed_sqlite(src, field_map, OUT_TYPED)
        print(f"wrote {OUT_TYPED}")
    elif not args.no_typed:
        print("skipping typed sqlite (need v5 with master_id/guid)")

    src.close()
    ros.close()


if __name__ == "__main__":
    main()

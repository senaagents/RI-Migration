from __future__ import annotations

import re
from collections.abc import Mapping


FIELD_CHAINS: dict[str, list[int]] = {
    "name": [0x0002, 0x01F7],
    "mailing_name": [0x0002, 0x01F7],
    "address": [0x0006, 0x01F8],
    "state": [0x0ACC, 0x0003],
    "country": [0x0A31, 0x0D4E, 0x0004],
    "pincode": [0x0A8F, 0x0005],
    "email": [0x0A90],
    "phone": [0x0A94],
    "pan": [0x0AC1],
    "gstin": [0x0ACA],
    "created_by": [0x0067],
}

ALLOWED_HIGH_CODEPOINTS = frozenset(
    {
        0x2010,
        0x2013,
        0x2014,
        0x2018,
        0x2019,
        0x201C,
        0x201D,
        0x20A8,
        0x20A9,
        0x20B9,
        0x2122,
        0x00A9,
        0x00AE,
        0x00B0,
        0x00BA,
    }
)

NAME_NOISE = frozenset(
    {
        "Primary Mobile No.",
        "Mobile No.",
        "Email",
        "Phone No.",
        "Whatsapp No.",
        "Office Address",
        "Home Address",
    }
)

TALLY_PREFIX_RE = re.compile(r"^[0-9]{2}[A-Za-z][0-9A-Za-z]?\s")
PINCODE_RE = re.compile(r"^\d{6}$")
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[A-Z\d]$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
PHONE_RE = re.compile(r"^[+\d\-\s()]{7,20}$")
EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w\-]+\.[\w.\-]+$")

KNOWN_STATES = frozenset(
    {
        "Andhra Pradesh",
        "Arunachal Pradesh",
        "Assam",
        "Bihar",
        "Chhattisgarh",
        "Chandigarh",
        "Dadra and Nagar Haveli",
        "Daman and Diu",
        "Delhi",
        "Goa",
        "Gujarat",
        "Haryana",
        "Himachal Pradesh",
        "Jammu and Kashmir",
        "Jharkhand",
        "Karnataka",
        "Kerala",
        "Ladakh",
        "Lakshadweep",
        "Madhya Pradesh",
        "Maharashtra",
        "Manipur",
        "Meghalaya",
        "Mizoram",
        "Nagaland",
        "New York",
        "Not Applicable",
        "Odisha",
        "Puducherry",
        "Pune",
        "Punjab",
        "Rajasthan",
        "Sikkim",
        "Tamil Nadu",
        "Telangana",
        "Tripura",
        "Uttar Pradesh",
        "Uttarakhand",
        "West Bengal",
        "Andaman and Nicobar Islands",
        "Other Territory",
    }
)

KNOWN_COUNTRIES = frozenset(
    {
        "India",
        "China",
        "United States of America",
        "USA",
        "United Kingdom",
        "UK",
        "Singapore",
        "Australia",
        "Canada",
        "Germany",
        "France",
        "Italy",
        "Spain",
        "Japan",
        "Korea",
        "South Korea",
        "Vietnam",
        "Thailand",
        "Malaysia",
        "Indonesia",
        "Philippines",
        "Taiwan",
        "United Arab Emirates",
        "UAE",
        "Saudi Arabia",
        "Switzerland",
        "Netherlands",
        "Belgium",
        "Sweden",
        "Denmark",
        "Norway",
        "Finland",
        "Russia",
        "Turkey",
        "Egypt",
        "South Africa",
        "Brazil",
        "Mexico",
        "Argentina",
        "Israel",
        "Ireland",
        "Lithuania",
        "Poland",
        "Portugal",
        "Greece",
        "Hong Kong",
        "New Zealand",
    }
)


def str_has_garbage(value: object) -> bool:
    if value is None:
        return True
    for char in str(value):
        codepoint = ord(char)
        if codepoint < 0x20 and char not in "\t\n\r":
            return True
        if 0xD800 <= codepoint <= 0xDFFF:
            return True
        if codepoint == 0xFFFD:
            return True
        if codepoint > 0x024F and codepoint not in ALLOWED_HIGH_CODEPOINTS:
            return True
    return False


def value_passes_shape(value: object, column: str) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if column == "pincode":
        return bool(PINCODE_RE.match(text))
    if column == "gstin":
        return bool(GSTIN_RE.match(text))
    if column == "pan":
        return bool(PAN_RE.match(text))
    if column == "phone":
        return bool(PHONE_RE.match(text))
    if column == "email":
        return bool(EMAIL_RE.match(text))
    if column == "state":
        return text in KNOWN_STATES
    if column == "country":
        return text in KNOWN_COUNTRIES
    return True


def pick_value(values: object, column: str, *, is_primary: bool) -> str | None:
    if not isinstance(values, list):
        candidates = [] if values is None else [values]
    else:
        candidates = values

    best: tuple[int, str] | None = None
    for index, value in enumerate(candidates):
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if str_has_garbage(text):
            continue
        if column in {"name", "mailing_name"} and text in NAME_NOISE:
            continue
        if not is_primary and not value_passes_shape(text, column):
            continue
        score = -index * 30
        if column in {"name", "mailing_name"} and TALLY_PREFIX_RE.match(text):
            score += 200
        if best is None or score > best[0]:
            best = (score, text)
    return best[1] if best else None


def pick_typed_fields(
    fields_by_id: Mapping[int, list[object]],
    *,
    kind: str | None = None,
) -> dict[str, str | None]:
    """Apply Claude's field-chain fallback with shape validators.

    The primary field id for a column is trusted after mojibake/noise filtering.
    Fallback ids are generic slots and must pass shape validation, preventing
    values such as GSTINs from being rendered as states or pincodes.
    """
    out: dict[str, str | None] = {}
    for column, field_chain in FIELD_CHAINS.items():
        value = None
        for index, field_id in enumerate(field_chain):
            candidate = pick_value(
                fields_by_id.get(field_id),
                column,
                is_primary=index == 0,
            )
            if candidate is not None:
                value = candidate
                break
        out[column] = value

    if kind == "ledgers":
        if out.get("state") is None:
            out["state"] = "Not Applicable"
        if out.get("country") is None:
            out["country"] = "India"
    return out

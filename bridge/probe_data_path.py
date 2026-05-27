"""Probe whether TallyPrime's HTTP/XML API exposes the company data folder path.

Run on the Windows machine where TallyPrime is running with at least one company
loaded. Talks to localhost:9000 by default. Tries several known TDL fields that
*might* return the data folder path; prints whatever Tally returns so we can
decide which field to use in the bridge.

    python probe_data_path.py
    python probe_data_path.py --host localhost --port 9000
"""

from __future__ import annotations

import argparse
import http.client
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html import escape


NATIVE_FIELDS = ["Name", "Guid"]

COMPUTE_EXPRESSIONS = [
    ("DataPath", "$$CmpDataPath:$Name"),
    ("DataPathNoArg", "$$CmpDataPath"),
    ("CmpPath", "$$CmpPath:$Name"),
    ("CmpFolder", "$$CmpFolder:$Name"),
    ("LocalPath", "$$LocalPath"),
    ("LocalConfigure", "$$LocalConfigure"),
    ("CmpFile", "$$CmpFile:$Name"),
    ("CmpRoot", "$$CmpRoot:$Name"),
    ("MainCmpDataPath", "$$MainCmpDataPath"),
    ("CompanyDataPath", "$$CompanyDataPath"),
    ("DataDir", "##SVCurrentCompany"),
]


def build_envelope() -> str:
    methods = "".join(f"<NATIVEMETHOD>{f}</NATIVEMETHOD>" for f in NATIVE_FIELDS)
    methods += "".join(f"<NATIVEMETHOD>{name}</NATIVEMETHOD>" for name, _ in COMPUTE_EXPRESSIONS)
    computes = "".join(
        f"<COMPUTE>{name} : {escape(expr)}</COMPUTE>"
        for name, expr in COMPUTE_EXPRESSIONS
    )
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>"
        "<TYPE>COLLECTION</TYPE><ID>CompanyInfoProbe</ID></HEADER><BODY><DESC>"
        "<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>"
        "<TDL><TDLMESSAGE>"
        '<COLLECTION NAME="CompanyInfoProbe" ISINITIALIZE="Yes">'
        "<TYPE>Company</TYPE>"
        f"{computes}"
        f"{methods}"
        "</COLLECTION>"
        "</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"
    )


def post(url: str, payload: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach Tally at {url}: {exc}")
    except http.client.RemoteDisconnected as exc:
        raise SystemExit(f"Tally closed the connection: {exc}")


def parse_companies(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"  (could not parse XML: {exc})")
        return []
    rows = []
    for company in root.iter("COMPANY"):
        row = {}
        for child in company:
            text = (child.text or "").strip()
            tag = child.tag
            if not text:
                continue
            row[tag] = text
        if row:
            rows.append(row)
    return rows


ALT_COLLECTIONS = [
    "List of Companies",
    "Companies on Disk",
    "List of Selected Companies",
    "List of Primary Companies",
    "ListOfCompaniesOnDisk",
    "CompanyOnDisk",
]


def build_alt_envelope(collection_name: str) -> str:
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>"
        f"<TYPE>COLLECTION</TYPE><ID>{escape(collection_name)}</ID></HEADER>"
        "<BODY><DESC>"
        "<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>"
        "</DESC></BODY></ENVELOPE>"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--raw", action="store_true", help="Print full raw XML, not just parsed companies")
    parser.add_argument("--probe-builtin", action="store_true", help="Probe built-in collections that may list companies on disk")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"Probing Tally at {url} for company data folder fields...\n")

    if args.probe_builtin:
        for col in ALT_COLLECTIONS:
            print(f"=== Collection: {col} ===")
            response = post(url, build_alt_envelope(col))
            print(response[:1500])
            print()
        return 0

    payload = build_envelope()
    response = post(url, payload)

    if args.raw:
        print("--- raw response ---")
        print(response)
        print("--- end raw response ---\n")

    companies = parse_companies(response)
    if not companies:
        print("Tally returned no <COMPANY> rows. Make sure a company is loaded.")
        print("\nFirst 800 chars of response for debugging:")
        print(response[:800])
        return 1

    print(f"Tally returned {len(companies)} loaded company row(s):\n")
    for i, row in enumerate(companies, 1):
        print(f"[{i}] Name: {row.get('NAME', '?')}")
        for tag, value in row.items():
            if tag in ("NAME",):
                continue
            print(f"     {tag}: {value}")
        print()

    path_tags = sorted({tag for row in companies for tag in row if "PATH" in tag.upper() or "FOLDER" in tag.upper()})
    print("=" * 60)
    if path_tags:
        print(f"Path-like fields Tally returned: {', '.join(path_tags)}")
        print("Pick whichever one shows the actual data folder.")
    else:
        print("Tally did NOT return any path-like field for the candidates tried.")
        print("Re-run with --raw to inspect the full response, or try --raw and grep")
        print("the XML for the folder path you expect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

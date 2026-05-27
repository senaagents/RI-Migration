#!/usr/bin/env python3
"""Sena Tally Bridge — pair, probe, dispatch.

Connects to Sena, posts heartbeats, polls for user-triggered commands, and
dispatches the work to one of two extraction paths the user picks in the
migration UI:

  command type=decode_1800   → tally_decode_path.run()  (.1800 binary decode)
  command type=http_extract  → tally_http_path.run()    (TDL/XML HTTP firehose)

Both paths write to a local SQLite file at extract_output_path(); the
bridge does NOT push records to the Sena server. Surfacing the SQLite back
to Sena (upload, syncthing, etc.) is a separate concern.

Dependency-free on purpose so it can be packaged with PyInstaller or run
under stock Python.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from pathlib import Path


__version__ = "0.3.0"

# PyInstaller's `--windowed` / `console=False` exe runs without an attached
# console: `sys.stdout` exists but every write to it raises
# `OSError [Errno 22] Invalid argument`, which crashes the bridge on the
# first print() before any logging gets surfaced. Redirect to NUL up front
# so prints become silent no-ops; main() will swap to the real log file
# once it's parsed --log-file. Only do this when frozen — running the
# script under a real Python keeps the normal terminal output.
if getattr(sys, "frozen", False):
	sys.stdout = open(os.devnull, "w", encoding="utf-8")
	sys.stderr = open(os.devnull, "w", encoding="utf-8")

DEFAULT_METHOD_PREFIX = "migration.core.api"
INVALID_XML_CHARS = re.compile(r"&#(?:[0-8]|1[0-1]|1[4-9]|2[0-9]|3[01]);")
BRIDGE_CAPABILITIES = {
	"bridge_version": __version__,
	"protocols": ["tally_http_xml", "tally_1800_decode"],
	"extraction_paths": ["decode_1800", "http_extract"],
}

# The codex .1800 decoder lives next to the bridge in the migration app.
# Add it to sys.path so `from tally1800.decoded_export import ...` works
# both from the source tree and from a PyInstaller bundle that includes it.
DECODER_ROOT = Path(__file__).resolve().parents[1] / "tally_1800" / "codex"
if DECODER_ROOT.exists() and str(DECODER_ROOT) not in sys.path:
	sys.path.insert(0, str(DECODER_ROOT))


@dataclass
class BridgeConfig:
	server: str
	pairing_code: str
	tally_host: str = "localhost"
	tally_port: int = 9000
	tally_data_dir: str = ""
	bridge_id: str = ""
	connection_id: str = ""
	bridge_token: str = ""
	config_path: str = ""
	timeout: int = 120
	once: bool = False

	@property
	def tally_url(self) -> str:
		return f"http://{self.tally_host}:{self.tally_port}"


# ---------------------------------------------------------------------------
# Tiny utilities
# ---------------------------------------------------------------------------
def log(message: str) -> None:
	timestamp = time.strftime("%H:%M:%S")
	print(f"[{timestamp}] {message}", flush=True)


def clean_xml(text: str) -> str:
	"""Strip XML control-char entities and raw control bytes that lxml/ET reject.

	Tally occasionally emits `&#0;` inside narration fields and bare control
	bytes inside encoded amounts. Both make ET.fromstring() raise. We drop
	them here so downstream parsers see well-formed XML.
	"""
	text = INVALID_XML_CHARS.sub("", text)
	return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def hash_text(value: str) -> str:
	return sha256(value.encode("utf-8")).hexdigest()


def compact_json(value: dict) -> str:
	return json.dumps(value, sort_keys=True, separators=(",", ":"))


def save_config_value(config_path: str, key: str, value: str) -> None:
	"""Persist a single key into bridge-config.json without a full rewrite.

	The Inno Setup installer drops bridge-config.json with the server URL
	and pairing code; we update connection_id / bridge_token / data_dir as
	we learn them so the next bridge run resumes without re-pairing.
	"""
	if not config_path:
		return
	path = Path(config_path)
	try:
		data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
	except Exception:
		data = {}
	data[key] = value
	path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def persist_data_dir(config: BridgeConfig, new_dir: str) -> None:
	config.tally_data_dir = new_dir
	if config.config_path:
		save_config_value(config.config_path, "tally_data_dir", new_dir)


def extract_output_path(company_id: str, run_id: str, kind: str) -> Path:
	"""Resolve the SQLite output path for one extract run.

	Layout: <root>/extracts/<company_id>/<run_id>/<kind>.sqlite

	Root resolution (first match wins):
	  1. SENA_BRIDGE_DATA_ROOT (override, useful in tests/CI)
	  2. %LOCALAPPDATA%/SenaTallyBridge      (Windows production)
	  3. ~/.local/share/sena-tally-bridge    (Linux/dev fallback)

	Both decode_1800 and http_extract land here; the `kind` segment lets
	a single company directory hold several side-by-side runs.
	"""
	override = os.environ.get("SENA_BRIDGE_DATA_ROOT")
	if override:
		root = Path(override)
	elif os.environ.get("LOCALAPPDATA"):
		root = Path(os.environ["LOCALAPPDATA"]) / "SenaTallyBridge"
	else:
		root = Path.home() / ".local" / "share" / "sena-tally-bridge"
	safe_company = "".join(c if c.isalnum() or c in "-_" else "_" for c in (company_id or "company"))[:80] or "company"
	safe_run = "".join(c if c.isalnum() else "_" for c in (run_id or uuid.uuid4().hex[:12]))[:32]
	out_dir = root / "extracts" / safe_company / safe_run
	out_dir.mkdir(parents=True, exist_ok=True)
	safe_kind = "".join(c if c.isalnum() else "_" for c in (kind or "extract")) or "extract"
	return out_dir / f"{safe_kind}.sqlite"


# ---------------------------------------------------------------------------
# Sena server HTTP (pairing, heartbeat, command poll)
# ---------------------------------------------------------------------------
def post_json(server: str, method: str, payload: dict, timeout: int = 120) -> dict:
	url = f"{server.rstrip('/')}/api/method/{DEFAULT_METHOD_PREFIX}.{method}"
	body = json.dumps(payload).encode("utf-8")
	req = urllib.request.Request(
		url,
		data=body,
		headers={"Content-Type": "application/json"},
		method="POST",
	)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			data = json.loads(resp.read().decode("utf-8"))
	except urllib.error.HTTPError as exc:
		detail = exc.read().decode("utf-8", errors="replace")
		raise RuntimeError(f"{method} failed with HTTP {exc.code}: {detail}") from exc
	except urllib.error.URLError as exc:
		raise RuntimeError(f"{method} failed: {exc}") from exc

	if data.get("exc"):
		raise RuntimeError(f"{method} failed: {data['exc']}")
	return data.get("message") or {}


def claim_pairing(config: BridgeConfig) -> dict:
	if config.connection_id and config.bridge_token:
		return {
			"connection_id": config.connection_id,
			"bridge_token": config.bridge_token,
			"status": "Active",
		}
	return post_json(
		config.server,
		"claim_integration_bridge_pairing",
		{
			"pairing_code": config.pairing_code,
			"bridge_id": config.bridge_id or f"sena-tally-{uuid.uuid4().hex[:10]}",
			"host": config.tally_host,
			"port": config.tally_port,
			"capabilities_json": json.dumps(build_capabilities(config)),
		},
		config.timeout,
	)


def heartbeat(config: BridgeConfig, connection_id: str, bridge_token: str, **updates) -> dict:
	# Standalone mode (no Sena server configured) — silently no-op so the
	# extract path modules can be driven from a test harness without
	# pairing.
	if not getattr(config, "server", ""):
		return {}
	payload = {
		"connection_id": connection_id,
		"bridge_token": bridge_token,
		"status": updates.pop("status", "Active"),
		**updates,
	}
	return post_json(config.server, "bridge_heartbeat", payload, config.timeout)


def poll_command(config: BridgeConfig, connection_id: str, bridge_token: str) -> dict | None:
	"""Cheap command-only poll between heartbeats. Returns the queued command
	(if any) without updating last_seen_at. Called inside the idle loop so
	user-triggered commands feel responsive without making heartbeats noisy.
	"""
	try:
		result = post_json(
			config.server,
			"poll_bridge_command",
			{"connection_id": connection_id, "bridge_token": bridge_token},
			timeout=10,
		)
	except Exception as exc:
		log(f"Command poll failed (non-fatal): {exc}")
		return None
	return result.get("command")


def ack_bridge_command(
	config: BridgeConfig, connection_id: str, bridge_token: str,
	command_id: str, status: str, result: dict | None = None, error: str = "",
) -> dict:
	# Standalone mode — print a structured ack to stdout and return so the
	# extract path's final result is still visible without Sena.
	if not getattr(config, "server", ""):
		log(f"[ack {command_id}] {status} {json.dumps(result or {}, ensure_ascii=False)[:200]}{' err='+error if error else ''}")
		return {}
	return post_json(
		config.server,
		"ack_bridge_command",
		{
			"connection_id": connection_id,
			"bridge_token": bridge_token,
			"command_id": command_id,
			"status": status,
			"result_json": json.dumps(result or {}),
			"error": error,
		},
		config.timeout,
	)


def build_capabilities(config: BridgeConfig | None = None) -> dict:
	"""Augment the static capability set with what the bridge currently sees
	on disk. The server stores this on the connection so the UI can show
	"we are scanning these folders" without a round-trip.
	"""
	caps = dict(BRIDGE_CAPABILITIES)
	if config is not None:
		caps["configured_data_dir"] = getattr(config, "tally_data_dir", "") or ""
		try:
			caps["discovered_roots"] = [str(p) for p in discover_tally_data_roots(config)]
		except Exception:
			# Discovery hits the filesystem; never let it crash a heartbeat.
			caps["discovered_roots"] = []
	return caps


# ---------------------------------------------------------------------------
# Tally HTTP (TDL/XML POST + retry)
# ---------------------------------------------------------------------------
def post_tally_xml(config: BridgeConfig, xml_payload: str) -> str:
	req = urllib.request.Request(
		config.tally_url,
		data=xml_payload.encode("utf-8"),
		headers={"Content-Type": "application/xml"},
		method="POST",
	)
	try:
		with urllib.request.urlopen(req, timeout=config.timeout) as resp:
			return clean_xml(resp.read().decode("utf-8", errors="replace"))
	except http.client.RemoteDisconnected as exc:
		raise RuntimeError("Tally closed the HTTP connection without sending a response") from exc
	except http.client.IncompleteRead as exc:
		# Tally truncates large responses occasionally; salvage the partial
		# bytes so the caller can decide whether to retry or accept what it got.
		return clean_xml(exc.partial.decode("utf-8", errors="replace"))
	except urllib.error.URLError as exc:
		raise RuntimeError(f"Could not read from Tally: {exc}") from exc


def post_tally_xml_with_retries(config: BridgeConfig, xml_payload: str, attempts: int = 2) -> str:
	last_error: Exception | None = None
	for attempt in range(1, attempts + 1):
		try:
			return post_tally_xml(config, xml_payload)
		except RuntimeError as exc:
			last_error = exc
			if attempt >= attempts:
				break
			log(f"Tally did not respond cleanly; retrying in {attempt + 1}s ({exc})")
			time.sleep(attempt + 1)
	assert last_error is not None
	raise last_error


def build_collection_xml(
	name: str,
	object_type: str | None,
	fields: list[str],
	static_variables: dict[str, str] | None = None,
	is_modify: bool = False,
	child_of: str | None = None,
) -> str:
	"""TDL <COLLECTION> envelope. Used by the connect-time probe and by
	tally_http_path's master/voucher pulls (which imports this).
	"""
	methods = "".join(f"<NATIVEMETHOD>{field}</NATIVEMETHOD>" for field in fields or [])
	collection_attrs = f'NAME="{escape(name)}"'
	if is_modify:
		collection_attrs += ' ISMODIFY="Yes"'
	type_xml = f"<TYPE>{escape(object_type)}</TYPE>" if object_type else ""
	child_xml = f"<ADD>CHILD OF : {escape(child_of)}</ADD>" if child_of else ""
	static_xml = "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
	for key, value in (static_variables or {}).items():
		static_xml += f"<{key}>{escape(str(value))}</{key}>"
	return (
		"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>"
		f"<TYPE>COLLECTION</TYPE><ID>{name}</ID></HEADER><BODY><DESC>"
		f"<STATICVARIABLES>{static_xml}</STATICVARIABLES>"
		"<TDL><TDLMESSAGE>"
		f"<COLLECTION {collection_attrs} ISINITIALIZE=\"Yes\">"
		f"{type_xml}{child_xml}{methods}</COLLECTION>"
		"</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"
	)


# ---------------------------------------------------------------------------
# XML element helpers (used by the license probe and tally_http_path)
# ---------------------------------------------------------------------------
def element_text(element: ET.Element, name: str) -> str:
	found = element.find(f".//{name}")
	if found is not None and found.text:
		return found.text.strip()
	return ""


def object_name(element: ET.Element) -> str:
	return element.attrib.get("NAME", "").strip() or element_text(element, "NAME")


def child_texts(element: ET.Element) -> dict:
	values = {}
	for child in list(element):
		if len(list(child)):
			continue
		text = (child.text or "").strip()
		if text:
			values[child.tag.lower()] = text
	return values


# ---------------------------------------------------------------------------
# License + product info probe
# ---------------------------------------------------------------------------
# Cast wide on purpose: $$LicenseInfo:* names shift across TallyERP9 /
# TallyPrime releases, so we ask several candidates per concept and parse
# whichever ones come back populated. Confirmed working on TallyPrime
# Educational: SerialNumber, AccountId, IsEducationalMode, IsSilver, IsGold,
# UserLevel, SiteId, IsRented. Returned blank (release-dependent or gated by
# educational mode) — extras kept so we can retest on a real licensed Tally:
# $$ProductName, $$ProductReleaseString, $$ProductVersion, $$IsTSSValid,
# $$LicenseInfo:HasMultiUser, $$LicenseInfo:IsRemote.
ENVIRONMENT_PROBE_FIELDS = [
	("LSerial",    "$$LicenseInfo:SerialNumber",      "SERIAL"),
	("LAccount",   "$$LicenseInfo:AccountId",         "ACCOUNT"),
	("LUserEmail", "$$LicenseInfo:UserEmail",         "USEREMAIL"),
	("LUserName",  "$$LicenseInfo:UserName",          "USERNAME"),
	("LEdu",       "$$LicenseInfo:IsEducationalMode", "EDU"),
	("LSilver",    "$$LicenseInfo:IsSilver",          "SILVER"),
	("LGold",      "$$LicenseInfo:IsGold",            "GOLD"),
	("LMulti",     "$$LicenseInfo:HasMultiUser",      "MULTIUSER"),
	("LTSS",       "$$LicenseInfo:IsTSSValid",        "TSS"),
	("LTSS2",      "$$IsTSSValid",                    "TSS2"),
	("LRemote",    "$$LicenseInfo:IsRemote",          "REMOTE"),
	("LRemote2",   "$$IsRemote",                      "REMOTE2"),
	("LRented",    "$$LicenseInfo:IsRented",          "RENTED"),
	("LSiteId",    "$$LicenseInfo:SiteId",            "SITEID"),
	("LUserLvl",   "$$LicenseInfo:UserLevel",         "USERLEVEL"),
	("LProduct",   "$$ProductName",                   "PRODUCT"),
	("LDspProd",   "$$DspProductName",                "DSPPRODUCT"),
	("LRelease",   "$$ProductReleaseString",          "RELEASE"),
	("LRelease2",  "$$ProductRelease",                "RELEASE2"),
	("LDspRel",    "$$DspProductRelease",             "DSPRELEASE"),
	("LVersion",   "$$ProductVersion",                "VERSION"),
	("LVerStr",    "$$VersionString",                 "VERSIONSTR"),
	("LMajor",     "$$ProductMajorVer",               "MAJOR"),
	("LMinor",     "$$ProductMinorVer",               "MINOR"),
	("LMachSer",   "$$LicenseInfo:LicenseSerial",     "LICSERIAL"),
	("LCmpName",   "$$Name",                          "COMPANY"),
]


def build_environment_probe_xml() -> str:
	"""TDL/XML report that evaluates each candidate accessor and emits XML.

	Wired as a Report iterating the Company collection — Tally requires a
	row context for FIELD <SET> formulas to render. Each row will repeat
	the scalar license values, so the parser just reads the first row.
	A loaded company is therefore required; with none, output is empty.
	"""
	field_names = ",".join(name for name, _, _ in ENVIRONMENT_PROBE_FIELDS)
	field_xml = "".join(
		f'<FIELD NAME="{name}"><SET>{formula}</SET><XMLTAG>"{tag}"</XMLTAG></FIELD>'
		for name, formula, tag in ENVIRONMENT_PROBE_FIELDS
	)
	return (
		"<ENVELOPE>"
		"<HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>"
		"<TYPE>DATA</TYPE><ID>SenaEnvProbe</ID></HEADER>"
		"<BODY><DESC>"
		"<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>"
		"<TDL><TDLMESSAGE>"
		'<REPORT NAME="SenaEnvProbe"><FORMS>SenaEnvProbe</FORMS></REPORT>'
		'<FORM NAME="SenaEnvProbe"><PARTS>SenaEnvProbe</PARTS></FORM>'
		'<PART NAME="SenaEnvProbe">'
		'<LINES>SenaEnvProbe</LINES>'
		'<REPEAT>SenaEnvProbe : SenaEnvCol</REPEAT>'
		'<SCROLLED>Vertical</SCROLLED>'
		'</PART>'
		f'<LINE NAME="SenaEnvProbe"><FIELDS>{field_names}</FIELDS></LINE>'
		f"{field_xml}"
		'<COLLECTION NAME="SenaEnvCol"><TYPE>Company</TYPE></COLLECTION>'
		"</TDLMESSAGE></TDL>"
		"</DESC></BODY></ENVELOPE>"
	)


def parse_environment_xml(xml_text: str) -> dict:
	cleaned = clean_xml(xml_text)
	out: dict = {"raw_xml": cleaned}
	try:
		root = ET.fromstring(cleaned)
	except ET.ParseError as exc:
		out["parse_error"] = str(exc)
		return out
	err = root.find(".//LINEERROR")
	if err is not None and (err.text or "").strip():
		out["tally_error"] = err.text.strip()
	for _name, _formula, tag in ENVIRONMENT_PROBE_FIELDS:
		elem = root.find(f".//{tag}")
		if elem is not None:
			text = (elem.text or "").strip()
			if text and tag.lower() not in out:
				out[tag.lower()] = text
	# Derive a clean edition label from whichever flags Tally answered.
	edu = (out.get("edu") or "").lower()
	silver = (out.get("silver") or "").lower()
	gold = (out.get("gold") or "").lower()
	if edu == "yes":
		out["edition"] = "Educational"
	elif gold == "yes" and silver != "yes":
		out["edition"] = "Gold"
	elif silver == "yes" and gold != "yes":
		out["edition"] = "Silver"
	else:
		out["edition"] = "Unknown"
	return out


def fetch_environment(config: BridgeConfig) -> dict:
	payload = build_environment_probe_xml()
	response = post_tally_xml_with_retries(config, payload, attempts=2)
	return parse_environment_xml(response)


# ---------------------------------------------------------------------------
# tally.ini discovery (for finding .1800 data folders without user input)
# ---------------------------------------------------------------------------
def tally_ini_locations() -> list[Path]:
	# TallyPrime drops tally.ini in a few standard places. Order matters —
	# Public is the modern default, the others are legacy / per-user overrides.
	candidates: list[Path] = []
	for env_name in ("PUBLIC", "ALLUSERSPROFILE"):
		base = os.environ.get(env_name)
		if base:
			candidates.append(Path(base) / "TallyPrime" / "tally.ini")
			candidates.append(Path(base) / "Tally.ERP9" / "tally.ini")
	for env_name in ("LOCALAPPDATA", "APPDATA"):
		base = os.environ.get(env_name)
		if base:
			candidates.append(Path(base) / "TallyPrime" / "tally.ini")
	for drive in ("C:", "D:"):
		candidates.extend([
			Path(f"{drive}\\Program Files\\TallyPrime\\tally.ini"),
			Path(f"{drive}\\Program Files (x86)\\TallyPrime\\tally.ini"),
		])
	return candidates


def parse_tally_ini_data_paths(ini_path: Path) -> list[Path]:
	# Lines look like `Data Path = C:\path\to\data` (case and whitespace
	# vary). Multiple Data entries are kept. Tally writes the file as cp1252
	# in older builds and utf-8 in newer ones, so try utf-8 first.
	if not ini_path.exists() or not ini_path.is_file():
		return []
	text: str | None = None
	for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
		try:
			text = ini_path.read_text(encoding=encoding)
			break
		except (UnicodeDecodeError, OSError):
			continue
	if text is None:
		return []
	# TallyPrime writes `Data = ...`; older docs / variants use `Data Path = ...`. Accept both.
	pattern = re.compile(r"^\s*data(?:\s+path)?\s*=\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
	paths: list[Path] = []
	for match in pattern.finditer(text):
		raw = match.group(1).strip().strip('"').strip("'")
		if raw:
			paths.append(Path(raw))
	return paths


_UNC_HOST_CACHE: dict[str, bool] = {}


def _unc_host_reachable(path_str: str, timeout: float = 0.8) -> bool:
	"""For UNC paths (\\\\server\\share\\...), confirm the host accepts SMB
	before letting Path.resolve()/exists() touch it. A stale UNC entry in
	tally.ini (e.g. an ex-fileserver) makes Path.resolve block ~2.7s while
	Windows times out the SMB session setup.
	"""
	if not path_str.startswith("\\\\"):
		return True
	parts = path_str[2:].split("\\", 1)
	host = parts[0] if parts else ""
	if not host:
		return False
	cached = _UNC_HOST_CACHE.get(host)
	if cached is not None:
		return cached
	reachable = False
	try:
		with socket.create_connection((host, 445), timeout=timeout):
			reachable = True
	except (OSError, socket.timeout):
		reachable = False
	_UNC_HOST_CACHE[host] = reachable
	return reachable


def discover_tally_data_roots(config: BridgeConfig) -> list[Path]:
	candidates: list[Path] = []
	configured = getattr(config, "tally_data_dir", "") or ""
	if configured:
		candidates.append(Path(configured))
	for env_name in ("TALLY_DATA_DIR", "TALLYPRIME_DATA"):
		if os.environ.get(env_name):
			candidates.append(Path(os.environ[env_name]))
	for ini_path in tally_ini_locations():
		candidates.extend(parse_tally_ini_data_paths(ini_path))
	local = os.environ.get("LOCALAPPDATA")
	roaming = os.environ.get("APPDATA")
	userprofile = os.environ.get("USERPROFILE")
	if local:
		candidates.extend([
			Path(local) / "TallyPrime" / "Data",
			Path(local) / "Tally" / "Data",
		])
	if roaming:
		candidates.extend([
			Path(roaming) / "TallyPrime" / "Data",
			Path(roaming) / "Tally" / "Data",
		])
	if userprofile:
		candidates.extend([
			Path(userprofile) / "Documents" / "TallyPrime" / "Data",
			Path(userprofile) / "Documents" / "Tally" / "Data",
		])

	seen = set()
	roots: list[Path] = []
	for candidate in candidates:
		if not _unc_host_reachable(str(candidate)):
			continue
		try:
			resolved = candidate.expanduser().resolve()
		except Exception:
			continue
		if resolved in seen or not resolved.exists() or not resolved.is_dir():
			continue
		seen.add(resolved)
		roots.append(resolved)
	return roots


# ---------------------------------------------------------------------------
# Quick company-name extraction from .1800 (for discovery without Tally HTTP)
# ---------------------------------------------------------------------------
# TallyPrime stores text as UTF-16-LE inside .1800 files. Pattern matches
# runs of (printable ASCII char, 0x00) — display names, addresses, GSTINs.
_COMPANY_NAME_UTF16_RE = re.compile(rb"(?:[\x20-\x7e]\x00){8,}")


def _inline_company_name_from_1800(company_file: Path) -> str:
	"""Fallback name extractor: scan the first 64 KiB of Company.1800 for the
	first UTF-16-LE string that looks like a company name. Used when
	tally1800.probe is unavailable (frozen exe missing imports).
	"""
	try:
		with company_file.open("rb") as fh:
			head = fh.read(65536)
	except OSError:
		return ""
	for match in _COMPANY_NAME_UTF16_RE.finditer(head):
		text = match.group(0).decode("utf-16-le", errors="ignore").strip()
		# Tally precedes UTF-16 strings with a length byte that decodes as a
		# stray leading char (e.g. 'X' for an 88-byte / 44-char string).
		text = text[1:].strip()
		if len(text) < 4:
			continue
		alpha = sum(1 for c in text if c.isalpha())
		if alpha < 3:
			continue
		# Reject obvious non-name strings: file paths, emails, all-digit IDs.
		lowered = text.lower()
		if "\\" in text or "/" in text or "@" in text or "tallyprime" in lowered:
			continue
		return text
	return ""


def quick_company_name(company_dir: Path) -> str:
	company_file = company_dir / "Company.1800"
	if not company_file.exists():
		return company_dir.name
	try:
		from tally1800.probe import extract_tagged_field_strings
		hits = extract_tagged_field_strings(company_file, min_chars=1)
		for hit in hits:
			text = (hit.text or "").strip()
			if text:
				return text
	except Exception:
		# tally1800 not bundled or probe failed — fall back to inline scan.
		pass
	inline = _inline_company_name_from_1800(company_file)
	return inline or company_dir.name


def discover_1800_companies(config: BridgeConfig) -> list[dict]:
	"""Walk the configured Tally data root(s) and report each `.1800` company
	folder. Cheap — pure file system, no Tally HTTP. Used by the migration
	UI to populate the company list before the user picks one.
	"""
	companies: list[dict] = []
	for root in discover_tally_data_roots(config):
		for child in sorted(root.iterdir()):
			if not child.is_dir():
				continue
			files = list(child.glob("*.1800"))
			if not files:
				continue
			companies.append({
				"company_id": child.name,
				"company_name": quick_company_name(child),
				"folder": str(child),
				"root": str(root),
				"has_company": (child / "Company.1800").exists(),
				"has_manager": (child / "Manager.1800").exists(),
				"has_tranmgr": (child / "TranMgr.1800").exists(),
			})
	return companies


# ---------------------------------------------------------------------------
# Command dispatch — slim. Each command type maps to one path module.
# ---------------------------------------------------------------------------
def handle_bridge_command(
	config: BridgeConfig, connection_id: str, bridge_token: str, command: dict | None,
) -> None:
	if not command:
		return
	if isinstance(command, str):
		try:
			command = json.loads(command)
		except json.JSONDecodeError:
			return
	command_id = command.get("id") or ""
	command_type = command.get("type") or ""
	try:
		# .1800 binary decode path (alias `extract_1800_company` for backward
		# compat with the previous bridge build the server may still queue).
		if command_type in ("decode_1800", "extract_1800_company"):
			from tally_decode_path import run as run_decode
			run_decode(config, connection_id, bridge_token, command_id, command)
			return
		# TDL/XML HTTP firehose path.
		if command_type in ("http_extract", "extract_http"):
			from tally_http_path import run as run_http
			run_http(config, connection_id, bridge_token, command_id, command)
			return
		if command_type == "discover_1800_companies":
			log("Discovering local Tally .1800 companies...")
			companies = discover_1800_companies(config)
			ack_bridge_command(
				config, connection_id, bridge_token, command_id, "Success",
				{"companies": companies, "count": len(companies)},
			)
			log(f"Discovered {len(companies)} local Tally companies.")
			return
		if command_type == "set_data_dir":
			new_dir = str(command.get("data_dir") or "").strip()
			persist_data_dir(config, new_dir)
			roots = [str(p) for p in discover_tally_data_roots(config)]
			log(f"Updated tally_data_dir to {config.tally_data_dir!r} ({len(roots)} root(s) now visible)")
			# Saving a folder path is always followed by wanting to see what's in
			# it — re-discover immediately so the UI doesn't make the user click
			# "Scan companies" as a separate step.
			companies = discover_1800_companies(config)
			ack_bridge_command(
				config, connection_id, bridge_token, command_id, "Success",
				{
					"configured_data_dir": config.tally_data_dir,
					"discovered_roots": roots,
					"companies": companies,
					"count": len(companies),
				},
			)
			return
		if command_type == "inspect_tally":
			info = fetch_environment(config)
			ack_bridge_command(
				config, connection_id, bridge_token, command_id, "Success",
				{"environment": info},
			)
			return
		ack_bridge_command(
			config, connection_id, bridge_token, command_id, "Error",
			error=f"Unknown command type: {command_type}",
		)
	except Exception as exc:
		log(f"Command {command_type or command_id} failed: {exc}")
		if command_id:
			try:
				ack_bridge_command(
					config, connection_id, bridge_token, command_id, "Error", error=str(exc),
				)
			except Exception:
				pass


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------
def run_once(config: BridgeConfig) -> dict:
	log("Claiming pairing code with Sena...")
	claim = claim_pairing(config)
	connection_id = claim["connection_id"]
	bridge_token = claim["bridge_token"]
	if not config.connection_id:
		config.connection_id = connection_id
		save_config_value(config.config_path, "connection_id", connection_id)
	if not config.bridge_token:
		config.bridge_token = bridge_token
		save_config_value(config.config_path, "bridge_token", bridge_token)
	log(f"Paired with Sena connection {connection_id}.")

	# Heartbeat first (without Tally) so any pending command — set_data_dir,
	# discover_1800_companies — is picked up immediately. Probing Tally would
	# block this for up to `timeout` seconds if Tally is unresponsive.
	log("Sending initial bridge heartbeat...")
	heartbeat_result = heartbeat(
		config,
		connection_id,
		bridge_token,
		status="Active",
		capabilities_json=json.dumps(build_capabilities(config)),
	)
	handle_bridge_command(config, connection_id, bridge_token, heartbeat_result.get("command"))

	# Idle-poll for user-triggered commands. 50s of polling at 3s intervals
	# matches the outer 60s sleep in main() for a continuous loop.
	log("Idle-polling for commands (50s)...")
	deadline = time.time() + 50
	while time.time() < deadline:
		pending = poll_command(config, connection_id, bridge_token)
		if pending:
			handle_bridge_command(config, connection_id, bridge_token, pending)
		time.sleep(3)

	log("Marking connection active...")
	heartbeat(config, connection_id, bridge_token, status="Active")
	log("Cycle finished.")
	return {"connection_id": connection_id}


# ---------------------------------------------------------------------------
# Self-test (no network)
# ---------------------------------------------------------------------------
def self_test() -> int:
	# License probe envelope shape
	payload = build_environment_probe_xml()
	assert "TYPE>DATA" in payload, "envelope missing DATA type"
	assert "SenaEnvProbe" in payload, "envelope missing report id"
	assert "$$LicenseInfo:SerialNumber" in payload, "envelope missing serial accessor"

	# License probe parser — Silver
	silver = ('<ENVELOPE><SERIAL>765900</SERIAL><EDU>No</EDU>'
	          '<SILVER>Yes</SILVER><GOLD>No</GOLD></ENVELOPE>')
	silver_info = parse_environment_xml(silver)
	assert silver_info.get("serial") == "765900"
	assert silver_info.get("edition") == "Silver"

	# Edu dominates Silver/Gold flags (educational mode quirk)
	edu = '<ENVELOPE><EDU>Yes</EDU><SILVER>No</SILVER><GOLD>Yes</GOLD></ENVELOPE>'
	edu_info = parse_environment_xml(edu)
	assert edu_info.get("edition") == "Educational"

	# Collection envelope shape (used by http_path)
	col = build_collection_xml("Ledgers", "Ledger", ["Name"])
	assert "TYPE>COLLECTION" in col
	assert "<NATIVEMETHOD>Name</NATIVEMETHOD>" in col

	# Output path resolution
	import tempfile
	with tempfile.TemporaryDirectory() as tmp:
		os.environ["SENA_BRIDGE_DATA_ROOT"] = tmp
		try:
			p = extract_output_path("Demo Co", "abc123", "decode")
			assert str(p).startswith(tmp), f"{p} not under {tmp}"
			assert p.name == "decode.sqlite"
		finally:
			del os.environ["SENA_BRIDGE_DATA_ROOT"]

	# clean_xml strips invalid control entities
	assert clean_xml("&#0;ok") == "ok"
	assert clean_xml("a\x00b") == "ab"

	print(json.dumps({"ok": True, "tests": 6}))
	return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str]) -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Sena Tally Bridge for Migration")
	parser.add_argument("--config", help="Path to bridge-config.json — overrides individual flags")
	parser.add_argument("--log-file", default="", help="Append all bridge output to this file (auto-created)")
	parser.add_argument("--server", help="Sena/Frappe server base URL, for example http://localhost:8001")
	parser.add_argument("--pairing-code", help="Pairing code displayed in Migration")
	parser.add_argument("--tally-host", default="localhost")
	parser.add_argument("--tally-port", default=9000, type=int)
	parser.add_argument("--tally-data-dir", default="", help="TallyPrime company Data directory containing .1800 files")
	parser.add_argument("--bridge-id", default="")
	parser.add_argument("--timeout", default=120, type=int)
	parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
	parser.add_argument("--self-test", action="store_true", help="Run parser/envelope self-test without network")
	parser.add_argument("--inspect-tally", action="store_true", help="Probe license + product info from a running Tally and print as JSON; no Sena server needed")
	parser.add_argument("--version", action="version", version=f"sena-tally-bridge {__version__}")
	return parser.parse_args(argv)


def _load_config_file(path: str) -> dict:
	with open(path, "r", encoding="utf-8") as fh:
		return json.load(fh)


def main(argv: list[str] | None = None) -> int:
	args = parse_args(argv or sys.argv[1:])

	# Config file beats individual flags. CLI flags fill in anything the JSON
	# omits, so existing PowerShell installs that pass --server etc. still work.
	cfg = _load_config_file(args.config) if args.config else {}

	# Wire stdout/stderr to the requested log file. The frozen-exe redirect
	# at module import sent them to NUL so import-time prints don't crash;
	# this swap moves runtime output to a durable location. Line-buffered
	# (buffering=1) so tailing the file shows progress live.
	log_path = (cfg.get("log_file") or args.log_file or "").strip()
	if log_path:
		os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
		fh = open(log_path, "a", encoding="utf-8", buffering=1)
		sys.stdout = fh
		sys.stderr = fh

	if args.self_test:
		return self_test()

	if args.inspect_tally:
		probe_config = BridgeConfig(
			server="",
			pairing_code="",
			tally_host=cfg.get("tally_host") or args.tally_host,
			tally_port=int(cfg.get("tally_port") or args.tally_port),
			timeout=int(cfg.get("timeout") or args.timeout),
		)
		log(f"Probing Tally at {probe_config.tally_url} ...")
		info = fetch_environment(probe_config)
		print(json.dumps(info, indent=2, ensure_ascii=False))
		return 0

	server = cfg.get("server") or args.server
	pairing_code = cfg.get("pairing_code") or args.pairing_code
	if not server or not pairing_code:
		raise SystemExit("server + pairing_code are required (via --config or --server/--pairing-code)")

	config = BridgeConfig(
		server=server,
		pairing_code=pairing_code,
		tally_host=cfg.get("tally_host") or args.tally_host,
		tally_port=int(cfg.get("tally_port") or args.tally_port),
		tally_data_dir=cfg.get("tally_data_dir") or args.tally_data_dir,
		bridge_id=cfg.get("bridge_id") or args.bridge_id,
		connection_id=cfg.get("connection_id") or "",
		bridge_token=cfg.get("bridge_token") or "",
		config_path=args.config or "",
		timeout=int(cfg.get("timeout") or args.timeout),
		once=args.once,
	)

	while True:
		result = run_once(config)
		print(json.dumps({"ok": True, **result}))
		if config.once:
			return 0
		time.sleep(60)


if __name__ == "__main__":
	raise SystemExit(main())

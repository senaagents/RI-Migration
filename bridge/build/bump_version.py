"""Patch the bridge version into all the places that need it.

Called by the release workflow with the tag-derived version (e.g. `0.1.2`):

    python build/bump_version.py 0.1.2

Updates:
  - bridge/sena_tally_bridge.py  (__version__)
  - bridge/build/version_info.txt  (Windows resource — both filevers and string fields)
  - bridge/build/installer.iss  (AppVersion + OutputBaseFilename)

Idempotent. Safe to run multiple times. Fails loud if any file doesn't
contain a substitutable token — bumping silently while one of the files
keeps the old version is the worst outcome.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"


def _replace_or_die(path: Path, pattern: str, replacement: str) -> None:
	text = path.read_text(encoding="utf-8")
	new, n = re.subn(pattern, replacement, text)
	if n == 0:
		raise SystemExit(f"bump_version: no match for /{pattern}/ in {path}")
	path.write_text(new, encoding="utf-8")
	print(f"  patched {n}× in {path.relative_to(ROOT.parent)}")


def main(version: str) -> int:
	if not re.fullmatch(r"\d+\.\d+\.\d+", version):
		raise SystemExit(f"bump_version: expected X.Y.Z, got {version!r}")

	major, minor, patch = (int(p) for p in version.split("."))
	four = f"({major}, {minor}, {patch}, 0)"

	# 1. bridge/sena_tally_bridge.py
	_replace_or_die(
		ROOT / "sena_tally_bridge.py",
		r'__version__ = "[\d.]+"',
		f'__version__ = "{version}"',
	)

	# 2. bridge/build/version_info.txt — both four-tuples and the strings
	_replace_or_die(
		BUILD / "version_info.txt",
		r"filevers=\([^)]+\)",
		f"filevers={four}",
	)
	_replace_or_die(
		BUILD / "version_info.txt",
		r"prodvers=\([^)]+\)",
		f"prodvers={four}",
	)
	_replace_or_die(
		BUILD / "version_info.txt",
		r"StringStruct\('FileVersion', '[\d.]+'\)",
		f"StringStruct('FileVersion', '{version}')",
	)
	_replace_or_die(
		BUILD / "version_info.txt",
		r"StringStruct\('ProductVersion', '[\d.]+'\)",
		f"StringStruct('ProductVersion', '{version}')",
	)

	# 3. bridge/build/installer.iss — Inno Setup uses whitespace alignment
	# in #define lines, so the regex has to be space-tolerant.
	_replace_or_die(
		BUILD / "installer.iss",
		r'#define\s+MyAppVersion\s+"[\d.]+"',
		f'#define MyAppVersion     "{version}"',
	)

	print(f"bump_version: bridge -> {version}")
	return 0


if __name__ == "__main__":
	if len(sys.argv) != 2:
		raise SystemExit("usage: bump_version.py <X.Y.Z>")
	raise SystemExit(main(sys.argv[1]))

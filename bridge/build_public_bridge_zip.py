#!/usr/bin/env python3
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT.parent
PUBLIC_BRIDGE = APP_ROOT / "migration" / "public" / "bridge"
DECODER_SRC = APP_ROOT / "tally_1800" / "codex" / "tally1800"

BRIDGE_FILES = [
    "README.md",
    "install_bridge_windows.ps1",
    "sena_tally_bridge.py",
    "tally_decode_path.py",
    "tally_http_path.py",
    "tally_http_pool.py",
]


def copy_public_files() -> None:
    PUBLIC_BRIDGE.mkdir(parents=True, exist_ok=True)
    for name in BRIDGE_FILES:
        source = APP_ROOT / "TALLY_EXTRACTION_README.md" if name == "README.md" else ROOT / name
        target = PUBLIC_BRIDGE / name
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copy2(source, target)

    decoder_target = PUBLIC_BRIDGE / "tally1800"
    if decoder_target.exists():
        shutil.rmtree(decoder_target)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(DECODER_SRC, decoder_target, ignore=ignore)


def iter_zip_files() -> list[Path]:
    files: list[Path] = []
    for name in BRIDGE_FILES:
        files.append(PUBLIC_BRIDGE / name)
    files.extend(
        path
        for path in sorted((PUBLIC_BRIDGE / "tally1800").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    )
    return files


def build_zip() -> Path:
    zip_path = PUBLIC_BRIDGE / "sena_tally_bridge.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in iter_zip_files():
            zf.write(path, path.relative_to(PUBLIC_BRIDGE).as_posix())
    return zip_path


def main() -> int:
    copy_public_files()
    zip_path = build_zip()
    print(zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

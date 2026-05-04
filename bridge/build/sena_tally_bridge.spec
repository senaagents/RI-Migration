# PyInstaller spec for Sena Tally Bridge.
#
# Built by .github/workflows/bridge-release.yml on a windows-latest runner.
# Output: dist/SenaTallyBridge.exe (one-file, no console attached so the
# scheduled task runs invisibly — logs go to %LOCALAPPDATA%\SenaTallyBridge\logs).
#
# Build locally on Windows for ad-hoc testing:
#   pip install pyinstaller
#   pyinstaller bridge/build/sena_tally_bridge.spec
#
# Do NOT add code-signing here. Signing is bolted onto the post-build step
# in CI when the cert + signtool become available.

import os
import sys

# Make tally1800 importable at spec-eval time so collect_submodules() can find
# every submodule. Past releases enumerated hidden imports by hand and missed
# transitive deps (typed_fields, sqlite_export, ...), which broke file-based
# extraction silently — the package failed to import in the bundled exe and
# run_1800_extract raised "No module named 'tally1800'" at runtime.
SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
DECODER_PATH = os.path.normpath(os.path.join(SPEC_DIR, '..', '..', 'tally_1800', 'codex'))
if DECODER_PATH not in sys.path:
    sys.path.insert(0, DECODER_PATH)

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['../sena_tally_bridge.py'],
    pathex=[DECODER_PATH],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules('tally1800'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SenaTallyBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # scheduled task runs headless; no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
)

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

block_cipher = None

a = Analysis(
    ['../sena_tally_bridge.py'],
    pathex=['../../tally_1800/codex'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tally1800',
        'tally1800.decoded_export',
        'tally1800.probe',
        'tally1800.model',
        'tally1800.ledger_decode',
        'tally1800.inventory_decode',
    ],
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

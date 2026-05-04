# Sena Tally Bridge — build & release

This directory holds everything needed to turn the Python bridge in
`bridge/sena_tally_bridge.py` into a Windows installer (`SenaTallyBridge-Setup-X.Y.Z.exe`)
and publish it as a GitHub Release that the Sena migration UI links to.

## File map

| File | Purpose |
|------|---------|
| `sena_tally_bridge.spec` | PyInstaller config — produces `dist/SenaTallyBridge.exe` (one-file, no console window). |
| `version_info.txt` | Windows version resource embedded into the exe. CI rewrites this on every release. |
| `installer.iss` | Inno Setup script — wraps the exe in a wizard installer. |
| `register_task.ps1` | Run by the installer's `[Run]` step. Registers a Scheduled Task that launches the bridge at user logon. |
| `unregister_task.ps1` | Run by the uninstaller. Removes the Scheduled Task and kills any in-flight process. |
| `bump_version.py` | Rewrites the version in all three of `sena_tally_bridge.py`, `version_info.txt`, and `installer.iss`. CI runs this from the tag. |

## How to ship a new version

1. Make your code changes on a feature branch.
2. Once merged into the migration repo's default branch, tag the release:
   ```bash
   git tag bridge-v0.1.1
   git push origin bridge-v0.1.1
   ```
3. The `bridge-release.yml` workflow on `windows-latest` will:
   - Run `bump_version.py 0.1.1` to update version strings.
   - Run PyInstaller against the spec → `dist/SenaTallyBridge.exe`.
   - Install Inno Setup (Chocolatey) and run `iscc` → `dist-installer/SenaTallyBridge-Setup-0.1.1.exe`.
   - Create a GitHub Release tagged `bridge-v0.1.1` and attach the installer as the only asset.
4. The Sena backend's `migration.core.api.get_bridge_release` reads the
   latest release through the GitHub API (cached 5 min in Redis). The
   frontend `BridgeConnect` view picks up the new download URL and shows
   an "Update available" banner to anyone whose installed bridge is older.

## Local dry-run on Windows

If you need to verify the installer wizard or scheduled-task registration
before tagging:

```powershell
pip install "pyinstaller>=6.5,<7"
python bridge\build\bump_version.py 0.1.1-dev
pyinstaller bridge\build\sena_tally_bridge.spec --noconfirm
choco install innosetup -y
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" bridge\build\installer.iss
```

Output: `dist-installer\SenaTallyBridge-Setup-0.1.1-dev.exe`. Double-click,
walk through the wizard, confirm the bridge runs (Task Scheduler → look for
`SenaTallyBridge` task; check `%LOCALAPPDATA%\SenaTallyBridge\logs\`).

## Code signing

Intentionally not configured. The installer is unsigned today. End users
will see a Windows SmartScreen warning ("Windows protected your PC")
on first install — they have to click *More info* → *Run anyway*. This
is the pragmatic v0.1 trade-off; an EV code-signing cert costs ~$500/yr
and removes that warning.

To turn signing on later:

1. Buy an EV (Extended Validation) Authenticode cert. Hardware token
   shipped by the CA — keep it offline.
2. Export to a PFX, upload base64 to repo secrets `CODE_SIGN_CERT_BASE64`
   and `CODE_SIGN_CERT_PASSWORD`.
3. Uncomment the `Sign installer` step in `bridge-release.yml`.
4. Uncomment the `SignTool=` directive at the top of `installer.iss`.

## Why a Scheduled Task and not a Windows Service

Tally Prime's HTTP/XML server binds to the user's interactive session.
A Service running as `LocalSystem` cannot reach `localhost:9000` without
extra config the user has to maintain. A Scheduled Task at user logon
runs in the same session as Tally — no firewall games, no per-user fiddling.

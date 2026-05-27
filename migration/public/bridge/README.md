# Tally Extraction Handoff

This folder contains two usable extraction paths for Tally/TallyPrime data:

- `.1800 decode`: reads a local Tally company data folder and writes decoded SQLite.
- `HTTP/XML extract`: talks to a running TallyPrime HTTP server and writes XML batches to SQLite.

Use `.1800 decode` first when you have the Tally data files. Use HTTP/XML when Tally is running and you need live XML truth, broad voucher XML, or repair data for decoder gaps.

## Important Paths

From repo root:

```text
bench/apps/migration/tally_1800/codex/tally1800/decoded_export.py
bench/apps/migration/bridge/sena_tally_bridge.py
bench/apps/migration/bridge/tally_decode_path.py
bench/apps/migration/bridge/tally_http_path.py
bench/apps/migration/bridge/tally_http_pool.py
bench/apps/migration/migration/public/bridge/sena_tally_bridge.zip
```

Sample local data is under:

```text
/home/arvis/SenaAgents/tally temp/070525
/home/arvis/SenaAgents/tally temp/100005
```

## Option 1: Run `.1800` Decode Directly

This does not require Tally to be open. It only needs the company folder containing files such as `Company.1800`, `Manager.1800`, `TranMgr.1800`, `VchStatus.1800`, and `LinkMgr.1800`.

Linux/macOS:

```bash
cd /home/arvis/SenaAgents/bench/apps/migration/tally_1800/codex
python3 -m tally1800 decode-sqlite "/path/to/tally/company-folder" "/path/to/out/decoded.sqlite3"
```

Windows PowerShell from an extracted handoff zip:

```powershell
cd C:\path\to\sena_tally_bridge
python -m tally1800 decode-sqlite "C:\path\to\Tally\Data\070525" "C:\path\to\decoded.sqlite3"
```

Expected output is a SQLite file. The production-facing views are:

```text
production_voucher_headers_1800
production_ledger_entries_1800
production_inventory_entries_1800
production_review_queue_1800
production_xml_repair_queue_1800
```

The underlying audit/repair tables are:

```text
voucher_decode_audit
xml_repair_queue
```

Do not treat `*_candidates` tables as final business rows. They are forensic evidence for improving or repairing the decode.

## Option 2: Run the Bridge Against Sena

Use this when the Sena migration UI will queue commands such as `decode_1800`, `discover_1800_companies`, or `http_extract`.

1. Extract `sena_tally_bridge.zip` on the Windows machine that can access Tally.
2. Run PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_bridge_windows.ps1 `
  -Server "https://your-sena-host" `
  -PairingCode "PAIRING-CODE-FROM-SENA" `
  -TallyHost "localhost" `
  -TallyPort 9000
```

The installer copies the bridge to:

```text
%LOCALAPPDATA%\SenaTallyBridge
```

It also writes:

```text
%LOCALAPPDATA%\SenaTallyBridge\bridge-config.json
%LOCALAPPDATA%\SenaTallyBridge\run-bridge.ps1
```

Run it manually with:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\SenaTallyBridge\run-bridge.ps1"
```

The bridge writes extract SQLite files under:

```text
%LOCALAPPDATA%\SenaTallyBridge\extracts\<company_id>\<run_id>\decode_1800.sqlite
%LOCALAPPDATA%\SenaTallyBridge\extracts\<company_id>\<run_id>\http_extract.sqlite
```

## Bridge Commands

`discover_1800_companies`

Finds Tally company folders under the configured data directory and common Tally data locations. It returns company IDs, names, and whether key `.1800` files exist.

`decode_1800`

Requires:

```json
{
  "type": "decode_1800",
  "company_id": "070525"
}
```

It reads that local company folder and writes `decode_1800.sqlite`.

`http_extract`

Requires TallyPrime running with HTTP enabled.

```json
{
  "type": "http_extract",
  "company_name": "Avinash Industries - Chennai Unit - 2025-26",
  "company_id": "070525",
  "from_date": "2025-04-01",
  "to_date": "2026-03-31",
  "tally_ports": [9000]
}
```

For parallel extraction, run multiple TallyPrime instances on separate ports and pass them:

```json
{
  "tally_ports": [9000, 9001, 9002]
}
```

The HTTP path stores masters in `masters`, voucher XML batches as gzipped blobs in `voucher_batches`, and failed pulls in `failed_batches`.

## Tally HTTP Requirements

TallyPrime must have its HTTP server enabled. In `tally.ini`, use the relevant setting for your Tally version, commonly:

```text
Server=Yes
Port=9000
```

Open the target company in Tally before running HTTP/XML extraction. If the date period in Tally excludes old vouchers, change the period in Tally first.

Quick probe:

```powershell
python "$env:LOCALAPPDATA\SenaTallyBridge\sena_tally_bridge.py" --config "$env:LOCALAPPDATA\SenaTallyBridge\bridge-config.json" --inspect-tally
```

## Known Limitations

- Vaulted/encrypted `.1800` files are not handled without the required Tally access/password path.
- The direct `.1800` decoder is conservative. It emits final views plus review/repair queues; XML repair is still needed for hard gaps.
- HTTP/XML extraction depends on the company being open in Tally and the requested period being visible.
- The bridge writes local SQLite files and acknowledges paths/results. Uploading those SQLite files into another system is a separate step.

## Rebuild the Public Zip

After changing bridge files, rebuild the static handoff zip from repo root:

```bash
cd /home/arvis/SenaAgents/bench/apps/migration
python3 bridge/build_public_bridge_zip.py
```

Then give the recipient:

```text
bench/apps/migration/migration/public/bridge/sena_tally_bridge.zip
bench/apps/migration/TALLY_EXTRACTION_README.md
```

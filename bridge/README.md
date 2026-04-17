# Sena Tally Bridge

Phase 1 bridge for Talk to Your Data.

This is a dependency-free Python script intended to run on the Windows machine
where TallyPrime is open. It connects to Tally over the local HTTP/XML server,
claims the pairing code created in the Migration agent app, and sends discovered
objects into the Talk to Your Data middle store.

## Run

```bash
python sena_tally_bridge.py \
  --server http://localhost:8001 \
  --pairing-code ABCD1234 \
  --tally-host localhost \
  --tally-port 9000 \
  --once
```

## Windows Self-Serve Install

Open PowerShell in this folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_bridge_windows.ps1
```

The script asks for the Sena server URL and the pairing code shown in Talk to
Your Data, copies the bridge to `%LOCALAPPDATA%\SenaTallyBridge`, saves a local
config file, and prints the command to start the bridge again.

## What It Syncs First

- Company name heartbeat
- Ledgers
- Groups
- Stock Items
- Raw XML payload snapshots for the synced objects
- Per-stream checkpoints using the highest seen Tally alter id

## Self-Test

```bash
python sena_tally_bridge.py --self-test
```

The self-test validates XML envelope generation and parser/cursor behavior
without needing Tally or a Sena server.

## Bridge E2E Test

```bash
python test_bridge_e2e.py
```

The bridge e2e test runs the real bridge orchestration against in-memory fake
Sena and Tally endpoints. It verifies pairing claim, heartbeat transitions,
stream checkpointing, object parsing, and raw payload ingest shape without
needing MariaDB, Frappe, or Tally.

## Packaging Later

For self-serve users, package this file as a Windows executable with PyInstaller
and ship a signed installer that can create a Start Menu shortcut and optional
background task. The bridge should remain local-first: it only talks to Tally on
`localhost:9000` and uses outbound HTTPS to Sena.

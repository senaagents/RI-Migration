# Live Tally Port Map

Verified on 2026-04-28 from macOS using the safe company-list collection.

## Current Mapping

| Host | Port | Loaded company | Company number | Use |
| --- | ---: | --- | --- | --- |
| `10.211.55.3` | `9000` | `Test Synthetic` | `100000` | Synthetic writes and before/after `.1800` mapping discovery |
| `10.211.55.3` | `9001` | `Avinash Industries - Chennai Unit - 2025-26` | `70525` | Targeted Avinash XML truth checks only |

## Rules

- Do not send Avinash company XML to port `9000`.
- Do not send Test Synthetic writes to port `9001`.
- Always verify company visibility before any write or company-specific XML.
- Keep Tally HTTP calls sequential. Do not parallelize requests across either port.
- Prefer narrow Object exports or lightweight collections for Avinash. Avoid broad full-detail/report/computed inventory XML.
- Synthetic writes must still use `scripts/synthetic_tally_lab.py` with the company guard and before/after snapshots.

## Safe Verification Command

```bash
PYTHONPATH=. python3 - <<'PY'
from scripts.synthetic_tally_lab import list_companies
for port in (9000, 9001):
    print(port, list_companies("10.211.55.3", port, 8))
PY
```

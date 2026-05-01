# Hybrid 1800 Bridge

This repo is the XML/Tally side of the hybrid `.1800 -> SQLite` loop.

Companion repo:

```text
/Users/aakashchid/workshop/sena/tallydatacrack-codex
```

Main bridge spec:

```text
/Users/aakashchid/workshop/sena/tallydatacrack-codex/HYBRID-1800-XML-AI-LOOP.md
```

## Responsibility Split

`tallydatacrack-codex`:

```text
decode .1800 directly
mark final/review/repair/audit rows
produce xml_repair_queue
learn byte parser rules from each XML-backed gap
```

`tally-db-pipeline`:

```text
verify live Tally health
run one HTTP request at a time
fetch narrow voucher details by MASTERID/GUID
parse XML into the production schema
preserve raw payloads and unknown sections
```

## Safe Repair Command

The direct decoder can generate a repair plan with:

```bash
cd /Users/aakashchid/workshop/sena/tallydatacrack-codex
PYTHONPATH=. python3 scripts/hybrid_1800_pipeline_loop.py plan \
  --decoded-db out/070525_decoded_laneA_sales_logical.sqlite3 \
  --company "Avinash Industries - Chennai Unit - 2025-26" \
  --host 10.211.55.3 \
  --port 9001 \
  --out-dir out/hybrid-repair-070525
```

The generated commands call this repo's proven narrow export path:

```bash
tally-db-pipeline sync-voucher-detail-master-id \
  --company "<exact open company>" \
  --master-id <voucher MASTERID>
```

## Policy

- Prefer `MASTERID` object export for repair.
- Avoid broad Day Book/full-range XML for gaps discovered by the direct parser.
- Keep Tally requests sequential.
- Validate the open company before repair.
- Store raw XML payloads; they are part of the learning/debug trail.
- Do not treat XML repair as a permanent defeat. Feed repaired gaps back into
  `tallydatacrack-codex` as parser-learning handoffs.

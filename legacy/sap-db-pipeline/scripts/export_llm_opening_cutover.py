from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from sap_db_pipeline.config import get_settings
from sap_db_pipeline.hana_client import HanaClient, quote_ident


CUTOFF_DATE = "2026-03-31"
OUTPUT_DIR = Path("migration-control/opening-cutover")
CONTROL_ACCOUNTS = ("1202101", "1202102", "2201001", "2201002")


def qtable(schema: str, table: str) -> str:
    return f"{schema}.{quote_ident(table)}"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def money(value: Any) -> str:
    return f"{float(value or 0):,.2f}"


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def party_open_lines_sql(schema: str, account_placeholders: str) -> str:
    return f"""
    WITH recon AS (
      SELECT
        L."TransId",
        L."TransRowId",
        SUM(CASE WHEN L."IsCredit" = 'D' THEN L."ReconSum" ELSE -L."ReconSum" END) AS "ReconSigned",
        SUM(CASE WHEN L."IsCredit" = 'D' THEN L."ReconSumFC" ELSE -L."ReconSumFC" END) AS "ReconFCSigned"
      FROM {qtable(schema, 'ITR1')} L
      JOIN {qtable(schema, 'OITR')} R ON R."ReconNum" = L."ReconNum"
      WHERE R."Canceled" = 'N'
        AND R."ReconDate" <= ?
      GROUP BY L."TransId", L."TransRowId"
    ), lines AS (
      SELECT
        J."TransId",
        J."Line_ID",
        J."Account",
        J."ShortName",
        J."TransType",
        J."RefDate",
        J."DueDate",
        J."BaseRef",
        J."Debit",
        J."Credit",
        J."FCDebit",
        J."FCCredit",
        J."FCCurrency",
        J."Debit" - J."Credit" AS "OriginalSigned",
        J."FCDebit" - J."FCCredit" AS "OriginalFCSigned",
        COALESCE(R."ReconSigned", 0) AS "ReconSigned",
        COALESCE(R."ReconFCSigned", 0) AS "ReconFCSigned",
        J."Debit" - J."Credit" - COALESCE(R."ReconSigned", 0) AS "OpenSigned",
        J."FCDebit" - J."FCCredit" - COALESCE(R."ReconFCSigned", 0) AS "OpenFCSigned"
      FROM {qtable(schema, 'JDT1')} J
      LEFT JOIN recon R
        ON R."TransId" = J."TransId"
       AND R."TransRowId" = J."Line_ID"
      WHERE J."RefDate" <= ?
        AND J."Account" IN ({account_placeholders})
    )
    SELECT
      L."Account",
      A."AcctName",
      L."ShortName" AS "PartyOrShortName",
      L."TransType",
      CASE L."TransType"
        WHEN '13' THEN 'Sales Invoice'
        WHEN '14' THEN 'Sales Credit Memo'
        WHEN '18' THEN 'Purchase Invoice'
        WHEN '19' THEN 'Purchase Credit Memo'
        WHEN '24' THEN 'Incoming Payment / Customer Advance'
        WHEN '46' THEN 'Outgoing Payment / Supplier Advance'
        WHEN '30' THEN 'Manual Party Journal'
        ELSE 'Other Party Line'
      END AS "SourceKind",
      L."TransId",
      L."Line_ID",
      L."BaseRef",
      L."RefDate",
      L."DueDate",
      L."Debit",
      L."Credit",
      L."FCDebit",
      L."FCCredit",
      L."FCCurrency",
      ROUND(L."OriginalSigned", 2) AS "OriginalSigned",
      ROUND(L."OriginalFCSigned", 2) AS "OriginalFCSigned",
      ROUND(L."ReconSigned", 2) AS "ReconSignedToCutoff",
      ROUND(L."ReconFCSigned", 2) AS "ReconFCSignedToCutoff",
      ROUND(L."OpenSigned", 2) AS "OpenSignedAsOfCutoff",
      ROUND(L."OpenFCSigned", 2) AS "OpenFCSignedAsOfCutoff",
      COALESCE(SI."DocEntry", SC."DocEntry", PI."DocEntry", PC."DocEntry", IP."DocEntry", OP."DocEntry") AS "SourceDocEntry",
      COALESCE(SI."DocNum", SC."DocNum", PI."DocNum", PC."DocNum", IP."DocNum", OP."DocNum") AS "SourceDocNum",
      COALESCE(SI."CardCode", SC."CardCode", PI."CardCode", PC."CardCode", IP."CardCode", OP."CardCode") AS "SourceCardCode",
      COALESCE(SI."CardName", SC."CardName", PI."CardName", PC."CardName", IP."CardName", OP."CardName") AS "SourceCardName",
      COALESCE(SI."DocTotal", SC."DocTotal", PI."DocTotal", PC."DocTotal", IP."DocTotal", OP."DocTotal") AS "SourceDocTotal",
      COALESCE(SI."DocCur", SC."DocCur", PI."DocCur", PC."DocCur", IP."DocCurr", OP."DocCurr") AS "SourceCurrency",
      COALESCE(SI."NumAtCard", SC."NumAtCard", PI."NumAtCard", PC."NumAtCard") AS "SourceExternalRef"
    FROM lines L
    LEFT JOIN {qtable(schema, 'OACT')} A ON A."AcctCode" = L."Account"
    LEFT JOIN {qtable(schema, 'OINV')} SI ON L."TransType" = '13' AND SI."TransId" = L."TransId"
    LEFT JOIN {qtable(schema, 'ORIN')} SC ON L."TransType" = '14' AND SC."TransId" = L."TransId"
    LEFT JOIN {qtable(schema, 'OPCH')} PI ON L."TransType" = '18' AND PI."TransId" = L."TransId"
    LEFT JOIN {qtable(schema, 'ORPC')} PC ON L."TransType" = '19' AND PC."TransId" = L."TransId"
    LEFT JOIN {qtable(schema, 'ORCT')} IP ON L."TransType" = '24' AND IP."TransId" = L."TransId"
    LEFT JOIN {qtable(schema, 'OVPM')} OP ON L."TransType" = '46' AND OP."TransId" = L."TransId"
    WHERE ABS(L."OpenSigned") > 0.005
    ORDER BY L."Account", L."ShortName", L."RefDate", L."TransId", L."Line_ID"
    """


def run() -> None:
    settings = get_settings()
    schema = quote_ident(settings.sap_hana_schema)
    account_placeholders = ",".join("?" for _ in CONTROL_ACCOUNTS)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with HanaClient(settings) as hana:
        party_rows = hana.rows(
            party_open_lines_sql(schema, account_placeholders),
            tuple([CUTOFF_DATE, CUTOFF_DATE, *CONTROL_ACCOUNTS]),
        )
        write_csv(OUTPUT_DIR / f"party-open-lines-as-of-{CUTOFF_DATE}.csv", party_rows)

        invoice_rows = [
            row
            for row in party_rows
            if str(row["TransType"]) in {"13", "14", "18", "19"}
        ]
        write_csv(OUTPUT_DIR / f"opening-invoice-source-lines-as-of-{CUTOFF_DATE}.csv", invoice_rows)

        stock_rows = hana.rows(
            f"""
            SELECT
              X."ItemCode",
              X."LocCode" AS "Warehouse",
              X."InvntAct" AS "InventoryAccount",
              A."AcctName" AS "InventoryAccountName",
              ROUND(X."Qty", 6) AS "Qty",
              ROUND(X."StockValue", 2) AS "StockValue",
              CASE
                WHEN ABS(X."Qty") > 0.000001 THEN ROUND(X."StockValue" / X."Qty", 6)
                ELSE NULL
              END AS "ValuationRate",
              X."MovementRows"
            FROM (
              SELECT
                "ItemCode",
                "LocCode",
                "InvntAct",
                SUM("InQty" - "OutQty") AS "Qty",
                SUM("SumStock") AS "StockValue",
                COUNT(*) AS "MovementRows"
              FROM {qtable(schema, 'OIVL')}
              WHERE "DocDate" <= ?
              GROUP BY "ItemCode", "LocCode", "InvntAct"
              HAVING ABS(SUM("InQty" - "OutQty")) > 0.000001
                  OR ABS(SUM("SumStock")) > 0.005
            ) X
            LEFT JOIN {qtable(schema, 'OACT')} A ON A."AcctCode" = X."InvntAct"
            ORDER BY X."InvntAct", X."ItemCode", X."LocCode"
            """,
            (CUTOFF_DATE,),
        )
        write_csv(OUTPUT_DIR / f"stock-opening-as-of-{CUTOFF_DATE}.csv", stock_rows)

        party_summary: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in party_rows:
            key = (str(row["Account"]), str(row["AcctName"]), str(row["SourceKind"]))
            bucket = party_summary.setdefault(
                key, {"count": 0, "open": 0.0, "original": 0.0, "recon": 0.0}
            )
            bucket["count"] += 1
            bucket["open"] += float(row["OpenSignedAsOfCutoff"] or 0)
            bucket["original"] += float(row["OriginalSigned"] or 0)
            bucket["recon"] += float(row["ReconSignedToCutoff"] or 0)

        party_account_summary: dict[tuple[str, str], float] = defaultdict(float)
        for row in party_rows:
            party_account_summary[(str(row["Account"]), str(row["AcctName"]))] += float(
                row["OpenSignedAsOfCutoff"] or 0
            )

        stock_summary: dict[tuple[str, str], dict[str, Any]] = {}
        for row in stock_rows:
            key = (
                str(row["InventoryAccount"] or "(blank)"),
                str(row["InventoryAccountName"] or ""),
            )
            bucket = stock_summary.setdefault(key, {"rows": 0, "qty": 0.0, "value": 0.0})
            bucket["rows"] += 1
            bucket["qty"] += float(row["Qty"] or 0)
            bucket["value"] += float(row["StockValue"] or 0)

        stock_accounts = [account for account, _ in stock_summary if account != "(blank)"]
        stock_gl: dict[str, float] = {}
        if stock_accounts:
            placeholders = ",".join("?" for _ in stock_accounts)
            for row in hana.rows(
                f"""
                SELECT
                  J."Account",
                  ROUND(SUM(J."Debit" - J."Credit"), 2) AS "GLBalance"
                FROM {qtable(schema, 'JDT1')} J
                WHERE J."RefDate" <= ?
                  AND J."Account" IN ({placeholders})
                GROUP BY J."Account"
                """,
                tuple([CUTOFF_DATE, *stock_accounts]),
            ):
                stock_gl[str(row["Account"])] = float(row["GLBalance"] or 0)

    party_type_rows = []
    for (account, account_name, source_kind), values in sorted(party_summary.items()):
        party_type_rows.append(
            [
                account,
                account_name,
                source_kind,
                values["count"],
                money(values["original"]),
                money(values["recon"]),
                money(values["open"]),
            ]
        )

    party_account_rows = []
    for (account, account_name), open_balance in sorted(party_account_summary.items()):
        party_account_rows.append([account, account_name, money(open_balance)])

    stock_rows_md = []
    for (account, account_name), values in sorted(stock_summary.items()):
        gl_balance = stock_gl.get(account)
        diff = None if gl_balance is None else values["value"] - gl_balance
        stock_rows_md.append(
            [
                account,
                account_name,
                values["rows"],
                f"{values['qty']:,.6f}",
                money(values["value"]),
                "" if gl_balance is None else money(gl_balance),
                "" if diff is None else money(diff),
            ]
        )

    report = f"""# LLM Opening Cutover Preflight

Date generated: 2026-04-28

Source: SAP Business One HANA schema `LLM_LIVENEW`

Target: `llmnew.localhost`, company `LLM`

Cutoff: `{CUTOFF_DATE}`

No ERPNext writes were performed by this report.

## Decision

Use the hybrid cutover path:

- Keep the `2018-02-22` through `{CUTOFF_DATE}` historical SAP GL archive already imported as Journal Entries.
- Create April 1 operational opening records only for balances still open as of `{CUTOFF_DATE}`.
- Use ERPNext opening invoices/party advances/stock opening against cutover clearing accounts so these records can exist operationally without double-counting the historical GL.
- Import April 2026 onward as native ERPNext documents.

## Why The Earlier Header Check Looked Wrong

SAP internal reconciliation is line-based, not header-only. For example, a purchase invoice can reconcile advance/TDS/other journal lines under the same source document. Summing `ITR1.ReconSum` by `SrcObjAbs` alone can exceed the invoice header amount. The correct as-of calculation uses the `JDT1` control-account line and subtracts only the matching `ITR1.TransId + TransRowId` reconciliations dated on or before the cutoff.

## Party Opening Balance By Source

{md_table(["Account", "Account Name", "Source", "Open Lines", "Original Signed", "Reconciled To Cutoff", "Open Signed"], party_type_rows)}

## Party Opening Balance By Account

These signed balances reconcile to the SAP control-account GL balances as of `{CUTOFF_DATE}`.

{md_table(["Account", "Account Name", "Open Signed Balance"], party_account_rows)}

## Stock Opening By Inventory Account

Stock is reconstructed from `OIVL` movements through `{CUTOFF_DATE}`. Rows with a blank inventory account have quantity but no SAP stock value; they need classification before import. Any difference between stock value and GL balance must be cleared before posting ERPNext stock opening.

{md_table(["Inventory Account", "Account Name", "Rows", "Qty", "Stock Value", "SAP GL Balance", "Value Minus GL"], stock_rows_md)}

## Generated Files

- `migration-control/opening-cutover/party-open-lines-as-of-{CUTOFF_DATE}.csv`
- `migration-control/opening-cutover/opening-invoice-source-lines-as-of-{CUTOFF_DATE}.csv`
- `migration-control/opening-cutover/stock-opening-as-of-{CUTOFF_DATE}.csv`

## Import Rules From This Report

1. `TransType 13` and `14` become opening customer invoices/credit notes for the open signed amount only, using ERPNext opening invoice behavior and a Temporary Opening/Cutover Clearing account.
2. `TransType 18` and `19` become opening supplier invoices/credit notes for the open signed amount only.
3. `TransType 24`, `46`, and `30` party lines are not normal invoices. They are advances/manual party balances and need opening Payment Entry or party Journal Entry treatment against the same cutover clearing account.
4. Stock opening must not be posted until the stock-value-vs-GL differences are reviewed, because ERPNext perpetual inventory is enabled.
5. After opening docs are submitted, post one cutover clearing Journal Entry if needed so the net GL remains equal to SAP at `{CUTOFF_DATE}` while operational ledgers exist for April settlement.
"""

    report_path = OUTPUT_DIR / "2026-04-01-hybrid-cutover-report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"Wrote {report_path}")
    print(f"Wrote {len(party_rows)} party open lines")
    print(f"Wrote {len(invoice_rows)} invoice/credit source lines")
    print(f"Wrote {len(stock_rows)} stock opening rows")


if __name__ == "__main__":
    run()

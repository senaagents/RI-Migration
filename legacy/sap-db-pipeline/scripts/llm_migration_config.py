from __future__ import annotations

import os
from pathlib import Path


SITES_PATH = Path(os.environ.get("SAP_SITES_PATH", "/Users/aakashchid/workshop/sena/senaBench/sites"))
SAP_DB = Path(
    os.environ.get("SAP_DB", "/Users/aakashchid/workshop/sap-db-pipeline/data/llm_sap_pipeline.sqlite3")
)
SITE = os.environ.get("SAP_TARGET_SITE", "llm.localhost")
COMPANY = os.environ.get("SAP_TARGET_COMPANY", "LLM")

HISTORY_FROM_DATE = os.environ.get("SAP_HISTORY_FROM_DATE", "2018-02-22")

# Migration control:
# - default ERPNext import scope is all staged history through March 2026
# - April 2026 and later must stay out of llmnew.localhost
CUTOFF_TO_DATE = os.environ.get("SAP_CUTOFF_TO_DATE", "2026-03-31")
NATIVE_FROM_DATE = os.environ.get("SAP_NATIVE_FROM_DATE", HISTORY_FROM_DATE)


def connect_frappe(frappe_module) -> None:
    os.chdir(SITES_PATH)
    frappe_module.init(site=SITE, sites_path=".", force=True)
    frappe_module.connect()

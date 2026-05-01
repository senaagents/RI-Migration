from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    sap_hana_address: str
    sap_hana_port: int
    sap_hana_user: str
    sap_hana_password: str
    sap_hana_schema: str
    database_url: str
    sap_query_limit: int


def get_settings() -> Settings:
    return Settings(
        sap_hana_address=os.getenv("SAP_HANA_ADDRESS", ""),
        sap_hana_port=int(os.getenv("SAP_HANA_PORT", "30015")),
        sap_hana_user=os.getenv("SAP_HANA_USER", ""),
        sap_hana_password=os.getenv("SAP_HANA_PASSWORD", ""),
        sap_hana_schema=os.getenv("SAP_HANA_SCHEMA", "LLM_LIVENEW"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/sap_pipeline.sqlite3"),
        sap_query_limit=int(os.getenv("SAP_QUERY_LIMIT", "0")),
    )

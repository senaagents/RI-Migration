from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_schema: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    from_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    to_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class TableProfile(Base):
    __tablename__ = "table_profiles"
    __table_args__ = (UniqueConstraint("table_name", "date_column", "from_date", "to_date", name="uq_table_profile_window"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    date_column: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    to_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    max_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    profiled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SapRawTableProfile(Base):
    __tablename__ = "sap_raw_table_profiles"
    __table_args__ = (UniqueConstraint("table_name", name="uq_sap_raw_table_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    staged_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="profiled")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    profiled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    staged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SapRawRow(Base):
    __tablename__ = "sap_raw_rows"
    __table_args__ = (UniqueConstraint("source_table", "row_ordinal", name="uq_sap_raw_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"), nullable=True)
    source_table: Mapped[str] = mapped_column(String(128), nullable=False)
    row_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SapMasterRecord(Base):
    __tablename__ = "sap_master_records"
    __table_args__ = (UniqueConstraint("record_type", "source_table", "source_key", name="uq_sap_master_record"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"), nullable=True)
    record_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SapDocument(Base):
    __tablename__ = "sap_documents"
    __table_args__ = (UniqueConstraint("source_table", "source_key", name="uq_sap_document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"), nullable=True)
    family: Mapped[str] = mapped_column(String(80), nullable=False)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_num: Mapped[str | None] = mapped_column(String(255), nullable=True)
    posting_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    card_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    card_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canceled: Mapped[str | None] = mapped_column(String(16), nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SapDocumentLine(Base):
    __tablename__ = "sap_document_lines"
    __table_args__ = (UniqueConstraint("source_table", "source_key", "line_num", name="uq_sap_document_line"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"), nullable=True)
    family: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    line_num: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    warehouse_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

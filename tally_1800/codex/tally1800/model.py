from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


KNOWN_ROLES = {
    "Company.1800": "company_profile_security_f11",
    "AddlCmp.1800": "additional_company_info",
    "CmpSave.1800": "company_metadata_checkpoint",
    "Manager.1800": "accounting_masters",
    "ExtMngr.1800": "edit_log_dependency_recompute",
    "LinkMgr.1800": "bill_order_reference_links",
    "TranMgr.1800": "transactions_vouchers",
    "SecTran.1800": "summary_exchange_export_return_transactions",
    "CfgRept.1800": "saved_report_configurations",
    "Aggr.1800": "aggregate_precomputed_values",
    "VchStatus.1800": "voucher_status",
    "StatStatus.1800": "statutory_status",
    "TSTATE.TSF": "database_state",
    "TMESSAGE.TSF": "database_update_queue",
    "TINTMSG.TSF": "intent_special_operation_queue",
    "TACCESS.TSF": "multi_client_access",
    "TEXCL.TSF": "exclusive_operation_access",
    "TUPDATE.TSF": "rollback_update_state",
}


@dataclass(frozen=True)
class DataFile:
    path: Path
    name: str
    size: int
    role: str
    suffix: str


@dataclass(frozen=True)
class PageProbe:
    page_size: int
    page_count: int
    exact_pages: bool
    fixed_word4_hits: int
    sequential_word12_hits: int
    crc32_le_hits: int
    crc32_be_hits: int
    score: float


@dataclass(frozen=True)
class StringHit:
    file_name: str
    offset: int
    encoding: str
    text: str


@dataclass(frozen=True)
class LengthPrefixedStringHit:
    file_name: str
    offset: int
    length_bytes: int
    text_offset: int
    text: str


@dataclass(frozen=True)
class FieldStringHit:
    file_name: str
    offset: int
    length_bytes: int
    text_offset: int
    text: str
    page_no: int
    page_offset: int
    field_id: int | None
    tag_prefix_hex: str | None


@dataclass(frozen=True)
class TLVHit:
    file_name: str
    offset: int
    page_no: int
    page_offset: int
    field_id: int
    type_hex: str
    length_bytes: int
    decoded: Any
    confidence: str


@dataclass(frozen=True)
class CompactNumericHit:
    file_name: str
    offset: int
    page_no: int
    page_offset: int
    field_id: int
    type_hex: str
    value: int


@dataclass(frozen=True)
class VchStatusSlot:
    file_name: str
    slot_seq: int
    offset: int
    page_no: int
    page_offset: int
    flag_word0: int
    flag_word1: int
    date_serial: int
    voucher_type_master_id: int
    word26_state_or_flag: int
    words: tuple[int, ...]

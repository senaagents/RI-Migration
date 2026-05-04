from __future__ import annotations

import json
import re
import sqlite3
import struct
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable

from .probe import (
    discover_files,
    extract_tagged_field_strings,
    iter_compact_numeric_fields,
    iter_extended_numeric_fields,
    iter_tlvs,
    iter_vch_status_slots,
    sha256_file,
)
from .typed_fields import pick_typed_fields
from .inventory_decode import build_inventory_candidates
from .ledger_decode import amount_value as ledger_amount_value
from .ledger_decode import iter_ledger_candidates, VoucherLedgerContext


MASTER_KIND_SUBRECORD = b"\x00\x50\xf7\x01\x00\x10"
MASTER_KIND_SLOT = b"\x02\x00\x00\x06"
MASTER_KIND_CODES = {
    2: "group",
    3: "ledger",
    4: "cost_category",
    5: "cost_centre",
    6: "godown",
    9: "stock_item",
    10: "voucher_type",
    11: "currency",
    12: "unit",
    17: "statutory_tds_nature",
    28: "tax_slab",
    29: "payroll_statutory_component",
    31: "gst_registration",
}
TYPED_MASTER_TABLES = {
    "groups": "group",
    "ledgers": "ledger",
    "stock_items": "stock_item",
    "units": "unit",
    "godowns": "godown",
    "cost_centres": "cost_centre",
}
IRN_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ACK_RE = re.compile(r"^\d{15}$")
DATE_TEXT_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
EWAY_BILL_RE = re.compile(r"^\d{12}$")
SQLITE_I64_MIN = -(1 << 63)
SQLITE_I64_MAX = (1 << 63) - 1


DECODED_SCHEMA = """
create table if not exists source_files (
  file_name text primary key,
  path text not null,
  role text not null,
  size integer not null,
  sha256 text not null
);

create table if not exists field_strings (
  file_name text not null,
  page_no integer not null,
  page_offset integer not null,
  absolute_offset integer not null,
  field_id integer,
  tag_prefix_hex text,
  length_bytes integer not null,
  text text not null
);

create table if not exists raw_tlvs (
  file_name text not null,
  page_no integer not null,
  page_offset integer not null,
  absolute_offset integer not null,
  field_id integer not null,
  type_hex text not null,
  length_bytes integer not null,
  confidence text not null,
  decoded_text text,
  decoded_number real
);

create index if not exists idx_raw_tlvs_file_field on raw_tlvs(file_name, field_id, type_hex);
create index if not exists idx_raw_tlvs_file_page on raw_tlvs(file_name, page_no);

create table if not exists compact_numeric_fields (
  file_name text not null,
  page_no integer not null,
  page_offset integer not null,
  absolute_offset integer not null,
  field_id integer not null,
  type_hex text not null,
  value integer not null
);

create index if not exists idx_compact_numeric_file_field
on compact_numeric_fields(file_name, field_id, type_hex);

create table if not exists extended_numeric_fields (
  file_name text not null,
  page_no integer not null,
  page_offset integer not null,
  absolute_offset integer not null,
  field_id integer not null,
  type_hex text not null,
  value integer not null
);

create index if not exists idx_extended_numeric_file_field
on extended_numeric_fields(file_name, field_id, type_hex);

create table if not exists voucher_header_candidates (
  master_id integer primary key,
  guid_guess text,
  page_no integer not null,
  page_group_id integer,
  page_group_page_count integer,
  absolute_offset integer not null,
  voucher_date_serial integer,
  voucher_date_guess text,
  key_raw text,
  base_type_code integer,
  key_offset integer,
  voucher_number_guess text,
  party_guess text,
  narration_guess text,
  paired_voucher_type_master_id integer,
  paired_vch_status_slot_seq integer,
  paired_voucher_type_confidence text
);

create table if not exists vch_status_slots (
  slot_seq integer primary key,
  file_name text not null,
  page_no integer not null,
  page_offset integer not null,
  absolute_offset integer not null,
  flag_word0 integer not null,
  flag_word1 integer not null,
  date_serial integer not null,
  date_guess text,
  voucher_type_master_id integer not null,
  word26_state_or_flag integer not null,
  words_json text not null
);

create index if not exists idx_vch_status_date_type
on vch_status_slots(date_serial, voucher_type_master_id);

create table if not exists company_strings (
  seq integer primary key autoincrement,
  field_id integer,
  page_no integer not null,
  page_offset integer not null,
  text text not null
);

create table if not exists company_guess (
  key text primary key,
  value text not null,
  evidence text not null
);

create table if not exists manager_records (
  page_no integer primary key,
  master_id integer,
  page_marker integer,
  header_words_json text not null,
  guid text,
  name text,
  name_field_id integer,
  user_text text,
  fields_json text not null,
  typed_fields_json text not null,
  state_guess text,
  country_guess text,
  pincode_guess text,
  email_guess text,
  phone_guess text,
  pan_guess text,
  gstin_guess text,
  text_count integer not null,
  all_strings_json text not null
);

create table if not exists voucher_text_pages (
  page_no integer primary key,
  page_marker integer,
  header_words_json text not null,
  text_count integer not null,
  party_or_name text,
  gstin text,
  narration_or_address text,
  all_strings_json text not null
);

create table if not exists manager_master_records (
  master_id integer primary key,
  page_start integer not null,
  page_count integer not null,
  guid text,
  name text,
  name_field_id integer,
  user_text text,
  fields_json text not null,
  typed_fields_json text not null,
  state_guess text,
  country_guess text,
  pincode_guess text,
  email_guess text,
  phone_guess text,
  pan_guess text,
  gstin_guess text,
  text_count integer not null,
  all_strings_json text not null
);

create table if not exists manager_master_kind_guesses (
  master_id integer primary key,
  kind_guess text not null,
  confidence text not null,
  evidence_json text not null
);

create index if not exists idx_manager_master_kind_guess
on manager_master_kind_guesses(kind_guess);

create table if not exists companies (
  company_id integer primary key,
  company_guid text,
  name text,
  gstin text,
  state_name text,
  country_name text,
  pincode text,
  email text,
  evidence_json text not null
);

create table if not exists groups (
  master_id integer primary key,
  guid text,
  name text,
  normalized_name text,
  name_field_id integer,
  source_kind text not null,
  kind_confidence text not null,
  kind_evidence_json text not null,
  page_start integer not null,
  page_count integer not null,
  created_by text,
  mailing_name text,
  address text,
  state_name text,
  country_name text,
  pincode text,
  email text,
  phone text,
  pan text,
  gstin text,
  fields_json text not null,
  typed_fields_json text not null,
  all_strings_json text not null
);

create index if not exists idx_groups_name on groups(name);

create table if not exists ledgers (
  master_id integer primary key,
  guid text,
  name text,
  normalized_name text,
  name_field_id integer,
  source_kind text not null,
  kind_confidence text not null,
  kind_evidence_json text not null,
  page_start integer not null,
  page_count integer not null,
  created_by text,
  mailing_name text,
  address text,
  state_name text,
  country_name text,
  pincode text,
  email text,
  phone text,
  pan text,
  gstin text,
  fields_json text not null,
  typed_fields_json text not null,
  all_strings_json text not null
);

create index if not exists idx_ledgers_name on ledgers(name);

create table if not exists stock_items (
  master_id integer primary key,
  guid text,
  name text,
  normalized_name text,
  name_field_id integer,
  source_kind text not null,
  kind_confidence text not null,
  kind_evidence_json text not null,
  page_start integer not null,
  page_count integer not null,
  created_by text,
  mailing_name text,
  address text,
  state_name text,
  country_name text,
  pincode text,
  email text,
  phone text,
  pan text,
  gstin text,
  fields_json text not null,
  typed_fields_json text not null,
  all_strings_json text not null
);

create index if not exists idx_stock_items_name on stock_items(name);

create table if not exists units (
  master_id integer primary key,
  guid text,
  name text,
  normalized_name text,
  name_field_id integer,
  source_kind text not null,
  kind_confidence text not null,
  kind_evidence_json text not null,
  page_start integer not null,
  page_count integer not null,
  created_by text,
  mailing_name text,
  address text,
  state_name text,
  country_name text,
  pincode text,
  email text,
  phone text,
  pan text,
  gstin text,
  fields_json text not null,
  typed_fields_json text not null,
  all_strings_json text not null
);

create index if not exists idx_units_name on units(name);

create table if not exists godowns (
  master_id integer primary key,
  guid text,
  name text,
  normalized_name text,
  name_field_id integer,
  source_kind text not null,
  kind_confidence text not null,
  kind_evidence_json text not null,
  page_start integer not null,
  page_count integer not null,
  created_by text,
  mailing_name text,
  address text,
  state_name text,
  country_name text,
  pincode text,
  email text,
  phone text,
  pan text,
  gstin text,
  fields_json text not null,
  typed_fields_json text not null,
  all_strings_json text not null
);

create index if not exists idx_godowns_name on godowns(name);

create table if not exists cost_centres (
  master_id integer primary key,
  guid text,
  name text,
  normalized_name text,
  name_field_id integer,
  source_kind text not null,
  kind_confidence text not null,
  kind_evidence_json text not null,
  page_start integer not null,
  page_count integer not null,
  created_by text,
  mailing_name text,
  address text,
  state_name text,
  country_name text,
  pincode text,
  email text,
  phone text,
  pan text,
  gstin text,
  fields_json text not null,
  typed_fields_json text not null,
  all_strings_json text not null
);

create index if not exists idx_cost_centres_name on cost_centres(name);

create table if not exists voucher_types (
  master_id integer primary key,
  guid text,
  name text,
  normalized_name text,
  name_field_id integer,
  source_kind text not null,
  kind_confidence text not null,
  kind_evidence_json text not null,
  page_start integer not null,
  page_count integer not null,
  created_by text,
  mailing_name text,
  address text,
  state_name text,
  country_name text,
  pincode text,
  email text,
  phone text,
  pan text,
  gstin text,
  is_referenced_by_vch_status integer not null,
  is_used_by_exportable_voucher integer not null,
  export_policy text not null,
  fields_json text not null,
  typed_fields_json text not null,
  all_strings_json text not null
);

create index if not exists idx_voucher_types_name on voucher_types(name);
create index if not exists idx_voucher_types_policy on voucher_types(export_policy);

create table if not exists voucher_inventory_entry_candidates (
  row_id integer primary key autoincrement,
  voucher_master_id integer not null,
  voucher_type_master_id integer,
  voucher_type_name text,
  stock_item_master_id integer not null,
  stock_item_name text,
  ledger_master_id integer,
  ledger_name text,
  unit_master_id integer,
  unit_name text,
  quantity_scaled integer not null,
  rate_scaled integer not null,
  amount_scaled integer not null,
  quantity_value real not null,
  rate_value real not null,
  amount_value real not null,
  source text not null,
  confidence text not null,
  page_no integer not null,
  page_offset integer not null,
  evidence_json text not null
);

create index if not exists idx_voucher_inventory_candidates_voucher
on voucher_inventory_entry_candidates(voucher_master_id);

create index if not exists idx_voucher_inventory_candidates_type
on voucher_inventory_entry_candidates(voucher_type_name);

create table if not exists voucher_inventory_entries_1800 (
  entry_id integer primary key autoincrement,
  voucher_master_id integer not null,
  voucher_type_master_id integer,
  voucher_type_name text,
  stock_item_master_id integer not null,
  stock_item_name text,
  ledger_master_id integer,
  ledger_name text,
  unit_master_id integer,
  unit_name text,
  quantity_scaled integer not null,
  rate_scaled integer not null,
  amount_scaled integer not null,
  quantity_value real not null,
  rate_value real not null,
  amount_value real not null,
  sources text not null,
  confidence text not null,
  evidence_count integer not null,
  first_page_no integer not null,
  first_page_offset integer not null
);

create index if not exists idx_voucher_inventory_entries_1800_voucher
on voucher_inventory_entries_1800(voucher_master_id);

create index if not exists idx_voucher_inventory_entries_1800_type
on voucher_inventory_entries_1800(voucher_type_name);

create table if not exists voucher_ledger_entry_candidates (
  row_id integer primary key autoincrement,
  voucher_master_id integer not null,
  voucher_type_master_id integer,
  voucher_type_name text,
  ledger_master_id integer not null,
  ledger_name text,
  amount_scaled integer not null,
  amount_value real not null,
  source text not null,
  confidence text not null,
  is_deemed_positive integer,
  sign_byte integer,
  page_no integer not null,
  page_offset integer not null,
  evidence_json text not null
);

create index if not exists idx_voucher_ledger_candidates_voucher
on voucher_ledger_entry_candidates(voucher_master_id);

create table if not exists voucher_ledger_entries_1800 (
  entry_id integer primary key autoincrement,
  voucher_master_id integer not null,
  voucher_type_master_id integer,
  voucher_type_name text,
  ledger_master_id integer not null,
  ledger_name text,
  amount_scaled integer not null,
  amount_value real not null,
  sources text not null,
  confidence text not null,
  evidence_count integer not null,
  first_page_no integer not null,
  first_page_offset integer not null
);

create index if not exists idx_voucher_ledger_entries_1800_voucher
on voucher_ledger_entries_1800(voucher_master_id);

create table if not exists voucher_statutory_status (
  status_id integer primary key autoincrement,
  file_name text not null,
  slot_seq integer not null,
  page_no integer not null,
  slot_idx integer not null,
  page_offset integer not null,
  absolute_offset integer not null,
  voucher_date_serial integer not null,
  voucher_date_guess text,
  gst_registration_master_id integer not null,
  gst_registration_name text,
  voucher_type_master_id integer not null,
  voucher_type_name text,
  return_line_seq integer not null,
  state_word0 integer not null,
  state_word1 integer not null,
  state_word2 integer not null,
  state_word3 integer not null,
  validity_marker integer not null,
  is_statutory_reportable integer not null,
  audit_warnings_json text not null,
  words_json text not null
);

create index if not exists idx_voucher_statutory_status_date_type
on voucher_statutory_status(voucher_date_serial, voucher_type_master_id);

create index if not exists idx_voucher_statutory_status_gst_reg
on voucher_statutory_status(gst_registration_master_id);

create table if not exists linkmgr_allocation_pages (
  page_no integer primary key,
  absolute_offset integer not null,
  voucher_master_id integer,
  voucher_resolves integer not null,
  ledger_master_id integer,
  ledger_name text,
  ledger_resolves integer not null,
  bill_name_guess text,
  bill_type_guess text,
  bill_type_confidence text,
  has_bill_date_slot integer not null,
  has_fid3_06 integer not null,
  page_class text not null,
  slot_values_json text not null,
  strings_json text not null
);

create index if not exists idx_linkmgr_allocation_voucher
on linkmgr_allocation_pages(voucher_master_id);

create index if not exists idx_linkmgr_allocation_ledger
on linkmgr_allocation_pages(ledger_master_id);

create table if not exists voucher_bill_allocation_candidates_1800 (
  row_id integer primary key autoincrement,
  source_file text not null,
  page_no integer not null,
  page_offset integer,
  absolute_offset integer not null,
  linkmgr_record_id integer,
  raw_232a_0d_value integer,
  voucher_master_id integer,
  voucher_resolves integer not null,
  raw_232d_03_value integer,
  ledger_master_id integer,
  ledger_name text,
  ledger_resolves integer not null,
  bill_name text,
  original_amount_scaled integer,
  application_date_serial integer,
  application_amount_scaled integer,
  voucher_key_raw text,
  voucher_key_high integer,
  voucher_key_low integer,
  application_id integer,
  bill_type_guess text,
  bill_type_confidence text not null,
  confidence text not null,
  source_json text not null
);

create index if not exists idx_bill_alloc_candidate_voucher
on voucher_bill_allocation_candidates_1800(voucher_master_id);

create index if not exists idx_bill_alloc_candidate_key
on voucher_bill_allocation_candidates_1800(voucher_key_high, voucher_key_low);

create table if not exists voucher_bank_allocation_candidates_1800 (
  row_id integer primary key autoincrement,
  source_file text not null,
  page_no integer not null,
  page_offset integer,
  absolute_offset integer not null,
  linkmgr_record_id integer,
  raw_232a_0d_value integer,
  voucher_master_id integer,
  voucher_resolves integer not null,
  raw_232d_03_value integer,
  ledger_master_id integer,
  ledger_name text,
  ledger_resolves integer not null,
  party text,
  amount_scaled integer,
  payment_mode text,
  utr_or_instrument text,
  crossing text,
  bank_name text,
  branch text,
  account_number text,
  print_status text,
  instrument_number text,
  voucher_key_raw text,
  voucher_key_high integer,
  voucher_key_low integer,
  application_id integer,
  transaction_type_guess text,
  transaction_type_confidence text not null,
  confidence text not null,
  source_json text not null
);

create index if not exists idx_bank_alloc_candidate_voucher
on voucher_bank_allocation_candidates_1800(voucher_master_id);

create index if not exists idx_bank_alloc_candidate_application
on voucher_bank_allocation_candidates_1800(application_id);

create table if not exists voucher_order_allocation_candidates_1800 (
  row_id integer primary key autoincrement,
  source_file text not null,
  page_no integer not null,
  page_offset integer,
  absolute_offset integer not null,
  linkmgr_record_id integer,
  raw_232a_0d_value integer,
  voucher_master_id integer,
  voucher_resolves integer not null,
  raw_232d_03_value integer,
  ledger_master_id integer,
  ledger_name text,
  ledger_resolves integer not null,
  order_ref_name text,
  order_number text,
  due_date_serial integer,
  amount_scaled integer,
  quantity_scaled integer,
  voucher_key_raw text,
  voucher_key_high integer,
  voucher_key_low integer,
  application_id integer,
  order_type_guess text,
  order_type_confidence text not null,
  confidence text not null,
  source_json text not null
);

create index if not exists idx_order_alloc_candidate_voucher
on voucher_order_allocation_candidates_1800(voucher_master_id);

create table if not exists voucher_invoice_metadata (
  metadata_id integer primary key autoincrement,
  record_id integer not null,
  voucher_master_id integer,
  irn text not null,
  source_fids_json text not null,
  source_blocks_json text not null,
  irn_ack_no text,
  signed_jwt_payload text,
  buyer_name text,
  buyer_address text,
  vehicle_number text,
  transport_mode text,
  vehicle_category text,
  transporter_date_0006 text,
  transporter_date_0009 text,
  conflict_irns_json text not null,
  has_conflict integer not null,
  evidence_json text not null
);

create unique index if not exists idx_voucher_invoice_metadata_record_irn
on voucher_invoice_metadata(record_id, irn);

create index if not exists idx_voucher_invoice_metadata_voucher
on voucher_invoice_metadata(voucher_master_id);

create table if not exists voucher_einvoice_archive (
  archive_id integer primary key autoincrement,
  record_id integer not null,
  archive_view_code integer not null,
  voucher_master_id integer,
  first_page_no integer not null,
  page_nos_json text not null,
  irns_json text not null,
  ack_nos_json text not null,
  eway_bill_nos_json text not null,
  jwt_payloads_json text not null,
  field_strings_json text not null,
  has_irn integer not null,
  has_ack integer not null,
  has_jwt integer not null,
  is_payload_archive integer not null,
  evidence_json text not null
);

create unique index if not exists idx_voucher_einvoice_archive_record_view
on voucher_einvoice_archive(record_id, archive_view_code, first_page_no);

create index if not exists idx_voucher_einvoice_archive_voucher
on voucher_einvoice_archive(voucher_master_id);

create table if not exists voucher_decode_audit (
  voucher_master_id integer primary key,
  guid_guess text,
  voucher_date_guess text,
  voucher_number_guess text,
  voucher_type_master_id integer,
  voucher_type_name text,
  inventory_best_rows integer not null,
  inventory_candidate_rows integer not null,
  inventory_candidate_unique_tuples integer not null,
  inventory_medium_candidate_rows integer not null,
  inventory_best_null_ledger_rows integer not null,
  issue_codes_json text not null,
  needs_xml_review integer not null,
  needs_xml_repair integer not null,
  repair_priority integer not null,
  evidence_json text not null
);

create index if not exists idx_voucher_decode_audit_repair
on voucher_decode_audit(needs_xml_repair, repair_priority);

create table if not exists xml_repair_queue (
  queue_id integer primary key autoincrement,
  voucher_master_id integer not null,
  guid_guess text,
  voucher_date_guess text,
  voucher_number_guess text,
  voucher_type_name text,
  repair_priority integer not null,
  reason_codes_json text not null,
  request_hint_json text not null
);

create index if not exists idx_xml_repair_queue_priority
on xml_repair_queue(repair_priority desc, voucher_master_id);

create view if not exists production_voucher_headers_1800 as
select
  v.master_id as voucher_master_id,
  v.guid_guess,
  v.voucher_date_guess,
  v.voucher_number_guess,
  v.paired_voucher_type_master_id as voucher_type_master_id,
  vt.name as voucher_type_name,
  v.page_no,
  v.page_group_id,
  v.key_raw,
  v.base_type_code,
  v.paired_voucher_type_confidence as voucher_type_confidence
from voucher_header_candidates v
left join manager_master_records vt
  on vt.master_id = v.paired_voucher_type_master_id
where v.key_raw is not null
  and coalesce(v.base_type_code, -1) != 31;

create view if not exists production_ledger_entries_1800 as
select
  l.entry_id,
  l.voucher_master_id,
  h.guid_guess,
  h.voucher_date_guess,
  h.voucher_number_guess,
  l.voucher_type_master_id,
  l.voucher_type_name,
  l.ledger_master_id,
  l.ledger_name,
  l.amount_scaled,
  l.amount_value,
  l.sources,
  l.confidence,
  l.evidence_count,
  l.first_page_no,
  l.first_page_offset
from voucher_ledger_entries_1800 l
join production_voucher_headers_1800 h
  on h.voucher_master_id = l.voucher_master_id
where l.confidence = 'high';

create view if not exists production_inventory_entries_1800 as
select
  i.entry_id,
  i.voucher_master_id,
  h.guid_guess,
  h.voucher_date_guess,
  h.voucher_number_guess,
  i.voucher_type_master_id,
  i.voucher_type_name,
  i.stock_item_master_id,
  i.stock_item_name,
  i.ledger_master_id,
  i.ledger_name,
  i.unit_master_id,
  i.unit_name,
  i.quantity_scaled,
  i.rate_scaled,
  i.amount_scaled,
  i.quantity_value,
  i.rate_value,
  i.amount_value,
  i.sources,
  i.confidence,
  i.evidence_count,
  i.first_page_no,
  i.first_page_offset
from voucher_inventory_entries_1800 i
join production_voucher_headers_1800 h
  on h.voucher_master_id = i.voucher_master_id
where i.confidence = 'high';

create view if not exists production_no_row_hard_misses_1800 as
select
  a.voucher_master_id,
  a.guid_guess,
  a.voucher_date_guess,
  a.voucher_number_guess,
  a.voucher_type_master_id,
  a.voucher_type_name,
  a.inventory_best_rows,
  a.inventory_candidate_rows,
  a.inventory_candidate_unique_tuples,
  a.inventory_medium_candidate_rows,
  a.issue_codes_json,
  a.repair_priority
from voucher_decode_audit a
join production_voucher_headers_1800 h
  on h.voucher_master_id = a.voucher_master_id
where a.needs_xml_repair = 1
  and a.inventory_best_rows = 0;

create view if not exists production_review_queue_1800 as
select
  a.voucher_master_id,
  a.guid_guess,
  a.voucher_date_guess,
  a.voucher_number_guess,
  a.voucher_type_master_id,
  a.voucher_type_name,
  a.inventory_best_rows,
  a.inventory_candidate_rows,
  a.inventory_candidate_unique_tuples,
  a.inventory_medium_candidate_rows,
  a.issue_codes_json,
  a.repair_priority
from voucher_decode_audit a
join production_voucher_headers_1800 h
  on h.voucher_master_id = a.voucher_master_id
where a.needs_xml_review = 1
  and a.needs_xml_repair = 0;

create view if not exists production_xml_repair_queue_1800 as
select
  q.voucher_master_id,
  q.guid_guess,
  q.voucher_date_guess,
  q.voucher_number_guess,
  q.voucher_type_name,
  q.repair_priority,
  q.reason_codes_json,
  q.request_hint_json
from xml_repair_queue q
join production_voucher_headers_1800 h
  on h.voucher_master_id = q.voucher_master_id;
"""


def _header_words(data: bytes, page_no: int, page_size: int = 512) -> list[int]:
    page = data[page_no * page_size : (page_no + 1) * page_size]
    return [
        struct.unpack_from("<I", page, offset)[0]
        for offset in range(0, min(64, len(page)), 4)
    ]


def _looks_like_guid(text: str) -> bool:
    parts = text.split("-")
    return len(parts) == 5 and all(parts) and all(c in "0123456789abcdefABCDEF-" for c in text)


def _looks_like_gstin(text: str) -> bool:
    return len(text) == 15 and text[:2].isdigit() and text.isalnum()


def _master_guid_guess(company_guid: str | None, master_id: int | None) -> str | None:
    if not company_guid:
        return None
    if len(company_guid) == 36 and master_id:
        return f"{company_guid}-{master_id:08x}"
    return company_guid


def _clean_name(text: str) -> str:
    # Tally master names often carry leading account/category codes.
    return text.strip()


def _serial_to_tally_date(value: int | None) -> str | None:
    if value is None or not 1 <= value <= 200000:
        return None
    return (date(1899, 12, 31) + timedelta(days=value)).isoformat()


KEY_RE = re.compile(r"^([0-9a-fA-F]{8})-([0-9a-fA-F]{8})-([0-9a-fA-F]{8})$")


def _parse_voucher_key_parts(text: str | None) -> tuple[int | None, int | None, int | None]:
    if not text:
        return None, None, None
    match = KEY_RE.match(text)
    if not match:
        return None, None, None
    return tuple(int(part, 16) for part in match.groups())


def _manager_record_stream(data: bytes, page_start: int, page_count: int, page_size: int = 512) -> bytes:
    return _manager_record_stream_for_pages(
        data,
        range(page_start, page_start + page_count),
        page_size=page_size,
    )


def _manager_record_stream_for_pages(
    data: bytes,
    pages: Iterable[int],
    page_size: int = 512,
) -> bytes:
    chunks = []
    for page_no in pages:
        page_start_offset = page_no * page_size
        page_end = page_start_offset + page_size
        if page_start_offset + 28 >= len(data):
            continue
        chunks.append(data[page_start_offset + 28 : min(page_end, len(data))])
    return b"".join(chunks)


def _extract_master_kind_codes(record_stream: bytes) -> Counter[int]:
    """Return 0x01f7 master kind codes from a page-header-stripped record.

    The proven body shape is:
      00 50 f7 01 00 10 <len:u32le> ... 02 00 00 06 <kind_code:u32le> ...

    Some records also contain unrelated u32 values in sibling cells, so callers
    should classify from the plausible mapped kind-code majority, not from every
    raw u32 observed in the body.
    """
    codes: Counter[int] = Counter()
    offset = 0
    while True:
        pos = record_stream.find(MASTER_KIND_SUBRECORD, offset)
        if pos < 0:
            break
        offset = pos + 1
        if pos + 10 > len(record_stream):
            continue
        length = struct.unpack_from("<I", record_stream, pos + 6)[0]
        end = pos + 10 + length
        if length > 4096 or end > len(record_stream):
            continue
        body = record_stream[pos + 10 : end]
        body_pos = 0
        while True:
            slot_pos = body.find(MASTER_KIND_SLOT, body_pos)
            if slot_pos < 0:
                break
            body_pos = slot_pos + 1
            if slot_pos + 8 <= len(body):
                codes[struct.unpack_from("<I", body, slot_pos + 4)[0]] += 1
    return codes


def _field_anchor_kind(
    master_id: int,
    fields: set[int],
    voucher_type_ids: set[int],
) -> tuple[str, str, list[str]]:
    evidence: list[str] = []
    kind = "unknown"
    confidence = "low"

    if 0x0FD4 in fields:
        kind = "stock_item"
        confidence = "high"
        evidence.append("field_0x0fd4_stock_item_anchor")
    elif 0x0067 in fields:
        kind = "ledger"
        confidence = "high"
        evidence.append("field_0x0067_ledger_user_anchor")
    elif master_id in voucher_type_ids:
        kind = "voucher_type"
        confidence = "high"
        evidence.append("referenced_by_vch_status_voucher_type_master_id")
    elif 0x1132 in fields:
        kind = "unit"
        confidence = "high"
        evidence.append("field_0x1132_unit_anchor")
    elif fields & {0x1902, 0x1903}:
        kind = "godown"
        confidence = "medium"
        evidence.append("field_0x1902_or_0x1903_godown_anchor")
    elif 0x0BFB in fields:
        kind = "cost_centre"
        confidence = "medium"
        evidence.append("field_0x0bfb_cost_centre_anchor")
    elif 0x0963 in fields:
        kind = "group_or_stock_group"
        confidence = "medium"
        evidence.append("field_0x0963_group_anchor")
    elif 0x0FA2 in fields and 0x01F7 in fields:
        kind = "voucher_type_or_stock_item"
        confidence = "low"
        evidence.append("field_0x0fa2_seen_on_voucher_types_and_some_stock_items")

    return kind, confidence, evidence


def _insert_field_strings(conn: sqlite3.Connection, file_name: str, hits) -> None:
    conn.executemany(
        """
        insert into field_strings
        (file_name, page_no, page_offset, absolute_offset, field_id, tag_prefix_hex,
         length_bytes, text)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                file_name,
                hit.page_no,
                hit.page_offset,
                hit.offset,
                hit.field_id,
                hit.tag_prefix_hex,
                hit.length_bytes,
                hit.text,
            )
            for hit in hits
        ],
    )


def _insert_raw_tlvs(conn: sqlite3.Connection, path: Path, data: bytes | None = None) -> None:
    rows = []
    for hit in iter_tlvs(path, data=data):
        decoded_text = hit.decoded if isinstance(hit.decoded, str) else None
        decoded_number = hit.decoded if isinstance(hit.decoded, (int, float)) else None
        rows.append(
            (
                hit.file_name,
                hit.page_no,
                hit.page_offset,
                hit.offset,
                hit.field_id,
                hit.type_hex,
                hit.length_bytes,
                hit.confidence,
                decoded_text,
                decoded_number,
            )
        )
        if len(rows) >= 10000:
            conn.executemany(
                """
                insert into raw_tlvs
                (file_name, page_no, page_offset, absolute_offset, field_id, type_hex,
                 length_bytes, confidence, decoded_text, decoded_number)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            rows.clear()
    if rows:
        conn.executemany(
            """
            insert into raw_tlvs
            (file_name, page_no, page_offset, absolute_offset, field_id, type_hex,
             length_bytes, confidence, decoded_text, decoded_number)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _insert_tranmgr_compact_fields(conn: sqlite3.Connection, path: Path, data: bytes | None = None) -> None:
    # 5 (field_id, type_hex) pairs we care about. Old code did 5 separate
    # iter_compact_numeric_fields walks; we now do ONE walk with the union of
    # filters, then dispatch per (field_id, type_hex) into the right buckets.
    # That's 5x less Python-level scanning over a multi-hundred-MB file.
    wanted = {
        (0x0BBB, "0006"),  # voucher MASTERID
        (0x0BBB, "0003"),  # voucher type MASTERID candidate on many pages
        (0x00CB, "0003"),  # usually mirrors 0x0BBB/0003 on tested pages
        (0x0BBF, "0006"),  # sparse candidate order/link field
        (0x0067, "000d"),  # voucher date serial candidate
    }
    if data is None:
        data = path.read_bytes()
    all_rows = []
    page_group_ids = [
        struct.unpack_from("<I", data, page_no * 512 + 4)[0]
        for page_no in range(len(data) // 512)
    ]
    page_group_counts: dict[int, int] = defaultdict(int)
    for group_id in page_group_ids:
        page_group_counts[group_id] += 1
    date_by_page: dict[int, int] = {}
    master_rows = []
    wanted_field_ids = {fid for fid, _ in wanted}
    wanted_type_hexes = {th for _, th in wanted}
    for hit in iter_compact_numeric_fields(
        path,
        field_ids=wanted_field_ids,
        type_hexes=wanted_type_hexes,
        data=data,
    ):
        if (hit.field_id, hit.type_hex) not in wanted:
            continue
        all_rows.append((
            hit.file_name,
            hit.page_no,
            hit.page_offset,
            hit.offset,
            hit.field_id,
            hit.type_hex,
            hit.value,
        ))
        if hit.field_id == 0x0067 and hit.type_hex == "000d":
            date_by_page[hit.page_no] = hit.value
        elif hit.field_id == 0x0BBB and hit.type_hex == "0006":
            master_rows.append(hit)
    conn.executemany(
        """
        insert into compact_numeric_fields
        (file_name, page_no, page_offset, absolute_offset, field_id, type_hex, value)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        all_rows,
    )

    company_guid = conn.execute(
        """
        select text
        from field_strings
        where file_name in ('Company.1800', 'CmpSave.1800', 'Manager.1800')
          and field_id = 507
          and length(text) = 36
        limit 1
        """
    ).fetchone()
    guid_prefix = company_guid[0] if company_guid else None
    strings_by_page: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for page_no, field_id, text in conn.execute(
        """
        select page_no, field_id, decoded_text
        from raw_tlvs
        where file_name = 'TranMgr.1800'
          and decoded_text is not null
          and field_id in (3, 203, 205, 206, 208, 2005, 6)
        order by absolute_offset
        """
    ):
        strings_by_page[page_no][field_id].append(text)

    def first_string(page_no: int, field_ids: tuple[int, ...]) -> str | None:
        fields = strings_by_page.get(page_no, {})
        for field_id in field_ids:
            values = fields.get(field_id)
            if values:
                return values[0]
        return None

    conn.executemany(
        """
        insert or replace into voucher_header_candidates
        (master_id, guid_guess, page_no, page_group_id, page_group_page_count,
         absolute_offset, voucher_date_serial,
         voucher_date_guess, key_raw, base_type_code, key_offset,
         voucher_number_guess, party_guess, narration_guess)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                hit.value,
                f"{guid_prefix}-{hit.value:08x}" if guid_prefix else None,
                hit.page_no,
                page_group_ids[hit.page_no],
                page_group_counts[page_group_ids[hit.page_no]],
                hit.offset,
                date_by_page.get(hit.page_no),
                _serial_to_tally_date(date_by_page.get(hit.page_no)),
                key_raw := first_string(hit.page_no, (0x0003,)),
                _parse_voucher_key_parts(key_raw)[1],
                _parse_voucher_key_parts(key_raw)[2],
                first_string(hit.page_no, (0x00CB, 0x07D5, 0x0006)),
                first_string(hit.page_no, (0x00CE, 0x00D0)),
                first_string(hit.page_no, (0x00CD,)),
            )
            for hit in master_rows
        ],
    )


def _insert_extended_numeric_fields(conn: sqlite3.Connection, path: Path, data: bytes | None = None) -> None:
    rows = []
    for hit in iter_extended_numeric_fields(path, type_hexes={"0009"}, data=data):
        rows.append(
            (
                hit.file_name,
                hit.page_no,
                hit.page_offset,
                hit.offset,
                hit.field_id,
                hit.type_hex,
                hit.value,
            )
        )
        if len(rows) >= 10000:
            conn.executemany(
                """
                insert into extended_numeric_fields
                (file_name, page_no, page_offset, absolute_offset, field_id, type_hex, value)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            rows.clear()
    if rows:
        conn.executemany(
            """
            insert into extended_numeric_fields
            (file_name, page_no, page_offset, absolute_offset, field_id, type_hex, value)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _insert_vch_status_slots(conn: sqlite3.Connection, path: Path, data: bytes | None = None) -> None:
    conn.executemany(
        """
        insert into vch_status_slots
        (slot_seq, file_name, page_no, page_offset, absolute_offset,
         flag_word0, flag_word1, date_serial, date_guess,
         voucher_type_master_id, word26_state_or_flag, words_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                slot.slot_seq,
                slot.file_name,
                slot.page_no,
                slot.page_offset,
                slot.offset,
                slot.flag_word0,
                slot.flag_word1,
                slot.date_serial,
                _serial_to_tally_date(slot.date_serial),
                slot.voucher_type_master_id,
                slot.word26_state_or_flag,
                json.dumps(slot.words),
            )
            for slot in iter_vch_status_slots(path, data=data)
        ],
    )


def _export_statutory_status(conn: sqlite3.Connection, path: Path, data: bytes | None = None) -> None:
    if data is None:
        data = path.read_bytes()
    page_size = 512
    slot_size = 0x80
    slot_offsets = (0x80, 0x100, 0x180)
    master_names = {
        int(master_id): name
        for master_id, name in conn.execute(
            "select master_id, name from manager_master_records"
        )
    }
    voucher_type_ids = {
        int(master_id)
        for (master_id,) in conn.execute(
            """
            select master_id
            from manager_master_kind_guesses
            where kind_guess = 'voucher_type'
            """
        )
    }
    gst_registration_ids = {
        int(master_id)
        for (master_id,) in conn.execute(
            """
            select master_id
            from manager_master_kind_guesses
            where kind_guess = 'gst_registration'
            """
        )
    }

    rows = []
    slot_seq = 0
    for page_no in range(1, len(data) // page_size):
        page_start = page_no * page_size
        for slot_idx, page_offset in enumerate(slot_offsets):
            offset = page_start + page_offset
            slot = data[offset : offset + slot_size]
            if len(slot) != slot_size or not any(slot):
                continue
            words = struct.unpack_from("<32I", slot, 0)
            date_serial = words[10]
            gst_registration_master_id = words[25]
            voucher_type_master_id = words[26]
            date_guess = _serial_to_tally_date(date_serial)
            warnings = []
            if date_guess is None:
                warnings.append("invalid_date_serial")
            if voucher_type_master_id not in voucher_type_ids:
                warnings.append("unknown_voucher_type_master_id")
            if gst_registration_master_id and gst_registration_master_id not in gst_registration_ids:
                warnings.append("unknown_gst_registration_master_id")
            if words[31] != 1:
                warnings.append("invalid_validity_marker")
            rows.append(
                (
                    path.name,
                    slot_seq,
                    page_no,
                    slot_idx,
                    page_offset,
                    offset,
                    date_serial,
                    date_guess,
                    gst_registration_master_id,
                    master_names.get(gst_registration_master_id),
                    voucher_type_master_id,
                    master_names.get(voucher_type_master_id),
                    words[30],
                    words[0],
                    words[1],
                    words[2],
                    words[3],
                    words[31],
                    1 if words[31] == 1 and date_guess is not None else 0,
                    json.dumps(warnings),
                    json.dumps(words),
                )
            )
            slot_seq += 1

    conn.executemany(
        """
        insert into voucher_statutory_status
        (file_name, slot_seq, page_no, slot_idx, page_offset, absolute_offset,
         voucher_date_serial, voucher_date_guess, gst_registration_master_id,
         gst_registration_name, voucher_type_master_id, voucher_type_name,
         return_line_seq, state_word0, state_word1, state_word2, state_word3,
         validity_marker, is_statutory_reportable, audit_warnings_json,
         words_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _linkmgr_compact_slot_rows(page: bytes) -> list[dict[str, int]]:
    slots = []
    pos = 28
    first_tlv = page.find(b"\x02\x10", 28)
    limit = first_tlv if first_tlv > 0 else len(page) - 10
    while pos + 10 <= limit:
        if page[pos] == 0 and page[pos + 1] == 0 and page[pos + 4] == 0:
            field_id = struct.unpack_from("<H", page, pos + 2)[0]
            type_byte = page[pos + 5]
            value = struct.unpack_from("<I", page, pos + 6)[0]
            if field_id:
                slots.append(
                    {
                        "page_offset": pos,
                        "field_id": field_id,
                        "type": type_byte,
                        "value": value,
                    }
                )
                pos += 10
                continue
        pos += 1
    return slots


def _linkmgr_compact_slots(page: bytes) -> list[tuple[int, int, int]]:
    return [
        (slot["field_id"], slot["type"], slot["value"])
        for slot in _linkmgr_compact_slot_rows(page)
    ]


def _linkmgr_extended_slots(page: bytes) -> list[dict[str, int]]:
    slots = []
    pos = 28
    first_tlv = page.find(b"\x02\x10", 28)
    limit = first_tlv if first_tlv > 0 else len(page)
    while pos + 14 <= limit:
        if page[pos + 4] == 0:
            field_id = struct.unpack_from("<H", page, pos + 2)[0]
            type_byte = page[pos + 5]
            if field_id == 0x0002 and type_byte == 0x07 and pos + 14 <= limit:
                low = struct.unpack_from("<I", page, pos + 6)[0]
                high = struct.unpack_from("<I", page, pos + 10)[0]
                slots.append(
                    {
                        "kind": "voucher_key",
                        "page_offset": pos,
                        "field_id": field_id,
                        "type": type_byte,
                        "low": low,
                        "high": high,
                    }
                )
            elif field_id in {0x0642, 0x232E} and type_byte == 0x09 and pos + 14 <= limit:
                amount = struct.unpack_from("<q", page, pos + 6)[0]
                slots.append(
                    {
                        "kind": "amount",
                        "page_offset": pos,
                        "field_id": field_id,
                        "type": type_byte,
                        "amount_scaled": amount,
                    }
                )
            elif field_id == 0x0644 and type_byte == 0x09 and pos + 18 <= limit:
                date_serial = struct.unpack_from("<I", page, pos + 6)[0]
                amount = struct.unpack_from("<q", page, pos + 10)[0]
                slots.append(
                    {
                        "kind": "application_amount",
                        "page_offset": pos,
                        "field_id": field_id,
                        "type": type_byte,
                        "date_serial": date_serial,
                        "amount_scaled": amount,
                    }
                )
        pos += 1
    return slots


def _linkmgr_tlv_strings(page: bytes) -> dict[int, list[str]]:
    strings: dict[int, list[str]] = defaultdict(list)
    idx = 28
    while True:
        pos = page.find(b"\x02\x10", idx)
        if pos < 0 or pos + 8 > len(page):
            break
        field_id = struct.unpack_from("<H", page, pos + 2)[0]
        type_bytes = page[pos + 4 : pos + 6]
        length = struct.unpack_from("<H", page, pos + 6)[0]
        end = pos + 8 + length
        if end > len(page):
            idx = pos + 1
            continue
        value = page[pos + 8 : end]
        if type_bytes == b"\x00\x0f" and length >= 2 and length % 2 == 0:
            try:
                payload = value[:-2] if value.endswith(b"\x00\x00") else value
                text = payload.decode("utf-16le", errors="strict").strip()
            except UnicodeDecodeError:
                text = ""
            if text:
                strings[field_id].append(text)
        idx = end
    return strings


def _first_linkmgr_string(strings: dict[int, list[str]], field_id: int) -> str | None:
    values = strings.get(field_id)
    return values[0] if values else None


def _first_linkmgr_slot(slots: list[dict[str, int]], field_id: int, type_byte: int) -> int | None:
    for slot in slots:
        if slot["field_id"] == field_id and slot["type"] == type_byte:
            return slot["value"]
    return None


def _first_linkmgr_extended(
    slots: list[dict[str, int]],
    *,
    kind: str,
    field_id: int | None = None,
) -> dict[str, int] | None:
    for slot in slots:
        if slot["kind"] != kind:
            continue
        if field_id is not None and slot["field_id"] != field_id:
            continue
        return slot
    return None


def _linkmgr_key_raw(key: dict[str, int] | None) -> str | None:
    if not key:
        return None
    return f"{key['high']:08x}:{key['low']:08x}"


def _linkmgr_source_json(
    *,
    compact_slots: list[dict[str, int]],
    extended_slots: list[dict[str, int]],
    strings: dict[int, list[str]],
) -> str:
    return json.dumps(
        {
            "compact_slots": compact_slots,
            "extended_slots": extended_slots,
            "strings": {str(field_id): values for field_id, values in strings.items()},
        },
        ensure_ascii=False,
    )


def _export_linkmgr_allocation_pages(conn: sqlite3.Connection, path: Path, data: bytes | None = None) -> None:
    if data is None:
        data = path.read_bytes()
    page_size = 512
    voucher_ids = {
        int(master_id)
        for (master_id,) in conn.execute("select master_id from voucher_header_candidates")
    }
    ledger_rows = {
        int(master_id): name
        for master_id, name in conn.execute(
            """
            select m.master_id, m.name
            from manager_master_records m
            join manager_master_kind_guesses k
              on k.master_id = m.master_id
            where k.kind_guess = 'ledger'
            """
        )
    }

    rows = []
    bill_rows = []
    bank_rows = []
    order_rows = []
    application_key_candidates: dict[int, tuple[int, int]] = {}
    application_key_collisions: set[int] = set()
    page_facts: list[dict[str, object]] = []
    for page_no in range(1, len(data) // page_size):
        page_start = page_no * page_size
        page = data[page_start : page_start + page_size]
        compact_slots = _linkmgr_compact_slot_rows(page)
        slots = [
            (slot["field_id"], slot["type"], slot["value"])
            for slot in compact_slots
        ]
        extended_slots = _linkmgr_extended_slots(page)
        strings = _linkmgr_tlv_strings(page)
        voucher_values = [
            value for field_id, type_byte, value in slots
            if field_id == 0x232A and type_byte == 0x0D
        ]
        ledger_values = [
            value for field_id, type_byte, value in slots
            if field_id == 0x232D and type_byte == 0x03
        ]
        voucher_master_id = voucher_values[0] if voucher_values else None
        ledger_master_id = ledger_values[0] if ledger_values else None
        voucher_resolves = 1 if voucher_master_id in voucher_ids else 0
        ledger_resolves = 1 if ledger_master_id in ledger_rows else 0
        resolved_voucher_master_id = voucher_master_id if voucher_resolves else None
        resolved_ledger_master_id = ledger_master_id if ledger_resolves else None
        bill_names = strings.get(0x0002) or []
        bill_name_guess = bill_names[0] if bill_names else None
        has_bill_date_slot = b"\x00\x00\x05\x00\x00\x0d" in page
        has_fid3_06 = b"\x00\x00\x03\x00\x00\x06" in page
        linkmgr_record_id = struct.unpack_from("<I", page, 4)[0]
        direct_key = _first_linkmgr_extended(extended_slots, kind="voucher_key")
        app2 = _first_linkmgr_slot(compact_slots, 0x0002, 0x06)
        app3 = _first_linkmgr_slot(compact_slots, 0x0003, 0x06)
        if app2 is not None and direct_key is not None:
            key_pair = (direct_key["high"], direct_key["low"])
            old_pair = application_key_candidates.get(app2)
            if old_pair is None:
                application_key_candidates[app2] = key_pair
            elif old_pair != key_pair:
                application_key_collisions.add(app2)
        if voucher_master_id is not None or ledger_master_id is not None:
            page_class = "allocation"
        elif strings.get(0x232E):
            page_class = "company_default"
        else:
            page_class = "unknown"
        bill_type_guess = None
        bill_type_confidence = None
        if bill_name_guess and page_class == "allocation":
            bill_type_guess = "Agst Ref" if has_bill_date_slot else "New Ref"
            bill_type_confidence = "heuristic"
        rows.append(
            (
                page_no,
                page_start,
                voucher_master_id,
                voucher_resolves,
                ledger_master_id,
                ledger_rows.get(ledger_master_id) if ledger_master_id is not None else None,
                ledger_resolves,
                bill_name_guess,
                bill_type_guess,
                bill_type_confidence,
                1 if has_bill_date_slot else 0,
                1 if has_fid3_06 else 0,
                page_class,
                json.dumps(
                    [
                        {
                            "page_offset": slot["page_offset"],
                            "field_id": slot["field_id"],
                            "type": slot["type"],
                            "value": slot["value"],
                        }
                        for slot in compact_slots
                    ]
                ),
                json.dumps({str(field_id): values for field_id, values in strings.items()}, ensure_ascii=False),
            )
        )
        page_facts.append(
            {
                "page_no": page_no,
                "page_start": page_start,
                "page": page,
                "linkmgr_record_id": linkmgr_record_id,
                "compact_slots": compact_slots,
                "extended_slots": extended_slots,
                "strings": strings,
                "raw_232a_0d_value": voucher_master_id,
                "voucher_master_id": resolved_voucher_master_id,
                "voucher_resolves": voucher_resolves,
                "raw_232d_03_value": ledger_master_id,
                "ledger_master_id": resolved_ledger_master_id,
                "ledger_name": ledger_rows.get(ledger_master_id) if ledger_resolves else None,
                "ledger_resolves": ledger_resolves,
                "bill_type_guess": bill_type_guess,
                "bill_type_confidence": bill_type_confidence or "unknown",
                "direct_key": direct_key,
                "application_id_0002": app2,
                "application_id_0003": app3,
            }
        )

    application_key_candidates = {
        app_id: key_pair
        for app_id, key_pair in application_key_candidates.items()
        if app_id not in application_key_collisions
    }

    for fact in page_facts:
        compact_slots = fact["compact_slots"]
        extended_slots = fact["extended_slots"]
        strings = fact["strings"]
        if not isinstance(compact_slots, list) or not isinstance(extended_slots, list):
            continue
        if not isinstance(strings, dict):
            continue
        direct_key = fact["direct_key"]
        key = direct_key
        application_id = fact["application_id_0003"] or fact["application_id_0002"]
        if key is None and application_id in application_key_candidates:
            high, low = application_key_candidates[application_id]
            key = {
                "kind": "voucher_key",
                "page_offset": fact["page_no"],
                "field_id": 0x0002,
                "type": 0x07,
                "high": high,
                "low": low,
            }
        source_json = _linkmgr_source_json(
            compact_slots=compact_slots,
            extended_slots=extended_slots,
            strings=strings,
        )
        original_amount = _first_linkmgr_extended(
            extended_slots,
            kind="amount",
            field_id=0x0642,
        )
        application_amounts = [
            slot for slot in extended_slots
            if slot["kind"] == "application_amount" and slot["field_id"] == 0x0644
        ]
        bill_name = _first_linkmgr_string(strings, 0x0002)
        bill_page = original_amount is not None or bool(application_amounts)
        if bill_page:
            amount_rows = application_amounts or [None]
            for app_amount in amount_rows:
                page_offset = (
                    app_amount["page_offset"]
                    if app_amount is not None
                    else original_amount["page_offset"] if original_amount is not None
                    else 28
                )
                bill_rows.append(
                    (
                        path.name,
                        fact["page_no"],
                        page_offset,
                        fact["page_start"] + page_offset,
                        fact["linkmgr_record_id"],
                        fact["raw_232a_0d_value"],
                        fact["voucher_master_id"],
                        fact["voucher_resolves"],
                        fact["raw_232d_03_value"],
                        fact["ledger_master_id"],
                        fact["ledger_name"],
                        fact["ledger_resolves"],
                        bill_name,
                        original_amount["amount_scaled"] if original_amount is not None else None,
                        app_amount["date_serial"] if app_amount is not None else None,
                        app_amount["amount_scaled"] if app_amount is not None else None,
                        _linkmgr_key_raw(key),
                        key["high"] if key else None,
                        key["low"] if key else None,
                        fact["application_id_0002"],
                        fact["bill_type_guess"],
                        fact["bill_type_confidence"],
                        "medium" if key or fact["voucher_resolves"] else "audit",
                        source_json,
                    )
                )

        bank_amount = _first_linkmgr_extended(
            extended_slots,
            kind="amount",
            field_id=0x232E,
        )
        payment_mode = _first_linkmgr_string(strings, 0x2346)
        bank_page = any(
            _first_linkmgr_string(strings, field_id) is not None
            for field_id in (0x232E, 0x232B, 0x232F, 0x2331, 0x2332, 0x2333, 0x2346, 0x2348, 0x2358)
        ) or bank_amount is not None
        if bank_page:
            transaction_type_guess = (
                "Inter Bank Transfer" if payment_mode in {"NEFT", "RTGS"} else None
            )
            bank_offset = bank_amount["page_offset"] if bank_amount else 28
            bank_rows.append(
                (
                    path.name,
                    fact["page_no"],
                    bank_offset,
                    fact["page_start"] + bank_offset,
                    fact["linkmgr_record_id"],
                    fact["raw_232a_0d_value"],
                    fact["voucher_master_id"],
                    fact["voucher_resolves"],
                    fact["raw_232d_03_value"],
                    fact["ledger_master_id"],
                    fact["ledger_name"],
                    fact["ledger_resolves"],
                    _first_linkmgr_string(strings, 0x232E),
                    bank_amount["amount_scaled"] if bank_amount else None,
                    payment_mode,
                    _first_linkmgr_string(strings, 0x232B) or _first_linkmgr_string(strings, 0x2358),
                    _first_linkmgr_string(strings, 0x232F),
                    _first_linkmgr_string(strings, 0x2331),
                    _first_linkmgr_string(strings, 0x2332),
                    _first_linkmgr_string(strings, 0x2333),
                    _first_linkmgr_string(strings, 0x2348),
                    _first_linkmgr_string(strings, 0x2358),
                    _linkmgr_key_raw(key),
                    key["high"] if key else None,
                    key["low"] if key else None,
                    application_id,
                    transaction_type_guess,
                    "heuristic" if transaction_type_guess else "unknown",
                    "medium" if key or fact["voucher_resolves"] else "audit",
                    source_json,
                )
            )

    conn.executemany(
        """
        insert or replace into linkmgr_allocation_pages
        (page_no, absolute_offset, voucher_master_id, voucher_resolves,
         ledger_master_id, ledger_name, ledger_resolves, bill_name_guess,
         bill_type_guess, bill_type_confidence, has_bill_date_slot, has_fid3_06,
         page_class, slot_values_json, strings_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.executemany(
        """
        insert into voucher_bill_allocation_candidates_1800
        (source_file, page_no, page_offset, absolute_offset, linkmgr_record_id,
         raw_232a_0d_value, voucher_master_id, voucher_resolves,
         raw_232d_03_value, ledger_master_id, ledger_name, ledger_resolves,
         bill_name, original_amount_scaled, application_date_serial,
         application_amount_scaled, voucher_key_raw, voucher_key_high,
         voucher_key_low, application_id, bill_type_guess, bill_type_confidence,
         confidence, source_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        bill_rows,
    )
    conn.executemany(
        """
        insert into voucher_bank_allocation_candidates_1800
        (source_file, page_no, page_offset, absolute_offset, linkmgr_record_id,
         raw_232a_0d_value, voucher_master_id, voucher_resolves,
         raw_232d_03_value, ledger_master_id, ledger_name, ledger_resolves,
         party, amount_scaled, payment_mode, utr_or_instrument, crossing,
         bank_name, branch, account_number, print_status, instrument_number,
         voucher_key_raw, voucher_key_high, voucher_key_low, application_id,
         transaction_type_guess, transaction_type_confidence, confidence, source_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        bank_rows,
    )


def _page_word(data: bytes, page_no: int, word_idx: int) -> int:
    offset = page_no * 512 + word_idx * 4
    if offset + 4 > len(data):
        return 0
    return struct.unpack_from("<I", data, offset)[0]


def _tranmgr_page_runs_by_group(data: bytes) -> Iterable[list[int]]:
    pages_by_group: dict[int, list[int]] = defaultdict(list)
    for page_no in range(1, len(data) // 512):
        group_id = _page_word(data, page_no, 1)
        if group_id:
            pages_by_group[group_id].append(page_no)
    for pages in pages_by_group.values():
        pages = sorted(pages)
        if not pages:
            continue
        run = [pages[0]]
        for page_no in pages[1:]:
            if page_no == run[-1] + 1:
                run.append(page_no)
            else:
                yield run
                run = [page_no]
        yield run


def _logical_stream_with_origins(data: bytes, pages: list[int]) -> tuple[bytes, list[int]]:
    chunks = []
    origins = []
    for page_no in pages:
        body_start = page_no * 512 + 28
        body_end = min((page_no + 1) * 512, len(data))
        body = data[body_start:body_end]
        chunks.append(body)
        origins.extend(range(body_start, body_start + len(body)))
    return b"".join(chunks), origins


def _iter_tranmgr_string_tlvs(data: bytes) -> Iterable[tuple[int, int, str, int]]:
    for pages in _tranmgr_page_runs_by_group(data):
        stream, origins = _logical_stream_with_origins(data, pages)
        idx = 0
        while idx < len(stream) - 8:
            tag_pos = stream.find(b"\x02\x10", idx)
            if tag_pos < 0 or tag_pos + 8 > len(stream):
                break
            field_id = struct.unpack_from("<H", stream, tag_pos + 2)[0]
            type_bytes = stream[tag_pos + 4 : tag_pos + 6]
            length = struct.unpack_from("<H", stream, tag_pos + 6)[0]
            end = tag_pos + 8 + length
            if type_bytes != b"\x00\x0f" or end > len(stream):
                idx = tag_pos + 1
                continue
            if length < 2 or length > 65534 or length % 2:
                idx = tag_pos + 1
                continue
            payload = stream[tag_pos + 8 : end]
            text = payload.decode("utf-16le", errors="ignore").rstrip("\x00")
            if text:
                physical_offset = origins[tag_pos]
                yield physical_offset // 512, field_id, text, physical_offset
            idx = end


def _first_matching(values: list[str], pattern: re.Pattern[str]) -> str | None:
    for value in values:
        if pattern.match(value):
            return value
    return None


def _export_invoice_metadata(conn: sqlite3.Connection, tran_path: Path, data: bytes | None = None) -> None:
    if data is None:
        data = tran_path.read_bytes()
    fields_by_record: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    offsets_by_record: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    pages_by_record: dict[int, set[int]] = defaultdict(set)
    for page_no, field_id, text, absolute_offset in _iter_tranmgr_string_tlvs(data):
        if _page_word(data, page_no, 5) != 1:
            continue
        record_id = _page_word(data, page_no, 1)
        if not record_id:
            continue
        fields_by_record[record_id][field_id].append(text)
        offsets_by_record[record_id][field_id].append(absolute_offset)
        pages_by_record[record_id].add(page_no)

    voucher_by_group = {
        int(page_group_id): int(master_id)
        for master_id, page_group_id in conn.execute(
            """
            select master_id, page_group_id
            from voucher_header_candidates
            where page_group_id is not null
            """
        )
    }

    rows = []
    for record_id, fields in fields_by_record.items():
        fids = set(fields)
        accepted: dict[str, set[str]] = defaultdict(set)
        blocks_by_irn: dict[str, set[str]] = defaultdict(set)

        if 0x01F7 in fids:
            for irn in (value for value in fields.get(0x01F8, []) if IRN_RE.match(value)):
                accepted[irn].add("0x01f8")
                blocks_by_irn[irn].add("submission")
        if {0x000D, 0x0017}.issubset(fids):
            for irn in (value for value in fields.get(0x0019, []) if IRN_RE.match(value)):
                accepted[irn].add("0x0019")
                blocks_by_irn[irn].add("buyer")
        if 0x0007 in fids:
            for irn in (value for value in fields.get(0x0001, []) if IRN_RE.match(value)):
                accepted[irn].add("0x0001")
                blocks_by_irn[irn].add("transporter")

        if not accepted:
            continue

        conflict_irns = sorted(accepted)
        has_conflict = len(conflict_irns) > 1
        ack = _first_matching(fields.get(0x01F7, []), ACK_RE)
        jwt = _first_matching(fields.get(0x01F9, []), JWT_RE)
        buyer_name = fields.get(0x000D, [None])[0]
        buyer_address = fields.get(0x0017, [None])[0]
        vehicle_number = fields.get(0x0007, [None])[0]
        transport_mode = fields.get(0x0004, [None])[0]
        vehicle_category = fields.get(0x0008, [None])[0]
        date_0006 = _first_matching(fields.get(0x0006, []), DATE_TEXT_RE)
        date_0009 = _first_matching(fields.get(0x0009, []), DATE_TEXT_RE)
        evidence = {
            "pages": sorted(pages_by_record[record_id]),
            "offsets_by_field": {
                f"0x{field_id:04x}": offsets
                for field_id, offsets in sorted(offsets_by_record[record_id].items())
                if field_id in {0x0001, 0x0004, 0x0006, 0x0007, 0x0008, 0x0009, 0x000D, 0x0017, 0x0019, 0x01F7, 0x01F8, 0x01F9}
            },
        }
        for irn, source_fids in sorted(accepted.items()):
            rows.append(
                (
                    record_id,
                    voucher_by_group.get(record_id),
                    irn,
                    json.dumps(sorted(source_fids)),
                    json.dumps(sorted(blocks_by_irn[irn])),
                    ack,
                    jwt,
                    buyer_name,
                    buyer_address,
                    vehicle_number,
                    transport_mode,
                    vehicle_category,
                    date_0006,
                    date_0009,
                    json.dumps(conflict_irns),
                    1 if has_conflict else 0,
                    json.dumps(evidence),
                )
            )

    conn.executemany(
        """
        insert or replace into voucher_invoice_metadata
        (record_id, voucher_master_id, irn, source_fids_json,
         source_blocks_json, irn_ack_no, signed_jwt_payload, buyer_name,
         buyer_address, vehicle_number, transport_mode, vehicle_category,
         transporter_date_0006, transporter_date_0009, conflict_irns_json,
         has_conflict, evidence_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _export_einvoice_archive(conn: sqlite3.Connection, tran_path: Path, data: bytes | None = None) -> None:
    if data is None:
        data = tran_path.read_bytes()
    fields_by_archive: dict[tuple[int, int], dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    offsets_by_archive: dict[tuple[int, int], dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    pages_by_archive: dict[tuple[int, int], set[int]] = defaultdict(set)
    for page_no, field_id, text, absolute_offset in _iter_tranmgr_string_tlvs(data):
        view_code = _page_word(data, page_no, 5)
        if view_code < 3:
            continue
        record_id = _page_word(data, page_no, 1)
        if not record_id:
            continue
        key = (record_id, view_code)
        fields_by_archive[key][field_id].append(text)
        offsets_by_archive[key][field_id].append(absolute_offset)
        pages_by_archive[key].add(page_no)

    voucher_by_group = {
        int(page_group_id): int(master_id)
        for master_id, page_group_id in conn.execute(
            """
            select master_id, page_group_id
            from voucher_header_candidates
            where page_group_id is not null
            """
        )
    }

    rows = []
    for (record_id, view_code), fields in fields_by_archive.items():
        all_values = [
            value
            for values in fields.values()
            for value in values
        ]
        irns = sorted({value for value in all_values if IRN_RE.match(value)})
        ack_nos = sorted({value for value in all_values if ACK_RE.match(value)})
        jwt_payloads = sorted({value for value in all_values if JWT_RE.match(value)})
        if not (irns or ack_nos or jwt_payloads):
            continue
        eway_bill_nos = sorted({value for value in all_values if EWAY_BILL_RE.match(value)})
        page_nos = sorted(pages_by_archive[(record_id, view_code)])
        evidence = {
            "offsets_by_field": {
                f"0x{field_id:04x}": offsets
                for field_id, offsets in sorted(offsets_by_archive[(record_id, view_code)].items())
            },
            "payload_gate": "irn_or_ack_or_jwt",
        }
        rows.append(
            (
                record_id,
                view_code,
                voucher_by_group.get(record_id),
                page_nos[0],
                json.dumps(page_nos),
                json.dumps(irns),
                json.dumps(ack_nos),
                json.dumps(eway_bill_nos),
                json.dumps(jwt_payloads),
                json.dumps(
                    {
                        f"0x{field_id:04x}": values
                        for field_id, values in sorted(fields.items())
                    },
                    ensure_ascii=False,
                ),
                1 if irns else 0,
                1 if ack_nos else 0,
                1 if jwt_payloads else 0,
                1,
                json.dumps(evidence),
            )
        )

    conn.executemany(
        """
        insert or replace into voucher_einvoice_archive
        (record_id, archive_view_code, voucher_master_id, first_page_no,
         page_nos_json, irns_json, ack_nos_json, eway_bill_nos_json,
         jwt_payloads_json, field_strings_json, has_irn, has_ack, has_jwt,
         is_payload_archive, evidence_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _reorder_vouchers_within_date(rows: list[dict[str, int | str | None]], gap: int = 5000) -> list[dict[str, int | str | None]]:
    if len(rows) <= 2:
        return sorted(rows, key=lambda row: int(row["master_id"] or 0))
    ordered = sorted(rows, key=lambda row: int(row["master_id"] or 0))
    clusters = [[ordered[0]]]
    for row in ordered[1:]:
        previous_mid = int(clusters[-1][-1]["master_id"] or 0)
        current_mid = int(row["master_id"] or 0)
        if current_mid - previous_mid <= gap:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    if len(clusters) == 1:
        return ordered
    clusters.sort(key=lambda cluster: (len(cluster), int(cluster[-1]["master_id"] or 0)), reverse=True)
    return [row for cluster in clusters for row in cluster]


def _pair_voucher_types_from_status(conn: sqlite3.Connection) -> None:
    """Attach VchStatus voucher type ids using Claude's date/cluster pairing.

    Only keyed vouchers participate. Within each date, the bulk master-id
    cluster is paired first and legacy low-id outliers are appended.
    """
    voucher_rows = [
        {"master_id": int(master_id), "date_serial": int(date_serial)}
        for master_id, date_serial in conn.execute(
            """
            select master_id, voucher_date_serial
            from voucher_header_candidates
            where voucher_date_serial is not null
              and key_raw is not null
            """
        )
    ]
    slots = [
        (int(slot_seq), int(date_serial), int(voucher_type_master_id))
        for slot_seq, date_serial, voucher_type_master_id in conn.execute(
            """
            select slot_seq, date_serial, voucher_type_master_id
            from vch_status_slots
            where date_serial is not null
            order by slot_seq
            """
        )
    ]
    if not voucher_rows or not slots:
        return

    vouchers_by_date: dict[int, list[dict[str, int | str | None]]] = defaultdict(list)
    for row in voucher_rows:
        vouchers_by_date[int(row["date_serial"] or 0)].append(row)
    for date_serial in list(vouchers_by_date):
        vouchers_by_date[date_serial] = _reorder_vouchers_within_date(vouchers_by_date[date_serial])

    slots_by_date: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for slot_seq, date_serial, voucher_type_master_id in slots:
        slots_by_date[date_serial].append((slot_seq, voucher_type_master_id))

    updates = []
    for date_serial, rows in vouchers_by_date.items():
        date_slots = slots_by_date.get(date_serial, [])
        for row, (slot_seq, voucher_type_master_id) in zip(rows, date_slots):
            updates.append((voucher_type_master_id, slot_seq, "voucher_key_date_cluster", int(row["master_id"] or 0)))

    conn.executemany(
        """
        update voucher_header_candidates
        set paired_voucher_type_master_id = ?,
            paired_vch_status_slot_seq = ?,
            paired_voucher_type_confidence = ?
        where master_id = ?
        """,
        updates,
    )


def _export_company(conn: sqlite3.Connection, hits) -> None:
    company_hits = [h for h in hits if h.file_name == "Company.1800"]
    conn.executemany(
        """
        insert into company_strings (field_id, page_no, page_offset, text)
        values (?, ?, ?, ?)
        """,
        [(h.field_id, h.page_no, h.page_offset, h.text) for h in company_hits],
    )
    texts = [h.text for h in company_hits]
    guesses: dict[str, tuple[str, str]] = {}
    if texts:
        guesses["company_name"] = (texts[0], "first length-prefixed string in Company.1800")
    for text in texts:
        if _looks_like_gstin(text):
            guesses.setdefault("gstin", (text, "first GSTIN-shaped string in Company.1800"))
        if "@" in text and "." in text:
            guesses.setdefault("email", (text, "first email-shaped string in Company.1800"))
        if text.isdigit() and len(text) == 6:
            guesses.setdefault("pincode", (text, "first 6-digit string in Company.1800"))
        if text.lower() in {"tamil nadu", "india"}:
            guesses.setdefault(text.lower().replace(" ", "_"), (text, "literal geographic string"))
    conn.executemany(
        "insert or replace into company_guess (key, value, evidence) values (?, ?, ?)",
        [(key, value, evidence) for key, (value, evidence) in guesses.items()],
    )


def _export_manager_records(conn: sqlite3.Connection, manager_path: Path, hits, data: bytes | None = None) -> None:
    if data is None:
        data = manager_path.read_bytes()
    by_page = defaultdict(list)
    for hit in hits:
        if hit.file_name == "Manager.1800":
            by_page[hit.page_no].append(hit)
    rows = []
    by_master: dict[int, list[object]] = defaultdict(list)
    for page_no, page_hits in sorted(by_page.items()):
        header_words = _header_words(data, page_no)
        master_id = header_words[1] if len(header_words) > 1 else None
        if master_id:
            by_master[int(master_id)].extend(page_hits)
        strings = [
            {
                "field_id": h.field_id,
                "offset": h.offset,
                "page_offset": h.page_offset,
                "text": h.text,
            }
            for h in sorted(page_hits, key=lambda h: h.offset)
        ]
        fields_by_id: dict[int, list[object]] = defaultdict(list)
        for hit in sorted(page_hits, key=lambda h: h.offset):
            if hit.field_id is not None:
                fields_by_id[int(hit.field_id)].append(hit.text)
        typed_fields = pick_typed_fields(fields_by_id)
        company_guid = next((h.text for h in page_hits if h.field_id == 0x01FB or _looks_like_guid(h.text)), None)
        guid = _master_guid_guess(company_guid, master_id)
        name_hit = next((h for h in page_hits if h.field_id == 0x0002), None)
        if name_hit is None:
            name_hit = next((h for h in page_hits if not _looks_like_guid(h.text) and h.field_id not in {0x0069, 0x0063}), None)
        user_text = next((h.text for h in page_hits if h.field_id == 0x0069), None)
        rows.append(
            (
                page_no,
                master_id,
                header_words[0] if header_words else None,
                json.dumps(header_words),
                guid,
                _clean_name(name_hit.text) if name_hit else None,
                name_hit.field_id if name_hit else None,
                user_text,
                json.dumps({str(key): value for key, value in fields_by_id.items()}, ensure_ascii=False),
                json.dumps(typed_fields, ensure_ascii=False),
                typed_fields.get("state"),
                typed_fields.get("country"),
                typed_fields.get("pincode"),
                typed_fields.get("email"),
                typed_fields.get("phone"),
                typed_fields.get("pan"),
                typed_fields.get("gstin"),
                len(page_hits),
                json.dumps(strings, ensure_ascii=False),
            )
        )
    conn.executemany(
        """
        insert or replace into manager_records
        (page_no, master_id, page_marker, header_words_json, guid, name, name_field_id,
         user_text, fields_json, typed_fields_json, state_guess, country_guess,
         pincode_guess, email_guess, phone_guess, pan_guess, gstin_guess,
         text_count, all_strings_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )

    master_rows = []
    for master_id, master_hits in sorted(by_master.items()):
        ordered_hits = sorted(master_hits, key=lambda h: h.offset)
        pages = sorted({h.page_no for h in ordered_hits})
        strings = [
            {
                "field_id": h.field_id,
                "offset": h.offset,
                "page_offset": h.page_offset,
                "text": h.text,
            }
            for h in ordered_hits
        ]
        fields_by_id: dict[int, list[object]] = defaultdict(list)
        for hit in ordered_hits:
            if hit.field_id is not None:
                fields_by_id[int(hit.field_id)].append(hit.text)
        typed_fields = pick_typed_fields(fields_by_id)
        company_guid = next((h.text for h in ordered_hits if h.field_id == 0x01FB or _looks_like_guid(h.text)), None)
        guid = _master_guid_guess(company_guid, master_id)
        name_hit = next((h for h in ordered_hits if h.field_id == 0x0002), None)
        if name_hit is None:
            name_hit = next((h for h in ordered_hits if not _looks_like_guid(h.text) and h.field_id not in {0x0069, 0x0063}), None)
        user_text = next((h.text for h in ordered_hits if h.field_id == 0x0069), None)
        master_rows.append(
            (
                master_id,
                pages[0],
                len(pages),
                guid,
                _clean_name(name_hit.text) if name_hit else None,
                name_hit.field_id if name_hit else None,
                user_text,
                json.dumps({str(key): value for key, value in fields_by_id.items()}, ensure_ascii=False),
                json.dumps(typed_fields, ensure_ascii=False),
                typed_fields.get("state"),
                typed_fields.get("country"),
                typed_fields.get("pincode"),
                typed_fields.get("email"),
                typed_fields.get("phone"),
                typed_fields.get("pan"),
                typed_fields.get("gstin"),
                len(ordered_hits),
                json.dumps(strings, ensure_ascii=False),
            )
        )
    conn.executemany(
        """
        insert or replace into manager_master_records
        (master_id, page_start, page_count, guid, name, name_field_id, user_text,
         fields_json, typed_fields_json, state_guess, country_guess, pincode_guess,
         email_guess, phone_guess, pan_guess, gstin_guess, text_count, all_strings_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        master_rows,
    )


def _export_manager_master_kind_guesses(conn: sqlite3.Connection, manager_path: Path, data: bytes | None = None) -> None:
    manager_data = data if data is not None else manager_path.read_bytes()
    pages_by_master: dict[int, list[int]] = defaultdict(list)
    for master_id, page_no in conn.execute(
        """
        select master_id, page_no
        from manager_records
        where master_id is not null
        order by page_no
        """
    ):
        pages_by_master[int(master_id)].append(int(page_no))
    voucher_type_ids = {
        int(row[0])
        for row in conn.execute(
            """
            select distinct voucher_type_master_id
            from vch_status_slots
            where voucher_type_master_id is not null
            """
        )
    }
    rows = []
    for master_id, page_start, page_count, fields_json in conn.execute(
        """
        select master_id, page_start, page_count, fields_json
        from manager_master_records
        """
    ):
        mid = int(master_id)
        fields = {int(key) for key in json.loads(fields_json or "{}").keys()}
        fallback_kind, fallback_confidence, fallback_evidence = _field_anchor_kind(
            mid,
            fields,
            voucher_type_ids,
        )
        pages = pages_by_master.get(mid)
        if pages:
            record_stream = _manager_record_stream_for_pages(manager_data, pages)
        else:
            record_stream = _manager_record_stream(manager_data, int(page_start), int(page_count))
        raw_kind_codes = _extract_master_kind_codes(record_stream)
        mapped_kind_codes = Counter(
            {
                code: count
                for code, count in raw_kind_codes.items()
                if code in MASTER_KIND_CODES
            }
        )
        evidence: dict[str, object] = {
            "field_ids": sorted(fields),
            "field_anchor_evidence": fallback_evidence,
        }

        kind = fallback_kind
        confidence = fallback_confidence
        if raw_kind_codes:
            evidence["raw_0x01f7_kind_codes"] = dict(sorted(raw_kind_codes.items()))

        if mapped_kind_codes:
            top_code, top_count = mapped_kind_codes.most_common(1)[0]
            tied_codes = [
                code
                for code, count in mapped_kind_codes.items()
                if count == top_count
            ]
            chosen_code = top_code
            if len(tied_codes) > 1 and fallback_kind != "unknown":
                for code in tied_codes:
                    if MASTER_KIND_CODES[code] == fallback_kind:
                        chosen_code = code
                        break
            kind = MASTER_KIND_CODES[chosen_code]
            confidence = "high" if len(tied_codes) == 1 or MASTER_KIND_CODES[chosen_code] == fallback_kind else "medium"
            evidence["classifier"] = "manager_0x01f7_kind_code"
            evidence["mapped_kind_codes"] = {
                f"0x{code:04x}": {
                    "kind": MASTER_KIND_CODES[code],
                    "count": count,
                }
                for code, count in sorted(mapped_kind_codes.items())
            }
            evidence["chosen_kind_code"] = f"0x{chosen_code:04x}"
            if len(tied_codes) > 1:
                evidence["tie_kind_codes"] = [f"0x{code:04x}" for code in sorted(tied_codes)]
        elif fallback_evidence:
            evidence["classifier"] = "field_anchor_fallback"
        else:
            evidence["classifier"] = "unknown"

        rows.append(
            (
                mid,
                kind,
                confidence,
                json.dumps(evidence, ensure_ascii=False),
            )
        )

    conn.executemany(
        """
        insert or replace into manager_master_kind_guesses
        (master_id, kind_guess, confidence, evidence_json)
        values (?, ?, ?, ?)
        """,
        rows,
    )


def _load_json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _company_guid_from_masters(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        select substr(guid, 1, 36)
        from manager_master_records
        where guid is not null
          and length(guid) >= 45
        limit 1
        """
    ).fetchone()
    return row[0] if row and row[0] else None


def _export_typed_company(conn: sqlite3.Connection) -> None:
    guesses = {
        key: {"value": value, "evidence": evidence}
        for key, value, evidence in conn.execute(
            "select key, value, evidence from company_guess"
        )
    }
    value_by_key = {key: str(row["value"]) for key, row in guesses.items()}
    conn.execute("delete from companies")
    conn.execute(
        """
        insert or replace into companies
        (company_id, company_guid, name, gstin, state_name, country_name,
         pincode, email, evidence_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            _company_guid_from_masters(conn),
            value_by_key.get("company_name"),
            value_by_key.get("gstin"),
            value_by_key.get("state") or value_by_key.get("tamil_nadu"),
            value_by_key.get("country") or value_by_key.get("india"),
            value_by_key.get("pincode"),
            value_by_key.get("email"),
            json.dumps(guesses, ensure_ascii=False),
        ),
    )


def _typed_master_base_rows(
    conn: sqlite3.Connection,
    kind: str,
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for (
        master_id,
        guid,
        name,
        name_field_id,
        page_start,
        page_count,
        fields_json,
        typed_fields_json,
        all_strings_json,
        kind_guess,
        confidence,
        evidence_json,
    ) in conn.execute(
        """
        select m.master_id, m.guid, m.name, m.name_field_id, m.page_start,
               m.page_count, m.fields_json, m.typed_fields_json,
               m.all_strings_json, k.kind_guess, k.confidence, k.evidence_json
        from manager_master_records m
        join manager_master_kind_guesses k
          on k.master_id = m.master_id
        where k.kind_guess = ?
          and k.confidence in ('high', 'medium')
        order by m.master_id
        """,
        (kind,),
    ):
        typed_fields = _load_json_object(typed_fields_json)
        rows.append(
            (
                master_id,
                guid,
                name,
                _clean_name(name) if name else None,
                name_field_id,
                kind_guess,
                confidence,
                evidence_json,
                page_start,
                page_count,
                typed_fields.get("created_by"),
                typed_fields.get("mailing_name"),
                typed_fields.get("address"),
                typed_fields.get("state"),
                typed_fields.get("country"),
                typed_fields.get("pincode"),
                typed_fields.get("email"),
                typed_fields.get("phone"),
                typed_fields.get("pan"),
                typed_fields.get("gstin"),
                fields_json,
                typed_fields_json,
                all_strings_json,
            )
        )
    return rows


def _export_typed_master_tables(conn: sqlite3.Connection) -> None:
    _export_typed_company(conn)
    generic_columns = """
        master_id, guid, name, normalized_name, name_field_id, source_kind,
        kind_confidence, kind_evidence_json, page_start, page_count, created_by,
        mailing_name, address, state_name, country_name, pincode, email, phone,
        pan, gstin, fields_json, typed_fields_json, all_strings_json
    """
    generic_placeholders = ", ".join("?" for _ in range(23))
    for table_name, kind in TYPED_MASTER_TABLES.items():
        conn.execute(f"delete from {table_name}")
        rows = _typed_master_base_rows(conn, kind)
        conn.executemany(
            f"""
            insert or replace into {table_name}
            ({generic_columns})
            values ({generic_placeholders})
            """,
            rows,
        )

    status_type_ids = {
        int(row[0])
        for row in conn.execute(
            """
            select distinct voucher_type_master_id
            from vch_status_slots
            where voucher_type_master_id is not null
            """
        )
    }
    exportable_type_ids = {
        int(row[0])
        for row in conn.execute(
            """
            select distinct paired_voucher_type_master_id
            from voucher_header_candidates
            where paired_voucher_type_master_id is not null
              and key_raw is not null
              and coalesce(base_type_code, -1) != 31
            """
        )
    }
    conn.execute("delete from voucher_types")
    voucher_type_rows = []
    for row in _typed_master_base_rows(conn, "voucher_type"):
        master_id = int(row[0])
        is_status = 1 if master_id in status_type_ids else 0
        is_exportable = 1 if master_id in exportable_type_ids else 0
        if is_exportable:
            export_policy = "exportable_observed"
        elif is_status:
            export_policy = "status_observed"
        else:
            export_policy = "internal_candidate"
        voucher_type_rows.append(
            row[:20]
            + (
                is_status,
                is_exportable,
                export_policy,
            )
            + row[20:]
        )
    conn.executemany(
        """
        insert or replace into voucher_types
        (master_id, guid, name, normalized_name, name_field_id, source_kind,
         kind_confidence, kind_evidence_json, page_start, page_count, created_by,
         mailing_name, address, state_name, country_name, pincode, email, phone,
         pan, gstin, is_referenced_by_vch_status, is_used_by_exportable_voucher,
         export_policy, fields_json, typed_fields_json, all_strings_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        voucher_type_rows,
    )


def _scaled_value(value: int) -> float:
    return value / 100000.0


def _fits_sqlite_i64(value: int | None) -> bool:
    return value is None or SQLITE_I64_MIN <= value <= SQLITE_I64_MAX


def _export_inventory_candidates(conn: sqlite3.Connection, tran_path: Path, data: bytes | None = None) -> None:
    stock_ids = {
        int(row[0])
        for row in conn.execute(
            """
            select master_id
            from manager_master_kind_guesses
            where kind_guess = 'stock_item'
              and confidence = 'high'
            """
        )
    }
    ledger_ids = {
        int(row[0])
        for row in conn.execute(
            """
            select master_id
            from manager_master_kind_guesses
            where kind_guess = 'ledger'
              and confidence = 'high'
            """
        )
    }
    unit_ids = {
        int(row[0])
        for row in conn.execute(
            """
            select master_id
            from manager_master_kind_guesses
            where kind_guess = 'unit'
              and confidence = 'high'
            """
        )
    }
    if not stock_ids:
        return

    group_to_mid = {
        int(group_id): int(master_id)
        for master_id, group_id in conn.execute(
            """
            select master_id, page_group_id
            from voucher_header_candidates
            where page_group_id is not null
              and key_raw is not null
              and base_type_code != 31
            """
        )
    }
    if not group_to_mid:
        return

    master_names = {
        int(master_id): name
        for master_id, name in conn.execute(
            """
            select master_id, name
            from manager_master_records
            where name is not null
            """
        )
    }
    voucher_type_by_mid: dict[int, str | None] = {}
    voucher_type_master_by_mid: dict[int, int | None] = {}
    for master_id, voucher_type_master_id, voucher_type_name in conn.execute(
        """
        select v.master_id, v.paired_voucher_type_master_id, m.name
        from voucher_header_candidates v
        left join manager_master_records m
          on m.master_id = v.paired_voucher_type_master_id
        where v.key_raw is not null
        """
    ):
        voucher_type_by_mid[int(master_id)] = voucher_type_name
        voucher_type_master_by_mid[int(master_id)] = (
            int(voucher_type_master_id) if voucher_type_master_id is not None else None
        )

    rows = build_inventory_candidates(
        data if data is not None else tran_path.read_bytes(),
        group_to_mid,
        voucher_type_by_mid,
        stock_ids,
        ledger_ids,
        unit_ids,
    )
    insert_rows = []
    for run in rows:
        if not (
            _fits_sqlite_i64(run.quantity_scaled)
            and _fits_sqlite_i64(run.rate_scaled)
            and _fits_sqlite_i64(run.amount_scaled)
        ):
            continue
        insert_rows.append(
            (
                run.voucher_master_id,
                voucher_type_master_by_mid.get(run.voucher_master_id),
                voucher_type_by_mid.get(run.voucher_master_id),
                run.stock_item_id,
                master_names.get(run.stock_item_id),
                run.ledger_id,
                master_names.get(run.ledger_id) if run.ledger_id is not None else None,
                run.unit_id,
                master_names.get(run.unit_id) if run.unit_id is not None else None,
                run.quantity_scaled,
                run.rate_scaled,
                run.amount_scaled,
                _scaled_value(run.quantity_scaled),
                _scaled_value(run.rate_scaled),
                _scaled_value(run.amount_scaled),
                run.source,
                run.confidence,
                run.page_no,
                run.page_offset,
                json.dumps({"source": run.source, "page_no": run.page_no, "page_offset": run.page_offset}),
            )
        )
    conn.executemany(
        """
        insert into voucher_inventory_entry_candidates
        (voucher_master_id, voucher_type_master_id, voucher_type_name,
         stock_item_master_id, stock_item_name, ledger_master_id, ledger_name,
         unit_master_id, unit_name, quantity_scaled, rate_scaled, amount_scaled,
         quantity_value, rate_value, amount_value, source, confidence,
         page_no, page_offset, evidence_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        insert_rows,
    )


def _export_ledger_candidates(conn: sqlite3.Connection, tran_path: Path, data: bytes | None = None) -> None:
    ledger_rows = [
        (int(master_id), name)
        for master_id, name in conn.execute(
            """
            select m.master_id, m.name
            from manager_master_records m
            join manager_master_kind_guesses k
              on k.master_id = m.master_id
            where k.kind_guess = 'ledger'
              and k.confidence = 'high'
            """
        )
    ]
    ledger_ids = {master_id for master_id, _name in ledger_rows}
    if not ledger_ids:
        return
    ledger_names = {
        master_id: name
        for master_id, name in ledger_rows
        if name is not None
    }
    vouchers = [
        VoucherLedgerContext(
            voucher_master_id=int(master_id),
            page_group_id=int(page_group_id),
            voucher_type_master_id=(
                int(voucher_type_master_id) if voucher_type_master_id is not None else None
            ),
            voucher_type_name=voucher_type_name,
        )
        for master_id, page_group_id, voucher_type_master_id, voucher_type_name in conn.execute(
            """
            select v.master_id, v.page_group_id, v.paired_voucher_type_master_id, m.name
            from voucher_header_candidates v
            left join manager_master_records m
              on m.master_id = v.paired_voucher_type_master_id
            where v.page_group_id is not null
              and v.key_raw is not null
              and v.base_type_code != 31
            order by v.master_id
            """
        )
    ]
    if not vouchers:
        return

    insert_sql = """
    insert into voucher_ledger_entry_candidates
    (voucher_master_id, voucher_type_master_id, voucher_type_name,
     ledger_master_id, ledger_name, amount_scaled, amount_value, source,
     confidence, is_deemed_positive, sign_byte, page_no, page_offset,
     evidence_json)
    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    batch = []
    for row in iter_ledger_candidates(
        data if data is not None else tran_path.read_bytes(),
        vouchers,
        ledger_ids,
        ledger_names,
    ):
        batch.append(
            (
                row.voucher_master_id,
                row.voucher_type_master_id,
                row.voucher_type_name,
                row.ledger_master_id,
                row.ledger_name,
                row.amount_scaled,
                ledger_amount_value(row.amount_scaled),
                row.source,
                row.confidence,
                1 if row.is_deemed_positive is True else 0 if row.is_deemed_positive is False else None,
                row.sign_byte,
                row.origin.page_no,
                row.origin.page_offset,
                json.dumps(row.evidence, ensure_ascii=False),
            )
        )
        if len(batch) >= 10000:
            conn.executemany(insert_sql, batch)
            batch.clear()
    if batch:
        conn.executemany(insert_sql, batch)


def _export_best_ledger_entries(conn: sqlite3.Connection) -> None:
    conn.execute("delete from voucher_ledger_entries_1800")
    conn.execute(
        """
        create index if not exists idx_voucher_ledger_candidates_dedupe
        on voucher_ledger_entry_candidates
        (confidence, voucher_master_id, ledger_master_id, amount_scaled)
        """
    )
    conn.execute(
        """
        insert into voucher_ledger_entries_1800
        (voucher_master_id, voucher_type_master_id, voucher_type_name,
         ledger_master_id, ledger_name, amount_scaled, amount_value, sources,
         confidence, evidence_count, first_page_no, first_page_offset)
        with grouped as (
          select
            voucher_master_id,
            min(voucher_type_master_id) as voucher_type_master_id,
            min(voucher_type_name) as voucher_type_name,
            ledger_master_id,
            min(ledger_name) as ledger_name,
            amount_scaled,
            amount_scaled / 100000.0 as amount_value,
            group_concat(distinct source) as sources,
            count(*) as evidence_count,
            min(page_no) as first_page_no,
            min(page_offset) as first_page_offset,
            max(case when source = '52d3_anchor' then 1 else 0 end) as has_52d3,
            max(case when source = 'subrecord_0002' then 1 else 0 end) as has_0002_strict,
            max(case when source = 'subrecord_0002_wide' then 1 else 0 end) as has_0002_wide,
            max(case when source = 'subrecord_0006' then 1 else 0 end) as has_0006,
            max(case when source = 'subrecord_2713' then 1 else 0 end) as has_2713
          from voucher_ledger_entry_candidates
          where confidence in ('high', 'medium')
          group by
            voucher_master_id,
            ledger_master_id,
            amount_scaled
        ),
        typed as (
          select *,
            case
              when instr(lower(coalesce(voucher_type_name, '')), 'stock journal') > 0 then 0
              when instr(lower(coalesce(voucher_type_name, '')), 'receipt note') > 0 then 0
              when instr(lower(coalesce(voucher_type_name, '')), 'purchase order') > 0 then 0
              when instr(lower(coalesce(voucher_type_name, '')), 'job order') > 0 then 0
              when instr(lower(coalesce(voucher_type_name, '')), 'delivery challan') > 0 then 0
              when instr(lower(coalesce(voucher_type_name, '')), 'payt') > 0 then 1
              when instr(lower(coalesce(voucher_type_name, '')), 'payment') > 0 then 1
              when instr(lower(coalesce(voucher_type_name, '')), 'receipt') > 0 then 1
              when instr(lower(coalesce(voucher_type_name, '')), 'receipts') > 0 then 1
              when instr(lower(coalesce(voucher_type_name, '')), 'receipt cash') > 0 then 1
              when instr(lower(coalesce(voucher_type_name, '')), 'journal') > 0 then 1
              when instr(lower(coalesce(voucher_type_name, '')), 'contra') > 0 then 1
              else 0
            end as is_accounting_voucher_type,
            case
              when instr(lower(coalesce(voucher_type_name, '')), 'cross journal') > 0 then 1
              else 0
            end as is_cross_journal_type,
            case
              when has_0002_strict = 1 or has_0002_wide = 1 then 1
              else 0
            end as has_0002
          from grouped
        ),
        gated as (
          select *,
            case
              when has_0006 = 1 and (has_52d3 = 1 or has_0002 = 1 or has_2713 = 1) then 'high'
              when has_52d3 = 1 and has_0002 = 1 and has_0006 = 0 and has_2713 = 0 then 'high'
              when has_0002 = 1 and has_52d3 = 0 and has_0006 = 0 and has_2713 = 0
                   and is_accounting_voucher_type = 1
                   and (has_0002_strict = 1 or is_cross_journal_type = 0) then 'high'
              when has_52d3 = 1 and has_0002 = 1 and has_0006 = 0 and has_2713 = 1
                   and is_accounting_voucher_type = 1 then 'high'
              when has_0002 = 1 and has_2713 = 1 and has_52d3 = 0 and has_0006 = 0
                   and is_accounting_voucher_type = 1
                   and (has_0002_strict = 1 or is_cross_journal_type = 0) then 'high'
              else null
            end as gated_confidence
          from typed
        )
        select
          voucher_master_id,
          voucher_type_master_id,
          voucher_type_name,
          ledger_master_id,
          ledger_name,
          amount_scaled,
          amount_value,
          sources,
          gated_confidence,
          evidence_count,
          first_page_no,
          first_page_offset
        from gated
        where gated_confidence is not null
        """
    )


def _export_best_inventory_entries(conn: sqlite3.Connection) -> None:
    """Collapse high-confidence candidates into the user-facing inventory table.

    The broader candidate table intentionally keeps medium-confidence columnar
    and Purchase Monthly single-ledger fallback rows. This table keeps only
    rows that have row-local high-confidence evidence.
    """
    conn.execute("delete from voucher_inventory_entries_1800")
    conn.execute(
        """
        insert into voucher_inventory_entries_1800
        (voucher_master_id, voucher_type_master_id, voucher_type_name,
         stock_item_master_id, stock_item_name, ledger_master_id, ledger_name,
         unit_master_id, unit_name, quantity_scaled, rate_scaled, amount_scaled,
         quantity_value, rate_value, amount_value, sources, confidence,
         evidence_count, first_page_no, first_page_offset)
        select
          voucher_master_id,
          voucher_type_master_id,
          voucher_type_name,
          stock_item_master_id,
          stock_item_name,
          ledger_master_id,
          ledger_name,
          min(unit_master_id),
          min(unit_name),
          quantity_scaled,
          rate_scaled,
          amount_scaled,
          quantity_value,
          rate_value,
          amount_value,
          group_concat(distinct source),
          case
            when min(confidence) = 'high' and max(confidence) = 'high' then 'high'
            else 'medium'
          end,
          count(*),
          min(page_no),
          min(page_offset)
        from voucher_inventory_entry_candidates
        where confidence = 'high'
        group by
          voucher_master_id,
          voucher_type_master_id,
          voucher_type_name,
          stock_item_master_id,
          stock_item_name,
          ledger_master_id,
          ledger_name,
          quantity_scaled,
          rate_scaled,
          amount_scaled,
          quantity_value,
          rate_value,
          amount_value
        """
    )


INVENTORY_VOUCHER_TYPES = {
    "31-Puchase(Monthly)",
    "61 Sales",
    "Conversion Stock Journal",
    "Purchase",
    "Sales",
    "Stock Journal",
}


def _export_decode_audit(conn: sqlite3.Connection) -> None:
    conn.execute("delete from voucher_decode_audit")
    conn.execute("delete from xml_repair_queue")

    candidate_stats = {
        int(row[0]): {
            "rows": int(row[1]),
            "unique_tuples": int(row[2]),
            "medium_rows": int(row[3]),
            "null_ledger_rows": int(row[4]),
        }
        for row in conn.execute(
            """
            select
              voucher_master_id,
              count(*),
              count(distinct stock_item_master_id || '|' ||
                     coalesce(ledger_master_id, '') || '|' ||
                     coalesce(unit_master_id, '') || '|' ||
                     quantity_scaled || '|' || rate_scaled || '|' || amount_scaled),
              sum(case when confidence != 'high' then 1 else 0 end),
              sum(case when ledger_master_id is null then 1 else 0 end)
            from voucher_inventory_entry_candidates
            group by voucher_master_id
            """
        )
    }
    best_stats = {
        int(row[0]): {
            "rows": int(row[1]),
            "null_ledger_rows": int(row[2]),
        }
        for row in conn.execute(
            """
            select
              voucher_master_id,
              count(*),
              sum(case when ledger_master_id is null then 1 else 0 end)
            from voucher_inventory_entries_1800
            group by voucher_master_id
            """
        )
    }

    audit_rows = []
    queue_rows = []
    for (
        master_id,
        guid_guess,
        date_guess,
        voucher_number,
        voucher_type_master_id,
        voucher_type_name,
    ) in conn.execute(
        """
        select v.master_id, v.guid_guess, v.voucher_date_guess, v.voucher_number_guess,
               v.paired_voucher_type_master_id, m.name
        from voucher_header_candidates v
        left join manager_master_records m
          on m.master_id = v.paired_voucher_type_master_id
        where v.key_raw is not null
        """
    ):
        mid = int(master_id)
        candidate = candidate_stats.get(
            mid,
            {"rows": 0, "unique_tuples": 0, "medium_rows": 0, "null_ledger_rows": 0},
        )
        best = best_stats.get(mid, {"rows": 0, "null_ledger_rows": 0})
        issue_codes: list[str] = []

        if voucher_type_master_id is None or voucher_type_name is None:
            issue_codes.append("voucher_type_unpaired")

        is_inventory_type = voucher_type_name in INVENTORY_VOUCHER_TYPES
        if is_inventory_type and best["rows"] == 0:
            issue_codes.append("inventory_expected_but_no_high_confidence_rows")
        if candidate["rows"] > best["rows"]:
            issue_codes.append("inventory_has_extra_candidates")
        if candidate["medium_rows"] > 0:
            issue_codes.append("inventory_has_medium_confidence_candidates")
        if candidate["unique_tuples"] > best["rows"] and is_inventory_type:
            issue_codes.append("inventory_candidate_tuple_ambiguity")
        if best["null_ledger_rows"] > 0 and voucher_type_name not in {
            "Conversion Stock Journal",
            "Stock Journal",
        }:
            issue_codes.append("inventory_missing_ledger_link")
        if voucher_type_name not in INVENTORY_VOUCHER_TYPES and candidate["rows"] > 0:
            issue_codes.append("inventory_rows_on_unsupported_voucher_type")

        needs_xml_repair = any(
            code
            in {
                "voucher_type_unpaired",
                "inventory_expected_but_no_high_confidence_rows",
                "inventory_missing_ledger_link",
                "inventory_rows_on_unsupported_voucher_type",
            }
            for code in issue_codes
        )
        needs_xml_review = needs_xml_repair or any(
            code
            in {
                "inventory_has_extra_candidates",
                "inventory_has_medium_confidence_candidates",
                "inventory_candidate_tuple_ambiguity",
            }
            for code in issue_codes
        )
        priority = 0
        if needs_xml_repair:
            priority += 100
        if is_inventory_type:
            priority += 50
        priority += min(candidate["medium_rows"], 25)
        priority += min(max(candidate["unique_tuples"] - best["rows"], 0), 25)

        evidence = {
            "confidence_definition": "internal_evidence_rule_not_truth",
            "best_inventory_rows": best["rows"],
            "candidate_inventory_rows": candidate["rows"],
            "candidate_unique_tuples": candidate["unique_tuples"],
            "medium_candidate_rows": candidate["medium_rows"],
            "best_null_ledger_rows": best["null_ledger_rows"],
            "calibration_note": "rules were calibrated against Rosetta/live snapshots, but production rows are not proven without XML or balance checks",
        }
        issue_json = json.dumps(issue_codes)
        audit_rows.append(
            (
                mid,
                guid_guess,
                date_guess,
                voucher_number,
                voucher_type_master_id,
                voucher_type_name,
                best["rows"],
                candidate["rows"],
                candidate["unique_tuples"],
                candidate["medium_rows"],
                best["null_ledger_rows"],
                issue_json,
                1 if needs_xml_review else 0,
                1 if needs_xml_repair else 0,
                priority,
                json.dumps(evidence),
            )
        )
        if needs_xml_repair:
            request_hint = {
                "preferred_lookup": "MASTERID",
                "master_id": mid,
                "guid_guess": guid_guess,
                "voucher_date_guess": date_guess,
                "voucher_number_guess": voucher_number,
            }
            queue_rows.append(
                (
                    mid,
                    guid_guess,
                    date_guess,
                    voucher_number,
                    voucher_type_name,
                    priority,
                    issue_json,
                    json.dumps(request_hint),
                )
            )

    conn.executemany(
        """
        insert or replace into voucher_decode_audit
        (voucher_master_id, guid_guess, voucher_date_guess, voucher_number_guess,
         voucher_type_master_id, voucher_type_name, inventory_best_rows,
         inventory_candidate_rows, inventory_candidate_unique_tuples,
         inventory_medium_candidate_rows, inventory_best_null_ledger_rows,
         issue_codes_json, needs_xml_review, needs_xml_repair, repair_priority,
         evidence_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        audit_rows,
    )
    conn.executemany(
        """
        insert into xml_repair_queue
        (voucher_master_id, guid_guess, voucher_date_guess, voucher_number_guess,
         voucher_type_name, repair_priority, reason_codes_json, request_hint_json)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        queue_rows,
    )


def _export_voucher_text_pages(conn: sqlite3.Connection, tran_path: Path, hits, data: bytes | None = None) -> None:
    if data is None:
        data = tran_path.read_bytes()
    by_page = defaultdict(list)
    for hit in hits:
        if hit.file_name == "TranMgr.1800":
            by_page[hit.page_no].append(hit)
    rows = []
    for page_no, page_hits in sorted(by_page.items()):
        ordered = sorted(page_hits, key=lambda h: h.offset)
        strings = [
            {
                "field_id": h.field_id,
                "offset": h.offset,
                "page_offset": h.page_offset,
                "text": h.text,
            }
            for h in ordered
        ]
        gstin = next((h.text for h in ordered if _looks_like_gstin(h.text)), None)
        party = next(
            (
                h.text
                for h in ordered
                if not _looks_like_gstin(h.text)
                and len(h.text) >= 8
                and any(ch.isalpha() for ch in h.text)
                and h.text.lower() not in {"tamil nadu", "regular", "india", "default"}
            ),
            None,
        )
        narration_or_address = next(
            (
                h.text
                for h in ordered
                if len(h.text) >= 20 and h.text != party and not _looks_like_gstin(h.text)
            ),
            None,
        )
        header_words = _header_words(data, page_no)
        rows.append(
            (
                page_no,
                header_words[0] if header_words else None,
                json.dumps(header_words),
                len(page_hits),
                party,
                gstin,
                narration_or_address,
                json.dumps(strings, ensure_ascii=False),
            )
        )
    conn.executemany(
        """
        insert or replace into voucher_text_pages
        (page_no, page_marker, header_words_json, text_count, party_or_name, gstin,
         narration_or_address, all_strings_json)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


HIGH_VALUE_FILES = {
    "Company.1800", "CmpSave.1800", "Manager.1800", "TranMgr.1800",
    "LinkMgr.1800", "Aggr.1800", "CfgRept.1800",
}


def export_decoded_sqlite(
    company_dir: Path,
    out_path: Path,
    progress: "Callable[[int, int, str, str], None] | None" = None,
) -> None:
    """Decode all .1800 files in `company_dir` into a SQLite at `out_path`.

    `progress(stage_index, total_stages, name, detail)` is called at the start
    of each major stage so callers can render progress + measure timings.
    Stage names are stable strings safe to surface to users.

    Bytes for each file are read at most once and threaded through every
    consumer; old code re-read each file 2-9 times on disk + redundantly
    parsed in Python. Eliminates the dominant non-CPU cost.
    """
    def _report(idx: int, total: int, name: str, detail: str = "") -> None:
        if progress is not None:
            try:
                progress(idx, total, name, detail)
            except Exception:
                pass

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    files = discover_files(company_dir)
    file_map = {f.name: f for f in files}

    # Conditional stage list — count only the stages that will actually run
    # (e.g. _export_voucher_text_pages skips if TranMgr.1800 is missing).
    stages: list[str] = ["init"]
    high_value_in_dir = [f for f in files if f.name in HIGH_VALUE_FILES]
    for f in high_value_in_dir:
        stages.append(f"raw_tlvs+field_strings:{f.name}")
    stages.append("export_company")
    if "Manager.1800" in file_map:
        stages.append("manager_records")
    if "TranMgr.1800" in file_map:
        stages.extend(["tranmgr_voucher_text_pages", "tranmgr_compact_fields", "tranmgr_extended_numerics"])
    if "VchStatus.1800" in file_map:
        stages.extend(["vch_status_slots", "pair_voucher_types"])
    if "Manager.1800" in file_map:
        stages.extend(["manager_master_kind_guesses", "typed_master_tables"])
    if "StatStatus.1800" in file_map:
        stages.append("statutory_status")
    if "LinkMgr.1800" in file_map:
        stages.append("linkmgr_allocation_pages")
    if "TranMgr.1800" in file_map:
        stages.extend([
            "inventory_candidates", "best_inventory",
            "ledger_candidates", "best_ledger",
            "invoice_metadata", "einvoice_archive",
            "decode_audit",
        ])
    total = len(stages)

    # Read each high-value file once. ~1 GB peak for a heavy company; freed
    # when this dict goes out of scope at function return.
    file_bytes: dict[str, bytes] = {}
    stage_idx = 0
    _report(stage_idx, total, "init", "reading files + computing hashes")
    stage_idx += 1
    for f in high_value_in_dir:
        file_bytes[f.name] = f.path.read_bytes()

    with sqlite3.connect(out_path) as conn:
        conn.executescript(DECODED_SCHEMA)
        conn.executemany(
            """
            insert or replace into source_files (file_name, path, role, size, sha256)
            values (?, ?, ?, ?, ?)
            """,
            [(f.name, str(f.path), f.role, f.size, sha256_file(f.path)) for f in files],
        )
        all_hits = []
        for data_file in high_value_in_dir:
            _report(stage_idx, total, f"raw_tlvs+field_strings:{data_file.name}", "")
            stage_idx += 1
            file_data = file_bytes[data_file.name]
            _insert_raw_tlvs(conn, data_file.path, data=file_data)
            hits = extract_tagged_field_strings(data_file.path, min_chars=1, data=file_data)
            all_hits.extend(hits)
            _insert_field_strings(conn, data_file.name, hits)
        _report(stage_idx, total, "export_company", "")
        stage_idx += 1
        _export_company(conn, all_hits)
        if "Manager.1800" in file_map:
            _report(stage_idx, total, "manager_records", "")
            stage_idx += 1
            _export_manager_records(conn, file_map["Manager.1800"].path, all_hits, data=file_bytes.get("Manager.1800"))
        if "TranMgr.1800" in file_map:
            tran_path = file_map["TranMgr.1800"].path
            tran_data = file_bytes.get("TranMgr.1800")
            _report(stage_idx, total, "tranmgr_voucher_text_pages", "")
            stage_idx += 1
            _export_voucher_text_pages(conn, tran_path, all_hits, data=tran_data)
            _report(stage_idx, total, "tranmgr_compact_fields", "")
            stage_idx += 1
            _insert_tranmgr_compact_fields(conn, tran_path, data=tran_data)
            _report(stage_idx, total, "tranmgr_extended_numerics", "")
            stage_idx += 1
            _insert_extended_numeric_fields(conn, tran_path, data=tran_data)
        if "VchStatus.1800" in file_map:
            _report(stage_idx, total, "vch_status_slots", "")
            stage_idx += 1
            _insert_vch_status_slots(conn, file_map["VchStatus.1800"].path)
            _report(stage_idx, total, "pair_voucher_types", "")
            stage_idx += 1
            _pair_voucher_types_from_status(conn)
        if "Manager.1800" in file_map:
            _report(stage_idx, total, "manager_master_kind_guesses", "")
            stage_idx += 1
            _export_manager_master_kind_guesses(conn, file_map["Manager.1800"].path, data=file_bytes.get("Manager.1800"))
            _report(stage_idx, total, "typed_master_tables", "")
            stage_idx += 1
            _export_typed_master_tables(conn)
        if "StatStatus.1800" in file_map:
            _report(stage_idx, total, "statutory_status", "")
            stage_idx += 1
            _export_statutory_status(conn, file_map["StatStatus.1800"].path)
        if "LinkMgr.1800" in file_map:
            _report(stage_idx, total, "linkmgr_allocation_pages", "")
            stage_idx += 1
            _export_linkmgr_allocation_pages(conn, file_map["LinkMgr.1800"].path, data=file_bytes.get("LinkMgr.1800"))
        if "TranMgr.1800" in file_map:
            tran_path = file_map["TranMgr.1800"].path
            tran_data = file_bytes.get("TranMgr.1800")
            _report(stage_idx, total, "inventory_candidates", "")
            stage_idx += 1
            _export_inventory_candidates(conn, tran_path, data=tran_data)
            _report(stage_idx, total, "best_inventory", "")
            stage_idx += 1
            _export_best_inventory_entries(conn)
            _report(stage_idx, total, "ledger_candidates", "")
            stage_idx += 1
            _export_ledger_candidates(conn, tran_path, data=tran_data)
            _report(stage_idx, total, "best_ledger", "")
            stage_idx += 1
            _export_best_ledger_entries(conn)
            _report(stage_idx, total, "invoice_metadata", "")
            stage_idx += 1
            _export_invoice_metadata(conn, tran_path, data=tran_data)
            _report(stage_idx, total, "einvoice_archive", "")
            stage_idx += 1
            _export_einvoice_archive(conn, tran_path, data=tran_data)
            _report(stage_idx, total, "decode_audit", "")
            stage_idx += 1
            _export_decode_audit(conn)
        conn.commit()

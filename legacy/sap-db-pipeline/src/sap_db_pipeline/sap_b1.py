from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MasterTable:
    record_type: str
    table_name: str
    key_column: str
    name_column: str | None = None


@dataclass(frozen=True)
class DocumentFamily:
    name: str
    header_table: str
    line_table: str | None
    date_column: str
    key_column: str = "DocEntry"
    line_number_column: str = "LineNum"
    audit_column: str | None = "UpdateDate"


MASTER_TABLES: tuple[MasterTable, ...] = (
    MasterTable("branch", "OBPL", "BPLId", "BPLName"),
    MasterTable("account", "OACT", "AcctCode", "AcctName"),
    MasterTable("bp_group", "OCRG", "GroupCode", "GroupName"),
    MasterTable("business_partner", "OCRD", "CardCode", "CardName"),
    MasterTable("bp_address", "CRD1", "Address"),
    MasterTable("bp_tax_id", "CRD7", "CardCode"),
    MasterTable("bp_contact", "OCPR", "CntctCode", "Name"),
    MasterTable("item_group", "OITB", "ItmsGrpCod", "ItmsGrpNam"),
    MasterTable("uom", "OUOM", "UomEntry", "UomName"),
    MasterTable("warehouse", "OWHS", "WhsCode", "WhsName"),
    MasterTable("item", "OITM", "ItemCode", "ItemName"),
    MasterTable("item_price", "ITM1", "ItemCode"),
    MasterTable("item_warehouse_balance", "OITW", "ItemCode"),
)


DOCUMENT_FAMILIES: tuple[DocumentFamily, ...] = (
    DocumentFamily("sales_invoices", "OINV", "INV1", "DocDate"),
    DocumentFamily("sales_credit_memos", "ORIN", "RIN1", "DocDate"),
    DocumentFamily("purchase_invoices", "OPCH", "PCH1", "DocDate"),
    DocumentFamily("purchase_credit_memos", "ORPC", "RPC1", "DocDate"),
    DocumentFamily("incoming_payments", "ORCT", None, "DocDate"),
    DocumentFamily("outgoing_payments", "OVPM", None, "DocDate"),
    DocumentFamily("journals", "OJDT", "JDT1", "RefDate", "TransId", "Line_ID", audit_column="CreateDate"),
    DocumentFamily("sales_orders", "ORDR", "RDR1", "DocDate"),
    DocumentFamily("purchase_orders", "OPOR", "POR1", "DocDate"),
    DocumentFamily("deliveries", "ODLN", "DLN1", "DocDate"),
    DocumentFamily("purchase_receipts", "OPDN", "PDN1", "DocDate"),
    DocumentFamily("goods_receipts", "OIGN", "IGN1", "DocDate"),
    DocumentFamily("goods_issues", "OIGE", "IGE1", "DocDate"),
    DocumentFamily("inventory_transfers", "OWTR", "WTR1", "DocDate"),
    DocumentFamily("production_orders", "OWOR", "WOR1", "PostDate"),
)


DOCUMENT_FAMILY_BY_NAME = {family.name: family for family in DOCUMENT_FAMILIES}

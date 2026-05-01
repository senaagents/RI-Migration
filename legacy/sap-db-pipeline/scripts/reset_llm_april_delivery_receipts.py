from __future__ import annotations

import json

import frappe

from llm_migration_config import SITE, connect_frappe


def main() -> None:
    if SITE != "llmnew.localhost":
        raise RuntimeError(f"Refusing to reset delivery/receipt docs in {SITE}; set SAP_TARGET_SITE=llmnew.localhost")
    connect_frappe(frappe)
    try:
        result = {}
        for doctype in ("Delivery Note", "Purchase Receipt"):
            rows = frappe.get_all(
                doctype,
                fields=["name", "custom_sap_docentry", "docstatus"],
                filters={"custom_sap_docentry": ["is", "set"]},
                order_by="posting_date desc, creation desc",
                limit_page_length=0,
            )
            canceled = 0
            retagged = 0
            for row in rows:
                doc = frappe.get_doc(doctype, row.name)
                old_key = str(doc.custom_sap_docentry or "")
                if doc.docstatus == 1:
                    doc.cancel()
                    canceled += 1
                if old_key and not old_key.startswith("CANCELED-NOTAX-"):
                    frappe.db.set_value(
                        doctype,
                        doc.name,
                        "custom_sap_docentry",
                        f"CANCELED-NOTAX-{old_key}-{doc.name}",
                        update_modified=False,
                    )
                    retagged += 1
                if (canceled + retagged) % 20 == 0:
                    frappe.db.commit()
                    print(f"reset {doctype}: canceled={canceled} retagged={retagged}")
            frappe.db.commit()
            result[doctype] = {"seen": len(rows), "canceled": canceled, "retagged": retagged}
        print(json.dumps(result, indent=2, sort_keys=True))
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()

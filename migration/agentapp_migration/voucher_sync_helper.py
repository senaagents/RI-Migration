"""One-off inspection/cleanup helper for Avinash voucher sync (INT-CONN-00008).

Run:
    bench --site avinash.localhost execute \
      migration.agentapp_migration.voucher_sync_helper.inspect
    bench --site avinash.localhost execute \
      migration.agentapp_migration.voucher_sync_helper.delete_stale_pairings
"""

import frappe


def inspect():
    conns = frappe.get_all(
        "Integration Source Connection",
        filters={"name": ["in", ["INT-CONN-00007", "INT-CONN-00008", "INT-CONN-00009"]]},
        fields=["name", "status", "source_type", "last_error"],
    )
    print("== Connections ==")
    for c in conns:
        print(c)

    print("\n== Normalized record counts by type (INT-CONN-00008) ==")
    rows = frappe.db.sql(
        """
        SELECT record_type, COUNT(*) AS n
        FROM `tabIntegration Normalized Source Record`
        WHERE connection = 'INT-CONN-00008'
        GROUP BY record_type
        ORDER BY n DESC
        """,
        as_dict=True,
    )
    for r in rows:
        print(r)

    print("\n== Source object counts by object_type (INT-CONN-00008) ==")
    rows = frappe.db.sql(
        """
        SELECT object_type, COUNT(*) AS n
        FROM `tabIntegration Source Object`
        WHERE connection = 'INT-CONN-00008'
        GROUP BY object_type
        ORDER BY n DESC
        """,
        as_dict=True,
    )
    for r in rows:
        print(r)

    for cn in ("INT-CONN-00007", "INT-CONN-00009"):
        so = frappe.db.count("Integration Source Object", {"connection": cn})
        nr = frappe.db.count("Integration Normalized Source Record", {"connection": cn})
        rp = frappe.db.count("Integration Raw Payload", {"connection": cn})
        print(f"\n{cn}: source_objects={so}, normalized={nr}, raw_payloads={rp}")


def delete_stale_pairings():
    """Delete INT-CONN-00007 and INT-CONN-00009 (stale zero-object Pairing rows)."""
    for cn in ("INT-CONN-00007", "INT-CONN-00009"):
        if not frappe.db.exists("Integration Source Connection", cn):
            print(f"{cn}: already gone")
            continue
        so = frappe.db.count("Integration Source Object", {"connection": cn})
        nr = frappe.db.count("Integration Normalized Source Record", {"connection": cn})
        rp = frappe.db.count("Integration Raw Payload", {"connection": cn})
        if so or nr or rp:
            print(f"{cn}: NOT empty (so={so}, nr={nr}, rp={rp}); skipping")
            continue
        frappe.delete_doc("Integration Source Connection", cn, force=1, ignore_permissions=True)
        print(f"{cn}: deleted")
    frappe.db.commit()


def voucher_counts():
    c = frappe.db.count(
        "Integration Normalized Source Record",
        {"connection": "INT-CONN-00008", "record_type": "Voucher"},
    )
    d = frappe.db.count(
        "Integration Normalized Source Record",
        {"connection": "INT-CONN-00008", "record_type": "Day Book Voucher"},
    )
    print(f"Voucher={c}, Day Book Voucher={d}")

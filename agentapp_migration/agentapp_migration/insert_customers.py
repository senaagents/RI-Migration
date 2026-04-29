"""Insert deduped Customers + Addresses into avinash.localhost.

Run via: bench --site avinash.localhost execute insert_customers.run
"""
import json
import frappe


CUSTOMER_GROUPS = [
    "Commercial",
    "Butterfly",
    "Faber",
    "Havells",
    "Philips",
    "Verusuni",
    "Raw Material Sales",
    "Scrap Sales",
]


def _ensure_customer_group_root():
    """Ensure 'All Customer Groups' exists as the root group."""
    if not frappe.db.exists("Customer Group", "All Customer Groups"):
        doc = frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": "All Customer Groups",
            "is_group": 1,
            "parent_customer_group": "",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        print(f"Created root: {doc.name}")


def _ensure_customer_group(name):
    if frappe.db.exists("Customer Group", name):
        return
    doc = frappe.get_doc({
        "doctype": "Customer Group",
        "customer_group_name": name,
        "is_group": 0,
        "parent_customer_group": "All Customer Groups",
    }).insert(ignore_permissions=True)
    print(f"  + Customer Group: {doc.name}")


def _ensure_territory():
    if not frappe.db.exists("Territory", "All Territories"):
        # Should exist already, but be safe
        frappe.get_doc({
            "doctype": "Territory",
            "territory_name": "All Territories",
            "is_group": 1,
        }).insert(ignore_permissions=True)


def _normalize_state(state):
    """Map Tally state to ERPNext canonical state if needed."""
    if not state:
        return ""
    s = state.strip()
    # Common normalizations
    fixes = {
        "Tamilnadu": "Tamil Nadu",
        "Pondicherry": "Puducherry",
    }
    return fixes.get(s, s)


def _resolve_customer_name(base, n):
    """Avoid name collisions in tabCustomer (PK = name = customer_name)."""
    cand = base
    i = 2
    while frappe.db.exists("Customer", cand):
        cand = f"{base} ({i})"
        i += 1
    return cand


def run():
    plan_data = json.loads(open("/tmp/customer_plan_v2.json").read())
    plan = plan_data["plan"]
    skipped = plan_data["skipped"]

    print(f"\n=== INSERT CUSTOMERS ===")
    print(f"Plan: {len(plan)} customers, {sum(len(p['addresses']) for p in plan)} addresses")
    print(f"Skipped from plan: {len(skipped)}")

    _ensure_territory()
    _ensure_customer_group_root()
    for cg in CUSTOMER_GROUPS:
        _ensure_customer_group(cg)

    inserted_customers = 0
    inserted_addresses = 0
    failed_customers = []
    failed_addresses = []

    for p in plan:
        cust_name = _resolve_customer_name(p["customer_name"], len(p["tally_aliases"]))

        try:
            cust_doc = frappe.get_doc({
                "doctype": "Customer",
                "customer_name": cust_name,
                "customer_type": p["customer_type"],
                "customer_group": p["customer_group"] if frappe.db.exists("Customer Group", p["customer_group"]) else "Commercial",
                "territory": "All Territories",
                "default_currency": "INR",
                "tax_id": p.get("gstin") or "",
                "mobile_no": p.get("primary_phone") or "",
                "email_id": p.get("primary_email") or "",
            }).insert(ignore_permissions=True, ignore_mandatory=True)
            inserted_customers += 1
        except Exception as e:
            failed_customers.append({"name": cust_name, "error": str(e)[:200]})
            continue

        # Insert addresses linked via Dynamic Link
        for idx, addr in enumerate(p["addresses"]):
            try:
                addr_doc = frappe.get_doc({
                    "doctype": "Address",
                    "address_title": addr["address_title"][:140] or cust_name,
                    "address_type": "Billing" if idx == 0 else "Shipping",
                    "address_line1": addr["address_line1"][:240] or addr["tally_ledger"][:240],
                    "address_line2": addr.get("address_line2", "")[:240] or None,
                    "city": addr.get("city") or None,
                    "state": _normalize_state(addr.get("state")) or None,
                    "country": addr.get("country") or "India",
                    "pincode": (addr.get("pincode") or "")[:10] or None,
                    "email_id": addr.get("email_id") or None,
                    "phone": (addr.get("phone") or "")[:20] or None,
                    "is_primary_address": 1 if idx == 0 else 0,
                    "is_shipping_address": 1,
                    "links": [{
                        "link_doctype": "Customer",
                        "link_name": cust_doc.name,
                    }],
                }).insert(ignore_permissions=True, ignore_mandatory=True)
                inserted_addresses += 1
            except Exception as e:
                failed_addresses.append({
                    "customer": cust_doc.name,
                    "tally_ledger": addr["tally_ledger"],
                    "error": str(e)[:200],
                })

    frappe.db.commit()

    print(f"\nInserted Customers: {inserted_customers}")
    print(f"Inserted Addresses: {inserted_addresses}")
    print(f"Failed customers: {len(failed_customers)}")
    for f in failed_customers[:10]:
        print(f"  ! {f['name']}: {f['error']}")
    print(f"Failed addresses: {len(failed_addresses)}")
    for f in failed_addresses[:10]:
        print(f"  ! {f['customer']} / {f['tally_ledger']}: {f['error']}")

    # Final counts
    print(f"\n=== FINAL DB COUNTS ===")
    print(f"Customer: {frappe.db.count('Customer')}")
    print(f"Customer Group: {frappe.db.count('Customer Group')}")
    print(f"Address: {frappe.db.count('Address')}")
    print(f"Dynamic Link to Customer: {frappe.db.count('Dynamic Link', {'link_doctype': 'Customer'})}")

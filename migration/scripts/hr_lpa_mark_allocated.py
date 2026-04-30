"""Step 2 of HR-6 rollback: flip leaves_allocated=1 on the 196 LPAs so HRMS
won't try to re-auto-allocate at any future trigger. Submitted-doc field
mutation via db.set_value (no workflow re-run needed).
"""

import frappe  # type: ignore


def run():
	names = frappe.get_all(
		"Leave Policy Assignment",
		filters={"docstatus": 1, "leaves_allocated": 0},
		pluck="name",
	)
	print(f"Found {len(names)} LPAs with docstatus=1 leaves_allocated=0")
	for name in names:
		frappe.db.set_value("Leave Policy Assignment", name, "leaves_allocated", 1)
	frappe.db.commit()
	verify = frappe.db.count("Leave Policy Assignment",
							 {"docstatus": 1, "leaves_allocated": 1})
	still_zero = frappe.db.count("Leave Policy Assignment",
								 {"docstatus": 1, "leaves_allocated": 0})
	print(f"Patched. docstatus=1 leaves_allocated=1 count: {verify}")
	print(f"docstatus=1 leaves_allocated=0 count (should be 0): {still_zero}")
	return {"patched": len(names), "leaves_allocated_1": verify, "leaves_allocated_0": still_zero}

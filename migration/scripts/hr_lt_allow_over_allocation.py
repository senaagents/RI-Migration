"""Step B of HR-6 fix: enable allow_over_allocation on CL/SL/EL Leave Types
so opening-balance allocations exceeding max_leaves_allowed are accepted."""

import frappe  # type: ignore


def run():
	for lt in ("Casual Leave", "Sick Leave", "Earned Leave"):
		before = frappe.db.get_value("Leave Type", lt, "allow_over_allocation")
		frappe.db.set_value("Leave Type", lt, "allow_over_allocation", 1)
		after = frappe.db.get_value("Leave Type", lt, "allow_over_allocation")
		print(f"  {lt:14}  allow_over_allocation: {before} -> {after}")
	frappe.db.commit()
	rows = frappe.db.sql(
		"""SELECT name, allow_over_allocation, max_leaves_allowed
		   FROM `tabLeave Type` WHERE name IN ('Casual Leave','Sick Leave','Earned Leave')""",
		as_dict=True,
	)
	print("\nFinal:", rows)
	return rows

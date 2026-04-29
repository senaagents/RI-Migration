"""HR-6 rollback: cancel + delete the 784 auto-created Leave Allocations
that HRMS auto-generated when we submitted Leave Policy Assignments.

Scope filter (any one of these is sufficient — using all three for safety):
  company = LLM
  from_date = 2026-01-01
  to_date = 2026-12-31
  docstatus = 1
  leave_policy_assignment IN (HR-LPOL-ASSGN-00001..00196)  -- the 196 we just submitted

Runs:
  bench --site llm.localhost execute agentapp_migration.scripts.hr_leave_opening_rollback.run --kwargs "{'dry_run': True}"
  bench --site llm.localhost execute agentapp_migration.scripts.hr_leave_opening_rollback.run --kwargs "{'dry_run': False}"

Per-row savepoint per scars/frappe-db-rollback-wipes-batched-inserts.md.
"""

import frappe  # type: ignore

COMPANY = "LLM"
FROM_DATE = "2026-01-01"
TO_DATE = "2026-12-31"


def _scope_rows(scope="lpa_linked"):
	"""Find the LAs to cancel.
	scope='lpa_linked'  → auto-created by HRMS at LPA submit (FK to LPA, step 1 path)
	scope='standalone'  → created standalone by Pass 2 with NULL FK (step C path)
	"""
	if scope == "lpa_linked":
		clause = "AND leave_policy_assignment IS NOT NULL AND leave_policy_assignment LIKE 'HR-LPOL-ASSGN-%%'"
	elif scope == "standalone":
		clause = "AND leave_policy_assignment IS NULL"
	else:
		raise ValueError(f"unknown scope {scope!r}")
	return frappe.db.sql(
		f"""SELECT name, employee, leave_type, leave_policy_assignment
		    FROM `tabLeave Allocation`
		    WHERE company = %s AND from_date = %s AND to_date = %s
		      AND docstatus = 1 {clause}""",
		(COMPANY, FROM_DATE, TO_DATE), as_dict=True,
	)


def run(dry_run=True, scope="lpa_linked"):
	rows = _scope_rows(scope)
	print(f"Scope: {len(rows)} Leave Allocation rows match the filter")
	print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
	if rows:
		# Sanity-print first 3
		for r in rows[:3]:
			print(f"  e.g. {r['name']} emp={r['employee']} type={r['leave_type']} lpa={r['leave_policy_assignment']}")

	if dry_run:
		print("\n(dry run — no changes made)")
		return {"matched": len(rows), "cancelled": 0, "deleted": 0, "errors": 0}

	cancelled = 0
	deleted = 0
	errors = 0
	for i, r in enumerate(rows):
		sp = f"larb_{i}"
		frappe.db.savepoint(sp)
		try:
			doc = frappe.get_doc("Leave Allocation", r["name"])
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Leave Allocation", r["name"], force=True, ignore_permissions=True)
			cancelled += 1
			deleted += 1
		except Exception as exc:
			frappe.db.rollback(save_point=sp)
			errors += 1
			print(f"  ERROR {r['name']}: {type(exc).__name__}: {exc}")
		if (i + 1) % 100 == 0:
			frappe.db.commit()
			print(f"  ...{i+1}/{len(rows)} cancelled+deleted (committed)")
	frappe.db.commit()

	# Verify — re-run the same scope filter
	remaining = len(_scope_rows(scope))
	print(f"\nDone. cancelled+deleted={deleted} errors={errors} remaining_in_scope={remaining}")
	return {"matched": len(rows), "cancelled": cancelled, "deleted": deleted,
			"errors": errors, "remaining_in_scope": remaining}

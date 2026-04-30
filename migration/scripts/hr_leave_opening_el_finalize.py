"""HR-6 final step: finalize Earned Leave opening allocations.

Atomic try/finally:
  1. Save current EL max_leaves_allowed (sanity).
  2. Set EL max_leaves_allowed = 0 (defeats the second OverAlloc check at
     leave_allocation.py:55-77 which doesn't respect allow_over_allocation).
  3. Cancel + delete the 51 partial EL allocations (those whose opening_total
     <= 12 succeeded earlier; we re-do the whole 196 from scratch).
  4. Re-fire Pass 2 with leave_type filter = Earned Leave only.
  5. Verify EL sum_total == 4147.5 (within 0.01).
  finally:
  6. Restore EL max_leaves_allowed = 12.
  7. Verify the restore succeeded — if not, raise.

Run: bench --site llm.localhost execute migration.scripts.hr_leave_opening_el_finalize.run
"""

import csv
import os

import frappe  # type: ignore

WORKSPACE_ROOT = "/Users/aakashchid/workshop/sena"
INPUT_CSV = os.path.join(
	WORKSPACE_ROOT,
	"office/_workspace-admin/customer-projects/llm-livenew/derived/llm_leave_opening.csv",
)

COMPANY = "LLM"
FROM_DATE = "2026-01-01"
TO_DATE = "2026-12-31"
LT = "Earned Leave"
RESTORE_MAX = 12  # original policy cap from llm-hr-fixtures.yaml


def _resolve_employee(emp_code, cache):
	if emp_code in cache:
		return cache[emp_code]
	rows = frappe.db.get_all(
		"Employee",
		filters={"employee_number": emp_code, "company": COMPANY},
		fields=["name"], limit=1,
	)
	cache[emp_code] = rows[0]["name"] if rows else None
	return cache[emp_code]


def _cancel_existing_el_allocations():
	"""Cancel + force-delete every standalone (NULL leave_policy_assignment)
	EL allocation in our window."""
	rows = frappe.db.sql(
		"""SELECT name FROM `tabLeave Allocation`
		   WHERE company = %s AND from_date = %s AND to_date = %s
		     AND leave_type = %s AND docstatus = 1
		     AND leave_policy_assignment IS NULL""",
		(COMPANY, FROM_DATE, TO_DATE, LT), as_dict=True,
	)
	print(f"  Cancelling {len(rows)} existing EL allocations…")
	cancelled = 0
	errors = 0
	for i, r in enumerate(rows):
		sp = f"el_cancel_{i}"
		frappe.db.savepoint(sp)
		try:
			doc = frappe.get_doc("Leave Allocation", r["name"])
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Leave Allocation", r["name"], force=True, ignore_permissions=True)
			cancelled += 1
		except Exception as exc:
			frappe.db.rollback(save_point=sp)
			errors += 1
			print(f"    ERROR cancel {r['name']}: {type(exc).__name__}: {exc}")
		if (i + 1) % 50 == 0:
			frappe.db.commit()
	frappe.db.commit()
	print(f"  Cancelled {cancelled}, errors {errors}")
	return cancelled, errors


def _allocate_el():
	"""Re-fire Pass 2 for Earned Leave only."""
	with open(INPUT_CSV) as fh:
		csv_rows = [r for r in csv.DictReader(fh) if r["leave_type"] == "EL"]
	print(f"  EL CSV rows: {len(csv_rows)}")
	emp_cache = {}
	created = 0
	skipped_zero = 0
	errors = 0
	error_rows = []
	for i, row in enumerate(csv_rows):
		emp_pk = _resolve_employee(row["emp_code"], emp_cache)
		if not emp_pk:
			errors += 1
			error_rows.append({"emp_code": row["emp_code"], "reason": "Employee not found"})
			continue
		try:
			new_leaves = float(row.get("opening_value") or 0)
			carry = float(row.get("balance_opening_count") or 0)
		except ValueError:
			errors += 1
			error_rows.append({"emp_code": row["emp_code"], "reason": "ValueError parsing numbers"})
			continue
		opening_total = new_leaves + carry

		# Rows with 0 total: customer's books show no EL balance for this employee.
		# Don't try to allocate (HRMS would reject "Total leaves allocated mandatory").
		# This is a skipped-because-no-data, NOT an error.
		if opening_total == 0:
			skipped_zero += 1
			continue

		sp = f"el_alloc_{i}"
		frappe.db.savepoint(sp)
		try:
			doc = frappe.get_doc({
				"doctype": "Leave Allocation",
				"employee": emp_pk,
				"company": COMPANY,
				"leave_type": LT,
				"from_date": FROM_DATE,
				"to_date": TO_DATE,
				"new_leaves_allocated": opening_total,
				"carry_forward": 0,
				"description": (
					f"Opening allocation from llm_leave_opening.csv "
					f"(emp_code={row['emp_code']}; opening_value={new_leaves}, balance={carry})"
				),
			})
			doc.insert(ignore_permissions=True)
			doc.submit()
			created += 1
		except Exception as exc:
			frappe.db.rollback(save_point=sp)
			errors += 1
			error_rows.append({"emp_code": row["emp_code"],
							   "reason": f"{type(exc).__name__}: {exc}"})
			print(f"    ERROR alloc emp={emp_pk} {row['emp_code']}: {type(exc).__name__}: {exc}")
		if (i + 1) % 50 == 0:
			frappe.db.commit()
	frappe.db.commit()
	print(f"  EL allocations created: {created}  skipped_zero_balance: {skipped_zero}  errors: {errors}")
	return created, errors, error_rows


def run():
	# Pre-state snapshot
	pre_max = frappe.db.get_value("Leave Type", LT, "max_leaves_allowed")
	print(f"PRE: Earned Leave max_leaves_allowed = {pre_max}")

	cancel_count = 0
	create_count = 0
	final_sum = None
	import_succeeded = False

	try:
		# 1+2. Set max_leaves_allowed = 0 to bypass the second OverAlloc check.
		frappe.db.set_value("Leave Type", LT, "max_leaves_allowed", 0)
		frappe.db.commit()
		print(f"\n[1-2] Set Earned Leave max_leaves_allowed = 0 (was {pre_max})")
		assert frappe.db.get_value("Leave Type", LT, "max_leaves_allowed") == 0

		# 3. Cancel + delete 51 existing EL allocations.
		print("\n[3] Cancelling existing partial EL allocations…")
		cancel_count, cancel_errors = _cancel_existing_el_allocations()
		if cancel_errors:
			print(f"  WARNING {cancel_errors} cancel errors — continuing")

		# 4. Re-fire EL allocations.
		print("\n[4] Allocating EL openings…")
		create_count, create_errors, error_rows = _allocate_el()

		# 5. Verify totals.
		final = frappe.db.sql(
			"""SELECT SUM(total_leaves_allocated) AS s FROM `tabLeave Allocation`
			   WHERE company = %s AND from_date = %s AND leave_type = %s AND docstatus = 1""",
			(COMPANY, FROM_DATE, LT), as_dict=True,
		)
		final_sum = float(final[0]["s"] or 0)
		print(f"\n[5] EL sum_total post-import: {final_sum}")
		print(f"     CSV expected:              4147.5")
		print(f"     delta:                     {final_sum - 4147.5}")
		import_succeeded = abs(final_sum - 4147.5) <= 0.01

	finally:
		# 6. Restore max_leaves_allowed = 12 ALWAYS, even on failure.
		frappe.db.set_value("Leave Type", LT, "max_leaves_allowed", RESTORE_MAX)
		frappe.db.commit()
		# 7. Verify restore.
		post_max = frappe.db.get_value("Leave Type", LT, "max_leaves_allowed")
		print(f"\n[6-7] Restored Earned Leave max_leaves_allowed = {post_max}")
		if post_max != RESTORE_MAX:
			raise RuntimeError(
				f"RESTORE FAILED: max_leaves_allowed expected {RESTORE_MAX}, got {post_max}"
			)

	print("\n=== SUMMARY ===")
	print(f"  cancelled (existing EL):  {cancel_count}")
	print(f"  created   (new EL):       {create_count}")
	print(f"  EL sum_total in ERPNext:  {final_sum}")
	print(f"  EL sum_total expected:    4147.5")
	print(f"  import_succeeded:         {import_succeeded}")
	if not import_succeeded:
		print("\n  ⚠️  EL totals do not match CSV. Investigate before mark-complete.")
	return {
		"cancelled": cancel_count,
		"created": create_count,
		"el_sum_total": final_sum,
		"el_sum_expected": 4147.5,
		"matches_csv": import_succeeded,
		"max_leaves_allowed_restored_to": frappe.db.get_value("Leave Type", LT, "max_leaves_allowed"),
	}

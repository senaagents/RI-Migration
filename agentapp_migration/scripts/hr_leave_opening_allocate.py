"""HR-6: Leave opening allocations for LLM employees on llm.localhost.

Reads office/_workspace-admin/customer-projects/llm-livenew/derived/llm_leave_opening.csv
(588 rows, columns: emp_code, emp_name, leave_type, opening_value,
balance_opening_count, esi_member).

Two passes:
  PASS 1 — Leave Policy Assignment per Employee
    Assigns each Employee to "LLM Leave Policy - ESI Members" if esi_member=Y,
    else "LLM Leave Policy - Non ESI Members". Effective 2026-01-01..2026-12-31.
    Uses assignment_based_on='' (manual) so no automatic allocation; we do
    the allocation explicitly in pass 2 from the CSV's opening figures.

  PASS 2 — Leave Allocation per (Employee, Leave Type) row
    new_leaves_allocated = opening_value
    carry_forwarded_leaves_count = balance_opening_count
    total_leaves_allocated = new + carry  (Leave Allocation requires this field
    set; it's normally computed by Frappe but we set it defensively).
    from_date 2026-01-01, to_date 2026-12-31. Submitted so balance is realized.

Idempotent: each pass checks for an existing matching submitted doc and skips.
Per-row try/except — errors logged to leave_errors.csv, batch continues.

Run via:
    bench --site llm.localhost execute office_workspace_admin.hr_leave_opening_allocate.run --kwargs '{"dry_run": true}'
or, since the office/ tree isn't a frappe app, copy this script into
agentapp_migration/scripts/ and execute via that dotted path:
    cp <this> apps/agentapp_migration/agentapp_migration/scripts/hr_leave_opening_allocate.py
    bench --site llm.localhost execute agentapp_migration.scripts.hr_leave_opening_allocate.run --kwargs '{"dry_run": true}'
    bench --site llm.localhost execute agentapp_migration.scripts.hr_leave_opening_allocate.run --kwargs '{"dry_run": false}'
"""

import csv
import os
from collections import defaultdict

import frappe  # type: ignore (provided by `bench execute` context)

WORKSPACE_ROOT = "/Users/aakashchid/workshop/sena"
INPUT_CSV = os.path.join(
	WORKSPACE_ROOT,
	"office/_workspace-admin/customer-projects/llm-livenew/derived/llm_leave_opening.csv",
)
OUT_DIR = os.path.join(
	WORKSPACE_ROOT,
	"office/_workspace-admin/customer-projects/llm-livenew/derived",
)
SKIP_LOG = os.path.join(OUT_DIR, "leave_skipped.csv")
ERROR_LOG = os.path.join(OUT_DIR, "leave_errors.csv")

COMPANY = "LLM"
FROM_DATE = "2026-01-01"
TO_DATE = "2026-12-31"

LEAVE_TYPE_MAP = {
	"CL": "Casual Leave",
	"SL": "Sick Leave",
	"EL": "Earned Leave",
	# Future-proof: full names pass through.
	"Casual Leave": "Casual Leave",
	"Sick Leave": "Sick Leave",
	"Earned Leave": "Earned Leave",
}

POLICY_ESI = "LLM Leave Policy - ESI Members"
POLICY_NON_ESI = "LLM Leave Policy - Non ESI Members"


def _load_rows():
	with open(INPUT_CSV) as fh:
		return list(csv.DictReader(fh))


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


def _resolve_policy_pk(title, cache):
	if title in cache:
		return cache[title]
	rows = frappe.db.get_all("Leave Policy", filters={"title": title}, fields=["name"], limit=1)
	cache[title] = rows[0]["name"] if rows else None
	return cache[title]


def _is_esi(esi_value):
	"""Y/yes/true → True. Blank or N → False (default non-ESI when unknown)."""
	if not esi_value:
		return False
	return esi_value.strip().upper() in ("Y", "YES", "TRUE", "1")


def assign_leave_policies(rows, dry_run, counts, errors_rows):
	"""Pass 1: one Leave Policy Assignment per Employee.

	The CSV has multiple rows per employee (one per leave_type). We collapse
	to one assignment per employee, picking ESI status from any row (they
	should all agree; if they conflict we take the first non-blank).
	"""
	emp_cache = {}
	policy_cache = {}
	emp_esi = {}  # emp_pk -> bool (first non-blank esi seen wins)
	for row in rows:
		emp_pk = _resolve_employee(row["emp_code"], emp_cache)
		if not emp_pk:
			continue  # logged in pass 2
		if emp_pk not in emp_esi or (row.get("esi_member") or "").strip():
			if (row.get("esi_member") or "").strip():
				emp_esi[emp_pk] = _is_esi(row["esi_member"])
			elif emp_pk not in emp_esi:
				emp_esi[emp_pk] = False  # default non-ESI

	policy_esi_pk = _resolve_policy_pk(POLICY_ESI, policy_cache)
	policy_non_esi_pk = _resolve_policy_pk(POLICY_NON_ESI, policy_cache)
	if not policy_esi_pk or not policy_non_esi_pk:
		raise RuntimeError(
			f"Leave Policies not found by title: ESI pk={policy_esi_pk!r} "
			f"non-ESI pk={policy_non_esi_pk!r}. Run hr_fixtures_seed first."
		)

	for emp_pk, is_esi in emp_esi.items():
		policy_pk = policy_esi_pk if is_esi else policy_non_esi_pk
		# Idempotency: any submitted (or draft) Leave Policy Assignment with
		# this (employee, leave_policy, effective_from) already in place?
		existing = frappe.db.get_all(
			"Leave Policy Assignment",
			filters={
				"employee": emp_pk,
				"leave_policy": policy_pk,
				"effective_from": FROM_DATE,
				"docstatus": ["<", 2],  # 0 draft or 1 submitted
			},
			fields=["name"], limit=1,
		)
		if existing:
			counts["assignments_skipped_exists"] += 1
			continue
		if dry_run:
			counts["assignments_would_create"] += 1
			continue
		# Per-row savepoint — see senacode/scars/frappe-db-rollback-wipes-batched-inserts.md
		sp = f"lpa_{emp_pk}".replace("-", "_")
		frappe.db.savepoint(sp)
		try:
			doc = frappe.get_doc({
				"doctype": "Leave Policy Assignment",
				"employee": emp_pk,
				"company": COMPANY,
				"leave_policy": policy_pk,
				"effective_from": FROM_DATE,
				"effective_to": TO_DATE,
				"assignment_based_on": "",  # manual; no auto-allocation
				"carry_forward": 0,
			})
			doc.insert(ignore_permissions=True)
			doc.submit()
			counts["assignments_created"] += 1
		except Exception as exc:
			frappe.db.rollback(save_point=sp)
			counts["assignments_errors"] += 1
			errors_rows.append({
				"pass": "assignment", "emp": emp_pk,
				"leave_type": "", "reason": f"{type(exc).__name__}: {exc}",
			})
			print(f"  ERROR assignment {emp_pk}: {type(exc).__name__}: {exc}")


def allocate_leave_openings(rows, dry_run, limit, counts, skipped_rows, errors_rows, totals):
	"""Pass 2: one Leave Allocation per CSV row."""
	emp_cache = {}
	processed = 0
	for row in rows:
		if limit and processed >= limit:
			break
		processed += 1

		emp_code = row["emp_code"]
		emp_pk = _resolve_employee(emp_code, emp_cache)
		if not emp_pk:
			counts["allocations_skipped_no_employee"] += 1
			skipped_rows.append({
				"emp_code": emp_code, "emp_name": row.get("emp_name", ""),
				"leave_type": row.get("leave_type", ""),
				"reason": "Employee not found by employee_number on company=LLM",
			})
			continue

		raw_type = (row.get("leave_type") or "").strip()
		leave_type = LEAVE_TYPE_MAP.get(raw_type)
		if not leave_type:
			counts["allocations_errors"] += 1
			errors_rows.append({
				"pass": "allocation", "emp": emp_pk, "leave_type": raw_type,
				"reason": f"unmapped leave_type {raw_type!r}",
			})
			continue

		try:
			new_leaves = float(row.get("opening_value") or 0)
			carry = float(row.get("balance_opening_count") or 0)
		except ValueError as exc:
			counts["allocations_errors"] += 1
			errors_rows.append({
				"pass": "allocation", "emp": emp_pk, "leave_type": leave_type,
				"reason": f"ValueError parsing numbers: {exc}",
			})
			continue

		# Idempotency: any non-cancelled allocation for this (emp, type, from_date)?
		# docstatus != 2 (i.e. draft or submitted, NOT cancelled). Defense-in-depth
		# even though we hard-deleted earlier auto-allocations.
		existing = frappe.db.get_all(
			"Leave Allocation",
			filters={
				"employee": emp_pk, "leave_type": leave_type,
				"from_date": FROM_DATE, "docstatus": ["!=", 2],
			},
			fields=["name"], limit=1,
		)
		if existing:
			counts["allocations_skipped_exists"] += 1
			continue

		if dry_run:
			counts["allocations_would_create"] += 1
			totals[leave_type] += new_leaves + carry
			continue

		# Per-row savepoint — see senacode/scars/frappe-db-rollback-wipes-batched-inserts.md
		sp = f"la_{emp_pk}_{leave_type}".replace("-", "_").replace(" ", "_")
		frappe.db.savepoint(sp)
		# Opening balance: lump opening_value + balance_opening_count into
		# new_leaves_allocated. carry_forwarded_leaves_count is an OUTPUT
		# field per scar hrms-leave-allocation-carry-forwarded-leaves-count-is-output-not-input.md.
		# HRMS recomputes total_leaves_allocated = unused_leaves + new (it ignores
		# any pre-set total). With no prior allocation chain, unused=0 → total=new.
		opening_total = new_leaves + carry
		try:
			doc = frappe.get_doc({
				"doctype": "Leave Allocation",
				"employee": emp_pk,
				"company": COMPANY,
				"leave_type": leave_type,
				"from_date": FROM_DATE,
				"to_date": TO_DATE,
				"new_leaves_allocated": opening_total,
				"carry_forward": 0,  # no prior allocation chain on llm.localhost
				"description": (
					f"Opening allocation from llm_leave_opening.csv "
					f"(emp_code={emp_code}; opening_value={new_leaves}, balance={carry})"
				),
			})
			doc.insert(ignore_permissions=True)
			doc.submit()
			counts["allocations_created"] += 1
			totals[leave_type] += opening_total
		except Exception as exc:
			frappe.db.rollback(save_point=sp)
			counts["allocations_errors"] += 1
			errors_rows.append({
				"pass": "allocation", "emp": emp_pk, "leave_type": leave_type,
				"reason": f"{type(exc).__name__}: {exc}",
			})
			print(f"  ERROR alloc {emp_pk} {leave_type}: {type(exc).__name__}: {exc}")


def run(dry_run=True, limit=None, skip_pass1=False):
	"""Entry point. Set dry_run=False to actually write.

	skip_pass1=True skips Leave Policy Assignment creation (use when LPAs
	already exist + are patched with leaves_allocated=1).
	"""
	rows = _load_rows()
	print(f"Loaded {len(rows)} rows from {INPUT_CSV}")
	print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}  limit={limit}")

	counts = {
		"assignments_created": 0,
		"assignments_skipped_exists": 0,
		"assignments_would_create": 0,
		"assignments_errors": 0,
		"allocations_created": 0,
		"allocations_skipped_exists": 0,
		"allocations_skipped_no_employee": 0,
		"allocations_would_create": 0,
		"allocations_errors": 0,
	}
	skipped_rows = []
	errors_rows = []
	totals_by_type = defaultdict(float)

	if skip_pass1:
		print("\n--- PASS 1: Leave Policy Assignment SKIPPED (skip_pass1=True) ---")
	else:
		print("\n--- PASS 1: Leave Policy Assignment ---")
		try:
			assign_leave_policies(rows, dry_run, counts, errors_rows)
			if not dry_run:
				frappe.db.commit()
		except Exception:
			import traceback
			traceback.print_exc()

	print("\n--- PASS 2: Leave Allocation ---")
	try:
		allocate_leave_openings(rows, dry_run, limit, counts, skipped_rows, errors_rows, totals_by_type)
		if not dry_run:
			frappe.db.commit()
	except Exception:
		import traceback
		traceback.print_exc()

	# Logs
	if skipped_rows:
		with open(SKIP_LOG, "w", newline="") as fh:
			w = csv.DictWriter(fh, fieldnames=["emp_code", "emp_name", "leave_type", "reason"])
			w.writeheader()
			for r in skipped_rows:
				w.writerow(r)
		print(f"\nSkipped (no Employee): {SKIP_LOG} ({len(skipped_rows)} rows)")
	if errors_rows:
		with open(ERROR_LOG, "w", newline="") as fh:
			w = csv.DictWriter(fh, fieldnames=["pass", "emp", "leave_type", "reason"])
			w.writeheader()
			for r in errors_rows:
				w.writerow(r)
		print(f"Errors: {ERROR_LOG} ({len(errors_rows)} rows)")

	print("\n=== SUMMARY ===")
	for k, v in counts.items():
		print(f"  {k:40} {v}")
	if totals_by_type:
		print("\n  Total leaves allocated by type (new + carry):")
		for lt, total in sorted(totals_by_type.items()):
			print(f"    {lt:20} {total}")
	return counts

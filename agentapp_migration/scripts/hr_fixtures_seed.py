"""HR-4: Idempotent seeder for LLM HR reference fixtures on llm.localhost.

Reads senacode/integrations/llm-hr-fixtures.yaml and creates Branches,
Departments (parents + sub-departments), Designations, Employee Grades,
Holiday List 2026, Leave Types, and Leave Policies. Skips if exists.

Run via:  bench --site llm.localhost console < hr_fixtures_seed.py

Outputs created/skipped/error counts per entity type and writes
hr_fixtures_skipped.csv next to this file for any rows that could not be
seeded (e.g. doctype not installed, validation error).

Notes:
  - Employee Category: HRMS v16 has NO 'Employee Category' doctype. Skipped
    with explanation; the YAML's `categories:` list is captured in the log.
  - Loan Type: the Lending app is not installed on this site, so 'Loan Type'
    / 'Loan Product' do not exist. Skipped with explanation.
  - Holiday List: 2nd & 4th Saturday rule cannot be expressed in HRMS native
    weekly_off (single value). We mark Sunday weekly-off, then add explicit
    holidays for the listed festivals; 2nd/4th Saturday handled in v2 via
    Attendance rules, NOT here.
"""

import csv
import datetime as _dt
import os
import sys
import traceback

import frappe  # type: ignore  (provided by `bench console`)
import yaml  # type: ignore  (frappe deps include it)

WORKSPACE_ROOT = "/Users/aakashchid/workshop/sena"
FIXTURES_YAML = os.path.join(
	WORKSPACE_ROOT, "senacode/integrations/llm-hr-fixtures.yaml"
)
SKIP_LOG = os.path.join(
	WORKSPACE_ROOT,
	"office/_workspace-admin/customer-projects/llm-livenew/scripts/hr_fixtures_skipped.csv",
)
COMPANY = "LLM"
HOLIDAY_LIST_NAME = "LLM-Holidays-2026"
ROOT_DEPARTMENT = "All Departments"


def _load():
	with open(FIXTURES_YAML) as fh:
		return yaml.safe_load(fh)


def _log_skip(rows, entity, identifier, reason):
	rows.append({"entity": entity, "identifier": identifier, "reason": reason})


def _try_insert(doc_dict, entity, identifier, counts, errors):
	"""Insert a doc idempotently. Returns True on insert, False on skip."""
	doctype = doc_dict["doctype"]
	# Idempotency check: name field varies, but get_doc + db_exists by 'name'
	# works once the doc has been saved or has explicit name. For doctypes
	# auto-named from a field, frappe.db.exists(doctype, name) is the canonical
	# check.
	if frappe.db.exists(doctype, identifier):
		counts["skipped"] += 1
		return False
	try:
		doc = frappe.get_doc(doc_dict)
		doc.insert(ignore_permissions=True)
		counts["created"] += 1
		return True
	except frappe.DuplicateEntryError:
		counts["skipped"] += 1
		return False
	except Exception as exc:
		counts["errors"] += 1
		errors.append({
			"entity": entity, "identifier": identifier,
			"reason": f"{type(exc).__name__}: {exc}",
		})
		print(f"  ERROR {entity} {identifier!r}: {type(exc).__name__}: {exc}")
		return False


def seed_branches(data, counts, errors):
	for b in data.get("branches", []):
		_try_insert(
			{"doctype": "Branch", "branch": b["name"]},
			"Branch", b["name"], counts, errors,
		)


def _resolve_department_pk(department_name):
	"""Department auto-suffixes name with company abbr (' - L'). Look up the
	actual primary key by department_name + company so links resolve."""
	rows = frappe.db.get_all(
		"Department",
		filters={"department_name": department_name, "company": COMPANY},
		fields=["name"], limit=1,
	)
	return rows[0]["name"] if rows else None


def seed_departments(data, counts, errors):
	depts = data.get("departments", {})
	# Parents first. Department primary key is auto-suffixed (e.g. "Production - L"),
	# so we insert by department_name and let Frappe build the PK; pre-existing
	# departments may have is_group=0 — promote them to group so children can link.
	for p in depts.get("parents", []):
		pk = _resolve_department_pk(p["name"])
		if pk:
			# Already exists — make sure it's a group and parented under root.
			doc = frappe.get_doc("Department", pk)
			changed = False
			if not doc.is_group:
				doc.is_group = 1; changed = True
			if doc.parent_department != ROOT_DEPARTMENT:
				doc.parent_department = ROOT_DEPARTMENT; changed = True
			if changed:
				try:
					doc.save(ignore_permissions=True)
					counts["created"] += 1  # treat as fixture-applied
				except Exception as exc:
					counts["errors"] += 1
					errors.append({"entity": "Department (parent promote)",
								   "identifier": pk,
								   "reason": f"{type(exc).__name__}: {exc}"})
					print(f"  ERROR promote {pk}: {exc}")
			else:
				counts["skipped"] += 1
			continue
		_try_insert(
			{
				"doctype": "Department",
				"department_name": p["name"],
				"parent_department": ROOT_DEPARTMENT,
				"company": COMPANY,
				"is_group": 1,
			},
			"Department (parent)", p["name"], counts, errors,
		)
	# Subs: resolve parent's actual PK before linking.
	for s in depts.get("sub_departments", []):
		parent_name = s["parent"]
		parent_pk = _resolve_department_pk(parent_name)
		if not parent_pk:
			counts["errors"] += 1
			errors.append({"entity": "Department (sub)", "identifier": s["name"],
						   "reason": f"parent '{parent_name}' not found in Department table"})
			print(f"  ERROR sub {s['name']!r}: parent {parent_name!r} not found")
			continue
		dept_name = s["name"]
		# Avoid name collision with the parent group of the same department_name.
		if dept_name == parent_name:
			dept_name = f"{s['name']} - Default"
		# Idempotency: skip if any case-variant exists with this department_name
		# under this company (parent may differ — Frappe auto-suffix collapses
		# case at PK level so duplicates manifest as PRIMARY KEY conflicts).
		existing = frappe.db.sql(
			"""SELECT name FROM `tabDepartment`
			   WHERE LOWER(department_name) = LOWER(%s) AND company = %s
			   LIMIT 1""",
			(dept_name, COMPANY), as_dict=True,
		)
		if existing:
			counts["skipped"] += 1
			continue
		try:
			doc = frappe.get_doc({
				"doctype": "Department",
				"department_name": dept_name,
				"parent_department": parent_pk,
				"company": COMPANY,
				"is_group": 0,
			})
			doc.insert(ignore_permissions=True)
			counts["created"] += 1
		except Exception as exc:
			counts["errors"] += 1
			errors.append({"entity": "Department (sub)", "identifier": dept_name,
						   "reason": f"{type(exc).__name__}: {exc}"})
			print(f"  ERROR sub {dept_name!r}: {type(exc).__name__}: {exc}")
	# Rebuild nested-set after bulk insert.
	try:
		from frappe.utils.nestedset import rebuild_tree  # type: ignore
		rebuild_tree("Department")
	except Exception as exc:
		print(f"  WARN rebuild_tree(Department) failed: {exc}")


def seed_designations(data, counts, errors):
	seen = set()
	for d in data.get("designations", {}).get("canonical", []):
		# YAML has duplicates by canonical name (e.g. Operator twice). De-dup.
		name = d["name"]
		if name in seen:
			continue
		seen.add(name)
		_try_insert(
			{"doctype": "Designation", "designation_name": name},
			"Designation", name, counts, errors,
		)


def seed_employee_grades(data, counts, errors):
	for g in data.get("employee_grades", []):
		_try_insert(
			{"doctype": "Employee Grade", "__newname": g["code"], "name": g["code"]},
			"Employee Grade", g["code"], counts, errors,
		)


def seed_employee_categories(data, counts, errors, skipped):
	# HRMS v16 has no Employee Category doctype.
	for c in data.get("categories", []):
		_log_skip(
			skipped, "Employee Category", c["name"],
			"Doctype 'Employee Category' not installed in HRMS v16; tracked in YAML only",
		)
		counts["skipped"] += 1


def seed_holiday_list(data, counts, errors):
	hl = data.get("holiday_list_2026", {})
	name = hl.get("name", HOLIDAY_LIST_NAME)
	if frappe.db.exists("Holiday List", name):
		counts["skipped"] += 1
		return
	holidays_rows = []
	for h in hl.get("holidays", []):
		date = h.get("date")
		if not date:  # OPEN-4/5 entries: date TBD, skip
			continue
		holidays_rows.append({
			"holiday_date": date,
			"description": f"{h['name']} ({h.get('type','')})",
		})
	doc = {
		"doctype": "Holiday List",
		"holiday_list_name": name,
		"from_date": hl.get("from_date"),
		"to_date": hl.get("to_date"),
		"weekly_off": "Sunday",  # 2nd/4th Saturday rule applied in attendance layer
		"holidays": holidays_rows,
	}
	_try_insert(doc, "Holiday List", name, counts, errors)


def seed_leave_types(data, counts, errors):
	for lt in data.get("leave_types", []):
		name = lt["name"]
		# Use non-ESI quota as max_leaves_allowed; ESI quotas are encoded in
		# the Leave Policy allocations. Maternity has null quotas → leave 0.
		quota = lt.get("annual_quota_non_esi") or lt.get("annual_quota_esi") or 0
		doc = {
			"doctype": "Leave Type",
			"leave_type_name": name,
			"max_leaves_allowed": float(quota) if quota else 0.0,
			"is_carry_forward": 1 if lt.get("carry_forward") else 0,
			"allow_encashment": 1 if lt.get("encashment") else 0,
			"applicable_after": int(lt.get("applicable_after_days") or 0),
		}
		# Earned Leave: mark as earned-leave with monthly accrual flag.
		if lt.get("accrual_rule") == "1_per_20_working_days":
			doc["is_earned_leave"] = 1
			doc["earned_leave_frequency"] = "Monthly"  # closest native option
			cap = lt.get("accumulation_cap_max")
			if cap:
				doc["maximum_carry_forwarded_leaves"] = float(cap)
		# Compensatory Off
		if lt.get("code") == "COMP-OFF":
			doc["is_compensatory"] = 1
		_try_insert(doc, "Leave Type", name, counts, errors)


def seed_leave_policies(data, counts, errors):
	# Leave Policy autoname is HR-LPOL-... so frappe.db.exists by title fails.
	# Check by title field instead.
	for lp in data.get("leave_policies", []):
		title = lp["name"]
		existing = frappe.db.get_all("Leave Policy", filters={"title": title}, fields=["name"], limit=1)
		if existing:
			counts["skipped"] += 1
			continue
		details = []
		for alloc in lp.get("leave_allocations", []):
			n = alloc.get("annual_allocation")
			if n is None:
				continue
			details.append({
				"leave_type": alloc["leave_type"],
				"annual_allocation": float(n),
			})
		try:
			doc = frappe.get_doc({
				"doctype": "Leave Policy",
				"title": title,
				"leave_policy_details": details,
			})
			doc.insert(ignore_permissions=True)
			counts["created"] += 1
		except Exception as exc:
			counts["errors"] += 1
			errors.append({"entity": "Leave Policy", "identifier": title,
						   "reason": f"{type(exc).__name__}: {exc}"})
			print(f"  ERROR Leave Policy {title!r}: {exc}")


def seed_loan_types(data, counts, errors, skipped):
	# Lending app not installed → no Loan Type / Loan Product doctype.
	loan_types = data.get("loan_types", {}).get("types", [])
	for lt in loan_types:
		_log_skip(
			skipped, "Loan Type", lt["name"],
			"Doctype 'Loan Type' / 'Loan Product' not available — Lending app not installed",
		)
		counts["skipped"] += 1


def seed_shift_types(data, counts, errors):
	for s in data.get("shift_types", []):
		# Shift Type uses 'prompt' autoname → we pass `name` explicitly.
		doc = {
			"doctype": "Shift Type",
			"name": s["name"],
			"start_time": s["start_time"] + ":00",
			"end_time": s["end_time"] + ":00",
		}
		_try_insert(doc, "Shift Type", s["name"], counts, errors)


def run():
	data = _load()
	totals = {}
	skipped_rows = []
	error_rows = []

	def go(label, fn, takes_skipped=False):
		c = {"created": 0, "skipped": 0, "errors": 0}
		print(f"\n=== {label} ===")
		try:
			if takes_skipped:
				fn(data, c, error_rows, skipped_rows)
			else:
				fn(data, c, error_rows)
			frappe.db.commit()
		except Exception:
			traceback.print_exc()
		totals[label] = c
		print(f"  {label}: created={c['created']} skipped={c['skipped']} errors={c['errors']}")

	go("Branches",            seed_branches)
	go("Departments",         seed_departments)
	go("Designations",        seed_designations)
	go("Employee Grades",     seed_employee_grades)
	go("Employee Categories", seed_employee_categories, takes_skipped=True)
	go("Holiday List 2026",   seed_holiday_list)
	go("Leave Types",         seed_leave_types)
	go("Leave Policies",      seed_leave_policies)
	go("Loan Types",          seed_loan_types, takes_skipped=True)
	go("Shift Types",         seed_shift_types)

	# Write skipped log
	if skipped_rows or error_rows:
		with open(SKIP_LOG, "w", newline="") as fh:
			w = csv.DictWriter(fh, fieldnames=["entity", "identifier", "reason"])
			w.writeheader()
			for r in skipped_rows + error_rows:
				w.writerow(r)
		print(f"\nSkip log: {SKIP_LOG} ({len(skipped_rows)} skipped, {len(error_rows)} errors)")

	print("\n=== TOTALS ===")
	for label, c in totals.items():
		print(f"  {label:22} created={c['created']:3} skipped={c['skipped']:3} errors={c['errors']:3}")
	gc = sum(c["created"] for c in totals.values())
	gs = sum(c["skipped"] for c in totals.values())
	ge = sum(c["errors"]  for c in totals.values())
	print(f"  {'GRAND TOTAL':22} created={gc:3} skipped={gs:3} errors={ge:3}")
	return totals

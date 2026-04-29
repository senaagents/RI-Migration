"""HR import for avinash.localhost — Task #10. Idempotent."""
import csv
import frappe
import time
from collections import defaultdict

IMP = "/Users/aakashchid/workshop/sena/office/avinash/hr/_import"
RESULTS = {}

def step_log(name, **kw):
    RESULTS[name] = kw
    print(f"[{name}] {kw}")

def read_csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def step1_employee_new():
    rows = read_csv(f"{IMP}/02_employee_new.csv")
    created, skipped, errors = [], [], []
    oldid_to_name = {}
    # Pre-load existing map for any already-matched employees
    for r in frappe.get_all("Employee", fields=["name", "employee_number"]):
        if r.employee_number:
            oldid_to_name[r.employee_number] = r.name
    for row in rows:
        emp_no = row["employee_number"].strip()
        if not emp_no:
            errors.append({"row": row, "error": "missing employee_number"})
            continue
        if emp_no in oldid_to_name:
            skipped.append({"employee_number": emp_no, "name": oldid_to_name[emp_no], "reason": "exists"})
            continue
        try:
            payload = {
                "doctype": "Employee",
                "naming_series": row.get("naming_series") or "HR-EMP-.#####",
                "employee_number": emp_no,
                "first_name": (row.get("first_name") or "").strip() or "Unknown",
                "last_name": (row.get("last_name") or "").strip() or None,
                "company": row.get("company") or "Avinash Industries",
                "status": row.get("status") or "Active",
                "gender": (row.get("gender") or "").strip() or "Male",
                "date_of_birth": row.get("date_of_birth") or "1990-01-01",
                "date_of_joining": row.get("date_of_joining") or "2025-04-01",
                "department": (row.get("department") or "").strip() or None,
                "designation": (row.get("designation") or "").strip() or None,
                "branch": (row.get("branch") or "").strip() or None,
                "cell_number": (row.get("cell_number") or "").strip() or None,
                "personal_email": (row.get("personal_email") or "").strip() or None,
                "company_email": (row.get("company_email") or "").strip() or None,
                "marital_status": (row.get("marital_status") or "").strip() or None,
                "current_address": (row.get("current_address") or "").strip() or None,
                "permanent_address": (row.get("permanent_address") or "").strip() or None,
            }
            payload = {k: v for k, v in payload.items() if v not in (None, "")}
            doc = frappe.get_doc(payload)
            doc.insert(ignore_permissions=True)
            oldid_to_name[emp_no] = doc.name
            created.append({"employee_number": emp_no, "name": doc.name})
            # Try set Employee Group if provided
            grp = (row.get("employee_group") or "").strip()
            if grp and frappe.db.exists("Employee Group", grp):
                # link via Employee Group child table
                try:
                    eg = frappe.get_doc("Employee Group", grp)
                    if not any(d.employee == doc.name for d in (eg.employee_list or [])):
                        eg.append("employee_list", {"employee": doc.name})
                        eg.save(ignore_permissions=True)
                except Exception:
                    pass
        except Exception as exc:
            errors.append({"employee_number": emp_no, "error": str(exc)[:300]})
    frappe.db.commit()
    step_log("step1_employee_new", target=len(rows), created=len(created), skipped=len(skipped), errors=len(errors), sample_errors=errors[:5])
    return oldid_to_name


def step2_employee_enrich():
    rows = read_csv(f"{IMP}/01_employee_enrich.csv")
    updated, skipped, errors = [], [], []
    for row in rows:
        name = row["name"].strip()
        branch = row.get("branch", "").strip()
        if not (name and branch):
            errors.append({"row": row, "error": "missing name or branch"})
            continue
        try:
            current = frappe.db.get_value("Employee", name, "branch")
            if current:
                skipped.append({"name": name, "current": current})
                continue
            frappe.db.set_value("Employee", name, "branch", branch)
            updated.append({"name": name, "branch": branch})
        except Exception as exc:
            errors.append({"name": name, "error": str(exc)[:300]})
    frappe.db.commit()
    step_log("step2_employee_enrich", target=len(rows), updated=len(updated), skipped=len(skipped), errors=len(errors))


def step3_cancel_drafts():
    drafts = frappe.get_all("Salary Structure", filters={"docstatus": 0}, pluck="name")
    deleted, errors = [], []
    for n in drafts:
        try:
            frappe.delete_doc("Salary Structure", n, ignore_permissions=True, force=True)
            deleted.append(n)
        except Exception as exc:
            errors.append({"name": n, "error": str(exc)[:300]})
    frappe.db.commit()
    step_log("step3_cancel_drafts", target=len(drafts), deleted=len(deleted), errors=len(errors), sample_errors=errors[:5])


def step4_salary_structures():
    structs = read_csv(f"{IMP}/03_salary_structures.csv")
    components = read_csv(f"{IMP}/03_salary_structure_components.csv")

    # Build component lookup by structure_name
    by_struct = defaultdict(lambda: {"earnings": [], "deductions": []})
    for c in components:
        sname = c["structure_name"]
        bucket = c.get("parentfield", "earnings")
        by_struct[sname][bucket].append({
            "salary_component": c["salary_component"],
            "amount": float(c["amount"] or 0),
            "amount_based_on_formula": int(c.get("amount_based_on_formula") or 0),
            "formula": c.get("formula") or None,
            "depends_on_payment_days": int(c.get("depends_on_payment_days") or 0),
        })

    # base_ctc map for SSA
    ctc_map = {}
    created, skipped, errors = [], [], []
    for row in structs:
        name = row["name"]
        ctc_map[name] = float(row.get("base_ctc_annual") or 0)
        if frappe.db.exists("Salary Structure", name):
            existing = frappe.db.get_value("Salary Structure", name, ["docstatus"], as_dict=True)
            if existing and existing.docstatus == 1:
                skipped.append({"name": name, "reason": "already submitted"})
                continue
            # Draft exists somehow — skip (step 3 should have cleared)
            skipped.append({"name": name, "reason": f"exists docstatus={existing.docstatus if existing else '?'}"})
            continue
        try:
            payload = {
                "doctype": "Salary Structure",
                "name": name,
                "company": row.get("company") or "Avinash Industries",
                "is_active": row.get("is_active") or "Yes",
                "currency": row.get("currency") or "INR",
                "payroll_frequency": row.get("payroll_frequency") or "Monthly",
                "salary_slip_based_on_timesheet": int(row.get("salary_slip_based_on_timesheet") or 0),
                "max_benefits": float(row.get("max_benefits") or 0),
                "from_date": row.get("from_date") or "2025-04-01",
            }
            doc = frappe.get_doc(payload)
            for e in by_struct.get(name, {}).get("earnings", []):
                doc.append("earnings", e)
            for d in by_struct.get(name, {}).get("deductions", []):
                doc.append("deductions", d)
            doc.insert(ignore_permissions=True)
            doc.submit()
            created.append(name)
        except Exception as exc:
            errors.append({"name": name, "error": str(exc)[:300]})
    frappe.db.commit()
    step_log("step4_salary_structures", target=len(structs), created=len(created), skipped=len(skipped), errors=len(errors), sample_errors=errors[:5])
    return ctc_map


def step5_ssa(oldid_to_name, ctc_map):
    rows = read_csv(f"{IMP}/04_salary_structure_assignments.csv")
    created, skipped, errors = [], [], []
    for row in rows:
        emp_ref = row["employee"]
        if emp_ref.startswith("NEW:"):
            emp_no = emp_ref[4:]
            emp_name = oldid_to_name.get(emp_no)
            if not emp_name:
                errors.append({"row": emp_ref, "error": f"no employee for {emp_no}"})
                continue
        else:
            emp_name = emp_ref
            if not frappe.db.exists("Employee", emp_name):
                errors.append({"row": emp_ref, "error": "employee not found"})
                continue
        ss = row["salary_structure"]
        from_date = row.get("from_date") or "2025-04-01"
        if frappe.db.exists("Salary Structure Assignment", {"employee": emp_name, "salary_structure": ss, "from_date": from_date, "docstatus": ["<", 2]}):
            skipped.append({"employee": emp_name, "salary_structure": ss})
            continue
        try:
            base_csv = float(row.get("base") or 0)
            base = base_csv if base_csv > 0 else (ctc_map.get(ss, 0) / 12.0)
            payload = {
                "doctype": "Salary Structure Assignment",
                "employee": emp_name,
                "salary_structure": ss,
                "from_date": from_date,
                "company": row.get("company") or "Avinash Industries",
                "currency": row.get("currency") or "INR",
                "base": base,
                "variable": float(row.get("variable") or 0),
                "payroll_payable_account": row.get("payroll_payable_account") or None,
            }
            payload = {k: v for k, v in payload.items() if v not in (None, "")}
            doc = frappe.get_doc(payload)
            doc.insert(ignore_permissions=True)
            doc.submit()
            created.append({"employee": emp_name, "ss": ss})
        except Exception as exc:
            errors.append({"employee": emp_name, "ss": ss, "error": str(exc)[:300]})
    frappe.db.commit()
    step_log("step5_ssa", target=len(rows), created=len(created), skipped=len(skipped), errors=len(errors), sample_errors=errors[:5])


def step6_attendance(oldid_to_name):
    rows = read_csv(f"{IMP}/05_attendance.csv")
    created, skipped, errors = 0, 0, []
    BATCH = 500
    t0 = time.time()
    for i, row in enumerate(rows):
        emp_ref = row["employee"]
        if emp_ref.startswith("NEW:"):
            emp_no = emp_ref[4:]
            emp_name = oldid_to_name.get(emp_no)
            if not emp_name:
                if len(errors) < 20:
                    errors.append({"row": emp_ref, "error": f"no employee for {emp_no}"})
                continue
        else:
            emp_name = emp_ref
        att_date = row["attendance_date"]
        if frappe.db.exists("Attendance", {"employee": emp_name, "attendance_date": att_date, "docstatus": ["<", 2]}):
            skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Attendance",
                "naming_series": "HR-ATT-.YYYY.-",
                "employee": emp_name,
                "attendance_date": att_date,
                "status": row.get("status") or "Present",
                "company": row.get("company") or "Avinash Industries",
            })
            if row.get("leave_type"):
                doc.leave_type = row["leave_type"]
            if row.get("shift"):
                doc.shift = row["shift"]
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as exc:
            if len(errors) < 30:
                errors.append({"employee": emp_name, "date": att_date, "error": str(exc)[:200]})
        if (i + 1) % BATCH == 0:
            frappe.db.commit()
            elapsed = time.time() - t0
            print(f"  attendance batch {i+1}/{len(rows)} created={created} skipped={skipped} errors={len(errors)} ({elapsed:.1f}s)")
    frappe.db.commit()
    step_log("step6_attendance", target=len(rows), created=created, skipped=skipped, errors=len(errors), sample_errors=errors[:5])


def step7_leave_alloc():
    rows = read_csv(f"{IMP}/06_leave_allocations.csv")
    created, skipped, errors = 0, 0, []
    for row in rows:
        emp = row["employee"]
        lt = row["leave_type"]
        fd = row["from_date"]
        td = row["to_date"]
        if frappe.db.exists("Leave Allocation", {"employee": emp, "leave_type": lt, "from_date": fd, "to_date": td, "docstatus": ["<", 2]}):
            skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Leave Allocation",
                "naming_series": "HR-LAL-.YYYY.-",
                "employee": emp,
                "leave_type": lt,
                "from_date": fd,
                "to_date": td,
                "new_leaves_allocated": float(row.get("new_leaves_allocated") or 0),
                "carry_forward": int(row.get("carry_forward") or 0),
                "company": row.get("company") or "Avinash Industries",
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as exc:
            if len(errors) < 20:
                errors.append({"employee": emp, "leave_type": lt, "error": str(exc)[:200]})
    frappe.db.commit()
    step_log("step7_leave_alloc", target=len(rows), created=created, skipped=skipped, errors=len(errors), sample_errors=errors[:5])


def step8_leave_app(oldid_to_name):
    rows = read_csv(f"{IMP}/07_leave_applications.csv")
    created, skipped, errors = 0, 0, []
    for row in rows:
        emp_ref = row["employee"]
        if emp_ref.startswith("NEW:"):
            emp_no = emp_ref[4:]
            emp_name = oldid_to_name.get(emp_no)
            if not emp_name:
                if len(errors) < 20:
                    errors.append({"row": emp_ref, "error": f"no employee for {emp_no}"})
                continue
        else:
            emp_name = emp_ref
        lt = row["leave_type"]
        fd = row["from_date"]
        td = row["to_date"]
        if frappe.db.exists("Leave Application", {"employee": emp_name, "leave_type": lt, "from_date": fd, "to_date": td, "docstatus": ["<", 2]}):
            skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Leave Application",
                "naming_series": "HR-LAP-.YYYY.-",
                "employee": emp_name,
                "leave_type": lt,
                "from_date": fd,
                "to_date": td,
                "status": row.get("status") or "Approved",
                "posting_date": row.get("posting_date") or fd,
                "total_leave_days": float(row.get("total_leave_days") or 1),
                "company": row.get("company") or "Avinash Industries",
                "leave_approver": "Administrator",
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as exc:
            if len(errors) < 20:
                errors.append({"employee": emp_name, "leave_type": lt, "error": str(exc)[:200]})
    frappe.db.commit()
    step_log("step8_leave_app", target=len(rows), created=created, skipped=skipped, errors=len(errors), sample_errors=errors[:5])


def step9_separations(oldid_to_name):
    sep_rows = read_csv(f"{IMP}/08_separations.csv")
    fnf_rows = read_csv(f"{IMP}/08_full_and_final.csv")
    sep_created, sep_skipped, sep_errors = 0, 0, []
    fnf_created, fnf_skipped, fnf_errors = 0, 0, []

    def resolve_emp(ref, emp_no):
        if not ref:
            return None
        if ref.startswith("UNMATCHED:"):
            return None
        if ref.startswith("NEW:"):
            return oldid_to_name.get(emp_no or ref[4:])
        if frappe.db.exists("Employee", ref):
            return ref
        return None

    for row in sep_rows:
        emp = resolve_emp(row.get("employee"), row.get("employee_number"))
        if not emp:
            sep_skipped += 1
            continue
        if frappe.db.exists("Employee Separation", {"employee": emp, "boarding_begins_on": row.get("boarding_begins_on")}):
            sep_skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Employee Separation",
                "employee": emp,
                "company": row.get("company") or "Avinash Industries",
                "boarding_begins_on": row.get("boarding_begins_on") or "2026-04-25",
                "resignation_letter_date": row.get("resignation_letter_date") or None,
                "reason_for_leaving": row.get("reason_for_leaving") or None,
                "leave_encashed": row.get("leave_encashed") or "No",
                "encashment_date": row.get("encashment_date") or None,
                "notes": "DATE_NEEDS_REVIEW (placeholder from rsync mtime)",
            })
            doc = frappe.get_doc({k: v for k, v in doc.as_dict().items() if v not in (None, "")})
            doc.insert(ignore_permissions=True)
            doc.submit()
            sep_created += 1
        except Exception as exc:
            if len(sep_errors) < 20:
                sep_errors.append({"employee": emp, "error": str(exc)[:200]})

    for row in fnf_rows:
        emp = resolve_emp(row.get("employee"), row.get("employee_number"))
        if not emp:
            fnf_skipped += 1
            continue
        if frappe.db.exists("Full and Final Statement", {"employee": emp, "transaction_date": row.get("transaction_date")}):
            fnf_skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Full and Final Statement",
                "employee": emp,
                "transaction_date": row.get("transaction_date") or "2026-04-25",
                "company": row.get("company") or "Avinash Industries",
                "status": "Draft",
            })
            doc.insert(ignore_permissions=True)
            # do NOT submit — placeholder dates
            fnf_created += 1
        except Exception as exc:
            if len(fnf_errors) < 20:
                fnf_errors.append({"employee": emp, "error": str(exc)[:200]})
    frappe.db.commit()
    step_log("step9_separations", target=len(sep_rows), created=sep_created, skipped=sep_skipped, errors=len(sep_errors), sample_errors=sep_errors[:5])
    step_log("step9_fnf", target=len(fnf_rows), created=fnf_created, skipped=fnf_skipped, errors=len(fnf_errors), sample_errors=fnf_errors[:5])


def run():
    print("=== STEP 1 employee_new ===")
    oldid_to_name = step1_employee_new()
    print(f"oldid_to_name size: {len(oldid_to_name)}")
    print("=== STEP 2 employee_enrich ===")
    step2_employee_enrich()
    print("=== STEP 3 cancel_drafts ===")
    step3_cancel_drafts()
    print("=== STEP 4 salary_structures ===")
    ctc_map = step4_salary_structures()
    print("=== STEP 5 SSA ===")
    step5_ssa(oldid_to_name, ctc_map)
    print("=== STEP 6 attendance ===")
    step6_attendance(oldid_to_name)
    print("=== STEP 7 leave_alloc ===")
    step7_leave_alloc()
    print("=== STEP 8 leave_app ===")
    step8_leave_app(oldid_to_name)
    print("=== STEP 9 separations + FFS ===")
    step9_separations(oldid_to_name)

    print("\n=== POST COUNTS ===")
    for dt in ["Employee", "Salary Structure", "Salary Structure Assignment", "Attendance", "Leave Allocation", "Leave Application", "Employee Separation", "Full and Final Statement"]:
        print(f"{dt}: {frappe.db.count(dt)}")

    import json
    print("\n=== RESULTS_JSON_START ===")
    print(json.dumps(RESULTS, default=str))
    print("=== RESULTS_JSON_END ===")


run()

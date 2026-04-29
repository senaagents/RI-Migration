"""HR import pass 2 — fix SSA, leave_alloc resolver, separations holiday_list+relieving."""
import csv
import frappe
from collections import defaultdict

IMP = "/Users/aakashchid/workshop/sena/office/avinash/hr/_import"
RESULTS = {}

def slog(name, **kw):
    RESULTS[name] = kw
    print(f"[{name}] {kw}")

def read_csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def ensure_payroll_payable_account():
    name = "Payroll Payable - AI"
    if frappe.db.exists("Account", name):
        return name
    parent = "Current Liabilities - AI"
    doc = frappe.get_doc({
        "doctype": "Account",
        "account_name": "Payroll Payable",
        "parent_account": parent,
        "company": "Avinash Industries",
        "is_group": 0,
        "root_type": "Liability",
        "report_type": "Balance Sheet",
        "account_type": "Payable",
        "account_currency": "INR",
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def build_oldid_map():
    m = {}
    for r in frappe.get_all("Employee", fields=["name", "employee_number"]):
        if r.employee_number:
            m[r.employee_number] = r.name
    return m


def resolve_emp(ref, emp_no, oldid_to_name):
    if not ref:
        return None
    if ref.startswith("UNMATCHED:"):
        return None
    if ref.startswith("NEW:"):
        return oldid_to_name.get(emp_no or ref[4:])
    if frappe.db.exists("Employee", ref):
        return ref
    return None


def step5_ssa_retry():
    payable = ensure_payroll_payable_account()
    print(f"  Using payable account: {payable}")
    rows = read_csv(f"{IMP}/04_salary_structure_assignments.csv")
    structs = {r["name"]: r for r in read_csv(f"{IMP}/03_salary_structures.csv")}
    oldid_to_name = build_oldid_map()
    created, skipped, errors = 0, 0, []
    for row in rows:
        emp_name = resolve_emp(row["employee"], row.get("employee_number"), oldid_to_name)
        if not emp_name:
            errors.append({"row": row["employee"], "error": "unresolved"})
            continue
        ss = row["salary_structure"]
        from_date = row.get("from_date") or "2025-04-01"
        if frappe.db.exists("Salary Structure Assignment", {"employee": emp_name, "salary_structure": ss, "from_date": from_date, "docstatus": ["<", 2]}):
            skipped += 1
            continue
        try:
            base_csv = float(row.get("base") or 0)
            if base_csv <= 0:
                ann = float(structs.get(ss, {}).get("base_ctc_annual") or 0)
                base_csv = ann / 12.0
            doc = frappe.get_doc({
                "doctype": "Salary Structure Assignment",
                "employee": emp_name,
                "salary_structure": ss,
                "from_date": from_date,
                "company": "Avinash Industries",
                "currency": "INR",
                "base": base_csv,
                "variable": float(row.get("variable") or 0),
                "payroll_payable_account": payable,
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as exc:
            if len(errors) < 20:
                errors.append({"employee": emp_name, "ss": ss, "error": str(exc)[:300]})
    frappe.db.commit()
    slog("step5_ssa_retry", target=len(rows), created=created, skipped=skipped, errors=len(errors), sample_errors=errors[:5])


def step7_leave_alloc_retry():
    """Re-run with NEW: resolver."""
    rows = read_csv(f"{IMP}/06_leave_allocations.csv")
    oldid_to_name = build_oldid_map()
    created, skipped, errors = 0, 0, []
    for row in rows:
        emp_ref = row["employee"]
        if emp_ref.startswith("NEW:"):
            emp_no = emp_ref[4:]
            emp = oldid_to_name.get(emp_no)
            if not emp:
                if len(errors) < 20:
                    errors.append({"row": emp_ref, "error": f"no employee for {emp_no}"})
                continue
        else:
            emp = emp_ref
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
                "company": "Avinash Industries",
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as exc:
            if len(errors) < 20:
                errors.append({"employee": emp, "leave_type": lt, "error": str(exc)[:200]})
    frappe.db.commit()
    slog("step7_leave_alloc_retry", target=len(rows), created=created, skipped=skipped, errors=len(errors), sample_errors=errors[:5])


def step8_leave_app_retry():
    rows = read_csv(f"{IMP}/07_leave_applications.csv")
    oldid_to_name = build_oldid_map()
    created, skipped_dup, skipped_noemp, errors = 0, 0, 0, []
    for row in rows:
        emp_ref = row["employee"]
        if emp_ref.startswith("NEW:"):
            emp_no = emp_ref[4:]
            emp = oldid_to_name.get(emp_no)
            if not emp:
                skipped_noemp += 1
                continue
        else:
            emp = emp_ref
        lt = row["leave_type"]
        fd = row["from_date"]
        td = row["to_date"]
        if frappe.db.exists("Leave Application", {"employee": emp, "leave_type": lt, "from_date": fd, "to_date": td, "docstatus": ["<", 2]}):
            skipped_dup += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Leave Application",
                "naming_series": "HR-LAP-.YYYY.-",
                "employee": emp,
                "leave_type": lt,
                "from_date": fd,
                "to_date": td,
                "status": row.get("status") or "Approved",
                "posting_date": row.get("posting_date") or fd,
                "total_leave_days": float(row.get("total_leave_days") or 1),
                "company": "Avinash Industries",
                "leave_approver": "Administrator",
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as exc:
            if len(errors) < 20:
                errors.append({"employee": emp, "leave_type": lt, "error": str(exc)[:250]})
    frappe.db.commit()
    slog("step8_leave_app_retry", target=len(rows), created=created, skipped_dup=skipped_dup, skipped_noemp=skipped_noemp, errors=len(errors), sample_errors=errors[:5])


def step9_separations_retry():
    """Set holiday_list + relieving_date on target employees, then create separations + FFS."""
    sep_rows = read_csv(f"{IMP}/08_separations.csv")
    fnf_rows = read_csv(f"{IMP}/08_full_and_final.csv")
    oldid_to_name = build_oldid_map()

    # Fix preconditions on each candidate employee
    fixed = 0
    sep_emp_dates = {}  # emp -> boarding_begins_on
    for row in sep_rows:
        emp = resolve_emp(row.get("employee"), row.get("employee_number"), oldid_to_name)
        if emp:
            sep_emp_dates[emp] = row.get("boarding_begins_on") or "2026-04-25"
            cur_hl = frappe.db.get_value("Employee", emp, "holiday_list")
            if not cur_hl:
                frappe.db.set_value("Employee", emp, "holiday_list", "Avinash 2025-26")
                fixed += 1
    frappe.db.commit()
    print(f"  patched holiday_list on {fixed} employees")

    # Existing separations from pass 1: 20 created, but step output said skipped=100/created=0 with 20 errors?
    # Actually pass 1 step9 results: created=0, skipped=100, errors=20. The 20 holiday-list errors prevented submit.
    # Live count shows Employee Separation: 20 — those were inserted but not submitted (or were submitted before validation).
    # We'll find any drafts and try to submit; insert any missing.

    sep_created, sep_skipped, sep_submitted, sep_errors = 0, 0, 0, []
    for row in sep_rows:
        emp = resolve_emp(row.get("employee"), row.get("employee_number"), oldid_to_name)
        if not emp:
            sep_skipped += 1
            continue
        bbo = row.get("boarding_begins_on") or "2026-04-25"
        existing = frappe.db.get_value("Employee Separation", {"employee": emp, "boarding_begins_on": bbo}, ["name", "docstatus"], as_dict=True)
        if existing:
            if existing.docstatus == 0:
                try:
                    d = frappe.get_doc("Employee Separation", existing.name)
                    d.submit()
                    sep_submitted += 1
                except Exception as exc:
                    if len(sep_errors) < 10:
                        sep_errors.append({"employee": emp, "stage": "submit-existing", "error": str(exc)[:250]})
            else:
                sep_skipped += 1
            continue
        try:
            d = frappe.get_doc({
                "doctype": "Employee Separation",
                "employee": emp,
                "company": "Avinash Industries",
                "boarding_begins_on": bbo,
                "resignation_letter_date": row.get("resignation_letter_date") or None,
                "reason_for_leaving": row.get("reason_for_leaving") or None,
                "leave_encashed": row.get("leave_encashed") or "No",
                "encashment_date": row.get("encashment_date") or None,
                "notes": "DATE_NEEDS_REVIEW (placeholder from rsync mtime)",
            })
            d = frappe.get_doc({k: v for k, v in d.as_dict().items() if v not in (None, "")})
            d.insert(ignore_permissions=True)
            d.submit()
            sep_created += 1
        except Exception as exc:
            if len(sep_errors) < 10:
                sep_errors.append({"employee": emp, "stage": "insert", "error": str(exc)[:250]})

    # FFS: needs Relieving Date on Employee
    fnf_created, fnf_skipped, fnf_errors = 0, 0, []
    relieving_set = 0
    for row in fnf_rows:
        emp = resolve_emp(row.get("employee"), row.get("employee_number"), oldid_to_name)
        if not emp:
            fnf_skipped += 1
            continue
        td = row.get("transaction_date") or "2026-04-25"
        cur_rel = frappe.db.get_value("Employee", emp, "relieving_date")
        if not cur_rel:
            frappe.db.set_value("Employee", emp, "relieving_date", td)
            relieving_set += 1
        if frappe.db.exists("Full and Final Statement", {"employee": emp, "transaction_date": td}):
            fnf_skipped += 1
            continue
        try:
            d = frappe.get_doc({
                "doctype": "Full and Final Statement",
                "employee": emp,
                "transaction_date": td,
                "company": "Avinash Industries",
                "status": "Draft",
            })
            d.insert(ignore_permissions=True)
            fnf_created += 1
        except Exception as exc:
            if len(fnf_errors) < 10:
                fnf_errors.append({"employee": emp, "error": str(exc)[:250]})
    frappe.db.commit()
    slog("step9_separations_retry", target=len(sep_rows), created=sep_created, submitted_existing=sep_submitted, skipped=sep_skipped, errors=len(sep_errors), sample_errors=sep_errors[:5])
    slog("step9_fnf_retry", target=len(fnf_rows), created=fnf_created, relieving_set=relieving_set, skipped=fnf_skipped, errors=len(fnf_errors), sample_errors=fnf_errors[:5])


def run():
    print("=== STEP 5 SSA retry ===")
    step5_ssa_retry()
    print("=== STEP 7 leave_alloc retry (NEW: resolver) ===")
    step7_leave_alloc_retry()
    print("=== STEP 8 leave_app retry ===")
    step8_leave_app_retry()
    print("=== STEP 9 separations + FFS retry ===")
    step9_separations_retry()

    print("\n=== POST COUNTS ===")
    for dt in ["Employee", "Salary Structure", "Salary Structure Assignment", "Attendance", "Leave Allocation", "Leave Application", "Employee Separation", "Full and Final Statement"]:
        print(f"{dt}: {frappe.db.count(dt)}")

    import json
    print("\n=== RESULTS_JSON_START ===")
    print(json.dumps(RESULTS, default=str))
    print("=== RESULTS_JSON_END ===")


run()

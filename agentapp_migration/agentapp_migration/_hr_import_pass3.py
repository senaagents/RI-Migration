"""HR import pass 3 — backfill holiday_list, retry leave applications, submit existing separations."""
import csv
import frappe

IMP = "/Users/aakashchid/workshop/sena/office/avinash/hr/_import"
RESULTS = {}

def slog(name, **kw):
    RESULTS[name] = kw
    print(f"[{name}] {kw}")

def read_csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def backfill_holiday_list():
    """Set holiday_list = Avinash 2025-26 on every Employee that has none."""
    blank = frappe.get_all("Employee", filters={"holiday_list": ["in", ["", None]]}, pluck="name")
    if not blank:
        # Frappe filter may not match NULL; do explicit SQL
        blank = [r[0] for r in frappe.db.sql("SELECT name FROM `tabEmployee` WHERE holiday_list IS NULL OR holiday_list = ''")]
    for emp in blank:
        frappe.db.set_value("Employee", emp, "holiday_list", "Avinash 2025-26")
    frappe.db.commit()
    slog("backfill_holiday_list", patched=len(blank))


def build_oldid_map():
    m = {}
    for r in frappe.get_all("Employee", fields=["name", "employee_number"]):
        if r.employee_number:
            m[r.employee_number] = r.name
    return m


def step8_leave_app_retry2():
    rows = read_csv(f"{IMP}/07_leave_applications.csv")
    oldid_to_name = build_oldid_map()
    # Pre-load allocations: dict (employee, leave_type) -> [(from_date, to_date), ...]
    allocs = {}
    for a in frappe.get_all("Leave Allocation", filters={"docstatus": 1}, fields=["employee", "leave_type", "from_date", "to_date"]):
        allocs.setdefault((a.employee, a.leave_type), []).append((a.from_date, a.to_date))

    created, skipped_dup, skipped_noemp, skipped_no_alloc, errors = 0, 0, 0, 0, []
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
        # Allocation check
        from datetime import date
        def to_d(s):
            return date.fromisoformat(s) if isinstance(s, str) else s
        fd_d, td_d = to_d(fd), to_d(td)
        emp_allocs = allocs.get((emp, lt), [])
        in_alloc = any(to_d(a_fd) <= fd_d and td_d <= to_d(a_td) for a_fd, a_td in emp_allocs)
        if not in_alloc:
            skipped_no_alloc += 1
            continue
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
            if len(errors) < 30:
                errors.append({"employee": emp, "leave_type": lt, "from": fd, "error": str(exc)[:250]})
    frappe.db.commit()
    slog("step8_leave_app_retry2", target=len(rows), created=created, skipped_dup=skipped_dup, skipped_noemp=skipped_noemp, skipped_no_alloc=skipped_no_alloc, errors=len(errors), sample_errors=errors[:5])


def submit_pending_separations():
    drafts = frappe.get_all("Employee Separation", filters={"docstatus": 0}, pluck="name")
    submitted, errors = 0, []
    for n in drafts:
        try:
            d = frappe.get_doc("Employee Separation", n)
            d.submit()
            submitted += 1
        except Exception as exc:
            if len(errors) < 10:
                errors.append({"name": n, "error": str(exc)[:250]})
    frappe.db.commit()
    slog("submit_pending_separations", drafts=len(drafts), submitted=submitted, errors=len(errors), sample_errors=errors[:5])


def fix_one_ssa_doj_conflict():
    """HR-EMP-00125 has DOJ 2025-04-28; SSA wants from_date 2025-04-01. Use DOJ as from_date."""
    rows = read_csv(f"{IMP}/04_salary_structure_assignments.csv")
    oldid_to_name = build_oldid_map()
    target_emp = "HR-EMP-00125"
    target_ss = "SS-E00287"
    if frappe.db.exists("Salary Structure Assignment", {"employee": target_emp, "salary_structure": target_ss, "docstatus": ["<", 2]}):
        slog("fix_doj_ssa", status="already exists")
        return
    payable = "Payroll Payable - AI"
    structs = {r["name"]: r for r in read_csv(f"{IMP}/03_salary_structures.csv")}
    matching = [r for r in rows if r["employee_number"] == "E00287"]
    if not matching:
        slog("fix_doj_ssa", status="row not found")
        return
    row = matching[0]
    doj = frappe.db.get_value("Employee", target_emp, "date_of_joining")
    try:
        ann = float(structs.get(target_ss, {}).get("base_ctc_annual") or 0)
        base = ann / 12.0
        d = frappe.get_doc({
            "doctype": "Salary Structure Assignment",
            "employee": target_emp,
            "salary_structure": target_ss,
            "from_date": str(doj),
            "company": "Avinash Industries",
            "currency": "INR",
            "base": base,
            "payroll_payable_account": payable,
        })
        d.insert(ignore_permissions=True)
        d.submit()
        slog("fix_doj_ssa", status="created", from_date=str(doj))
    except Exception as exc:
        slog("fix_doj_ssa", status="failed", error=str(exc)[:300])


def run():
    print("=== Backfill holiday_list on all Employees ===")
    backfill_holiday_list()
    print("=== Fix DOJ conflict for SSA HR-EMP-00125 ===")
    fix_one_ssa_doj_conflict()
    print("=== Step 8 leave_app retry with allocation pre-check ===")
    step8_leave_app_retry2()
    print("=== Submit pending Employee Separation drafts ===")
    submit_pending_separations()

    print("\n=== POST COUNTS ===")
    for dt in ["Employee", "Salary Structure", "Salary Structure Assignment", "Attendance", "Leave Allocation", "Leave Application", "Employee Separation", "Full and Final Statement"]:
        print(f"{dt}: {frappe.db.count(dt)}  (submitted={frappe.db.count(dt, {'docstatus': 1})})" if dt not in ("Employee","Holiday List") else f"{dt}: {frappe.db.count(dt)}")

    import json
    print("\n=== RESULTS_JSON_START ===")
    print(json.dumps(RESULTS, default=str))
    print("=== RESULTS_JSON_END ===")


run()

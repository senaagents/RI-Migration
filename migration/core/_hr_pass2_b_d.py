"""HR pass-2 B1+B2+D-prereq: incremental employees, attendance, leave, orphan replay."""
import csv
import frappe
import time
from datetime import date

INC = "/Users/aakashchid/workshop/sena/office/avinash/hr/_import/incremental"
IMP = "/Users/aakashchid/workshop/sena/office/avinash/hr/_import"
RUNLOG = "/Users/aakashchid/workshop/sena/office/avinash/hr/_runlog"
COMPANY = "Avinash Industries"

RESULTS = {}

def slog(name, **kw):
    RESULTS[name] = kw
    print(f"[{name}] {kw}")

def read_csv(p):
    with open(p, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def step0_employee_groups():
    needed = ["Contract Labour", "Neem Trainee", "NAPS Apprentice", "Apprentice"]
    created, skipped = [], []
    for g in needed:
        if frappe.db.exists("Employee Group", g):
            skipped.append(g)
            continue
        try:
            frappe.get_doc({"doctype": "Employee Group", "employee_group_name": g}).insert(ignore_permissions=True)
            created.append(g)
        except Exception as exc:
            print(f"  err {g}: {exc}")
    frappe.db.commit()
    slog("step0_employee_groups", created=len(created), skipped=len(skipped), names_created=created)


def insert_employees(csv_path, label):
    rows = read_csv(csv_path)
    created, skipped, errors = [], [], []
    # Pre-load existing
    existing = {r.employee_number: r.name for r in frappe.get_all("Employee", fields=["name", "employee_number"]) if r.employee_number}
    for row in rows:
        ext = (row.get("employee_number") or "").strip()
        if not ext:
            errors.append({"row": row, "error": "missing employee_number"})
            continue
        if ext in existing:
            skipped.append(ext)
            continue
        try:
            payload = {
                "doctype": "Employee",
                "naming_series": row.get("naming_series") or "HR-EMP-.#####",
                "employee_number": ext,
                "first_name": (row.get("first_name") or "").strip() or "Unknown",
                "last_name": (row.get("last_name") or "").strip() or None,
                "company": row.get("company") or COMPANY,
                "status": row.get("status") or "Active",
                "gender": (row.get("gender") or "").strip() or "Male",
                "date_of_birth": row.get("date_of_birth") or "1990-01-01",
                "date_of_joining": row.get("date_of_joining") or "2025-04-01",
                "department": (row.get("department") or "").strip() or None,
                "designation": (row.get("designation") or "").strip() or None,
                "branch": (row.get("branch") or "").strip() or None,
                "father_name": (row.get("father_name") or "").strip() or None,
                "provident_fund_account": (row.get("provident_fund_account") or "").strip() or None,
                "pan_number": (row.get("pan_number") or "").strip() or None,
                "bank_ac_no": (row.get("bank_account_no") or "").strip() or None,
                "bank_name": (row.get("bank_name") or "").strip() or None,
                "ifsc_code": (row.get("ifsc_code") or "").strip() or None,
                "holiday_list": "Avinash 2025-26",
            }
            # Status=Left: ensure relieving_date set
            if payload["status"] == "Left":
                payload["relieving_date"] = row.get("date_of_leaving") or "2025-04-01"
                payload["date_of_leaving"] = row.get("date_of_leaving") or "2025-04-01"
            payload = {k: v for k, v in payload.items() if v not in (None, "")}
            doc = frappe.get_doc(payload)
            doc.insert(ignore_permissions=True)
            existing[ext] = doc.name
            created.append({"ext": ext, "name": doc.name})
            # Employee Group linkage if specified
            grp = (row.get("employee_group") or "").strip()
            if grp and frappe.db.exists("Employee Group", grp):
                try:
                    eg = frappe.get_doc("Employee Group", grp)
                    if not any(d.employee == doc.name for d in (eg.employee_list or [])):
                        eg.append("employee_list", {"employee": doc.name})
                        eg.save(ignore_permissions=True)
                except Exception:
                    pass
        except Exception as exc:
            if len(errors) < 20:
                errors.append({"ext": ext, "error": str(exc)[:300]})
    frappe.db.commit()
    slog(label, target=len(rows), created=len(created), skipped=len(skipped), errors=len(errors), sample_errors=errors[:5])


def build_oldid_map():
    return {r.employee_number: r.name for r in frappe.get_all("Employee", fields=["name", "employee_number"]) if r.employee_number}


def insert_attendance(csv_path, label, oldid_to_name):
    rows = read_csv(csv_path)
    BATCH = 500
    created, skipped, skipped_noemp, errors = 0, 0, 0, []
    t0 = time.time()
    unmapped = set()
    for i, row in enumerate(rows):
        ref = row.get("employee", "")
        if ref.startswith("NEW:"):
            ext = ref[4:]
            emp = oldid_to_name.get(ext)
            if not emp:
                skipped_noemp += 1
                unmapped.add(ext)
                continue
        else:
            emp = ref
        att_date = row.get("attendance_date")
        if not att_date:
            continue
        if frappe.db.exists("Attendance", {"employee": emp, "attendance_date": att_date, "docstatus": ["<", 2]}):
            skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Attendance",
                "naming_series": "HR-ATT-.YYYY.-",
                "employee": emp,
                "attendance_date": att_date,
                "status": row.get("status") or "Present",
                "company": row.get("company") or COMPANY,
            })
            if row.get("leave_type"):
                doc.leave_type = row["leave_type"]
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as exc:
            if len(errors) < 20:
                errors.append({"emp": emp, "date": att_date, "error": str(exc)[:200]})
        if (i + 1) % BATCH == 0:
            frappe.db.commit()
            elapsed = time.time() - t0
            print(f"  {label} batch {i+1}/{len(rows)} created={created} skipped={skipped} noemp={skipped_noemp} errors={len(errors)} ({elapsed:.1f}s)")
    frappe.db.commit()
    slog(label, target=len(rows), created=created, skipped_dup=skipped, skipped_noemp=skipped_noemp, errors=len(errors),
         unmapped_empnums=sorted(unmapped)[:30], unmapped_count=len(unmapped), sample_errors=errors[:5])


def insert_leave_alloc(csv_path, label, oldid_to_name):
    rows = read_csv(csv_path)
    created, skipped, skipped_noemp, errors = 0, 0, 0, []
    for row in rows:
        ref = row.get("employee", "")
        if ref.startswith("NEW:"):
            ext = ref[4:]
            emp = oldid_to_name.get(ext)
            if not emp:
                skipped_noemp += 1
                continue
        else:
            emp = ref
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
                "company": COMPANY,
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as exc:
            if len(errors) < 20:
                errors.append({"emp": emp, "lt": lt, "error": str(exc)[:250]})
    frappe.db.commit()
    slog(label, target=len(rows), created=created, skipped_dup=skipped, skipped_noemp=skipped_noemp, errors=len(errors), sample_errors=errors[:5])


def insert_leave_app(csv_path, label, oldid_to_name):
    rows = read_csv(csv_path)
    # Pre-load allocations for pre-check
    allocs = {}
    for a in frappe.get_all("Leave Allocation", filters={"docstatus": 1}, fields=["employee", "leave_type", "from_date", "to_date"]):
        allocs.setdefault((a.employee, a.leave_type), []).append((a.from_date, a.to_date))

    from datetime import date as _date
    def to_d(s):
        return _date.fromisoformat(s) if isinstance(s, str) else s

    created, skipped_dup, skipped_noemp, skipped_no_alloc, errors = 0, 0, 0, 0, []
    for row in rows:
        ref = row.get("employee", "")
        if ref.startswith("NEW:"):
            ext = ref[4:]
            emp = oldid_to_name.get(ext)
            if not emp:
                skipped_noemp += 1
                continue
        else:
            emp = ref
        lt = row["leave_type"]
        fd = row["from_date"]
        td = row["to_date"]
        emp_allocs = allocs.get((emp, lt), [])
        if not any(to_d(a_fd) <= to_d(fd) and to_d(td) <= to_d(a_td) for a_fd, a_td in emp_allocs):
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
                "company": COMPANY,
                "leave_approver": "Administrator",
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as exc:
            if len(errors) < 30:
                errors.append({"emp": emp, "lt": lt, "from": fd, "error": str(exc)[:250]})
    frappe.db.commit()
    slog(label, target=len(rows), created=created, skipped_dup=skipped_dup, skipped_noemp=skipped_noemp, skipped_no_alloc=skipped_no_alloc, errors=len(errors), sample_errors=errors[:5])


def replay_orig_attendance(oldid_to_name):
    """Replay 05_attendance.csv now that more employees exist."""
    label = "step6_orig_attendance_replay"
    insert_attendance(f"{IMP}/05_attendance.csv", label, oldid_to_name)


def step7_unmatched_separations_retry():
    """Re-run UNMATCHED separation fuzzy match against the now-larger Employee table."""
    from rapidfuzz import process, fuzz
    import re

    sep_rows = read_csv(f"{IMP}/08_separations.csv")
    employees = frappe.get_all("Employee", fields=["name", "employee_name", "employee_number"])
    name_to_emp = {(e.employee_name or "").lower().strip(): e for e in employees if e.employee_name}
    candidate_names = list(name_to_emp.keys())

    def normalize(s):
        return re.sub(r"[^a-z ]", " ", (s or "").lower()).strip()

    def first_token(s):
        toks = normalize(s).split()
        return toks[0] if toks else ""

    matched, ambiguous, none_match = [], [], []
    for row in sep_rows:
        ref = row.get("employee", "")
        if not ref.startswith("UNMATCHED:"):
            continue
        src_name = row.get("employee_name_source") or ref[len("UNMATCHED:"):]
        src_name_n = normalize(src_name)
        src_first = first_token(src_name)
        results = process.extract(src_name_n, candidate_names, scorer=fuzz.WRatio, limit=5)
        top = []
        for name, score, _ in results:
            if score < 85:
                continue
            cand_first = first_token(name)
            if score >= 92 or (cand_first and cand_first == src_first):
                top.append((name, score))
        if len(top) == 0:
            none_match.append({"src": src_name, "best": results[0] if results else None})
        elif len(top) == 1 or (len(top) > 1 and top[0][1] - top[1][1] >= 5):
            emp = name_to_emp[top[0][0]]
            matched.append({"src": src_name, "match_name": emp.name, "match_employee_name": emp.employee_name, "score": top[0][1]})
        else:
            ambiguous.append({"src": src_name, "candidates": [(name_to_emp[n].name, n, s) for n, s in top]})

    # Persist updated map
    out_path = f"{RUNLOG}/_unmatched_match_map.csv"
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["src_name", "matched_employee", "matched_employee_name", "score"])
        for m in matched:
            w.writerow([m["src"], m["match_name"], m["match_employee_name"], m["score"]])

    slog("step7_unmatched_retry",
         total_unmatched_in_csv=sum(1 for r in sep_rows if r["employee"].startswith("UNMATCHED:")),
         matched=len(matched), ambiguous=len(ambiguous), none_match=len(none_match),
         sample_matched=matched[:5], sample_ambiguous=ambiguous[:5], sample_none=none_match[:5])
    return matched, ambiguous, none_match


def step8_audit_03b():
    """Append a 03b section to 03_audit.md with before/after deltas."""
    out = []
    out.append("\n\n---\n\n# Runlog 03b — Pass2 update (Task #12 phase B+D)\n")
    out.append("**Run:** 2026-04-25 (post B1+B2+D-prereq)\n\n")
    out.append("## Live counts (after pass 2)\n")
    out.append("| DocType | Total | Submitted | Draft |\n|---|---|---|---|")
    deltas = {}
    for dt in ["Employee", "Salary Structure", "Salary Structure Assignment", "Salary Slip",
               "Attendance", "Leave Allocation", "Leave Application",
               "Employee Separation", "Full and Final Statement",
               "Holiday List Assignment", "Salary Component", "Leave Type",
               "Employee Group"]:
        total = frappe.db.count(dt)
        if frappe.get_meta(dt).is_submittable:
            sub = frappe.db.count(dt, {"docstatus": 1})
            drf = frappe.db.count(dt, {"docstatus": 0})
        else:
            sub, drf = "n/a", "n/a"
        out.append(f"| {dt} | {total} | {sub} | {drf} |")
        deltas[dt] = total

    # Per-month coverage
    out.append("\n## Attendance per-month coverage (post pass 2)\n| Month | Att rows | Distinct emp |\n|---|---|---|")
    months = []
    y, m = 2025, 4
    while (y, m) <= (2026, 4):
        months.append((y, m))
        m += 1
        if m == 13: m, y = 1, y+1
    for (yy, mm) in months:
        first = date(yy, mm, 1)
        if mm == 12:
            last = date(yy+1, 1, 1)
        else:
            last = date(yy, mm+1, 1)
        rows = frappe.db.sql("""SELECT COUNT(*), COUNT(DISTINCT employee) FROM `tabAttendance` WHERE attendance_date >= %s AND attendance_date < %s AND docstatus = 1""", (first, last))
        cnt, distinct = rows[0]
        out.append(f"| {yy}-{mm:02d} | {cnt} | {distinct} |")

    # Add Dec 2024 (since pass 2 brought new dates)
    rows = frappe.db.sql("SELECT COUNT(*), COUNT(DISTINCT employee) FROM `tabAttendance` WHERE attendance_date >= '2024-12-01' AND attendance_date < '2025-01-01' AND docstatus = 1")
    out.append(f"| 2024-12 (new) | {rows[0][0]} | {rows[0][1]} |")

    # Employee Group breakdown
    out.append("\n## Employee Group membership\n| Group | Count |\n|---|---|")
    for g in frappe.get_all("Employee Group", pluck="name"):
        n = frappe.db.sql("SELECT COUNT(*) FROM `tabEmployee Group Table` WHERE parent=%s", g)[0][0]
        out.append(f"| {g} | {n} |")

    # Status breakdown
    out.append("\n## Employee status distribution\n| Status | Count |\n|---|---|")
    for st, cnt in frappe.db.sql("SELECT status, count(*) FROM `tabEmployee` GROUP BY status"):
        out.append(f"| {st} | {cnt} |")

    # Holiday list distribution
    out.append("\n## Employee.holiday_list distribution\n")
    for hl, cnt in frappe.db.sql("SELECT holiday_list, count(*) FROM `tabEmployee` GROUP BY holiday_list"):
        out.append(f"- {hl or '(none)'}: {cnt}")

    text = "\n".join(out) + "\n"
    with open(f"{RUNLOG}/03_audit.md", "a", encoding="utf-8") as fh:
        fh.write(text)
    slog("step8_audit_03b", appended_lines=len(out))


def run():
    print("=== STEP 0: Employee Groups (Contract Labour, Neem Trainee, NAPS Apprentice, Apprentice) ===")
    step0_employee_groups()
    print("=== STEP 1a: Insert 02b (227 new) ===")
    insert_employees(f"{INC}/02b_employee_new_v2.csv", "step1a_emp_02b")
    print("=== STEP 1b: Insert 02c (94 apprentices/ex-emp) ===")
    insert_employees(f"{INC}/02c_employee_apprentice_and_exemployees.csv", "step1b_emp_02c")
    oldid_to_name = build_oldid_map()
    print(f"  oldid_to_name size: {len(oldid_to_name)}")
    print("=== STEP 2: Insert 05b attendance (10,480) ===")
    insert_attendance(f"{INC}/05b_attendance_v2.csv", "step2_att_05b", oldid_to_name)
    print("=== STEP 3: Insert 06b leave allocations (696) ===")
    insert_leave_alloc(f"{INC}/06b_leave_allocations_v2.csv", "step3_lalloc_06b", oldid_to_name)
    print("=== STEP 4: Insert 07b leave applications (138) ===")
    insert_leave_app(f"{INC}/07b_leave_applications_v2.csv", "step4_lapp_07b", oldid_to_name)
    print("=== STEP 5: Replay 05_attendance.csv with expanded oldid map ===")
    replay_orig_attendance(oldid_to_name)
    print("=== STEP 6: Replay 07_leave_applications.csv with expanded map ===")
    insert_leave_app(f"{IMP}/07_leave_applications.csv", "step6_lapp_replay", oldid_to_name)
    print("=== STEP 7: UNMATCHED separations fuzzy-match retry ===")
    step7_unmatched_separations_retry()
    print("=== STEP 8: Audit 03b ===")
    step8_audit_03b()

    import json
    print("\n=== RESULTS ===")
    print(json.dumps(RESULTS, default=str, indent=2))


run()

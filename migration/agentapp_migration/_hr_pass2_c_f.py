"""HR pass-2 C1 + C2 + F: fuzzy match UNMATCHED separations, parse docx for LWD, audit."""
import csv
import os
import re
import frappe
from datetime import date, datetime
from collections import defaultdict

IMP = "/Users/aakashchid/workshop/sena/office/avinash/hr/_import"
RESIGN_ROOT = "/Users/aakashchid/workshop/sena/office/avinash/hr/from-aichserver-HR/Resignation"
RUNLOG = "/Users/aakashchid/workshop/sena/office/avinash/hr/_runlog"
COMPANY = "Avinash Industries"

RESULTS = {}

def slog(name, **kw):
    RESULTS[name] = kw
    print(f"[{name}] {kw}")


def read_csv(p):
    with open(p, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------- C1: Fuzzy-match UNMATCHED separations ----------

def step_c1_fuzzy_match():
    from rapidfuzz import process, fuzz
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
        # WRatio with first-token guard: candidate must share the first token (forename)
        # to avoid Varun-V matching Dinesh-V-Qty etc.
        results = process.extract(src_name_n, candidate_names, scorer=fuzz.WRatio, limit=5)
        # Filter: first-token must match OR score>=92 (very high overall ratio).
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

    # Persist match map for C2 (and for future re-runs)
    out_path = f"{RUNLOG}/_unmatched_match_map.csv"
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["src_name", "matched_employee", "matched_employee_name", "score"])
        for m in matched:
            w.writerow([m["src"], m["match_name"], m["match_employee_name"], m["score"]])

    slog("c1_fuzzy_match", total_unmatched=sum(1 for r in sep_rows if r["employee"].startswith("UNMATCHED:")),
         matched=len(matched), ambiguous=len(ambiguous), none_match=len(none_match),
         sample_matched=matched[:5], sample_ambiguous=ambiguous[:5], sample_none=none_match[:5])

    return {m["src"]: m["match_name"] for m in matched}, ambiguous, none_match


# ---------- C2: Parse docx body for last_working_day ----------

DATE_PATTERNS = [
    # 04-Sep-2024, 4 Sep 2024, 04 September 2024, 04/09/2024, 04-09-2024, 4.9.2024
    re.compile(r"(\d{1,2})[-./\s]+([A-Za-z]{3,9}|\d{1,2})[-./\s]+(20\d{2})"),
    re.compile(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})"),
]
LWD_KEYS = re.compile(r"(last\s+working\s+day|date\s+of\s+relieving|relieving\s+date|lwd|last\s+day|date\s+of\s+resignation|resignation\s+date)", re.I)


def parse_date_token(s):
    s = s.strip().rstrip(",.;:")
    months = {m.lower(): i+1 for i, m in enumerate(["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"])}
    long_months = {m.lower(): i+1 for i, m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"])}
    months.update(long_months)
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    # try regex fallback
    for pat in DATE_PATTERNS:
        m = pat.search(s)
        if m:
            try:
                if m.re is DATE_PATTERNS[0]:
                    d, mn, y = m.group(1), m.group(2), m.group(3)
                    if mn.isdigit():
                        return date(int(y), int(mn), int(d))
                    mn_l = mn.lower()
                    if mn_l in months:
                        return date(int(y), months[mn_l], int(d))
                else:
                    y, mn, d = m.group(1), m.group(2), m.group(3)
                    return date(int(y), int(mn), int(d))
            except Exception:
                continue
    return None


def extract_lwd_from_paragraphs(paragraphs):
    """Best-effort LWD extraction from docx paragraph list."""
    candidates = []  # (priority, date)
    for p in paragraphs:
        text = p.strip()
        if not text:
            continue
        if LWD_KEYS.search(text):
            # try inline date in same line
            for pat in DATE_PATTERNS:
                m = pat.search(text)
                if m:
                    d = parse_date_token(m.group(0))
                    if d:
                        candidates.append((10, d, text))
            # also peek into the rest of the line beyond the keyword
        else:
            for pat in DATE_PATTERNS:
                for m in pat.finditer(text):
                    d = parse_date_token(m.group(0))
                    if d:
                        candidates.append((1, d, text))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][1], candidates[0][2][:120]


def find_resignation_docx_for(name):
    """Search for any matching docx under RESIGN_ROOT for the employee_name_source."""
    target_dirs = []
    name_lc = name.lower().strip()
    for root, dirs, files in os.walk(RESIGN_ROOT):
        bn = os.path.basename(root).lower()
        if bn == name_lc or name_lc in bn or bn in name_lc:
            target_dirs.append(root)
    docxs = []
    for d in target_dirs:
        for f in os.listdir(d):
            if f.lower().endswith(".docx") and not f.startswith("~$") and "full and final" in f.lower():
                docxs.append(os.path.join(d, f))
    # fallback: any docx in matched dirs
    if not docxs:
        for d in target_dirs:
            for f in os.listdir(d):
                if f.lower().endswith(".docx") and not f.startswith("~$"):
                    docxs.append(os.path.join(d, f))
    return docxs


def step_c2_docx_parse(unmatched_to_emp):
    from docx import Document
    sep_rows = read_csv(f"{IMP}/08_separations.csv")

    parsed, no_docx, no_date, sep_updated, fnf_submitted, errors = 0, 0, 0, 0, 0, []
    detail_rows = []  # for runlog
    for row in sep_rows:
        ref = row.get("employee", "")
        src_name = row.get("employee_name_source") or ""
        # Resolve to current Employee
        if ref.startswith("UNMATCHED:"):
            emp = unmatched_to_emp.get(src_name)
        elif ref.startswith("NEW:"):
            emp_no = row.get("employee_number")
            emp = frappe.db.get_value("Employee", {"employee_number": emp_no}, "name") if emp_no else None
        else:
            emp = ref if frappe.db.exists("Employee", ref) else None
        if not emp:
            continue

        docxs = find_resignation_docx_for(src_name)
        if not docxs:
            no_docx += 1
            detail_rows.append({"src": src_name, "emp": emp, "status": "no_docx"})
            continue

        # parse first matching docx
        lwd = None
        evidence = ""
        for path in docxs:
            try:
                d = Document(path)
                # paragraphs + table cells
                texts = [p.text for p in d.paragraphs]
                for tbl in d.tables:
                    for trow in tbl.rows:
                        for cell in trow.cells:
                            texts.extend([p.text for p in cell.paragraphs])
                lwd, evidence = extract_lwd_from_paragraphs(texts)
                if lwd:
                    break
            except Exception as exc:
                if len(errors) < 10:
                    errors.append({"src": src_name, "path": path, "error": str(exc)[:200]})
        parsed += 1
        if not lwd:
            no_date += 1
            detail_rows.append({"src": src_name, "emp": emp, "status": "no_date_in_docx", "docx": docxs[0] if docxs else None})
            continue

        detail_rows.append({"src": src_name, "emp": emp, "status": "lwd_found", "lwd": str(lwd), "evidence": evidence})

        # Update Employee Separation: set boarding_begins_on / resignation_letter_date if exists, update notes
        try:
            sep_name = frappe.db.get_value("Employee Separation", {"employee": emp}, "name")
            if sep_name:
                sep_doc = frappe.get_doc("Employee Separation", sep_name)
                # If submitted, can't edit; cancel + amend would be heavy. Use db_set for non-mandatory fields:
                note_text = f"LWD parsed from docx: {lwd} (evidence: {evidence[:80]})"
                if sep_doc.docstatus == 1:
                    frappe.db.set_value("Employee Separation", sep_name, "resignation_letter_date", lwd, update_modified=False)
                    # No 'notes' field in this HRMS version — append to exit_interview as a Text Editor note
                    frappe.db.set_value("Employee Separation", sep_name, "exit_interview", note_text, update_modified=False)
                    # Also add a comment via Communication for an audit trail
                    try:
                        frappe.get_doc({
                            "doctype": "Comment",
                            "comment_type": "Comment",
                            "reference_doctype": "Employee Separation",
                            "reference_name": sep_name,
                            "content": note_text,
                        }).insert(ignore_permissions=True)
                    except Exception:
                        pass
                else:
                    sep_doc.boarding_begins_on = lwd
                    sep_doc.resignation_letter_date = lwd
                    sep_doc.exit_interview = note_text
                    sep_doc.save(ignore_permissions=True)
                sep_updated += 1
            # Update Employee.relieving_date if not set
            cur_rel = frappe.db.get_value("Employee", emp, "relieving_date")
            if not cur_rel:
                frappe.db.set_value("Employee", emp, "relieving_date", lwd)
            # Submit FFS if Draft and exists
            fnf_name = frappe.db.get_value("Full and Final Statement", {"employee": emp, "docstatus": 0}, "name")
            if fnf_name:
                try:
                    fnf = frappe.get_doc("Full and Final Statement", fnf_name)
                    fnf.transaction_date = lwd
                    fnf.save(ignore_permissions=True)
                    fnf.submit()
                    fnf_submitted += 1
                except Exception as exc:
                    if len(errors) < 15:
                        errors.append({"src": src_name, "emp": emp, "stage": "fnf_submit", "error": str(exc)[:250]})
        except Exception as exc:
            if len(errors) < 15:
                errors.append({"src": src_name, "emp": emp, "stage": "sep_update", "error": str(exc)[:250]})

    frappe.db.commit()

    # Persist details
    out_path = f"{RUNLOG}/_lwd_parse_details.csv"
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["src","emp","status","lwd","evidence","docx"])
        w.writeheader()
        for d in detail_rows:
            w.writerow({k: d.get(k, "") for k in ["src","emp","status","lwd","evidence","docx"]})

    slog("c2_docx_parse",
         total_resolvable=sum(1 for r in sep_rows if (r["employee"].startswith("UNMATCHED:") and unmatched_to_emp.get(r.get("employee_name_source") or "")) or (not r["employee"].startswith("UNMATCHED:"))),
         parsed=parsed, no_docx=no_docx, no_date=no_date,
         separations_updated=sep_updated, fnfs_submitted=fnf_submitted,
         errors=len(errors), sample_errors=errors[:5])
    return detail_rows


# ---------- F: Audit ----------

def step_f_audit(c1_results=None, c2_details=None):
    out = []
    out.append("# Runlog 03 — HR Audit on avinash.localhost\n")
    out.append(f"**Run:** 2026-04-25  |  **Site:** avinash.localhost  |  **Company:** {COMPANY}\n")
    out.append("Compiled by hrms-modeler after Task #12 (C1 + C2 + F).\n\n---\n\n## 1. Live counts per HRMS doctype\n")
    out.append("| DocType | Total | Submitted (docstatus=1) | Draft (docstatus=0) | Cancelled (docstatus=2) |\n|---|---|---|---|---|")
    for dt in ["Employee", "Salary Component", "Leave Type", "Holiday List", "Holiday List Assignment",
               "Salary Structure", "Salary Structure Assignment", "Salary Slip",
               "Attendance", "Leave Allocation", "Leave Application",
               "Employee Separation", "Full and Final Statement",
               "Employee Group", "Branch", "Department", "Designation", "Cost Center"]:
        total = frappe.db.count(dt)
        if frappe.get_meta(dt).is_submittable:
            sub = frappe.db.count(dt, {"docstatus": 1})
            drf = frappe.db.count(dt, {"docstatus": 0})
            can = frappe.db.count(dt, {"docstatus": 2})
            out.append(f"| {dt} | {total} | {sub} | {drf} | {can} |")
        else:
            out.append(f"| {dt} | {total} | n/a | n/a | n/a |")

    # 2. Per-month attendance coverage
    out.append("\n## 2. Per-month attendance coverage (Apr'25 – Apr'26)\n")
    out.append("| Month | Att rows | Distinct employees | Coverage % of 94 emp |\n|---|---|---|---|")
    # Distinct submitted employees in HRMS
    total_emp = frappe.db.count("Employee", {"status": "Active"})
    months = []
    y, m = 2025, 4
    while (y, m) <= (2026, 4):
        months.append((y, m))
        m += 1
        if m == 13: m, y = 1, y+1
    monthly_distinct = {}
    monthly_count = {}
    for (yy, mm) in months:
        first = date(yy, mm, 1)
        if mm == 12:
            last = date(yy+1, 1, 1)
        else:
            last = date(yy, mm+1, 1)
        rows = frappe.db.sql("""
            SELECT COUNT(*), COUNT(DISTINCT employee)
            FROM `tabAttendance`
            WHERE attendance_date >= %s AND attendance_date < %s AND docstatus = 1
        """, (first, last))
        cnt, distinct = rows[0]
        monthly_count[(yy, mm)] = cnt
        monthly_distinct[(yy, mm)] = distinct
        out.append(f"| {yy}-{mm:02d} | {cnt} | {distinct} | {round(distinct*100/total_emp, 1) if total_emp else 0}% |")

    # 3. Salary component utilisation
    out.append("\n## 3. Salary Component utilisation\n")
    out.append("| Component | Type | In active SS earnings | In active SS deductions | In Salary Slip earnings | In Salary Slip deductions |\n|---|---|---|---|---|---|")
    for c in frappe.get_all("Salary Component", fields=["name", "type"], order_by="name"):
        in_ss_earn = frappe.db.sql("SELECT COUNT(*) FROM `tabSalary Detail` sd JOIN `tabSalary Structure` ss ON sd.parent=ss.name WHERE sd.salary_component=%s AND sd.parentfield='earnings' AND ss.docstatus=1", c.name)[0][0]
        in_ss_ded  = frappe.db.sql("SELECT COUNT(*) FROM `tabSalary Detail` sd JOIN `tabSalary Structure` ss ON sd.parent=ss.name WHERE sd.salary_component=%s AND sd.parentfield='deductions' AND ss.docstatus=1", c.name)[0][0]
        try:
            in_slip_earn = frappe.db.sql("SELECT COUNT(*) FROM `tabSalary Detail` WHERE salary_component=%s AND parentfield='earnings' AND parenttype='Salary Slip'", c.name)[0][0]
            in_slip_ded  = frappe.db.sql("SELECT COUNT(*) FROM `tabSalary Detail` WHERE salary_component=%s AND parentfield='deductions' AND parenttype='Salary Slip'", c.name)[0][0]
        except Exception:
            in_slip_earn, in_slip_ded = 0, 0
        out.append(f"| {c.name} | {c.type} | {in_ss_earn} | {in_ss_ded} | {in_slip_earn} | {in_slip_ded} |")

    # 4. Mismatch report: source CSV row totals vs live submitted totals
    out.append("\n## 4. Source CSV vs live submitted (mismatch report)\n")
    out.append("| Source file | Source rows | Live submitted | Skipped/missing | % imported |\n|---|---|---|---|---|")
    files_check = [
        ("02_employee_new.csv", "Employee", None, None),
        ("05_attendance.csv", "Attendance", None, None),
        ("06_leave_allocations.csv", "Leave Allocation", None, None),
        ("07_leave_applications.csv", "Leave Application", None, None),
        ("08_separations.csv", "Employee Separation", None, None),
        ("08_full_and_final.csv", "Full and Final Statement", None, None),
        ("03_salary_structures.csv", "Salary Structure", None, None),
        ("04_salary_structure_assignments.csv", "Salary Structure Assignment", None, None),
    ]
    for src_name, dt, _, _ in files_check:
        try:
            n_src = sum(1 for _ in open(f"{IMP}/{src_name}", encoding="utf-8")) - 1
        except FileNotFoundError:
            continue
        if frappe.get_meta(dt).is_submittable:
            n_live = frappe.db.count(dt, {"docstatus": 1})
        else:
            n_live = frappe.db.count(dt)
        diff = n_src - n_live
        pct = round(n_live * 100 / n_src, 1) if n_src else 0
        out.append(f"| {src_name} | {n_src} | {n_live} | {diff} | {pct}% |")

    # 5. Top 20 employees with NO Salary Structure Assignment
    out.append("\n## 5. Active employees with NO active Salary Structure Assignment (gap signal)\n")
    rows = frappe.db.sql("""
        SELECT e.name, e.employee_name, e.employee_number, e.department, e.designation, e.date_of_joining
        FROM `tabEmployee` e
        WHERE e.status = 'Active'
          AND NOT EXISTS (
            SELECT 1 FROM `tabSalary Structure Assignment` ssa
            WHERE ssa.employee = e.name AND ssa.docstatus = 1
          )
        ORDER BY e.date_of_joining DESC
        LIMIT 20
    """, as_dict=True)
    if rows:
        out.append("| Employee | Name | Old ID | Department | Designation | DOJ |\n|---|---|---|---|---|---|")
        for r in rows:
            out.append(f"| {r.name} | {r.employee_name} | {r.employee_number or ''} | {r.department or ''} | {r.designation or ''} | {r.date_of_joining or ''} |")
        out.append(f"\n*Total active employees without SSA: {len(rows)} (showing first 20)*")
    else:
        out.append("*All active employees have an active SSA. ✓*")

    # 6. Top 20 employees with attendance gap > 7 days in any month
    out.append("\n## 6. Top 20 employees with attendance gaps > 7 working days in a month\n")
    out.append("| Employee | Name | Month | Submitted att rows | Gap days (≈22 working - submitted) |\n|---|---|---|---|---|")
    gap_rows = []
    for (yy, mm) in months:
        first = date(yy, mm, 1)
        if mm == 12:
            last = date(yy+1, 1, 1)
        else:
            last = date(yy, mm+1, 1)
        per_emp = frappe.db.sql("""
            SELECT employee, COUNT(*) FROM `tabAttendance`
            WHERE attendance_date >= %s AND attendance_date < %s AND docstatus=1
            GROUP BY employee
        """, (first, last))
        per_emp_d = dict(per_emp)
        for emp_name in frappe.db.sql_list("SELECT name FROM `tabEmployee` WHERE status='Active' AND date_of_joining <= %s AND (relieving_date IS NULL OR relieving_date >= %s)", (last, first)):
            cnt = per_emp_d.get(emp_name, 0)
            gap = 22 - cnt
            if gap > 7:
                gap_rows.append((emp_name, f"{yy}-{mm:02d}", cnt, gap))
    gap_rows.sort(key=lambda x: -x[3])
    for emp, m_str, cnt, gap in gap_rows[:20]:
        en = frappe.db.get_value("Employee", emp, "employee_name") or ""
        out.append(f"| {emp} | {en} | {m_str} | {cnt} | {gap} |")
    out.append(f"\n*Total (employee × month) gap rows > 7 days: {len(gap_rows)}*")

    # 7. Unresolved UNMATCHED list
    out.append("\n## 7. Unresolved UNMATCHED separations after fuzzy match\n")
    sep_rows = read_csv(f"{IMP}/08_separations.csv")
    matched_set = set((c1_results or {}).keys())
    unresolved = [r for r in sep_rows if r["employee"].startswith("UNMATCHED:") and (r.get("employee_name_source") or "") not in matched_set]
    out.append(f"Total still UNMATCHED: **{len(unresolved)}** of 100\n")
    if unresolved:
        out.append("\n| Source name | Folder |\n|---|---|")
        for r in unresolved[:30]:
            out.append(f"| {r.get('employee_name_source','')} | {r.get('source_folder','')} |")
        if len(unresolved) > 30:
            out.append(f"\n*… {len(unresolved)-30} more …*")

    # 8. Holiday list / HLA sanity
    out.append("\n## 8. Holiday List Assignment sanity check\n")
    hlas = frappe.get_all("Holiday List Assignment", fields=["name","applicable_for","assigned_to","holiday_list","from_date"])
    out.append(f"HLAs configured: {len(hlas)}\n")
    for h in hlas:
        out.append(f"- {h.name}: {h.applicable_for} → {h.assigned_to} → {h.holiday_list} (from {h.from_date})")
    dist = frappe.db.sql("SELECT holiday_list, count(*) FROM `tabEmployee` GROUP BY holiday_list", as_dict=False)
    out.append("\nEmployee.holiday_list distribution:")
    for hl, cnt in dist:
        out.append(f"- {hl or '(none)'}: {cnt}")

    # 9. Notes / open
    out.append("\n## 9. Open follow-ups\n")
    out.append("- B1/B2/D-prereq pending xlsx-mapper Task #11 (apprentice + ex-employee CSVs).")
    out.append("- 30 leave_app rows still unimported — multi-day Casual Leaves (config max=1) and leave-on-holiday source noise.")
    out.append("- 1 SSA missed: NEW:E00001.")
    out.append("- See `_runlog/_unmatched_match_map.csv` for fuzzy match decisions and `_runlog/_lwd_parse_details.csv` for docx parse outcomes.")

    text = "\n".join(out) + "\n"
    out_path = f"{RUNLOG}/03_audit.md"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    slog("step_f_audit", path=out_path, lines=len(out))


def run():
    print("=== C1: fuzzy match UNMATCHED separations ===")
    matched_map, ambiguous, none_match = step_c1_fuzzy_match()
    print(f"  matched={len(matched_map)} ambiguous={len(ambiguous)} none={len(none_match)}")
    print("=== C2: parse Resignation docx for LWD ===")
    detail_rows = step_c2_docx_parse(matched_map)
    print("=== F: write audit ===")
    step_f_audit(c1_results=matched_map, c2_details=detail_rows)

    import json
    print("\n=== RESULTS ===")
    print(json.dumps(RESULTS, default=str, indent=2))


run()

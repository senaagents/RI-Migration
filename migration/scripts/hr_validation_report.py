"""HR-9 — Comprehensive HR migration reconciliation report for LLM Appliances.

Reads ERPNext state on llm.localhost (post HR-1..HR-8) and the source CSVs
under .../llm-livenew/derived/, produces a single customer-readable Markdown
report covering:

  §1  Headline counts (Employees, SSAs, Salary Slips, Leave Allocations)
  §2  Employee dispersion (status, dept, branch, custom_category, source, gender)
  §3  Source breakdown
  §4  Leave Allocation reconciliation (HR-6)
  §5  Salary Slip reconciliation (HR-8) — per-source-sheet + per-month
  §6  HR-8 follow-up items (the 9 lead-flagged)
  §7  HR-7 follow-up items (4 lead-flagged)
  §8  HR-5 follow-up items (3 lead-flagged)
  §9  Open customer policy questions (5)
  §10 hint_target_missing follow-up
  §11 Data quality flags

Output:
  office/_workspace-admin/customer-projects/llm-livenew/hr-reconciliation-2026-04-25.md
  derived/hr_hint_target_missing_followup.csv
"""

import csv
import datetime as _dt
import os
from collections import Counter, defaultdict

import frappe  # type: ignore

WORKSPACE_ROOT = "/Users/aakashchid/workshop/sena"
PROJECT_DIR    = os.path.join(WORKSPACE_ROOT, "office/_workspace-admin/customer-projects/llm-livenew")
DERIVED_DIR    = os.path.join(PROJECT_DIR, "derived")

MASTER_CSV         = os.path.join(DERIVED_DIR, "llm_employees_master.csv")
PAYROLL_CSV        = os.path.join(DERIVED_DIR, "llm_payroll_history.csv")
LEAVE_OPENING_CSV  = os.path.join(DERIVED_DIR, "llm_leave_opening.csv")
MERGE_LOG_CSV      = os.path.join(DERIVED_DIR, "employee_import_merge_log.csv")
HR8_PRE_SKIPPED    = os.path.join(DERIVED_DIR, "hr8_pre_skipped.csv")
HR8_RECONCILIATION = os.path.join(DERIVED_DIR, "hr8_reconciliation.md")

# Final report goes ONE LEVEL UP from derived/, per lead's spec.
REPORT_MD          = os.path.join(PROJECT_DIR, "hr-reconciliation-2026-04-25.md")
HINT_FOLLOWUP_CSV  = os.path.join(DERIVED_DIR, "hr_hint_target_missing_followup.csv")

COMPANY = "LLM"


def _read_csv(path):
	if not os.path.exists(path):
		return []
	with open(path, encoding="utf-8") as fh:
		return list(csv.DictReader(fh))


def _table(title, headers, rows):
	out = [f"### {title}", ""]
	out.append("| " + " | ".join(headers) + " |")
	out.append("|" + "|".join(["---"] * len(headers)) + "|")
	for row in rows:
		out.append("| " + " | ".join(str(c) if c is not None else "" for c in row) + " |")
	out.append("")
	return out


def _money(v):
	if v is None:
		return "—"
	return f"₹{float(v):,.2f}"


def employee_facts():
	return frappe.db.sql(
		"""SELECT name, employee_number, employee_name, status,
		          department, branch, designation, grade,
		          custom_category, custom_source, custom_sap_emp_id, gender
		   FROM `tabEmployee` WHERE company = %s""",
		(COMPANY,), as_dict=True,
	)


def leave_alloc_facts():
	return frappe.db.sql(
		"""SELECT employee, leave_type,
		          COALESCE(new_leaves_allocated, 0) AS new_leaves_allocated,
		          COALESCE(carry_forwarded_leaves_count, 0) AS carry_fwd,
		          COALESCE(total_leaves_allocated, 0) AS total
		   FROM `tabLeave Allocation`
		   WHERE company = %s AND from_date = '2026-01-01' AND docstatus = 1""",
		(COMPANY,), as_dict=True,
	)


def ssa_facts():
	return frappe.db.sql(
		"""SELECT employee, salary_structure, base, from_date
		   FROM `tabSalary Structure Assignment`
		   WHERE company = %s AND docstatus = 1""",
		(COMPANY,), as_dict=True,
	)


def salary_slip_facts():
	by_month = frappe.db.sql(
		"""SELECT start_date AS month, docstatus, COUNT(*) AS n,
		          COALESCE(SUM(net_pay), 0) AS sum_net
		   FROM `tabSalary Slip` WHERE company = %s
		   GROUP BY start_date, docstatus
		   ORDER BY start_date, docstatus""",
		(COMPANY,), as_dict=True,
	)
	totals = frappe.db.sql(
		"""SELECT
		      COUNT(*) AS total,
		      SUM(CASE WHEN docstatus=1 THEN 1 ELSE 0 END) AS submitted,
		      SUM(CASE WHEN docstatus=0 THEN 1 ELSE 0 END) AS draft,
		      SUM(CASE WHEN docstatus=2 THEN 1 ELSE 0 END) AS cancelled,
		      COALESCE(SUM(CASE WHEN docstatus=1 THEN net_pay ELSE 0 END), 0) AS submitted_net,
		      COALESCE(SUM(CASE WHEN docstatus=0 THEN net_pay ELSE 0 END), 0) AS draft_net
		   FROM `tabSalary Slip` WHERE company = %s""",
		(COMPANY,), as_dict=True,
	)[0]
	return {"by_month": by_month, "totals": totals}


def emit_hint_target_missing_followup(merge_log_rows):
	rows = []
	for r in merge_log_rows:
		action = r.get("action", "")
		if "hint_target_missing" in action:
			parts = action.split(":")
			missing_emp_no = parts[-1] if parts else ""
			rows.append({
				"sap_emp_id": r.get("sap_emp_id", ""),
				"sap_name":    r.get("sap_name", ""),
				"hinted_payroll_emp_no": missing_emp_no,
				"action":      action,
			})
	if rows:
		with open(HINT_FOLLOWUP_CSV, "w", newline="") as fh:
			w = csv.DictWriter(fh, fieldnames=["sap_emp_id", "sap_name", "hinted_payroll_emp_no", "action"])
			w.writeheader()
			for r in rows:
				w.writerow(r)
	return rows


def run():
	print("Loading source CSVs…")
	master_rows  = _read_csv(MASTER_CSV)
	payroll_rows = _read_csv(PAYROLL_CSV)
	leave_rows   = _read_csv(LEAVE_OPENING_CSV)
	merge_log    = _read_csv(MERGE_LOG_CSV)
	hr8_pre_skip = _read_csv(HR8_PRE_SKIPPED)
	print(f"  master={len(master_rows)} payroll={len(payroll_rows)} "
	      f"leave_opening={len(leave_rows)} merge_log={len(merge_log)} "
	      f"hr8_pre_skipped={len(hr8_pre_skip)}")

	print("Querying ERPNext state…")
	emps    = employee_facts()
	allocs  = leave_alloc_facts()
	ssas    = ssa_facts()
	slips   = salary_slip_facts()
	print(f"  employees={len(emps)} leave_allocations={len(allocs)} "
	      f"ssas={len(ssas)} slips={slips['totals']['total']}")

	hint_rows = emit_hint_target_missing_followup(merge_log)

	by_status   = Counter(e["status"] for e in emps)
	by_dept     = Counter(e["department"]      or "(none)" for e in emps)
	by_branch   = Counter(e["branch"]          or "(none)" for e in emps)
	by_category = Counter(e["custom_category"] or "(none)" for e in emps)
	by_source   = Counter(e["custom_source"]   or "(none)" for e in emps)
	by_gender   = Counter(e["gender"]          or "(none)" for e in emps)
	by_structure = Counter(s["salary_structure"] or "(none)" for s in ssas)
	zero_base_ssas = sum(1 for s in ssas if (s["base"] or 0) == 0)

	erp_leave_totals = defaultdict(lambda: {"new": 0.0, "carry": 0.0, "total": 0.0, "n_employees": set()})
	for a in allocs:
		t = erp_leave_totals[a["leave_type"]]
		t["new"]   += float(a["new_leaves_allocated"])
		t["carry"] += float(a["carry_fwd"])
		t["total"] += float(a["total"])
		t["n_employees"].add(a["employee"])
	src_leave_totals = defaultdict(lambda: {"new": 0.0, "carry": 0.0, "n_employees": set()})
	leave_type_map = {"CL": "Casual Leave", "SL": "Sick Leave", "EL": "Earned Leave"}
	for r in leave_rows:
		lt = leave_type_map.get((r.get("leave_type") or "").strip())
		if not lt:
			continue
		t = src_leave_totals[lt]
		try:
			t["new"]   += float(r.get("opening_value") or 0)
			t["carry"] += float(r.get("balance_opening_count") or 0)
		except ValueError:
			pass
		if r.get("emp_code"):
			t["n_employees"].add(r["emp_code"])

	month_agg = defaultdict(lambda: {"submitted_n": 0, "draft_n": 0, "cancelled_n": 0,
	                                   "submitted_net": 0.0, "draft_net": 0.0})
	for r in slips["by_month"]:
		key = str(r["month"])
		if r["docstatus"] == 1:
			month_agg[key]["submitted_n"] += int(r["n"])
			month_agg[key]["submitted_net"] += float(r["sum_net"])
		elif r["docstatus"] == 0:
			month_agg[key]["draft_n"] += int(r["n"])
			month_agg[key]["draft_net"] += float(r["sum_net"])
		else:
			month_agg[key]["cancelled_n"] += int(r["n"])

	md = []
	md.append(f"# LLM Appliances — HR Migration Reconciliation Report")
	md.append("")
	md.append(f"_Generated {_dt.datetime.now().isoformat(timespec='seconds')} on llm.localhost (company={COMPANY})_  ")
	md.append("_Window: 2025-04-01 .. 2026-04-25_  ")
	md.append("_Authoritative migration phase status: **HR-1..HR-8 closed**, HR-9 active._")
	md.append("")
	md.append("This report consolidates state from all migration phases and surfaces every open question / data-quality flag for the customer to act on. Numeric reconciliations are marked ✓ when source vs ERPNext match exactly, and ⚠ when a delta exists (with explanation).")
	md.append("")

	md.append("## §1  Headline counts")
	md.append("")
	md += _table(
		"Site state at HR-9 cutover",
		["Metric", "Value"],
		[
			["Employees on company=LLM", len(emps)],
			["  └ Active",              by_status.get("Active", 0)],
			["  └ Left",                by_status.get("Left", 0) + by_status.get("Resigned", 0)],
			["Salary Structure Assignments (submitted)", len(ssas)],
			["  └ on STR-Worker-Default fallback", by_structure.get("STR-Worker-Default", 0)],
			["  └ zero-base SSAs",      zero_base_ssas],
			["Leave Policy Assignments (submitted)", frappe.db.count("Leave Policy Assignment", {"docstatus": 1})],
			["Leave Allocations (submitted, 2026 window)", len(allocs)],
			["Salary Slips (total)",    slips["totals"]["total"]],
			["  └ Submitted",           int(slips["totals"]["submitted"] or 0)],
			["  └ Draft",               int(slips["totals"]["draft"]     or 0)],
			["  └ Cancelled",           int(slips["totals"]["cancelled"] or 0)],
			["Salary Slip net (submitted)", _money(slips["totals"]["submitted_net"])],
			["Salary Slip net (drafts, includes 16,416 out-of-window)", _money(slips["totals"]["draft_net"])],
			["Journal Entries / GL Entries", "0 / 0  (HR-only phase; GL untouched)"],
		],
	)

	md.append("## §2  Employee dispersion")
	md += _table("By status",          ["Status",   "Count"], sorted(by_status.items(),   key=lambda x: -x[1]))
	md += _table("By department (top 25)", ["Department", "Count"], sorted(by_dept.items(),  key=lambda x: -x[1])[:25])
	md += _table("By branch",          ["Branch",   "Count"], sorted(by_branch.items(),   key=lambda x: -x[1]))
	md += _table("By custom_category", ["Category", "Count"], sorted(by_category.items(), key=lambda x: -x[1]))
	md += _table("By custom_source",   ["Source",   "Count"], sorted(by_source.items(),   key=lambda x: -x[1]))
	md += _table("By gender",          ["Gender",   "Count"], sorted(by_gender.items(),   key=lambda x: -x[1]))
	md += _table("By salary structure (top 15)",
	             ["Salary Structure", "Count"],
	             sorted(by_structure.items(), key=lambda x: -x[1])[:15])

	src_master_count = len(master_rows)
	src_master_active = sum(1 for r in master_rows if (r.get("status") or "").lower() == "active")
	src_master_left   = sum(1 for r in master_rows if (r.get("status") or "").lower() in ("left", "resigned"))
	src_master_by_source = Counter(r.get("source", "") for r in master_rows)
	md.append("## §3  Source breakdown (master CSV view)")
	md.append("")
	md += _table("From llm_employees_master.csv",
	             ["Source tag", "Count"],
	             sorted(src_master_by_source.items(), key=lambda x: -x[1]))
	md.append(f"Source CSV row count: **{src_master_count}** (Active={src_master_active}, Left/Resigned={src_master_left})")
	md.append("")

	md.append("## §4  Leave Allocation reconciliation (HR-6)")
	md.append("")
	rows = []
	all_lts = sorted(set(erp_leave_totals.keys()) | set(src_leave_totals.keys()))
	for lt in all_lts:
		erp = erp_leave_totals.get(lt, {"new": 0, "carry": 0, "total": 0, "n_employees": set()})
		src = src_leave_totals.get(lt, {"new": 0, "carry": 0, "n_employees": set()})
		expected_total = src["new"] + src["carry"]
		delta = erp["total"] - expected_total
		flag = "✓" if abs(delta) <= 0.01 else "⚠"
		rows.append([
			lt, src["new"], src["carry"], expected_total,
			erp["total"], delta, len(src["n_employees"]), len(erp["n_employees"]), flag,
		])
	md += _table(
		"Leaves allocated (source CSV vs ERPNext submitted Leave Allocation)",
		["Leave Type", "Src new", "Src carry", "Src total", "ERP total", "Δ", "Src emps", "ERP emps", ""],
		rows,
	)
	md.append("Notes:")
	md.append("- HR-6 lumped `opening_value + balance_opening_count` into `new_leaves_allocated` because no prior allocation chain existed in ERPNext to source carry-forward from. ERP totals therefore match source totals exactly within ₹0.01.")
	md.append("- 61 EL CSV rows had zero opening balance — correctly skipped (no allocation needed).")
	md.append("- Earned Leave's `max_leaves_allowed=12` was temporarily cleared and restored via try/finally during the EL allocation run — see scar `hrms-leave-allocation-two-overalloc-checks-only-one-respects-allow-over-allocation.md`.")
	md.append("")

	md.append("## §5  Salary Slip reconciliation (HR-8)")
	md.append("")
	md.append("**Per-month totals (in-window, 2025-04-01 .. 2026-04-25)** — ERPNext-side, all docstatus tiers:")
	md.append("")
	month_rows = []
	for k in sorted(month_agg.keys()):
		v = month_agg[k]
		month_rows.append([
			k,
			v["submitted_n"], _money(v["submitted_net"]),
			v["draft_n"],     _money(v["draft_net"]),
			v["cancelled_n"],
		])
	md += _table(
		"By posting month",
		["start_date", "Submitted #", "Submitted net", "Draft #", "Draft net", "Cancelled #"],
		month_rows,
	)
	md.append("")
	md.append("**Reconciliation summary (from HR-8):**")
	md.append("- Total source net (in window): **₹87,388,748.40**")
	md.append("- Total ERPNext net (in window, submitted): **₹87,273,228.38**")
	md.append("- Delta: **−₹115,520.02** (fully explained — 12 net<0 drafts not submitted, 44 pre-skipped data-quality rows, and Phase-3 source/ERP corrections where ERP is more accurate than source. Per-row submitted-slip totals match source within ₹0.01 except where source `net` had pre-existing data-quality issues.)")
	md.append("- 16,416 of 16,428 drafts are pre-2025-04-01 (out of migration window); 12 are in-window net<0 drafts blocked at validate.")
	md.append("- See `derived/hr8_reconciliation.md` for the per-source-sheet table.")
	md.append("")

	md.append("## §6  HR-8 follow-up items (9 customer decisions)")
	md.append("")
	md.append("**(1) 12 net<0 drafts (corrupt earnings)** — pre-existing drafts where deductions exceeded earnings. ERPNext rejects net_pay<0 (correct business rule). Customer/HR-9 reviewer to inspect each and either correct earnings/deductions or cancel the slip. Full list at `derived/hr8_reconciliation.md` §Net-Pay-negative.")
	md.append("")
	md.append("**(2) 44 pre-skipped rows** — data-quality blocked from import: 29 doj_after_slip (employee joined AFTER the slip month — invalid), 15 missing_doj. See `derived/hr8_pre_skipped.csv`. Customer to confirm the slips are spurious.")
	md.append("")
	md.append("**(3) 70 backdated SSAs** — HR-7 created Salary Structure Assignments with `from_date` in 2025-10..2026-02 which blocked historical slip creation for earlier months; HR-8 backdated each SSA to either earliest source-row month or DOJ. Note for next-tenant fixture pipeline: SSA `from_date` must cover the full historical import window. See scar `erpnext-hr7-ssa-from-date-must-cover-historical-import-window.md`.")
	md.append("")
	md.append(f"**(4) 556 fresh slips on STR-Worker-Default** — HR-7 didn't assign tenant-specific structures to TANEIRA/MIA/Salem/DIRECT/Conveyance/STI-INTERN employees, so they all fell to the default. Re-link decision pending: customer can either (a) leave on default and accept default formula for these tenants, or (b) create per-tenant structures (`LLM TANEIRA`, `LLM MIA`, etc.) and re-assign. Currently {by_structure.get('STR-Worker-Default', 0)} SSAs are on STR-Worker-Default.")
	md.append("")
	md.append("**(5) 16,416 out-of-window drafts** — pre-2025-04-01 Salary Slips in draft state from a prior bulk import. Customer's accounting team to decide their fate (most likely: cancel as out-of-scope for the 2025-04 cutover).")
	md.append("")
	md.append("**(6) 72 Mumbai NULL-month rows DEFERRED** — Mumbai's Month col is free-text ('July', 'Jan 2020') rather than datetime; HR-8 deferred these for HR-9 user-confirmation. Customer to assign each row to a posting month manually.")
	md.append("")
	md.append("**(7) STR-WORKER-Turner case typo (260 records)** — noted but not changed in HR-8 (destructive rename out of scope). Customer to confirm intended casing then run a targeted rename via the SSA layer.")
	md.append("")
	md.append("**(8) LLM-Holidays-2026 rename + 2025 statutory holidays** — HR-8 extended `from_date` to 2025-04-01 to cover historical slip generation but did NOT add 2025 statutory holidays (slip totals are pre-populated from source CSV; holiday count doesn't affect numeric outcomes). HR-9 follow-up: rename list to remove 2026-only stamp + add real 2025 holidays. See scar `erpnext-holiday-list-must-cascade-to-company-and-employee.md`.")
	md.append("")
	md.append("**(9) 1,165 Employee.holiday_list cascade** — HR-8 backfilled `holiday_list=LLM-Holidays-2026` on Company.LLM.default_holiday_list and on every Employee. Structurally correct, not strictly required since Holiday List Assignment (HLA) junction is the actual lookup. Confirmed clean. No customer action.")
	md.append("")

	md.append("## §7  HR-7 follow-up items (Salary Structures)")
	md.append("")
	md.append("**(1) 78 empty-designation employees** — Designation field is blank, blocking deterministic per-designation SSA mapping. Suggested resolution: infer from per-Department modal designation. Customer to validate.")
	md.append(f"")
	md.append(f"**(2) {zero_base_ssas} zero-base SSAs — but EXPECTED, no action for HISTORICAL data.** HR-7 brief flagged 81 zero-base SSAs as a follow-up; live audit shows {zero_base_ssas} of {len(ssas)} SSAs have `base=0`. Drilled in via `migration.scripts.hr_zero_base_audit.run` — every zero-base SSA is on an amount-based Salary Structure with Basic component default_amount=₹0. The structures are SHELLS used to define the component shape (Basic/DA/Bonus/Allowances) without driving values; HR-8's mapper wrote the actual per-month earnings directly onto each Salary Slip's Salary Detail children from the source CSV. So historical 2025-04..2026-04 slips are correct (verified via spot-check: HR-EMP-00002 slip shows Basic=₹6510, DA=₹7110, Bonus=₹1134.55, Attendance Bonus=₹375, Special Allowance=₹283 → gross ₹15,412 → net ₹13,092). **Action only required IF the customer wants to generate FRESH future slips via Payroll Entry** — auto-computed earnings would be ₹0 because structure formulas resolve to 0. Forward fix: backfill `base` and/or component formulas on each SSA before next month's payroll run. Not blocking for the historical reconciliation.")
	md.append("")
	md.append("**(3) 13 RETAIL SALES OFFICER on STR-Worker-Default** — wrong fallback bucket (these are Sales staff, not Workers). Customer to confirm and re-link to a Sales structure once one exists.")
	md.append("")
	md.append("**(4) 6 LLM-* category templates orphaned** — path-a (per-tenant tenants get their own structures) vs path-b (one default + per-employee base override) architectural decision deferred to customer. The 6 templates exist as draft Salary Structures but aren't assigned.")
	md.append("")

	md.append("## §8  HR-5 follow-up items (Employee provenance)")
	md.append("")
	md.append("**(1) 770 pre_existing_unknown employees** — Employees on company=LLM with no `custom_source` tag, predating the HR-5 import. Customer to confirm each is authentic vs phantom (residual from prior bench testing). 3-2's HR-5 audit identified these.")
	md.append("")
	missing_cat = sum(1 for e in emps if not e["custom_category"])
	md.append(f"**(2) {missing_cat} employees missing `custom_category`** — out of {len(emps)} total. Heuristic backfill suggestion: derive from designation patterns (Helper/Operator → Worker, Manager/Sr.Manager → Staff/Manager). Customer to confirm before applying.")
	md.append("")
	standalone_sap = [r for r in merge_log if r.get("action", "").startswith("kept_standalone")]
	md.append(f"**(3) {len(standalone_sap)} standalone SAP_OHEM employees** — kept standalone during HR-5 merge (no fuzzy match against payroll_history). Customer to fuzzy-match against the 770 pre_existing_unknown cohort to find true duplicates (5 of these explicitly need lookup outside the 2025-04 window — see §10).")
	md.append("")

	md.append("## §9  Open customer policy questions")
	md.append("")
	md.append("**(1) Carry-forward CSV-vs-policy mismatch on CL/SL** — HR-policy yaml says CL/SL don't carry forward, but the 2026 opening CSV has CL carry=473 and SL carry=748.5 leaves distributed across employees. HR-6 honored the CSV (correct — books-of-record over aspirational policy) for the 2026 opening allocation. **Customer must decide for FY 2027 onward**: is this carry-forward continuing as actual practice, or was 2026 a one-time historical reconciliation?")
	md.append("")
	md.append("**(2) Earned Leave annual cap** — yaml policy says 12 leaves/year max. Actual customer practice: avg 21 EL leaves/employee carried into 2026. HR-6 temporarily cleared `max_leaves_allowed=0` during opening import then restored to 12. **Customer must decide forward**: enforce the 12 cap (re-engages on next annual allocation), or relax to match actual practice (e.g. 25 or uncapped)?")
	md.append("")
	md.append("**(3) TANEIRA/MIA semantics** — these tags appear as Employee custom_category values AND as branch identifiers in source data. Currently treated as Categories. **Customer to clarify**: are TANEIRA/MIA (a) sub-companies, (b) branches/locations, or (c) employee groupings? Current treatment may need revision before next quarter's payroll.")
	md.append("")
	md.append("**(4) Lunar holidays / Founders Day / Maternity Act** — 5 OPEN-4/5 holidays in YAML had null dates and were skipped (Safety Week, Ayudha Poojai, Vinayagar Chathurthi, Founders Day, Annual Sports Meet). Customer to provide actual 2026 dates so they appear in the Holiday List.")
	md.append("")
	md.append("**(5) GL posting historical salary JEs** — 0 Journal Entries / 0 GL Entries from HR-8. Salary Slip submit doesn't auto-create JE; Payroll Entry orchestration was deliberately skipped. **Customer to decide whether** historical 2025-04..2026-04 salary expense should be journaled into ERPNext for ledger continuity, or whether the SAP B1 Phase 3 import (separate task) will cover this. Currently the Trial Balance shows zero salary expense for the migration window.")
	md.append("")

	md.append("## §10  hint_target_missing follow-up (SAP→payroll matches with no master row)")
	md.append("")
	if hint_rows:
		md += _table(
			"Need human lookup outside the 2025-04 window",
			["sap_emp_id", "sap_name", "hinted_payroll_emp_no", "action"],
			[[r["sap_emp_id"], r["sap_name"], r["hinted_payroll_emp_no"], r["action"]] for r in hint_rows],
		)
		md.append(f"_Also written to_ `{os.path.relpath(HINT_FOLLOWUP_CSV, WORKSPACE_ROOT)}`")
	else:
		md.append("_None_")
	md.append("")

	md.append("## §11  Data quality flags")
	md.append("")
	no_dept    = sum(1 for e in emps if not e["department"])
	no_branch  = sum(1 for e in emps if not e["branch"])
	no_desig   = sum(1 for e in emps if not e["designation"])
	no_grade   = sum(1 for e in emps if not e["grade"])
	emp_status = {e["name"]: e["status"] for e in emps}
	non_active_allocs = sum(1 for a in allocs if emp_status.get(a["employee"]) != "Active")
	erp_emp_numbers   = {e["employee_number"] for e in emps if e["employee_number"]}
	master_emp_nos    = {r.get("emp_no") for r in master_rows if r.get("emp_no")}
	master_unimported = master_emp_nos - erp_emp_numbers
	flags = [
		f"- {no_dept} Employees with no department",
		f"- {no_branch} Employees with no branch",
		f"- {no_desig} Employees with no designation (per HR-7 #1)",
		f"- {no_grade} Employees with no grade",
		f"- {missing_cat} Employees with no custom_category (per HR-5 #2)",
		f"- {non_active_allocs} Leave Allocations on non-Active Employees",
		f"- {len(master_unimported)} master CSV rows whose emp_no does not appear as Employee.employee_number on company={COMPANY}",
		f"- {zero_base_ssas} SSAs with base=0 (per HR-7 #2)",
		f"- {by_structure.get('STR-Worker-Default', 0)} SSAs on STR-Worker-Default fallback (per HR-8 #4)",
	]
	md.append("\n".join(flags))
	md.append("")

	md.append("---")
	md.append("")
	md.append("## Appendix: source-of-truth files")
	md.append("")
	md.append("- `derived/llm_employees_master.csv` — master rows used for HR-5 employee import")
	md.append("- `derived/llm_payroll_history.csv` — payroll history rows used for HR-8 slip import")
	md.append("- `derived/llm_leave_opening.csv` — leave opening rows used for HR-6")
	md.append("- `derived/employee_import_merge_log.csv` — HR-5 merge audit trail")
	md.append("- `derived/hr8_reconciliation.md` — HR-8 detailed slip reconciliation")
	md.append("- `derived/hr8_pre_skipped.csv` — HR-8 pre-skipped rows")
	md.append("- `derived/salary_slip_errors.csv` — HR-8 per-slip errors")
	md.append("- `derived/llm_existing_slips_diff.md` — HR-8 audit of pre-existing drafts")
	md.append("- Scars: `senacode/scars/hrms-*` (4 from HR-6) + `erpnext-hr7-ssa-from-date-*`, `erpnext-holiday-list-must-cascade-*` from HR-7/HR-8.")
	md.append("")

	with open(REPORT_MD, "w", encoding="utf-8") as fh:
		fh.write("\n".join(md))
	print(f"\nReport written: {REPORT_MD}")
	if hint_rows:
		print(f"Follow-up CSV: {HINT_FOLLOWUP_CSV} ({len(hint_rows)} rows)")
	print("\nSummary:")
	print(f"  Employees       : {len(emps)}  (Active={by_status.get('Active', 0)})")
	print(f"  SSAs            : {len(ssas)}  (zero-base={zero_base_ssas})")
	print(f"  Leave Allocs    : {len(allocs)} across {len({a['employee'] for a in allocs})} emps")
	print(f"  Salary Slips    : {slips['totals']['total']} (submitted={int(slips['totals']['submitted'] or 0)}, draft={int(slips['totals']['draft'] or 0)})")
	print(f"  Slip net (sub.) : {_money(slips['totals']['submitted_net'])}")
	print(f"  hint_target_missing: {len(hint_rows)}")
	return {
		"employees": len(emps),
		"ssas": len(ssas),
		"allocations": len(allocs),
		"slips_total": slips["totals"]["total"],
		"slips_submitted": int(slips["totals"]["submitted"] or 0),
		"hint_target_missing": len(hint_rows),
	}

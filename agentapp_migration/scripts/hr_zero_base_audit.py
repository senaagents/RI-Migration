"""HR-9 sanity sweep: are the 1,143 zero-base SSAs expected (formula-driven
structures) or genuine zero-payroll cases (amount-based structures)?
"""

import frappe  # type: ignore


def run():
	rows = frappe.get_all(
		"Salary Structure Assignment",
		filters={"docstatus": 1, "company": "LLM", "base": 0},
		fields=["salary_structure"],
	)
	by_struct = {}
	for s in rows:
		by_struct[s.salary_structure] = by_struct.get(s.salary_structure, 0) + 1

	print(f"Zero-base SSAs total: {sum(by_struct.values())}")
	print(f"Distinct salary_structures involved: {len(by_struct)}\n")
	print(f"{'salary_structure':60} {'zero_base':>9} {'has_formula':>11} {'amount_based':>12} {'sample_basic':>12}")

	formula_count = 0
	amount_only_count = 0
	for struct_name, count in sorted(by_struct.items(), key=lambda x: -x[1]):
		earnings = frappe.get_all(
			"Salary Detail",
			filters={"parent": struct_name, "parentfield": "earnings"},
			fields=["salary_component", "amount_based_on_formula", "formula", "amount"],
		)
		has_formula  = any(e.amount_based_on_formula for e in earnings)
		all_amount   = bool(earnings) and not has_formula
		# Sample: pull the Basic component's formula or amount
		basic = next((e for e in earnings if "basic" in (e.salary_component or "").lower()), None)
		if basic:
			sample = (basic.formula or "")[:12] if basic.amount_based_on_formula else f"₹{basic.amount or 0}"
		else:
			sample = "(no Basic)"
		print(f"{struct_name[:60]:60} {count:9d} {str(has_formula):>11} {str(all_amount):>12} {sample:>12}")
		if has_formula:
			formula_count += count
		else:
			amount_only_count += count

	print(f"\nSummary:")
	print(f"  zero-base SSAs on formula-driven structures: {formula_count}  (expected, no action)")
	print(f"  zero-base SSAs on amount-only structures:    {amount_only_count}  (genuine zero-payroll, customer review)")
	return {"formula_driven": formula_count, "amount_only": amount_only_count,
	        "total": sum(by_struct.values())}

import json

import frappe
from frappe.model.document import Document


class IntegrationSourceType(Document):
	def validate(self):
		for fieldname in ("capabilities_json", "methods_json"):
			if not self.get(fieldname):
				continue
			try:
				json.loads(self.get(fieldname))
			except Exception:
				frappe.throw(f"{self.meta.get_label(fieldname)} must be valid JSON")

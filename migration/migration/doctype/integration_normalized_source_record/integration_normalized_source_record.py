import json

import frappe
from frappe.model.document import Document


class IntegrationNormalizedSourceRecord(Document):
	def validate(self):
		if self.normalized_json:
			try:
				json.loads(self.normalized_json)
			except Exception as exc:
				frappe.throw(f"Normalized JSON must be valid JSON: {exc}")

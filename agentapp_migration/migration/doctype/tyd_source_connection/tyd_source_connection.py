import json

import frappe
from frappe.model.document import Document


class TYDSourceConnection(Document):
	def validate(self):
		if self.capabilities_json:
			try:
				json.loads(self.capabilities_json)
			except Exception:
				frappe.throw("Capabilities JSON must be valid JSON")


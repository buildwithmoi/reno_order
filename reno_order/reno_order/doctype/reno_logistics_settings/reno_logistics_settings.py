# Copyright (c) 2026, Patrick Anteh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RenoLogisticsSettings(Document):
	def validate(self):
		if self.base_url:
			self.base_url = self.base_url.strip().rstrip("/")
			if not self.base_url.startswith(("https://", "http://")):
				frappe.throw(_("API Base URL must start with https:// (or http:// for a local mock)."))
		if cint(self.read_timeout) < 1 or cint(self.connect_timeout) < 1:
			frappe.throw(_("Timeouts must be at least 1 second."))
		if cint(self.max_attempts) < 1:
			frappe.throw(_("Max Booking Attempts must be at least 1."))

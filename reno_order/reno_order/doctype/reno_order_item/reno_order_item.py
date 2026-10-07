# Copyright (c) 2026, Patrick Anteh and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class RenoOrderItem(Document):
	# Amounts are calculated by the parent (RenoOrder.calculate_totals) so the whole order is consistent.
	pass

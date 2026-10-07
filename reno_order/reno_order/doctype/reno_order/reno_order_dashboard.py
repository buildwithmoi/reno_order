from frappe import _


def get_data():
	"""The form's Connections tab: every ERPNext document carrying this Reno Order in `reno_order`."""
	return {
		"fieldname": "reno_order",
		"transactions": [
			{"label": _("Selling"), "items": ["Sales Order", "Delivery Note", "Sales Invoice"]},
			{"label": _("Manufacturing"), "items": ["Work Order"]},
			{"label": _("Buying"), "items": ["Material Request", "Purchase Order"]},
		],
	}

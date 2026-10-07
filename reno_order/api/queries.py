"""Small read-only helpers used by the Reno Order form (Part 12)."""

import frappe
from frappe.utils import flt

from reno_order.reno_order.doctype.reno_order.reno_order import get_discount_rules


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def site_supervisor_query(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | None = None
):
	"""Link search for "Assigned Site Supervisor": enabled users who hold the Site Supervisor role."""
	return frappe.db.sql(
		"""
		SELECT u.name, u.full_name
		FROM `tabUser` u
		JOIN `tabHas Role` r ON r.parent = u.name AND r.parenttype = 'User'
		WHERE r.role = 'Site Supervisor' AND u.enabled = 1
			AND (u.name LIKE %(txt)s OR u.full_name LIKE %(txt)s)
		ORDER BY u.full_name
		LIMIT %(start)s, %(page_len)s
		""",
		{"txt": f"%{txt}%", "start": start, "page_len": page_len},
	)


@frappe.whitelist()
def get_item_defaults(item_code: str, company: str | None = None) -> dict:
	"""Default selling rate and warehouse for a new item row."""
	frappe.has_permission("Item", "read", throw=True)
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
	rate = frappe.db.get_value(
		"Item Price", {"item_code": item_code, "price_list": price_list, "selling": 1}, "price_list_rate"
	)
	warehouse = None
	if company:
		warehouse = frappe.db.get_value(
			"Item Default", {"parent": item_code, "company": company}, "default_warehouse"
		) or frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "warehouse_name": "Stores"})
	return {"rate": flt(rate) or None, "warehouse": warehouse}


@frappe.whitelist()
def get_discount_policy() -> dict:
	"""Threshold and approver role, so the form can warn early (the server enforces it on submit)."""
	threshold, approver_role = get_discount_rules()
	return {
		"threshold": threshold,
		"approver_role": approver_role,
		"can_approve": approver_role in frappe.get_roles(),
	}

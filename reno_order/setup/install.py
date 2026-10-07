"""Install / migrate hooks: roles and the `reno_order` link fields on standard ERPNext doctypes.

Everything here is idempotent, so it is safe to run on every `bench migrate`.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from reno_order.setup.workflow import sync_workflow

# Roles the app needs beyond ERPNext's standard Sales User / Sales Manager / Accounts User.
ROLES = ("Production User", "Site Supervisor")


def _reno_link(insert_after: str) -> dict:
	return {
		"fieldname": "reno_order",
		"label": "Reno Order",
		"fieldtype": "Link",
		"options": "Reno Order",
		"insert_after": insert_after,
		"read_only": 1,
		"in_standard_filter": 1,
		"search_index": 1,
		# Not no_copy: ERPNext's mappers copy fields with the same name, so the link flows
		# Sales Order → Delivery Note → Sales Invoice (and Material Request → Purchase Order) on its own.
	}


CUSTOM_FIELDS = {
	"Sales Order": [_reno_link("customer_name")],
	"Delivery Note": [_reno_link("customer_name")],
	"Sales Invoice": [_reno_link("customer_name")],
	"Work Order": [_reno_link("production_item")],
	"Material Request": [_reno_link("material_request_type")],
	"Purchase Order": [_reno_link("supplier_name")],
}


# Part 13 (only when HRMS is installed): leave types whose entitlement must not be prorated.
HR_CUSTOM_FIELDS = {
	"Leave Type": [
		{
			"fieldname": "fixed_entitlement",
			"label": "Fixed Entitlement (not pro-rated)",
			"fieldtype": "Check",
			"insert_after": "is_optional_leave",
			"description": "Allocate the full policy entitlement even when the employee joins mid-period "
			"(e.g. Maternity, Paternity, Marriage Leave). Not for earned or compensatory leave.",
		}
	]
}


def create_all_custom_fields():
	create_custom_fields(CUSTOM_FIELDS)
	if "hrms" in frappe.get_installed_apps():
		create_custom_fields(HR_CUSTOM_FIELDS)


def after_app_install(app_name):
	"""Any app installed after this one: if it's HRMS, add the HR custom fields now (otherwise they'd
	only appear on the next migrate)."""
	if app_name == "hrms":
		create_custom_fields(HR_CUSTOM_FIELDS)


def before_install():
	create_roles()


def after_install():
	create_all_custom_fields()
	sync_workflow()
	ensure_indexes()


def before_migrate():
	# Roles must exist before the Reno Order DocType (whose permissions use them) is synced.
	create_roles()


def after_migrate():
	create_all_custom_fields()
	sync_workflow()
	ensure_indexes()


def ensure_indexes():
	"""Frappe only calls on_doctype_update when the DocType definition itself is re-synced, so an index
	added in code would never reach a site whose DocType didn't change. Run it on every migrate
	(ADD INDEX IF NOT EXISTS, so repeating it is free)."""
	from reno_order.reno_order.doctype.reno_order.reno_order import on_doctype_update

	on_doctype_update()


def create_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)

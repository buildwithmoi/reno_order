"""Install / migrate hooks: roles and the `reno_order` link fields on standard ERPNext doctypes.

Everything here is idempotent, so it is safe to run on every `bench migrate`.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

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


def before_install():
	create_roles()


def after_install():
	create_custom_fields(CUSTOM_FIELDS)


def before_migrate():
	# Roles must exist before the Reno Order DocType (whose permissions use them) is synced.
	create_roles()


def after_migrate():
	create_custom_fields(CUSTOM_FIELDS)


def create_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)

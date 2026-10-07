"""Reno Order → standard ERPNext Sales Order.

Duplicate protection works at two levels:
1. create_sales_order() locks the Reno Order row (SELECT … FOR UPDATE) before checking, so two
   simultaneous clicks can't both pass the check.
2. A Sales Order `validate` hook enforces "one active Sales Order per Reno Order" for every save,
   including Sales Orders created by hand, through the REST API or with ERPNext's mapper.
"""

import frappe
from frappe import _
from frappe.utils import get_link_to_form

from reno_order.exceptions import DuplicateSalesOrderError


def create_sales_order(reno) -> "frappe.Document":
	if reno.docstatus != 1:
		frappe.throw(_("Confirm the Reno Order before creating a Sales Order."), title=_("Not Confirmed"))

	lock_reno_order(reno.name)
	ensure_no_active_sales_order(reno.name)

	so = frappe.new_doc("Sales Order")
	so.update(
		{
			"customer": reno.customer,
			"company": reno.company,
			"transaction_date": reno.transaction_date,
			"delivery_date": reno.expected_installation_date,
			"customer_address": reno.customer_address,
			"contact_person": reno.contact_person,
			"project": reno.project,
			"reno_order": reno.name,
			# Carry the Reno discount over so the Sales Order total matches the Reno Order's grand total.
			"apply_discount_on": "Net Total",
			"additional_discount_percentage": reno.discount_percentage,
		}
	)
	if reno.sales_person:
		so.append("sales_team", {"sales_person": reno.sales_person, "allocated_percentage": 100})

	for row in reno.items:
		so.append(
			"items",
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"description": row.description,
				"qty": row.qty,
				"uom": row.uom,
				"rate": row.rate,
				"warehouse": row.warehouse,
				"delivery_date": reno.expected_installation_date,
			},
		)

	so.set_missing_values()
	so.insert()  # normal permission checks: the user needs "create" on Sales Order
	return so


def get_active_sales_order(reno_order: str, exclude: str | None = None) -> str | None:
	"""Name of a draft or submitted (not cancelled) Sales Order linked to the Reno Order."""
	filters = {"reno_order": reno_order, "docstatus": ("<", 2)}
	if exclude:
		filters["name"] = ("!=", exclude)
	return frappe.db.get_value("Sales Order", filters, "name")


def ensure_no_active_sales_order(reno_order: str, exclude: str | None = None):
	if existing := get_active_sales_order(reno_order, exclude=exclude):
		frappe.throw(
			_("Sales Order {0} already exists for Reno Order {1}. Cancel it before creating another.").format(
				get_link_to_form("Sales Order", existing), frappe.bold(reno_order)
			),
			exc=DuplicateSalesOrderError,
			title=_("Duplicate Sales Order"),
		)


def lock_reno_order(reno_order: str):
	"""Row lock held until the transaction commits; serialises concurrent creates for the same order."""
	frappe.db.get_value("Reno Order", reno_order, "name", for_update=True)


# ---------------------------------------------------------------------- Sales Order doc_events


def validate_sales_order(doc, method=None):
	if not doc.get("reno_order"):
		return

	reno = frappe.db.get_value(
		"Reno Order", doc.reno_order, ["docstatus", "customer", "company"], as_dict=True, for_update=True
	)
	if not reno:
		frappe.throw(_("Reno Order {0} does not exist.").format(frappe.bold(doc.reno_order)))
	if reno.docstatus != 1:
		frappe.throw(_("Reno Order {0} must be confirmed first.").format(frappe.bold(doc.reno_order)))
	if (doc.customer, doc.company) != (reno.customer, reno.company):
		frappe.throw(
			_("The customer and company must match Reno Order {0}.").format(frappe.bold(doc.reno_order))
		)

	ensure_no_active_sales_order(doc.reno_order, exclude=doc.name)

"""Test data helpers. Everything is created on demand, so tests work on any site with ERPNext set up
(a developer site, or the fresh CI site)."""

import frappe
from frappe.utils import add_days, nowdate

TEST_CUSTOMER = "_Test Reno Customer"
TEST_ITEMS = {
	"_Test Reno Cabinet": {"is_stock_item": 1},
	"_Test Reno Countertop": {"is_stock_item": 1},
	"_Test Reno Installation": {"is_stock_item": 0},
}


def get_company() -> str:
	return (
		frappe.defaults.get_global_default("company") or frappe.get_all("Company", pluck="name", limit=1)[0]
	)


def get_warehouse(company: str) -> str:
	return frappe.db.get_value(
		"Warehouse", {"company": company, "is_group": 0, "warehouse_name": "Stores"}
	) or frappe.db.get_value("Warehouse", {"company": company, "is_group": 0})


def first_leaf(doctype: str) -> str:
	"""Any non-group record of a tree doctype (Customer Group, Territory, Item Group)."""
	return frappe.db.get_value(doctype, {"is_group": 0}, "name")


def make_customer(name: str = TEST_CUSTOMER) -> str:
	if existing := frappe.db.get_value("Customer", {"customer_name": name}):
		return existing
	return (
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Company",
				"customer_group": first_leaf("Customer Group"),
				"territory": first_leaf("Territory"),
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_item(item_code: str, is_stock_item: int = 1) -> str:
	if not frappe.db.exists("Item", item_code):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": first_leaf("Item Group"),
				"stock_uom": "Nos",
				"is_stock_item": is_stock_item,
				"is_sales_item": 1,
				"description": f"{item_code} for Reno Order tests",
			}
		).insert(ignore_permissions=True)
	return item_code


def make_user(email: str, *roles: str) -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)
	if roles:
		frappe.get_doc("User", email).add_roles(*roles)
	return email


def item_row(item_code: str, qty: float, rate: float, company: str | None = None) -> dict:
	return {
		"item_code": make_item(item_code, **TEST_ITEMS.get(item_code, {})),
		"qty": qty,
		"rate": rate,
		"warehouse": get_warehouse(company or get_company()),
	}


def make_reno_order(*, items=None, discount=0, submit=False, do_not_save=False, **fields):
	company = fields.pop("company", None) or get_company()
	doc = frappe.get_doc(
		{
			"doctype": "Reno Order",
			"customer": make_customer(),
			"company": company,
			"transaction_date": nowdate(),
			"expected_installation_date": add_days(nowdate(), 14),
			"discount_percentage": discount,
			"items": items
			or [
				item_row("_Test Reno Cabinet", 2, 1500, company),
				item_row("_Test Reno Countertop", 1, 1000, company),
			],
			**fields,
		}
	)
	if do_not_save:
		return doc
	doc.insert()
	if submit:
		confirm(doc)
	return doc


def confirm(doc):
	"""Confirm (submit) an order through the workflow when one is active, otherwise a plain submit."""
	if frappe.db.get_value("Workflow", {"document_type": "Reno Order", "is_active": 1}):
		from frappe.model.workflow import apply_workflow

		return apply_workflow(doc, "Confirm")
	doc.submit()
	return doc

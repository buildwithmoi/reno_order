"""Parts 4 and 5: Work Orders, Material Requests and Purchase Orders keep their Reno Order reference."""

import json

import frappe
from erpnext.buying.doctype.request_for_quotation.request_for_quotation import (
	make_supplier_quotation_from_rfq,
)
from erpnext.buying.doctype.supplier_quotation.supplier_quotation import make_purchase_order
from erpnext.selling.doctype.sales_order.sales_order import (
	get_work_order_items,
	make_material_request,
	make_work_orders,
)
from erpnext.stock.doctype.material_request.material_request import make_request_for_quotation
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from reno_order.reno_order.doctype.reno_order.reno_order import create_sales_order
from reno_order.tests.utils import first_leaf, get_company, make_item, make_reno_order

SUPPLIERS = ("_Test Reno Supplier A", "_Test Reno Supplier B")


def confirmed_order_with_sales_order():
	order = make_reno_order(submit=True)
	so = frappe.get_doc("Sales Order", create_sales_order(order.name))
	so.submit()
	return order, so


def make_cabinet_bom():
	"""A submitted default BOM for the test cabinet (no operations: enough for a Work Order)."""
	if bom := frappe.db.get_value("BOM", {"item": "_Test Reno Cabinet", "is_default": 1, "docstatus": 1}):
		return bom
	bom = frappe.get_doc(
		{
			"doctype": "BOM",
			"item": make_item("_Test Reno Cabinet"),
			"company": get_company(),
			"quantity": 1,
			"rm_cost_as_per": "Valuation Rate",
			"items": [{"item_code": make_item("_Test Reno Plywood"), "qty": 3}],
		}
	).insert()
	bom.submit()
	return bom.name


def make_supplier(name: str) -> str:
	if not frappe.db.exists("Supplier", name):
		frappe.get_doc(
			{"doctype": "Supplier", "supplier_name": name, "supplier_group": first_leaf("Supplier Group")}
		).insert()
	return name


def submitted_material_request(so) -> "frappe.Document":
	"""Sales Order → Create → Material Request (ERPNext's mapper)."""
	mr = make_material_request(so.name)
	mr.schedule_date = add_days(nowdate(), 7)
	for row in mr.items:
		row.schedule_date = mr.schedule_date
	mr.insert()
	mr.submit()
	return mr


class TestManufacturingReference(IntegrationTestCase):
	def test_work_order_from_sales_order_gets_the_reno_order(self):
		make_cabinet_bom()
		order, so = confirmed_order_with_sales_order()

		items = get_work_order_items(so.name)  # what "Sales Order → Create → Work Order" offers
		work_orders = make_work_orders(json.dumps({"items": items}), so.name, so.company)

		self.assertEqual(len(work_orders), 1)
		self.assertEqual(frappe.db.get_value("Work Order", work_orders[0], "reno_order"), order.name)


class TestBuyingReference(IntegrationTestCase):
	def test_material_request_from_sales_order_carries_the_reno_order(self):
		order, so = confirmed_order_with_sales_order()
		self.assertEqual(submitted_material_request(so).reno_order, order.name)

	def test_purchase_order_from_supplier_quotation_recovers_the_reno_order(self):
		"""MR → RFQ → Supplier Quotation → PO: the RFQ and quotation have no reno_order field, so the
		PO arrives blank and the hook recovers it from the Material Request on its rows."""
		order, so = confirmed_order_with_sales_order()
		mr = submitted_material_request(so)

		rfq = make_request_for_quotation(mr.name)
		rfq.message_for_supplier = "Please quote."
		for supplier in SUPPLIERS:
			rfq.append("suppliers", {"supplier": make_supplier(supplier)})
		rfq.insert()
		rfq.submit()

		sq = make_supplier_quotation_from_rfq(rfq.name, for_supplier=SUPPLIERS[1])
		for row in sq.items:
			row.rate = 88
		sq.insert()
		sq.submit()

		po = make_purchase_order(sq.name)
		self.assertFalse(po.get("reno_order"))  # lost on the way, as expected
		po.schedule_date = add_days(nowdate(), 7)
		for row in po.items:
			row.schedule_date = po.schedule_date
		po.insert()

		self.assertEqual(po.reno_order, order.name)
		self.assertEqual({row.material_request for row in po.items}, {mr.name})

	def test_purchase_order_for_several_reno_orders_is_left_blank(self):
		"""One PO consolidating two kitchens' requests has no single Reno Order; rows still trace back."""
		requests = [submitted_material_request(confirmed_order_with_sales_order()[1]) for _ in range(2)]
		schedule_date = add_days(nowdate(), 7)
		po = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": make_supplier(SUPPLIERS[0]),
				"company": get_company(),
				"schedule_date": schedule_date,
				"items": [
					{
						"item_code": mr.items[0].item_code,
						"qty": mr.items[0].qty,
						"rate": 90,
						"schedule_date": schedule_date,
						"warehouse": mr.items[0].warehouse,
						"material_request": mr.name,
						"material_request_item": mr.items[0].name,
					}
					for mr in requests
				],
			}
		).insert()

		self.assertFalse(po.reno_order)
		self.assertEqual(len({row.material_request for row in po.items}), 2)

	def test_an_explicit_reno_order_is_kept(self):
		order, so = confirmed_order_with_sales_order()
		other, _so = confirmed_order_with_sales_order()
		mr = make_material_request(so.name)
		mr.reno_order = other.name  # set by the user: not overwritten
		mr.schedule_date = add_days(nowdate(), 7)
		for row in mr.items:
			row.schedule_date = mr.schedule_date
		mr.insert()

		self.assertEqual(mr.reno_order, other.name)
		self.assertNotEqual(order.name, other.name)

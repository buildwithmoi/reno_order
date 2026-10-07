"""Part 3 (+ Part 2 automation): Reno Order → Sales Order → Delivery Note → Sales Invoice."""

import frappe
from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice
from frappe.tests import IntegrationTestCase, change_settings

from reno_order.erpnext_flow.delivery import prepare_delivery_note, retry_failed_delivery_notes
from reno_order.reno_order.doctype.reno_order.reno_order import create_sales_order
from reno_order.tests.utils import advance, make_reno_order


def installed_order(submit_sales_order: bool = True):
	order = make_reno_order(submit=True)
	so = frappe.get_doc("Sales Order", create_sales_order(order.name))
	if submit_sales_order:
		so.submit()
	order = advance(order, "Start Production", "Mark Ready for Installation", "Mark Installed")
	return order, so


def reno_value(name: str, field: str):
	return frappe.db.get_value("Reno Order", name, field)


class TestInstalledAutomation(IntegrationTestCase):
	def test_marking_installed_queues_the_delivery_note(self):
		order, _so = installed_order()
		self.assertEqual(reno_value(order.name, "delivery_status"), "Queued")

	def test_job_prepares_a_draft_delivery_note_linked_both_ways(self):
		order, so = installed_order()
		dn = frappe.get_doc("Delivery Note", prepare_delivery_note(order.name))

		self.assertEqual(dn.docstatus, 0)  # prepared for the warehouse to check and submit
		self.assertEqual(dn.reno_order, order.name)
		self.assertEqual({row.against_sales_order for row in dn.items}, {so.name})
		self.assertEqual(reno_value(order.name, "delivery_note"), dn.name)
		self.assertEqual(reno_value(order.name, "delivery_status"), "Prepared")

	def test_running_the_job_twice_creates_one_delivery_note(self):
		order, _so = installed_order()
		first = prepare_delivery_note(order.name)
		second = prepare_delivery_note(order.name)

		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("Delivery Note", {"reno_order": order.name}), 1)

	def test_failure_is_recorded_then_retried(self):
		order, so = installed_order(submit_sales_order=False)  # nothing to deliver against yet

		self.assertIsNone(prepare_delivery_note(order.name))
		self.assertEqual(reno_value(order.name, "delivery_status"), "Failed")
		self.assertEqual(reno_value(order.name, "delivery_attempts"), 1)
		self.assertIn("Sales Order", reno_value(order.name, "delivery_error"))
		self.assertEqual(frappe.db.count("Delivery Note", {"reno_order": order.name}), 0)

		so.submit()
		retry_failed_delivery_notes()  # hourly job re-queues it
		self.assertEqual(reno_value(order.name, "delivery_status"), "Queued")
		self.assertTrue(prepare_delivery_note(order.name))
		self.assertEqual(reno_value(order.name, "delivery_status"), "Prepared")

	@change_settings("Reno Settings", {"auto_submit_delivery_note": 1})
	@change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_auto_submit_setting_submits_the_delivery_note(self):
		order, _so = installed_order()
		dn = frappe.get_doc("Delivery Note", prepare_delivery_note(order.name))
		self.assertEqual(dn.docstatus, 1)


class TestOrderToCash(IntegrationTestCase):
	@change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_reference_flows_to_the_sales_invoice(self):
		order, _so = installed_order()
		dn = frappe.get_doc("Delivery Note", prepare_delivery_note(order.name))
		dn.submit()

		si = make_sales_invoice(dn.name)  # ERPNext's standard "Create > Sales Invoice"
		si.insert()
		si.submit()

		self.assertEqual(si.reno_order, order.name)
		self.assertEqual(reno_value(order.name, "sales_invoice"), si.name)
		self.assertEqual(si.grand_total, order.grand_total)

	@change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_cancelling_the_delivery_note_clears_the_link(self):
		order, _so = installed_order()
		dn = frappe.get_doc("Delivery Note", prepare_delivery_note(order.name))
		dn.submit()
		dn.cancel()
		self.assertIsNone(reno_value(order.name, "delivery_note"))

	def test_reno_order_cannot_be_cancelled_while_its_sales_order_is_submitted(self):
		order = make_reno_order(submit=True)
		frappe.get_doc("Sales Order", create_sales_order(order.name)).submit()
		with self.assertRaises(frappe.LinkExistsError):
			advance(order, "Cancel")

"""Part 2: the order lifecycle is enforced on the server, per role."""

import frappe
from frappe.model.workflow import WorkflowPermissionError, WorkflowTransitionError
from frappe.tests import IntegrationTestCase

from reno_order.reno_order.doctype.reno_order.reno_order import create_sales_order
from reno_order.tests.utils import advance, make_reno_order, make_user


class TestRenoOrderWorkflow(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_full_lifecycle_by_the_right_roles(self):
		sales = make_user("reno.wf.sales@example.com", "Sales User")
		production = make_user("reno.wf.production@example.com", "Production User")
		supervisor = make_user("reno.wf.supervisor@example.com", "Site Supervisor")
		manager = make_user("reno.wf.manager@example.com", "Sales Manager")

		frappe.set_user(sales)
		order = make_reno_order(assigned_supervisor=supervisor)
		self.assertEqual(order.status, "Draft")
		order = advance(order, "Confirm")
		self.assertEqual((order.status, order.docstatus), ("Confirmed", 1))

		frappe.set_user(production)
		order = advance(order, "Start Production", "Mark Ready for Installation")
		self.assertEqual(order.status, "Ready for Installation")

		frappe.set_user(supervisor)
		order = advance(order, "Mark Installed")
		self.assertEqual(order.status, "Installed")
		self.assertTrue(order.installed_on)

		frappe.set_user(manager)
		order.db_set("owner", manager)  # managers see their own orders; ownership keeps this test focused
		order = advance(order, "Close")
		self.assertEqual(order.status, "Closed")

	def test_role_cannot_take_another_roles_step(self):
		frappe.set_user(make_user("reno.wf.sales2@example.com", "Sales User"))
		order = advance(make_reno_order(), "Confirm")
		with self.assertRaises(WorkflowTransitionError):
			advance(order, "Start Production")  # production's step, not sales'

	def test_status_cannot_be_jumped_by_editing_the_field(self):
		order = advance(make_reno_order(), "Confirm")
		order.status = "Installed"  # e.g. PUT /api/resource/Reno Order/<name> {"status": "Installed"}
		with self.assertRaises(WorkflowPermissionError):
			order.save()

	def test_new_order_cannot_start_in_a_later_state(self):
		with self.assertRaises(WorkflowPermissionError):
			make_reno_order(status="Closed")

	def test_installed_order_cannot_be_cancelled(self):
		order = advance(
			make_reno_order(), "Confirm", "Start Production", "Mark Ready for Installation", "Mark Installed"
		)
		with self.assertRaises(WorkflowTransitionError):
			advance(order, "Cancel")

	def test_cancel_before_installation(self):
		order = advance(make_reno_order(), "Confirm", "Start Production", "Cancel")
		self.assertEqual((order.status, order.docstatus), ("Cancelled", 2))

	def test_system_fields_are_not_client_settable(self):
		# A client tries to create an order that claims another order's real Sales Order.
		other = make_reno_order(submit=True)
		real_so = create_sales_order(other.name)

		order = make_reno_order(is_overdue=1, sales_order=real_so, installed_on="2026-01-01 10:00:00")
		self.assertFalse(order.is_overdue)
		self.assertFalse(order.sales_order)
		self.assertFalse(order.installed_on)

		# …or edits them later: stored values win.
		order.is_overdue = 1
		order.sales_order = real_so
		order.save()
		self.assertFalse(frappe.db.get_value("Reno Order", order.name, "sales_order"))

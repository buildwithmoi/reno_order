"""Part 1: totals, validations, discount authorisation, Sales Order creation and duplicate prevention."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from reno_order.exceptions import DiscountApprovalRequiredError, DuplicateSalesOrderError
from reno_order.reno_order.doctype.reno_order.reno_order import create_sales_order
from reno_order.tests.utils import confirm, item_row, make_reno_order, make_user


class TestRenoOrderTotals(IntegrationTestCase):
	def test_line_amounts_and_totals_are_calculated(self):
		order = make_reno_order(
			items=[item_row("_Test Reno Cabinet", 2, 1500), item_row("_Test Reno Countertop", 1, 1000)],
			discount=10,
		)
		self.assertEqual([row.amount for row in order.items], [3000, 1000])
		self.assertEqual(order.total_amount, 4000)
		self.assertEqual(order.discount_amount, 400)
		self.assertEqual(order.grand_total, 3600)

	def test_client_supplied_totals_are_ignored(self):
		order = make_reno_order(do_not_save=True, discount=10)
		# Pretend a client or API call sends doctored numbers.
		order.items[0].amount = 1
		order.total_amount = 1
		order.discount_amount = 999_999
		order.grand_total = 1
		order.insert()

		self.assertEqual(order.items[0].amount, 3000)
		self.assertEqual(order.total_amount, 4000)
		self.assertEqual(order.grand_total, 3600)

	def test_totals_recalculated_when_qty_changes(self):
		order = make_reno_order()
		order.items[0].qty = 4
		order.save()
		self.assertEqual(order.total_amount, 7000)


class TestRenoOrderValidation(IntegrationTestCase):
	def test_installation_date_before_order_date_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_reno_order(expected_installation_date=add_days(nowdate(), -1))

	def test_negative_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_reno_order(items=[item_row("_Test Reno Cabinet", -1, 1500)])

	def test_zero_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_reno_order(items=[item_row("_Test Reno Cabinet", 0, 1500)])

	def test_negative_rate_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_reno_order(items=[item_row("_Test Reno Cabinet", 1, -10)])

	def test_discount_must_be_between_0_and_100(self):
		with self.assertRaises(frappe.ValidationError):
			make_reno_order(discount=120)


class TestDiscountAuthorisation(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_discount_above_threshold_needs_approver_role(self):
		user = make_user("reno.sales.discount@example.com", "Sales User")
		frappe.set_user(user)
		order = make_reno_order(discount=25)  # default threshold is 10%

		with self.assertRaises(DiscountApprovalRequiredError):
			confirm(order)

		# The same user can confirm once they hold the approver role (Sales Manager by default).
		frappe.set_user("Administrator")
		frappe.get_doc("User", user).add_roles("Sales Manager")
		frappe.set_user(user)
		confirm(frappe.get_doc("Reno Order", order.name))
		self.assertEqual(frappe.db.get_value("Reno Order", order.name, "docstatus"), 1)

	def test_discount_within_threshold_needs_no_approval(self):
		frappe.set_user(make_user("reno.sales.small@example.com", "Sales User"))
		order = make_reno_order(discount=5)
		confirm(order)
		self.assertEqual(order.docstatus, 1)


class TestSalesOrderCreation(IntegrationTestCase):
	def test_sales_order_is_created_and_linked(self):
		order = make_reno_order(discount=5, submit=True)
		so = frappe.get_doc("Sales Order", create_sales_order(order.name))

		self.assertEqual(so.docstatus, 0)
		self.assertEqual(so.reno_order, order.name)
		self.assertEqual(so.customer, order.customer)
		self.assertEqual(so.grand_total, order.grand_total)
		self.assertEqual(frappe.db.get_value("Reno Order", order.name, "sales_order"), so.name)

	def test_unconfirmed_order_cannot_create_sales_order(self):
		order = make_reno_order()
		with self.assertRaises(frappe.ValidationError):
			create_sales_order(order.name)

	def test_duplicate_sales_order_is_prevented(self):
		order = make_reno_order(submit=True)
		first = create_sales_order(order.name)

		with self.assertRaises(DuplicateSalesOrderError):
			create_sales_order(order.name)

		# A copy made outside our button (UI, API, mapper) is caught by the Sales Order validate hook.
		with self.assertRaises(DuplicateSalesOrderError):
			frappe.copy_doc(frappe.get_doc("Sales Order", first)).insert()

	def test_new_sales_order_allowed_after_cancelling_the_old_one(self):
		order = make_reno_order(submit=True)
		so = frappe.get_doc("Sales Order", create_sales_order(order.name))
		so.submit()
		so.cancel()

		self.assertIsNone(frappe.db.get_value("Reno Order", order.name, "sales_order"))
		self.assertTrue(create_sales_order(order.name))

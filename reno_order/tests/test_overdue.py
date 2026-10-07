"""Part 2: the daily job that flags overdue installations."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from reno_order.tasks import flag_overdue_installations
from reno_order.tests.utils import advance, make_reno_order


def is_overdue(name: str) -> int:
	return frappe.db.get_value("Reno Order", name, "is_overdue")


class TestOverdueInstallations(IntegrationTestCase):
	def late_order(self):
		return make_reno_order(
			transaction_date=add_days(nowdate(), -20), expected_installation_date=add_days(nowdate(), -2)
		)

	def test_late_open_order_is_flagged(self):
		order = advance(self.late_order(), "Confirm")
		flag_overdue_installations()
		self.assertEqual(is_overdue(order.name), 1)

	def test_future_and_finished_orders_are_not_flagged(self):
		on_time = advance(make_reno_order(), "Confirm")
		installed = advance(
			self.late_order(), "Confirm", "Start Production", "Mark Ready for Installation", "Mark Installed"
		)
		flag_overdue_installations()
		self.assertEqual(is_overdue(on_time.name), 0)
		self.assertEqual(is_overdue(installed.name), 0)

	def test_flag_clears_once_installed(self):
		order = advance(self.late_order(), "Confirm", "Start Production", "Mark Ready for Installation")
		flag_overdue_installations()
		self.assertEqual(is_overdue(order.name), 1)

		advance(order, "Mark Installed")
		flag_overdue_installations()
		self.assertEqual(is_overdue(order.name), 0)

	def test_job_is_safe_to_run_twice(self):
		order = advance(self.late_order(), "Confirm")
		flag_overdue_installations()
		flag_overdue_installations()
		self.assertEqual(is_overdue(order.name), 1)

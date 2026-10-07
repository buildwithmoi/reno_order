"""Part 9 (backfill patch) and Part 10 (monthly value report)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from reno_order.patches.v1_0 import backfill_order_type
from reno_order.reno_order.report.monthly_reno_order_value import monthly_reno_order_value as report
from reno_order.tests.utils import advance, get_company, item_row, make_reno_order, make_user


class TestBackfillOrderTypePatch(IntegrationTestCase):
	def test_fills_blanks_keeps_real_values_and_is_idempotent(self):
		null_type = make_reno_order().name
		empty_type = make_reno_order().name
		premium = make_reno_order(order_type="Premium").name
		frappe.db.sql("UPDATE `tabReno Order` SET order_type = NULL WHERE name = %s", null_type)
		frappe.db.sql("UPDATE `tabReno Order` SET order_type = '' WHERE name = %s", empty_type)

		# The patch commits per batch on a real site; keep this test inside its rolled-back transaction.
		with patch.object(frappe.db, "commit"):
			first_run = backfill_order_type.execute()
			second_run = backfill_order_type.execute()

		values = dict(
			frappe.db.sql(
				"SELECT name, order_type FROM `tabReno Order` WHERE name IN %s",
				[(null_type, empty_type, premium)],
			)
		)
		self.assertEqual(values, {null_type: "Standard", empty_type: "Standard", premium: "Premium"})
		self.assertEqual(first_run, 2)
		self.assertEqual(second_run, 0)  # idempotent

	def test_patch_is_registered_after_model_sync(self):
		patches = frappe.get_file_items(frappe.get_app_path("reno_order", "patches.txt"))
		self.assertIn("reno_order.patches.v1_0.backfill_order_type", patches)


class TestMonthlyValueReport(IntegrationTestCase):
	def setUp(self):
		# 2019 dates keep these orders apart from demo or synthetic data on a developer site.
		self.FILTERS = {"from_date": "2019-01-01", "to_date": "2019-12-31", "company": get_company()}

	def order(self, day, rate, actions=()):
		doc = make_reno_order(transaction_date=day, items=[item_row("_Test Reno Cabinet", 1, rate)])
		return advance(doc, *actions) if actions else doc

	def test_value_is_grouped_by_month_and_status(self):
		self.order("2019-03-05", 1000)
		self.order("2019-03-20", 2000, ["Confirm"])
		self.order("2019-03-25", 500, ["Confirm"])
		self.order("2019-07-10", 4000, ["Confirm", "Start Production"])

		_columns, data, _message, chart = report.execute(self.FILTERS)
		rows = {row["month"]: row for row in data}

		self.assertEqual(len(data), 12)  # every month in range, even empty ones
		self.assertEqual((rows["2019-03"]["draft"], rows["2019-03"]["confirmed"]), (1000, 2500))
		self.assertEqual((rows["2019-03"]["total"], rows["2019-03"]["orders"]), (3500, 3))
		self.assertEqual(rows["2019-07"]["in_production"], 4000)
		self.assertEqual(rows["2019-05"]["total"], 0)
		self.assertEqual(len(chart["data"]["datasets"]), len(report.STATUSES))

	def test_report_respects_row_level_permissions(self):
		self.order("2019-04-01", 9000)  # created by Administrator
		manager = make_user("reno.report.manager@example.com", "Sales Manager")
		frappe.set_user(manager)
		try:
			mine = make_reno_order(
				transaction_date="2019-04-15", items=[item_row("_Test Reno Cabinet", 1, 700)]
			)
			_columns, data, *_ = report.execute(self.FILTERS)
		finally:
			frappe.set_user("Administrator")

		april = next(row for row in data if row["month"] == "2019-04")
		self.assertEqual(april["total"], 700)  # only what this manager is allowed to see
		self.assertTrue(mine.name)

	def test_report_query_uses_the_covering_index(self):
		query = report.MONTHLY_QUERY.format(permission_condition="")
		plan = frappe.db.sql(f"EXPLAIN {query}", {**self.FILTERS}, as_dict=True)[0]
		self.assertIn("company_date_status_total_index", plan.possible_keys or "")

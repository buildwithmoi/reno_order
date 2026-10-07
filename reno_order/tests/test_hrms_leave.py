"""Part 13: a mid-year joiner gets prorated Annual Leave but the full fixed (event-based) entitlement.

Skipped automatically on sites without HRMS.
"""

import unittest

import frappe
from frappe.tests import IntegrationTestCase

from reno_order.tests.utils import get_company, make_employee, make_user

HRMS_INSTALLED = "hrms" in frappe.get_installed_apps()

# leave type: (annual allocation, fixed entitlement?)
POLICY = {
	"_Reno Annual Leave": (24, 0),
	"_Reno Maternity Leave": (84, 1),
	"_Reno Paternity Leave": (5, 1),
	"_Reno Marriage Leave": (3, 1),
}


@unittest.skipUnless(HRMS_INSTALLED, "HRMS is not installed on this site")
class TestFixedEntitlementLeave(IntegrationTestCase):
	PERIOD = ("2026-01-01", "2026-12-31")

	def setUp(self):
		for leave_type, (_days, fixed) in POLICY.items():
			if not frappe.db.exists("Leave Type", leave_type):
				frappe.get_doc({"doctype": "Leave Type", "leave_type_name": leave_type}).insert()
			frappe.db.set_value("Leave Type", leave_type, "fixed_entitlement", fixed)
		frappe.clear_cache(doctype="Leave Type")

		# HRMS allows one Leave Period per company and date range: reuse it if it already exists.
		period = {"from_date": self.PERIOD[0], "to_date": self.PERIOD[1], "company": get_company()}
		existing = frappe.db.get_value("Leave Period", period)
		self.period = (
			frappe.get_doc("Leave Period", existing)
			if existing
			else frappe.get_doc({"doctype": "Leave Period", "is_active": 1, **period}).insert()
		)
		self.policy = frappe.get_doc(
			{
				"doctype": "Leave Policy",
				"title": "_Reno Staff Policy",
				"leave_policy_details": [
					{"leave_type": lt, "annual_allocation": days} for lt, (days, _fixed) in POLICY.items()
				],
			}
		).insert()
		self.policy.submit()

	def allocate(self, email: str, date_of_joining: str) -> dict[str, float]:
		employee = make_employee(make_user(email))
		frappe.db.set_value("Employee", employee, "date_of_joining", date_of_joining)
		assignment = frappe.get_doc(
			{
				"doctype": "Leave Policy Assignment",
				"employee": employee,
				"company": get_company(),
				"leave_policy": self.policy.name,
				"assignment_based_on": "Leave Period",
				"leave_period": self.period.name,
			}
		).insert()
		assignment.submit()
		return dict(
			frappe.get_all(
				"Leave Allocation",
				filters={"leave_policy_assignment": assignment.name, "docstatus": 1},
				fields=["leave_type", "new_leaves_allocated"],
				as_list=True,
			)
		)

	def test_mid_year_joiner_gets_full_fixed_entitlement(self):
		allocations = self.allocate("reno.leave.midyear@example.com", "2026-07-01")

		self.assertEqual(allocations["_Reno Annual Leave"], 12)  # 24 x 184/365, rounded: still prorated
		self.assertEqual(allocations["_Reno Maternity Leave"], 84)
		self.assertEqual(allocations["_Reno Paternity Leave"], 5)
		self.assertEqual(allocations["_Reno Marriage Leave"], 3)

	def test_without_the_flag_hrms_prorates_it_reproducing_the_bug(self):
		frappe.db.set_value("Leave Type", "_Reno Marriage Leave", "fixed_entitlement", 0)
		frappe.clear_cache(doctype="Leave Type")
		allocations = self.allocate("reno.leave.bug@example.com", "2026-07-01")
		self.assertEqual(
			allocations["_Reno Marriage Leave"], 2
		)  # 3 x 184/365 = 1.5, rounded: the reported bug

	def test_late_joiner_still_gets_fixed_entitlement(self):
		# Joining on 29 December: Annual Leave = 24 x 3/365 = 0.2, rounded to 0, so HRMS skips that
		# allocation entirely. The fixed entitlement must still be granted in full.
		allocations = self.allocate("reno.leave.late@example.com", "2026-12-29")
		self.assertNotIn("_Reno Annual Leave", allocations)
		self.assertEqual(allocations["_Reno Marriage Leave"], 3)

	def test_full_year_employee_unaffected(self):
		allocations = self.allocate("reno.leave.fullyear@example.com", "2025-03-01")
		self.assertEqual(allocations["_Reno Annual Leave"], 24)
		self.assertEqual(allocations["_Reno Maternity Leave"], 84)

	def test_flag_rejected_on_earned_leave(self):
		leave_type = frappe.get_doc(
			{
				"doctype": "Leave Type",
				"leave_type_name": "_Reno Earned Leave",
				"is_earned_leave": 1,
				"earned_leave_frequency": "Monthly",
				"fixed_entitlement": 1,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			leave_type.insert()

"""Part 13: fixed-entitlement (event-based) leave must not be prorated for mid-year joiners.

Root cause (HRMS v16, LeavePolicyAssignment.get_new_leaves): every leave type that is neither earned
nor compensatory goes through calculate_pro_rated_leaves(). That scales the annual allocation by
(period end - joining date + 1) / (period length) whenever the employee joined after the period
started. That's right for Annual Leave, but wrong for Maternity, Paternity or Marriage Leave, whose
entitlement doesn't depend on how much of the year is left. HRMS has no per-leave-type setting for
this, and it refuses two overlapping policy assignments for one employee, so the leave types can't be
split across two differently-dated policies.

Fix:
- a Leave Type checkbox, **Fixed Entitlement (not pro-rated)** (custom field, see setup/install.py)
- this subclass, registered with `override_doctype_class`, which returns the full annual allocation
  for flagged types and leaves everything else to HRMS

No HRMS code is changed. (In a real project this would live in the company's HR customisation app.)
See docs/hrms-leave-allocation.md.
"""

import frappe
from frappe import _
from frappe.utils import flt
from hrms.hr.doctype.leave_policy_assignment.leave_policy_assignment import LeavePolicyAssignment


class RenoLeavePolicyAssignment(LeavePolicyAssignment):
	def get_new_leaves(self, annual_allocation, leave_details, date_of_joining):
		if frappe.get_cached_value("Leave Type", leave_details.name, "fixed_entitlement"):
			return flt(annual_allocation)
		return super().get_new_leaves(annual_allocation, leave_details, date_of_joining)


def validate_leave_type(doc, method=None):
	"""Earned and compensatory leave accrue over time, so "fixed entitlement" makes no sense for them."""
	if doc.get("fixed_entitlement") and (doc.is_earned_leave or doc.is_compensatory):
		frappe.throw(
			_("Fixed Entitlement can't be used with Earned or Compensatory leave types."),
			title=_("Invalid Leave Type"),
		)

"""Business-rule errors raised by the Reno Order app.

Subclassing frappe.ValidationError keeps Frappe's normal handling (rollback + error message),
while giving tests and API callers a precise exception type to check for.
"""

import frappe


class DiscountApprovalRequiredError(frappe.ValidationError):
	"""The discount is above the approval threshold and the user lacks the approver role."""


class DuplicateSalesOrderError(frappe.ValidationError):
	"""An active (draft or submitted) Sales Order already exists for the Reno Order."""

"""Business-rule errors raised by the Reno Order app.

Subclassing frappe.ValidationError keeps Frappe's normal handling (rollback + error message),
while giving tests and API callers a precise exception type to check for.
"""

import frappe


class DiscountApprovalRequiredError(frappe.ValidationError):
	"""The discount is above the approval threshold and the user lacks the approver role."""


class DuplicateSalesOrderError(frappe.ValidationError):
	"""An active (draft or submitted) Sales Order already exists for the Reno Order."""


class InvalidStatusTransitionError(frappe.ValidationError):
	"""The requested status (or action) isn't allowed from the order's current status for this user."""

	http_status_code = 409  # Conflict with the order's current state


class InvalidInputError(frappe.ValidationError):
	"""A request parameter is missing or malformed (empty remarks, a non-image upload, …)."""

	http_status_code = 422  # Unprocessable: the request is understood but its content is invalid

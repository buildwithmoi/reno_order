"""Scheduled jobs (registered in hooks.scheduler_events)."""

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import getdate

DONE_STATUSES = ("Installed", "Closed", "Cancelled")


def flag_overdue_installations():
	"""Daily: flag orders whose Expected Installation Date has passed but aren't installed, closed or
	cancelled, clear the flag on orders that are no longer overdue, and notify the Sales Managers.

	Two set-based UPDATEs instead of loading every document: fast on large tables, and both
	statements use the index on expected_installation_date.
	"""
	params = {"today": getdate(), "done": DONE_STATUSES}

	frappe.db.sql(
		"""
		UPDATE `tabReno Order` SET is_overdue = 1
		WHERE docstatus < 2 AND is_overdue = 0
			AND expected_installation_date < %(today)s AND status NOT IN %(done)s
		""",
		params,
	)
	frappe.db.sql(
		"""
		UPDATE `tabReno Order` SET is_overdue = 0
		WHERE is_overdue = 1
			AND (expected_installation_date >= %(today)s OR status IN %(done)s OR docstatus = 2)
		""",
		params,
	)

	overdue = frappe.get_all(
		"Reno Order", filters={"is_overdue": 1, "docstatus": ("<", 2)}, pluck="name", ignore_permissions=True
	)
	if overdue:
		_notify_sales_managers(overdue)
	return overdue


def _notify_sales_managers(overdue: list[str]):
	managers = frappe.get_all(
		"Has Role",
		filters={
			"role": "Sales Manager",
			"parenttype": "User",
			"parent": ("not in", ("Administrator", "Guest")),
		},
		pluck="parent",
		distinct=True,
	)
	if not managers:
		return
	enqueue_create_notification(
		managers,
		{
			"type": "Alert",
			"document_type": "Reno Order",
			"subject": _("{0} Reno Order(s) are past their expected installation date").format(len(overdue)),
			"email_content": _("Overdue: {0}").format(", ".join(overdue[:50])),
		},
	)

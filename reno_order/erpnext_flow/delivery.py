"""Part 2 automation: when a Reno Order is marked Installed, prepare its Delivery Note.

The work runs in a background job, so marking an order Installed stays fast, and it is safe to run
more than once:
- `enqueue_after_commit`: the job is queued only after the Installed status is committed, so it
  never sees uncommitted data, and nothing is queued if the save fails.
- `job_id` + `deduplicate`: RQ won't queue a second job for the same order while one is waiting or
  running.
- Inside the job, the Reno Order row is locked and an existing Delivery Note is reused rather than
  creating another.
Failures are recorded on the order (status, attempts, last error) and in the Error Log. An hourly job
retries them up to MAX_ATTEMPTS, and the form has a manual "Retry" for after that.
"""

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from frappe import _
from frappe.utils import cint, strip_html

from reno_order.utils import run_as

MAX_ATTEMPTS = 3
DONE_STATUSES = ("Installed", "Closed")


def queue_delivery_note(reno_order: str):
	frappe.db.set_value(
		"Reno Order", reno_order, {"delivery_status": "Queued", "delivery_error": None}, update_modified=False
	)
	frappe.enqueue(
		"reno_order.erpnext_flow.delivery.prepare_delivery_note",
		queue="default",  # quick local work; slow external API calls go on "long" (Part 8)
		job_id=f"reno_order::prepare_delivery_note::{reno_order}",
		deduplicate=True,
		enqueue_after_commit=True,
		reno_order=reno_order,
	)


def prepare_delivery_note(reno_order: str) -> str | None:
	"""Background job. Returns the Delivery Note name, or None if there was nothing to do or it failed."""
	settings = frappe.get_cached_doc("Reno Settings")
	# Run as the configured service account, not as whoever clicked "Mark Installed"
	# (a Site Supervisor has no stock permissions), and without bypassing permission checks.
	with run_as(settings.automation_user or "Administrator"):
		return _prepare(reno_order, auto_submit=cint(settings.auto_submit_delivery_note))


def _prepare(reno_order: str, auto_submit: bool) -> str | None:
	reno = frappe.db.get_value(
		"Reno Order", reno_order, ["docstatus", "status", "delivery_attempts"], as_dict=True, for_update=True
	)
	if not reno or reno.docstatus != 1 or reno.status not in DONE_STATUSES:
		return None  # the order changed after the job was queued

	if existing := get_active_delivery_note(reno_order):
		_record(reno_order, "Prepared", delivery_note=existing)
		return existing

	frappe.db.savepoint("reno_delivery_note")
	try:
		sales_order = frappe.db.get_value("Sales Order", {"reno_order": reno_order, "docstatus": 1}, "name")
		if not sales_order:
			frappe.throw(
				_("Reno Order {0} has no submitted Sales Order to deliver against.").format(reno_order)
			)
		dn = make_delivery_note(sales_order)  # ERPNext's own mapper: pending quantities, warehouses, prices
		dn.insert()
		if auto_submit:
			dn.submit()
	except Exception as e:
		# Undo anything half-done (e.g. a Delivery Note inserted but not submitted), keep the failure.
		frappe.db.rollback(save_point="reno_delivery_note")
		_record(
			reno_order,
			"Failed",
			error=strip_html(str(e)) or e.__class__.__name__,
			attempts=cint(reno.delivery_attempts) + 1,
		)
		frappe.log_error(
			title=f"Reno Order {reno_order}: Delivery Note automation failed",
			reference_doctype="Reno Order",
			reference_name=reno_order,
		)
		return None

	_record(reno_order, "Prepared", delivery_note=dn.name)
	return dn.name


def get_active_delivery_note(reno_order: str) -> str | None:
	return frappe.db.get_value(
		"Delivery Note", {"reno_order": reno_order, "docstatus": ("<", 2), "is_return": 0}, "name"
	)


def retry_failed_delivery_notes():
	"""Hourly: re-queue failed automations that haven't used up their attempts."""
	for name in frappe.get_all(
		"Reno Order",
		filters={"delivery_status": "Failed", "delivery_attempts": ("<", MAX_ATTEMPTS), "docstatus": 1},
		pluck="name",
	):
		queue_delivery_note(name)


@frappe.whitelist(methods=["POST"])
def retry_delivery_note(reno_order: str):
	"""Manual retry from the form, e.g. after fixing the cause (submitting the Sales Order)."""
	frappe.get_doc("Reno Order", reno_order).check_permission("write")
	queue_delivery_note(reno_order)


def _record(reno_order: str, status: str, delivery_note=None, error=None, attempts=None):
	values = {"delivery_status": status, "delivery_error": error}
	if delivery_note:
		values["delivery_note"] = delivery_note
	if attempts is not None:
		values["delivery_attempts"] = attempts
	frappe.db.set_value("Reno Order", reno_order, values, update_modified=False)

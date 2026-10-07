"""Parts 7 + 8: book delivery of an order's components with the logistics provider.

The provider's API is slow (10-20 s) and sometimes fails, so it never runs while a user waits:

1. Marking an order *Ready for Installation* only **queues** a job (`on_update_after_submit`):
   queued after the save commits, deduplicated by job id, on the **long** queue. The default/short
   queues are for quick work, and a slow call plus in-job retries needs long's generous timeout.
2. The job **claims** the order with one conditional UPDATE (Queued/Failed → Booking) and commits at
   once. Only one worker can win the claim, and no row lock is held during the slow network call,
   so users can keep working on the order.
3. Every request carries an **Idempotency-Key**. If our timeout fires after the provider already
   created the shipment, the retry gets the same shipment back instead of a duplicate.
4. **Transient failures** (timeouts, connection errors, 5xx/429) are retried inside the job with
   exponential backoff. If they keep failing, the order goes to *Failed* with a next-retry time
   (5, 10, 20… min), and the hourly job picks it up again, up to *Max Booking Attempts*.
   **Permanent failures** (401, 422, …) wait for a person to fix the cause and press Retry.
5. A worker that dies mid-call leaves the order in *Booking*. The hourly job treats a claim older
   than 15 minutes as failed and re-queues it; the Idempotency-Key makes that safe.
"""

import random
import time
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from reno_order.integrations.logistics.client import (
	LogisticsClient,
	LogisticsError,
	LogisticsTransientError,
)
from reno_order.utils import run_as

QUEUE = "long"
JOB_TIMEOUT = 300  # seconds: a 20 s call x in-job retries, with plenty of margin
IN_JOB_ATTEMPTS = 3
STALE_CLAIM = timedelta(minutes=15)
CLAIMABLE_STATUSES = ("Queued", "Failed")


def is_enabled() -> bool:
	return bool(cint(frappe.db.get_single_value("Reno Logistics Settings", "enabled")))


def queue_shipment_booking(reno_order: str):
	if not is_enabled():
		return
	_set(reno_order, logistics_status="Queued", logistics_next_retry=None)
	frappe.enqueue(
		"reno_order.integrations.logistics.booking.book_shipment",
		queue=QUEUE,
		timeout=JOB_TIMEOUT,
		job_id=f"reno_order::book_shipment::{reno_order}",
		deduplicate=True,
		enqueue_after_commit=True,
		reno_order=reno_order,
	)


def book_shipment(reno_order: str, sleep=time.sleep) -> str | None:
	"""Background job. Returns the shipment id, or None if skipped or failed."""
	settings = frappe.get_single("Reno Logistics Settings")
	if not cint(settings.enabled) or not _claim(reno_order):
		return None  # disabled, already booked, or another worker is on it
	if not frappe.in_test:  # tests run inside one transaction that is rolled back afterwards
		# Make the claim visible and release the row lock before the slow network call (claim → commit → call).
		frappe.db.commit()  # nosemgrep

	automation_user = frappe.db.get_single_value("Reno Settings", "automation_user") or "Administrator"
	with run_as(automation_user):
		try:
			response = _with_retries(
				lambda: LogisticsClient(settings).create_shipment(
					build_shipment_payload(reno_order),
					idempotency_key=f"reno-order:{reno_order}:delivery",
					reference_name=reno_order,
				),
				sleep=sleep,
			)
		except LogisticsError as e:
			_record_failure(reno_order, e, cint(settings.max_attempts) or 5)
			return None

	shipment_id = response.get("shipment_id")
	_set(
		reno_order,
		logistics_status="Booked",
		logistics_reference=shipment_id,
		logistics_error=None,
		logistics_next_retry=None,
	)
	return shipment_id


def build_shipment_payload(reno_order: str) -> dict:
	"""What the provider needs: where, when and what (stock items only; services aren't shipped)."""
	reno = frappe.get_doc("Reno Order", reno_order)
	address = frappe.get_doc("Address", reno.customer_address) if reno.customer_address else None
	contact = frappe.get_doc("Contact", reno.contact_person) if reno.contact_person else None
	return {
		"reference": reno.name,
		"scheduled_date": str(reno.expected_installation_date),
		"deliver_to": {
			"name": reno.customer_name,
			"address": reno.address_display or "",
			"city": address.city if address else None,
			"country": address.country if address else None,
			"phone": (contact.mobile_no or contact.phone)
			if contact
			else (address.phone if address else None),
		},
		"items": [
			{"item_code": row.item_code, "description": row.item_name, "qty": row.qty, "uom": row.uom}
			for row in reno.items
			if frappe.get_cached_value("Item", row.item_code, "is_stock_item")
		],
	}


def retry_failed_bookings():
	"""Hourly: re-queue failed bookings that are due, and recover claims left by a dead worker."""
	if not is_enabled():
		return
	now = now_datetime()
	frappe.db.sql(
		"""
		UPDATE `tabReno Order`
		SET logistics_status = 'Failed', logistics_error = %(error)s, logistics_next_retry = %(now)s
		WHERE logistics_status = 'Booking' AND logistics_updated_on < %(stale_before)s
		""",
		{
			"error": "Booking was interrupted (worker stopped). Retrying.",
			"now": now,
			"stale_before": now - STALE_CLAIM,
		},
	)
	max_attempts = cint(frappe.db.get_single_value("Reno Logistics Settings", "max_attempts")) or 5
	for name in frappe.get_all(
		"Reno Order",
		filters={
			"docstatus": 1,
			"logistics_status": "Failed",
			"logistics_attempts": ("<", max_attempts),
			"logistics_next_retry": ("<=", now),
		},
		pluck="name",
	):
		queue_shipment_booking(name)


@frappe.whitelist(methods=["POST"])
def retry_shipment_booking(reno_order: str):
	"""Manual retry from the form after fixing the cause (credentials, address, …)."""
	frappe.get_doc("Reno Order", reno_order).check_permission("write")
	if frappe.db.get_value("Reno Order", reno_order, "logistics_status") != "Failed":
		frappe.throw(_("Only failed bookings can be retried."))
	queue_shipment_booking(reno_order)


# ---------------------------------------------------------------------- internals


def _claim(reno_order: str) -> bool:
	"""Atomically move Queued/Failed → Booking. Exactly one concurrent worker gets rowcount 1."""
	frappe.db.sql(
		"""
		UPDATE `tabReno Order`
		SET logistics_status = 'Booking', logistics_updated_on = %(now)s,
			logistics_attempts = IFNULL(logistics_attempts, 0) + 1
		WHERE name = %(name)s AND docstatus = 1 AND logistics_status IN %(claimable)s
			AND IFNULL(logistics_reference, '') = ''
		""",
		{"name": reno_order, "now": now_datetime(), "claimable": CLAIMABLE_STATUSES},
	)
	return frappe.db._cursor.rowcount == 1


def _with_retries(call, sleep=time.sleep):
	"""Retry transient errors with exponential backoff and jitter (≈2 s, 4 s)."""
	for attempt in range(1, IN_JOB_ATTEMPTS + 1):
		try:
			return call()
		except LogisticsTransientError:
			if attempt == IN_JOB_ATTEMPTS:
				raise
			sleep(2**attempt + random.uniform(0, 1))


def _record_failure(reno_order: str, error: LogisticsError, max_attempts: int):
	attempts = cint(frappe.db.get_value("Reno Order", reno_order, "logistics_attempts"))
	will_retry = isinstance(error, LogisticsTransientError) and attempts < max_attempts
	_set(
		reno_order,
		logistics_status="Failed",
		logistics_error=str(error)[:1000],
		logistics_next_retry=now_datetime() + timedelta(minutes=min(5 * 2 ** (attempts - 1), 120))
		if will_retry
		else None,
	)
	frappe.log_error(
		title=f"Reno Order {reno_order}: delivery booking failed",
		message=str(error),
		reference_doctype="Reno Order",
		reference_name=reno_order,
	)


def _set(reno_order: str, **values):
	values["logistics_updated_on"] = now_datetime()
	frappe.db.set_value("Reno Order", reno_order, values, update_modified=False)

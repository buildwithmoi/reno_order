"""Webhook receiver: the logistics provider reports shipment status changes (Part 7).

    POST /api/method/reno_order.integrations.logistics.webhook.shipment_status
    X-Reno-Timestamp: <unix seconds>
    X-Reno-Signature: hex(HMAC-SHA256(webhook_secret, "<timestamp>.<raw body>"))
    {"shipment_id": "SHP-10001", "status": "delivered"}

The provider can't log in, so the endpoint is open to guests, but nothing happens unless the
signature matches the shared secret (constant-time comparison) and the timestamp is recent, which
stops replays of old messages. It can only change the order's logistics status fields.
"""

import hashlib
import hmac
import json
import time

import frappe
from frappe import _

from reno_order.integrations.logistics.client import SERVICE_NAME

MAX_AGE_SECONDS = 300
STATUS_MAP = {"scheduled": "Booked", "in_transit": "In Transit", "delivered": "Delivered"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def shipment_status():
	return process_status_update(
		frappe.request.get_data(),
		frappe.get_request_header("X-Reno-Timestamp"),
		frappe.get_request_header("X-Reno-Signature"),
	)


def process_status_update(raw_body: bytes, timestamp: str | None, signature: str | None) -> dict:
	verify_signature(raw_body, timestamp, signature)

	try:
		data = json.loads(raw_body)
		shipment_id, provider_status = data["shipment_id"], data["status"]
	except ValueError, KeyError, TypeError:
		frappe.throw(_("Expected JSON with shipment_id and status."), frappe.ValidationError)

	status = STATUS_MAP.get(provider_status)
	if not status:
		frappe.throw(_("Unknown shipment status {0}.").format(provider_status), frappe.ValidationError)

	reno_order = frappe.db.get_value("Reno Order", {"logistics_reference": shipment_id}, "name")
	if not reno_order:
		raise frappe.DoesNotExistError(_("No Reno Order for shipment {0}.").format(shipment_id))

	# Authenticated by signature, not by a user session: a direct, narrow update of the two
	# logistics fields; nothing else on the order can be touched from here.
	frappe.db.set_value(
		"Reno Order",
		reno_order,
		{"logistics_status": status, "logistics_updated_on": frappe.utils.now_datetime()},
		update_modified=False,
	)
	frappe.get_doc(
		{
			"doctype": "Integration Request",
			"integration_request_service": SERVICE_NAME,
			"request_description": "Webhook: shipment status",
			"is_remote_request": 1,
			"request_id": shipment_id,
			"data": raw_body.decode("utf-8", "replace"),
			"status": "Completed",
			"reference_doctype": "Reno Order",
			"reference_docname": reno_order,
		}
	).insert(ignore_permissions=True)
	return {"reno_order": reno_order, "logistics_status": status}


def verify_signature(raw_body: bytes, timestamp: str | None, signature: str | None):
	secret = frappe.get_single("Reno Logistics Settings").get_password(
		"webhook_secret", raise_exception=False
	)
	if not secret:
		raise frappe.AuthenticationError(_("Webhook signing secret is not configured."))
	if not timestamp or not signature:
		raise frappe.AuthenticationError(_("Missing signature headers."))
	try:
		age = abs(time.time() - int(timestamp))
	except ValueError:
		raise frappe.AuthenticationError(_("Invalid timestamp."))
	if age > MAX_AGE_SECONDS:
		raise frappe.AuthenticationError(_("Signature has expired."))

	expected = sign(secret, raw_body, timestamp)
	if not hmac.compare_digest(expected, signature):
		raise frappe.AuthenticationError(_("Invalid signature."))


def sign(secret: str, raw_body: bytes, timestamp: str) -> str:
	return hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()

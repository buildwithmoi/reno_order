"""Parts 7 + 8: logistics integration: auth, timeouts, retries, logging, idempotency, webhook."""

import json
import time
from datetime import timedelta
from unittest.mock import patch

import frappe
import requests
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from reno_order.integrations.logistics import booking, client, webhook
from reno_order.tests.utils import advance, make_reno_order

API_KEY = "test-api-key-123"
WEBHOOK_SECRET = "test-webhook-secret"


class FakeResponse:
	def __init__(self, status_code: int, body: dict):
		self.status_code = status_code
		self._body = body
		self.text = json.dumps(body)

	@property
	def ok(self):
		return self.status_code < 400

	def json(self):
		return self._body


def http(*responses):
	"""Patch the HTTP call; each item is a FakeResponse or an exception to raise, in order."""
	return patch.object(client.requests, "request", side_effect=list(responses))


class LogisticsTestCase(IntegrationTestCase):
	def setUp(self):
		settings = frappe.get_single("Reno Logistics Settings")
		settings.update(
			{
				"enabled": 1,
				"base_url": "https://logistics.example",
				"api_key": API_KEY,
				"webhook_secret": WEBHOOK_SECRET,
				"connect_timeout": 5,
				"read_timeout": 30,
				"max_attempts": 3,
			}
		)
		settings.save()
		self.sleeps = []

	def ready_order(self):
		return advance(make_reno_order(), "Confirm", "Start Production", "Mark Ready for Installation")

	def book(self, name):
		return booking.book_shipment(name, sleep=self.sleeps.append)  # no real waiting in tests

	def logistics(self, name, *fields):
		return frappe.db.get_value("Reno Order", name, fields or "logistics_status", as_dict=bool(fields))


class TestBooking(LogisticsTestCase):
	def test_ready_for_installation_queues_the_booking(self):
		self.assertEqual(self.logistics(self.ready_order().name), "Queued")

	def test_successful_booking_is_recorded_and_logged(self):
		order = self.ready_order()
		with http(FakeResponse(201, {"shipment_id": "SHP-1", "status": "scheduled"})) as call:
			self.assertEqual(self.book(order.name), "SHP-1")

		kwargs = call.call_args.kwargs
		self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {API_KEY}")
		self.assertEqual(kwargs["headers"]["Idempotency-Key"], f"reno-order:{order.name}:delivery")
		self.assertEqual(kwargs["timeout"], (5, 30))
		self.assertEqual(kwargs["json"]["reference"], order.name)

		state = self.logistics(order.name, "logistics_status", "logistics_reference", "logistics_attempts")
		self.assertEqual(
			(state.logistics_status, state.logistics_reference, state.logistics_attempts),
			("Booked", "SHP-1", 1),
		)

		log = frappe.get_last_doc("Integration Request", {"reference_docname": order.name})
		self.assertEqual(log.status, "Completed")
		self.assertNotIn(API_KEY, log.request_headers)  # the secret never reaches the log

	def test_transient_errors_are_retried_with_backoff(self):
		order = self.ready_order()
		with http(
			requests.Timeout(),
			FakeResponse(503, {"error": "service_unavailable"}),
			FakeResponse(201, {"shipment_id": "SHP-2"}),
		) as call:
			self.assertEqual(self.book(order.name), "SHP-2")
		self.assertEqual(call.call_count, 3)
		self.assertEqual(len(self.sleeps), 2)
		self.assertLess(self.sleeps[0], self.sleeps[1])  # exponential backoff

	def test_provider_down_schedules_a_later_retry(self):
		order = self.ready_order()
		with http(*[requests.ConnectionError()] * 3):
			self.assertIsNone(self.book(order.name))

		state = self.logistics(order.name, "logistics_status", "logistics_next_retry", "logistics_error")
		self.assertEqual(state.logistics_status, "Failed")
		self.assertGreater(state.logistics_next_retry, now_datetime())
		self.assertIn("Could not connect", state.logistics_error)

	def test_permanent_error_is_not_retried(self):
		order = self.ready_order()
		with http(FakeResponse(422, {"error": "validation_failed"})) as call:
			self.assertIsNone(self.book(order.name))
		self.assertEqual(call.call_count, 1)
		state = self.logistics(order.name, "logistics_status", "logistics_next_retry")
		self.assertEqual(state.logistics_status, "Failed")
		self.assertIsNone(state.logistics_next_retry)  # waits for a person to fix it and press Retry

	def test_booked_order_is_never_booked_twice(self):
		order = self.ready_order()
		with http(FakeResponse(201, {"shipment_id": "SHP-3"})):
			self.book(order.name)
		with http() as call:  # any HTTP call would fail this test
			self.assertIsNone(self.book(order.name))
		self.assertEqual(call.call_count, 0)

	def test_hourly_job_requeues_due_failures_and_stale_claims(self):
		due = self.ready_order().name
		stale = self.ready_order().name
		frappe.db.set_value(
			"Reno Order",
			due,
			{
				"logistics_status": "Failed",
				"logistics_attempts": 1,
				"logistics_next_retry": now_datetime() - timedelta(minutes=1),
			},
		)
		frappe.db.set_value(
			"Reno Order",
			stale,
			{
				"logistics_status": "Booking",
				"logistics_attempts": 1,
				"logistics_updated_on": now_datetime() - timedelta(hours=1),
			},
		)
		booking.retry_failed_bookings()
		self.assertEqual(self.logistics(due), "Queued")
		self.assertEqual(self.logistics(stale), "Queued")  # interrupted claim recovered and re-queued

	def test_disabled_integration_does_nothing(self):
		frappe.db.set_single_value("Reno Logistics Settings", "enabled", 0)
		order = self.ready_order()
		self.assertFalse(self.logistics(order.name))
		with http() as call:
			self.assertIsNone(self.book(order.name))
		self.assertEqual(call.call_count, 0)


class TestWebhook(LogisticsTestCase):
	def booked_order(self, shipment_id="SHP-9"):
		order = self.ready_order()
		frappe.db.set_value(
			"Reno Order", order.name, {"logistics_status": "Booked", "logistics_reference": shipment_id}
		)
		return order

	def signed(self, body: dict, secret=WEBHOOK_SECRET, timestamp=None):
		raw = json.dumps(body).encode()
		timestamp = timestamp or str(int(time.time()))
		return raw, timestamp, webhook.sign(secret, raw, timestamp)

	def test_signed_status_update_is_applied(self):
		order = self.booked_order()
		result = webhook.process_status_update(*self.signed({"shipment_id": "SHP-9", "status": "delivered"}))
		self.assertEqual(result["reno_order"], order.name)
		self.assertEqual(self.logistics(order.name), "Delivered")

	def test_bad_signature_is_rejected(self):
		self.booked_order()
		with self.assertRaises(frappe.AuthenticationError):
			webhook.process_status_update(
				*self.signed({"shipment_id": "SHP-9", "status": "delivered"}, secret="wrong")
			)

	def test_replayed_old_message_is_rejected(self):
		self.booked_order()
		old = str(int(time.time()) - 3600)
		with self.assertRaises(frappe.AuthenticationError):
			webhook.process_status_update(
				*self.signed({"shipment_id": "SHP-9", "status": "delivered"}, timestamp=old)
			)

	def test_unknown_shipment_is_not_found(self):
		with self.assertRaises(frappe.DoesNotExistError):
			webhook.process_status_update(*self.signed({"shipment_id": "SHP-404", "status": "delivered"}))

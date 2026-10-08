"""Part 6: the Site Supervisor REST API: authentication, permissions, transitions and errors."""

import io

import frappe
from frappe.tests import IntegrationTestCase
from PIL import Image

from reno_order.api import supervisor as api
from reno_order.api.queries import get_customer_defaults
from reno_order.exceptions import InvalidInputError, InvalidStatusTransitionError
from reno_order.tests.utils import advance, make_customer, make_reno_order, make_user


def png_bytes() -> bytes:
	buffer = io.BytesIO()
	Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
	return buffer.getvalue()


class TestSupervisorAPI(IntegrationTestCase):
	def setUp(self):
		self.supervisor = make_user("reno.api.supervisor@example.com", "Site Supervisor")
		self.other_supervisor = make_user("reno.api.supervisor2@example.com", "Site Supervisor")
		self.order = advance(
			make_reno_order(assigned_supervisor=self.supervisor),
			"Confirm",
			"Start Production",
			"Mark Ready for Installation",
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- authentication & permissions

	def test_guest_cannot_call_the_api(self):
		frappe.set_user("Guest")
		for method in (api.update_installation_status, api.add_installation_remarks, api.upload_site_photo):
			with self.assertRaises(frappe.PermissionError):
				frappe.is_whitelisted(method)  # the gate Frappe applies to /api/method/... requests

	def test_supervisor_cannot_update_someone_elses_order(self):
		frappe.set_user(self.other_supervisor)
		with self.assertRaises(frappe.PermissionError):
			api.update_installation_status(self.order.name, "Installed")
		with self.assertRaises(frappe.PermissionError):
			api.add_installation_remarks(self.order.name, "Not my site")

	def test_unknown_order_returns_not_found(self):
		frappe.set_user(self.supervisor)
		with self.assertRaises(frappe.DoesNotExistError):
			api.update_installation_status("RO-99999", "Installed")

	# --- status transitions

	def test_supervisor_marks_order_installed(self):
		frappe.set_user(self.supervisor)
		result = api.update_installation_status(self.order.name, "Installed")

		self.assertEqual(result["status"], "Installed")
		self.assertTrue(result["installed_on"])
		self.assertEqual(result["delivery_status"], "Queued")  # downstream work queued, not done inline

	def test_repeating_the_same_status_is_harmless(self):
		frappe.set_user(self.supervisor)
		api.update_installation_status(self.order.name, "Installed")
		self.assertEqual(api.update_installation_status(self.order.name, "Installed")["status"], "Installed")

	def test_status_outside_the_supervisors_transitions_is_refused(self):
		frappe.set_user(self.supervisor)
		with self.assertRaises(InvalidStatusTransitionError):
			api.update_installation_status(self.order.name, "Closed")
		with self.assertRaises(InvalidStatusTransitionError):
			api.update_installation_status(self.order.name, "Nonsense")

	def test_sales_user_cannot_mark_installed(self):
		frappe.set_user("Administrator")
		self.order.db_set("owner", make_user("reno.api.sales@example.com", "Sales User"))
		frappe.set_user("reno.api.sales@example.com")
		with self.assertRaises(InvalidStatusTransitionError):
			api.update_installation_status(self.order.name, "Installed")

	# --- remarks

	def test_remarks_are_appended_with_user_and_time(self):
		frappe.set_user(self.supervisor)
		api.add_installation_remarks(self.order.name, "Cabinets fitted.")
		result = api.add_installation_remarks(self.order.name, "Countertop sealed.")

		lines = result["installation_remarks"].splitlines()
		self.assertEqual(len(lines), 2)
		self.assertIn(self.supervisor, lines[0])
		self.assertTrue(lines[1].endswith("Countertop sealed."))

	def test_empty_remarks_are_rejected(self):
		frappe.set_user(self.supervisor)
		with self.assertRaises(InvalidInputError):
			api.add_installation_remarks(self.order.name, "   ")

	def test_remarks_only_once_ready_for_installation(self):
		draft = make_reno_order(assigned_supervisor=self.supervisor)
		frappe.set_user(self.supervisor)
		with self.assertRaises(InvalidStatusTransitionError):
			api.add_installation_remarks(draft.name, "Too early")

	# --- photos

	def test_site_photo_is_attached_privately(self):
		frappe.set_user(self.supervisor)
		result = api.attach_site_photo(self.order.name, "kitchen.png", png_bytes())

		file = frappe.get_doc("File", {"file_url": result["file_url"]})
		self.assertEqual((file.attached_to_doctype, file.attached_to_name), ("Reno Order", self.order.name))
		self.assertTrue(file.is_private)

	def test_non_image_upload_is_rejected(self):
		frappe.set_user(self.supervisor)
		with self.assertRaises(InvalidInputError):
			api.attach_site_photo(self.order.name, "photo.jpg", b"MZ\x90\x00 definitely not an image")
		with self.assertRaises(InvalidInputError):
			api.attach_site_photo(self.order.name, "notes.pdf", png_bytes())


class TestFormServerCalls(IntegrationTestCase):
	"""The Reno Order form's calls to the server (Part 12)."""

	def test_every_method_the_form_calls_is_whitelisted(self):
		"""A browser can only call whitelisted methods. Frappe v16 doesn't whitelist some helpers older
		code used (e.g. get_default_contact), and that only shows up in the browser, not in Python tests."""
		import re
		from pathlib import Path

		app = Path(frappe.get_app_path("reno_order"))
		sources = [path for path in app.rglob("*.js") if "node_modules" not in path.parts]
		methods = {
			method
			for path in sources
			for method in re.findall(r'xcall\(\s*"([\w.]+)"|method:\s*"([\w.]+)"', path.read_text())
			for method in method
			if method
		}

		self.assertIn("reno_order.api.queries.get_customer_defaults", methods)
		for method in methods:
			with self.subTest(method=method):
				self.assertIn(frappe.get_attr(method), frappe.whitelisted)

	def test_customer_defaults_returns_primary_address_and_contact(self):
		customer = make_customer()
		link = [{"link_doctype": "Customer", "link_name": customer}]
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": customer,
				"address_type": "Billing",
				"address_line1": "12 Liberation Road",
				"city": "Accra",
				"country": frappe.db.get_value("Country", {}, "name"),
				"is_primary_address": 1,
				"links": link,
			}
		).insert()
		contact = frappe.get_doc(
			{"doctype": "Contact", "first_name": "Ama", "is_primary_contact": 1, "links": link}
		).insert()

		self.assertEqual(
			get_customer_defaults(customer),
			{"customer_address": address.name, "contact_person": contact.name},
		)

	def test_customer_defaults_need_read_access_to_the_customer(self):
		customer = make_customer()
		frappe.set_user(make_user("_test_reno_no_roles@example.com"))
		try:
			with self.assertRaises(frappe.PermissionError):
				get_customer_defaults(customer)
		finally:
			frappe.set_user("Administrator")

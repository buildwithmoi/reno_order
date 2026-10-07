"""Part 11: who can see and change which Reno Orders. All rules are checked on the server."""

import frappe
from frappe.desk.form.assign_to import add as assign_to
from frappe.tests import IntegrationTestCase

from reno_order.permissions import clear_team_cache
from reno_order.tests.utils import (
	advance,
	make_employee,
	make_reno_order,
	make_sales_person,
	make_user,
)


def visible_orders(user: str) -> set[str]:
	frappe.set_user(user)
	names = set(frappe.get_list("Reno Order", pluck="name"))
	frappe.set_user("Administrator")
	return names


def can_read(user: str, name: str) -> bool:
	return frappe.has_permission("Reno Order", "read", doc=name, user=user)


class TestSalesVisibility(IntegrationTestCase):
	def setUp(self):
		clear_team_cache()
		self.alice = make_user("reno.perm.alice@example.com", "Sales User")
		self.bob = make_user("reno.perm.bob@example.com", "Sales User")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_sales_user_sees_own_orders_only(self):
		frappe.set_user(self.alice)
		alices = make_reno_order().name
		frappe.set_user(self.bob)
		bobs = make_reno_order().name
		frappe.set_user("Administrator")

		self.assertIn(alices, visible_orders(self.alice))
		self.assertNotIn(bobs, visible_orders(self.alice))
		self.assertFalse(can_read(self.alice, bobs))

	def test_sales_user_sees_orders_assigned_to_them(self):
		frappe.set_user(self.bob)
		bobs = make_reno_order().name
		frappe.set_user("Administrator")
		assign_to({"doctype": "Reno Order", "name": bobs, "assign_to": [self.alice]})

		self.assertIn(bobs, visible_orders(self.alice))
		self.assertTrue(can_read(self.alice, bobs))

	def test_sales_manager_sees_their_teams_orders(self):
		manager = make_user("reno.perm.manager@example.com", "Sales Manager")
		lead = make_sales_person("_Reno Team Lead", is_group=1, employee=make_employee(manager))
		member = make_sales_person("_Reno Team Member", parent=lead)
		outsider = make_sales_person("_Reno Other Rep")

		frappe.set_user(self.bob)
		team_order = make_reno_order(sales_person=member).name
		other_order = make_reno_order(sales_person=outsider).name
		frappe.set_user("Administrator")

		visible = visible_orders(manager)
		self.assertIn(team_order, visible)
		self.assertNotIn(other_order, visible)
		self.assertTrue(can_read(manager, team_order))
		self.assertFalse(can_read(manager, other_order))


class TestOperationalRoles(IntegrationTestCase):
	def setUp(self):
		self.supervisor = make_user("reno.perm.supervisor@example.com", "Site Supervisor")
		self.other_supervisor = make_user("reno.perm.supervisor2@example.com", "Site Supervisor")

	def tearDown(self):
		frappe.set_user("Administrator")

	def ready_order(self, **fields):
		return advance(
			make_reno_order(assigned_supervisor=self.supervisor, **fields),
			"Confirm",
			"Start Production",
			"Mark Ready for Installation",
		)

	def test_site_supervisor_sees_only_assigned_orders(self):
		mine = self.ready_order().name
		theirs = advance(make_reno_order(assigned_supervisor=self.other_supervisor), "Confirm").name

		visible = visible_orders(self.supervisor)
		self.assertIn(mine, visible)
		self.assertNotIn(theirs, visible)

	def test_site_supervisor_can_update_installation_details(self):
		name = self.ready_order().name
		frappe.set_user(self.supervisor)
		order = frappe.get_doc("Reno Order", name)
		order.installation_remarks = "Wall cabinets fitted; countertop sealed."
		order.save()
		self.assertEqual(
			frappe.db.get_value("Reno Order", name, "installation_remarks"),
			"Wall cabinets fitted; countertop sealed.",
		)

	def test_site_supervisor_cannot_change_commercial_fields(self):
		name = self.ready_order(discount=5).name
		frappe.set_user(self.supervisor)

		for field, value in (("discount_percentage", 50), ("customer", None)):
			order = frappe.get_doc("Reno Order", name)
			order.set(field, value or order.customer + "x")
			with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
				order.save()

		order = frappe.get_doc("Reno Order", name)
		order.items[0].rate = 1
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			order.save()

		# A field that *is* editable after submit, but not by a supervisor: our own guard catches it.
		order = frappe.get_doc("Reno Order", name)
		order.assigned_supervisor = self.other_supervisor
		with self.assertRaises(frappe.PermissionError):
			order.save()

		self.assertEqual(frappe.db.get_value("Reno Order", name, "discount_percentage"), 5)

	def test_site_supervisor_cannot_create_orders(self):
		frappe.set_user(self.supervisor)
		with self.assertRaises(frappe.PermissionError):
			make_reno_order()

	def test_production_user_sees_confirmed_orders_only(self):
		production = make_user("reno.perm.production@example.com", "Production User")
		draft = make_reno_order().name
		confirmed = advance(make_reno_order(), "Confirm").name

		visible = visible_orders(production)
		self.assertIn(confirmed, visible)
		self.assertNotIn(draft, visible)

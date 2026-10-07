from contextlib import contextmanager

import frappe


@contextmanager
def run_as(user: str):
	"""Act as another user for a block of code (e.g. a service account for automation), then switch
	back. Permission checks still apply, to *that* user."""
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user(previous)

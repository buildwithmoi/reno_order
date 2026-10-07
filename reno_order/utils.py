from contextlib import contextmanager

import frappe


@contextmanager
def run_as(user: str):
	"""Act as another user for a block of code (e.g. a service account for automation), then switch
	back. Permission checks still apply, to *that* user."""
	previous = frappe.session.user
	# Only called from server code with a configured service account, never with request input.
	frappe.set_user(user)  # nosemgrep
	try:
		yield
	finally:
		frappe.set_user(previous)  # nosemgrep

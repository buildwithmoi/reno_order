"""The Reno Order lifecycle, defined in code and synced on install / migrate.

	Draft → Confirmed → In Production → Ready for Installation → Installed → Closed
	                ↘ Cancelled (from Confirmed, In Production or Ready for Installation)

Frappe enforces the transitions on the server (frappe.model.workflow.validate_workflow): a new
document must start in the first state, and every state change must be an allowed transition for
one of the user's roles. That applies to the form, the REST API and scripts alike.
"""

import frappe

WORKFLOW_NAME = "Reno Order Workflow"

# state: (docstatus, role allowed to edit in this state, indicator style)
STATES = {
	"Draft": (0, "Sales User", ""),
	"Confirmed": (1, "Sales Manager", "Primary"),
	"In Production": (1, "Production User", "Warning"),
	"Ready for Installation": (1, "Site Supervisor", "Info"),
	"Installed": (1, "Sales Manager", "Success"),
	"Closed": (1, "Sales Manager", "Inverse"),
	"Cancelled": (2, "Sales Manager", "Danger"),
}

# (from state, action, to state, roles allowed)
TRANSITIONS = [
	("Draft", "Confirm", "Confirmed", ["Sales User", "Sales Manager"]),
	("Confirmed", "Start Production", "In Production", ["Production User"]),
	("In Production", "Mark Ready for Installation", "Ready for Installation", ["Production User"]),
	("Ready for Installation", "Mark Installed", "Installed", ["Site Supervisor"]),
	("Installed", "Close", "Closed", ["Sales Manager"]),
	# Once installed, goods have reached the customer: reversals go through returns, not cancellation.
	("Confirmed", "Cancel", "Cancelled", ["Sales Manager"]),
	("In Production", "Cancel", "Cancelled", ["Sales Manager"]),
	("Ready for Installation", "Cancel", "Cancelled", ["Sales Manager"]),
]


def sync_workflow():
	"""Create or update the workflow so it always matches this file (idempotent)."""
	for state, (_docstatus, _role, style) in STATES.items():
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert(ignore_permissions=True)
	for action in {t[1] for t in TRANSITIONS}:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)

	workflow = (
		frappe.get_doc("Workflow", WORKFLOW_NAME)
		if frappe.db.exists("Workflow", WORKFLOW_NAME)
		else frappe.new_doc("Workflow")
	)
	workflow.update(
		{
			"workflow_name": WORKFLOW_NAME,
			"document_type": "Reno Order",
			"workflow_state_field": "status",
			"is_active": 1,
			"send_email_alert": 0,
		}
	)
	workflow.set(
		"states",
		[
			{"state": state, "doc_status": str(docstatus), "allow_edit": role}
			for state, (docstatus, role, _style) in STATES.items()
		],
	)
	workflow.set(
		"transitions",
		[
			{"state": src, "action": action, "next_state": dst, "allowed": role, "allow_self_approval": 1}
			for src, action, dst, roles in TRANSITIONS
			for role in roles
		],
	)
	workflow.flags.ignore_permissions = True
	workflow.save()

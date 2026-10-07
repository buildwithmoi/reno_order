"""Row-level visibility for Reno Order, enforced on the server (Part 11).

Two Frappe hooks share the same rules:
- `permission_query_conditions` adds a WHERE clause to list views, reports, link searches and
  /api/resource queries, so users only *see* the orders they're entitled to.
- `has_permission` checks a single document (form, API get/update), so guessing an order name
  doesn't help.

Who sees what:
- Sales User: orders they created, or that are assigned to them (the standard "Assign To").
- Sales Manager: the above, plus orders whose Sales Person is in their team. The team is their
  own Sales Person node and every node under it in the Sales Person tree, linked through
  Sales Person → Employee → User.
- Site Supervisor: orders where they are the Assigned Site Supervisor.
- Production User: confirmed (submitted) orders, which they need to plan production.
- System Manager / Accounts User: every order (administration, invoicing).
A user with several roles sees the union of their rules.
"""

import json

import frappe

UNRESTRICTED_ROLES = {"System Manager", "Accounts User"}
SALES_ROLES = {"Sales User", "Sales Manager"}


def get_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	user = user or frappe.session.user
	if _is_unrestricted(user):
		return ""

	roles = set(frappe.get_roles(user))
	escaped_user = frappe.db.escape(user)
	conditions = []

	if roles & SALES_ROLES:
		conditions.append(f"`tabReno Order`.`owner` = {escaped_user}")
		conditions.append(f"`tabReno Order`.`_assign` like {frappe.db.escape('%' + json.dumps(user) + '%')}")
	if "Sales Manager" in roles and (team := get_team_sales_persons(user)):
		conditions.append(
			"`tabReno Order`.`sales_person` in ({})".format(", ".join(frappe.db.escape(sp) for sp in team))
		)
	if "Site Supervisor" in roles:
		conditions.append(f"`tabReno Order`.`assigned_supervisor` = {escaped_user}")
	if "Production User" in roles:
		conditions.append("`tabReno Order`.`docstatus` = 1")

	return "({})".format(" or ".join(conditions)) if conditions else "1 = 0"


def has_permission(doc, ptype: str | None = None, user: str | None = None, debug: bool = False) -> bool:
	"""Return False to deny. In Frappe v16, returning None also counts as a denial, so always return a bool."""
	user = user or frappe.session.user
	if _is_unrestricted(user) or doc.is_new():
		return True  # role permissions (DocPerm) still decide whether the user may create at all

	roles = set(frappe.get_roles(user))
	if roles & SALES_ROLES and (doc.owner == user or user in _assigned_users(doc)):
		return True
	if "Sales Manager" in roles and doc.sales_person and doc.sales_person in get_team_sales_persons(user):
		return True
	if "Site Supervisor" in roles and doc.assigned_supervisor == user:
		return True
	if "Production User" in roles and doc.docstatus == 1:
		return True
	return False


def get_team_sales_persons(user: str) -> list[str]:
	"""Sales Persons in the user's team: their own node(s) in the Sales Person tree and all below."""
	cache = getattr(frappe.local, "reno_team_cache", None)
	if cache is None:
		cache = frappe.local.reno_team_cache = {}  # per request: frappe.local is reset between requests
	if user in cache:
		return cache[user]

	team: list[str] = []
	if employee := frappe.db.get_value("Employee", {"user_id": user}, "name"):
		for node in frappe.get_all("Sales Person", filters={"employee": employee}, fields=["lft", "rgt"]):
			team += frappe.get_all(
				"Sales Person", filters={"lft": (">=", node.lft), "rgt": ("<=", node.rgt)}, pluck="name"
			)
	cache[user] = sorted(set(team))
	return cache[user]


def clear_team_cache():
	frappe.local.reno_team_cache = {}


# Fields each operational role may change on an order. Production and installation staff need
# "write" permission because Frappe checks it on every save (including workflow actions on a
# submitted order), so this list is what really limits them, on the server.
OPERATIONAL_EDITABLE_FIELDS = {
	"Production User": {"status"},
	"Site Supervisor": {"status", "installation_remarks"},
}


def get_restricted_editable_fields(user: str | None = None) -> set[str] | None:
	"""Fields an operational-only user may change, or None if the user isn't restricted this way.

	Users holding any sales or admin role aren't restricted here; their limits come from role
	permissions, permission levels and the workflow."""
	roles = set(frappe.get_roles(user or frappe.session.user))
	if roles & (SALES_ROLES | UNRESTRICTED_ROLES):
		return None
	operational = roles & OPERATIONAL_EDITABLE_FIELDS.keys()
	if not operational:
		return None
	return set().union(*(OPERATIONAL_EDITABLE_FIELDS[role] for role in operational))


def _is_unrestricted(user: str) -> bool:
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & UNRESTRICTED_ROLES)


def _assigned_users(doc) -> list[str]:
	return json.loads(doc.get("_assign") or "[]")

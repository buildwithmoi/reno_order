"""REST API for the Site Supervisor mobile app (Part 6). Documentation and curl examples: docs/api.md.

Every endpoint:
- needs an authenticated user (none are `allow_guest`). The app sends
  `Authorization: token <api_key>:<api_secret>` for the supervisor's *own* Frappe user.
- runs every check as that user: document permissions (only orders assigned to them), the workflow
  (only the transitions their role allows) and the field guard (installation fields only). There
  is no all-powerful API account.
- accepts POST only and returns a short JSON summary of the order.

Errors use meaningful HTTP status codes: 401 not logged in, 403 not permitted, 404 unknown order,
409 not allowed from the order's current status, 422 invalid input.
"""

import io

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import cstr, format_datetime, now_datetime

from reno_order.exceptions import InvalidInputError, InvalidStatusTransitionError

INSTALLATION_STATUSES = ("Ready for Installation", "Installed")
MAX_REMARKS_LENGTH = 2000
PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_PHOTO_BYTES = 10 * 1024 * 1024


@frappe.whitelist(methods=["POST"])
def update_installation_status(reno_order: str, status: str) -> dict:
	"""{"reno_order": "RO-00001", "status": "Installed"}"""
	doc = _get_order(reno_order, "write")
	status = cstr(status).strip()

	if doc.status == status:
		return _summary(doc)  # idempotent: a retry from a flaky mobile connection is harmless

	transitions = get_transitions(doc)  # only those this user's roles allow from the current state
	transition = next((t for t in transitions if t.next_state == status), None)
	if not transition:
		allowed = sorted({t.next_state for t in transitions})
		frappe.throw(
			_("Reno Order {0} is {1} and can't be changed to {2}. Allowed for you: {3}.").format(
				doc.name, doc.status, status or "''", ", ".join(allowed) or _("none")
			),
			InvalidStatusTransitionError,
			title=_("Status Change Not Allowed"),
		)

	return _summary(apply_workflow(doc, transition.action))


@frappe.whitelist(methods=["POST"])
def add_installation_remarks(reno_order: str, remarks: str) -> dict:
	"""{"reno_order": "RO-00001", "remarks": "Installation completed successfully."}

	Remarks are appended with a timestamp and the user, so earlier site notes are never lost."""
	remarks = cstr(remarks).strip()
	if not remarks:
		frappe.throw(_("Remarks cannot be empty."), InvalidInputError)
	if len(remarks) > MAX_REMARKS_LENGTH:
		frappe.throw(
			_("Remarks are limited to {0} characters.").format(MAX_REMARKS_LENGTH), InvalidInputError
		)

	doc = _get_order(reno_order, "write")
	_require_installation_stage(doc)

	entry = f"[{format_datetime(now_datetime(), 'yyyy-MM-dd HH:mm')} · {frappe.session.user}] {remarks}"
	doc.installation_remarks = "\n".join(part for part in (doc.installation_remarks, entry) if part)
	doc.save()
	return _summary(doc)


@frappe.whitelist(methods=["POST"])
def upload_site_photo(reno_order: str) -> dict:
	"""multipart/form-data with fields `reno_order` and `file` (a JPG, PNG or WebP up to 10 MB)."""
	upload = frappe.request.files.get("file") if getattr(frappe, "request", None) else None
	if not upload:
		frappe.throw(_("Send the photo as the multipart form field 'file'."), InvalidInputError)
	return attach_site_photo(reno_order, upload.filename, upload.stream.read())


def attach_site_photo(reno_order: str, filename: str, content: bytes) -> dict:
	"""Validate an image and attach it to the order as a *private* file: only users who can read the
	order can open it."""
	doc = _get_order(reno_order, "write")
	_require_installation_stage(doc)
	extension = _validate_photo(filename, content)

	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{doc.name}-site-{now_datetime():%Y%m%d-%H%M%S}.{extension}",
			"attached_to_doctype": "Reno Order",
			"attached_to_name": doc.name,
			"is_private": 1,
			"content": content,
		}
	)
	file.insert()  # normal permission checks; File access then follows the Reno Order's permissions
	return {"reno_order": doc.name, "file_name": file.file_name, "file_url": file.file_url}


# ---------------------------------------------------------------------- helpers


def _get_order(reno_order: str, ptype: str):
	if not reno_order or not frappe.db.exists("Reno Order", reno_order):
		raise frappe.DoesNotExistError(_("Reno Order {0} was not found.").format(cstr(reno_order)))
	doc = frappe.get_doc("Reno Order", reno_order)
	doc.check_permission(ptype)  # row-level rules: a supervisor only reaches their assigned orders
	return doc


def _require_installation_stage(doc):
	if doc.status not in INSTALLATION_STATUSES:
		frappe.throw(
			_(
				"Installation updates are only possible once the order is Ready for Installation (it is {0})."
			).format(doc.status),
			InvalidStatusTransitionError,
		)


def _validate_photo(filename: str, content: bytes) -> str:
	extension = cstr(filename).rsplit(".", 1)[-1].lower() if "." in cstr(filename) else ""
	if extension not in PHOTO_EXTENSIONS:
		frappe.throw(
			_("Only {0} photos are accepted.").format(", ".join(sorted(PHOTO_EXTENSIONS))), InvalidInputError
		)
	if not content:
		frappe.throw(_("The photo is empty."), InvalidInputError)
	if len(content) > MAX_PHOTO_BYTES:
		frappe.throw(
			_("Photos are limited to {0} MB.").format(MAX_PHOTO_BYTES // (1024 * 1024)), InvalidInputError
		)

	# Check the bytes really are an image: a renamed executable must not get through.
	from PIL import Image

	try:
		Image.open(io.BytesIO(content)).verify()
	except Exception:
		frappe.throw(_("The file is not a valid image."), InvalidInputError)
	return "jpg" if extension == "jpeg" else extension


def _summary(doc) -> dict:
	values = frappe.db.get_value(
		"Reno Order",
		doc.name,
		["status", "installed_on", "installation_remarks", "delivery_status"],
		as_dict=True,
	)
	return {"reno_order": doc.name, **values}

"""Keep each Reno Order's links to its Sales Order, Delivery Note and Sales Invoice up to date.

ERPNext's own mappers copy the `reno_order` field forward (Sales Order → Delivery Note → Sales
Invoice), so every downstream document knows its Reno Order. These doc_events write the reverse link
onto the Reno Order, and clear it again when that document is cancelled or deleted.
"""

import frappe

LINK_FIELD = {
	"Sales Order": "sales_order",
	"Delivery Note": "delivery_note",
	"Sales Invoice": "sales_invoice",
}


def link_reno_order(doc, method=None):
	"""after_insert: point the Reno Order at this document (covers amended documents too)."""
	if _tracks(doc):
		frappe.db.set_value(
			"Reno Order", doc.reno_order, LINK_FIELD[doc.doctype], doc.name, update_modified=False
		)


def unlink_reno_order(doc, method=None):
	"""on_cancel / on_trash: clear the link if it points at this document."""
	if not _tracks(doc):
		return
	fieldname = LINK_FIELD[doc.doctype]
	if frappe.db.get_value("Reno Order", doc.reno_order, fieldname) == doc.name:
		frappe.db.set_value("Reno Order", doc.reno_order, fieldname, None, update_modified=False)


def _tracks(doc) -> bool:
	# Returns and credit notes copy reno_order from the original; they must not replace its link.
	return bool(doc.get("reno_order")) and not doc.get("is_return")

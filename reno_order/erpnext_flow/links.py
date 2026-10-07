"""Keep the `reno_order` reference intact across ERPNext documents.

ERPNext's own mappers copy the `reno_order` field forward (Sales Order → Delivery Note → Sales
Invoice, Sales Order → Material Request → Purchase Order), so every downstream document knows its
Reno Order. These doc_events:
- write the reverse link onto the Reno Order, and clear it again when that document is cancelled
  or deleted (Sales Order, Delivery Note, Sales Invoice);
- fill the reference on documents ERPNext builds *without* the mapper (Work Order, Purchase Order
  from a Supplier Quotation) from the Sales Order or Material Request their rows point at.
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


def inherit_reno_order(doc, method=None):
	"""validate (Work Order, Material Request, Purchase Order): take `reno_order` from the source documents.

	"Sales Order → Create → Work Order" (`make_work_orders`) doesn't use the mapper, and the
	RFQ → Supplier Quotation steps have no `reno_order`, so those documents arrive blank. Their header
	or rows still reference the Sales Order or Material Request, which carry it. A document serving
	several Reno Orders (one Purchase Order for hinges for three kitchens) is left blank: each row
	still traces back through its own Material Request.
	"""
	if doc.get("reno_order"):
		return
	rows = doc.get("items") or []
	sources = {
		"Sales Order": {doc.get("sales_order")} | {row.get("sales_order") for row in rows},
		"Material Request": {row.get("material_request") for row in rows},
	}
	found = set()
	for doctype, names in sources.items():
		if names := [name for name in names if name]:
			found.update(frappe.get_all(doctype, {"name": ("in", names)}, pluck="reno_order"))
	found.discard(None)
	found.discard("")
	if len(found) == 1:
		doc.reno_order = found.pop()

# Copyright (c) 2026, Patrick Anteh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime

from reno_order.erpnext_flow import sales_order as sales_order_flow
from reno_order.erpnext_flow.delivery import queue_delivery_note
from reno_order.exceptions import DiscountApprovalRequiredError
from reno_order.integrations.logistics.booking import queue_shipment_booking
from reno_order.permissions import get_restricted_editable_fields

# Set only by the app's own code (db_set / scheduler), never through a document save.
SYSTEM_FIELDS = (
	"is_overdue",
	"installed_on",
	"sales_order",
	"delivery_note",
	"sales_invoice",
	"delivery_status",
	"delivery_attempts",
	"delivery_error",
	"logistics_status",
	"logistics_reference",
	"logistics_updated_on",
	"logistics_attempts",
	"logistics_next_retry",
	"logistics_error",
)


class RenoOrder(Document):
	def before_insert(self):
		# A client can't create an order that already looks overdue, installed or linked to an SO.
		for fieldname in SYSTEM_FIELDS:
			self.set(fieldname, None)

	def validate(self):
		self.validate_restricted_changes()
		self.validate_customer_links()
		self.validate_dates()
		self.validate_items()
		self.validate_discount_range()
		# Always recalculated on the server: whatever amounts the client or API sends are overwritten,
		# so totals can't be manipulated.
		self.calculate_totals()

	def before_submit(self):
		self.validate_discount_authority()

	def on_submit(self):
		# The workflow normally sets the status before submit; this covers submits without it.
		if self.status == "Draft":
			self.db_set("status", "Confirmed")

	def before_update_after_submit(self):
		# validate() doesn't run for updates to a submitted order, so repeat the guard here.
		self.validate_restricted_changes()
		if self.has_value_changed("status") and self.status == "Installed":
			self.installed_on = now_datetime()

	def on_update_after_submit(self):
		# Slow or follow-up work is queued to run after this save commits, so the user never waits.
		if not self.has_value_changed("status"):
			return
		if self.status == "Ready for Installation":
			queue_shipment_booking(self.name)  # external logistics API, 10-20 s (Parts 7 + 8)
		elif self.status == "Installed":
			queue_delivery_note(self.name)

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	# ------------------------------------------------------------------ server-side guards

	def validate_restricted_changes(self):
		editable = get_restricted_editable_fields()  # None = user isn't limited to specific fields
		before = self.get_doc_before_save()
		if not before:
			if editable is not None:
				frappe.throw(
					_("Production and installation staff cannot create Reno Orders."), frappe.PermissionError
				)
			return

		# System fields always keep their stored values. This also stops a stale form (opened before
		# the scheduler flagged the order, say) from silently undoing a background update.
		for fieldname in SYSTEM_FIELDS:
			self.set(fieldname, before.get(fieldname))

		# Production / installation staff may only change their own fields. Status changes are also
		# limited by the workflow to the transitions their role is allowed.
		if editable is not None and (changed := self.get_changed_fields(before, exclude=editable)):
			frappe.throw(
				_("Your role can only update {0}. Not allowed to change: {1}").format(
					", ".join(sorted(self.meta.get_label(f) for f in editable)), ", ".join(changed)
				),
				frappe.PermissionError,
				title=_("Not Permitted"),
			)

	def get_changed_fields(self, before, exclude: set[str]) -> list[str]:
		"""Labels of fields (and child rows) that differ from the version before this save."""
		changed = []
		for df in self.meta.get("fields"):
			if df.fieldname in exclude or df.fieldtype in frappe.model.no_value_fields:
				continue
			if df.fieldtype in frappe.model.table_fields:
				if _rows(before.get(df.fieldname)) != _rows(self.get(df.fieldname)):
					changed.append(df.label)
			elif before.get(df.fieldname) != self.get(df.fieldname):
				changed.append(df.label)
		return changed

	# ------------------------------------------------------------------ validations

	def validate_customer_links(self):
		"""The address and contact must belong to the customer (the form filters them; the API can't be
		trusted to). The formatted address is filled in here, so API clients get it too."""
		for fieldname, doctype in (("customer_address", "Address"), ("contact_person", "Contact")):
			name = self.get(fieldname)
			if name and not frappe.db.exists(
				"Dynamic Link",
				{
					"parenttype": doctype,
					"parent": name,
					"link_doctype": "Customer",
					"link_name": self.customer,
				},
			):
				frappe.throw(
					_("{0} {1} does not belong to customer {2}.").format(
						_(doctype), frappe.bold(name), frappe.bold(self.customer)
					),
					title=_("Wrong {0}").format(_(doctype)),
				)
		self.address_display = get_address_display(self.customer_address) if self.customer_address else None

	def validate_dates(self):
		if (
			self.transaction_date
			and self.expected_installation_date
			and getdate(self.expected_installation_date) < getdate(self.transaction_date)
		):
			frappe.throw(
				_("Expected Installation Date ({0}) cannot be before the Transaction Date ({1}).").format(
					frappe.format(self.expected_installation_date, "Date"),
					frappe.format(self.transaction_date, "Date"),
				),
				title=_("Invalid Installation Date"),
			)

	def validate_items(self):
		if not self.items:
			frappe.throw(_("Add at least one item to the Reno Order."), title=_("No Items"))

		for row in self.items:
			if flt(row.qty) <= 0:
				frappe.throw(
					_("Row {0}: Quantity for {1} must be greater than zero.").format(
						row.idx, frappe.bold(row.item_code)
					),
					title=_("Invalid Quantity"),
				)
			if flt(row.rate) < 0:
				frappe.throw(
					_("Row {0}: Rate for {1} cannot be negative.").format(
						row.idx, frappe.bold(row.item_code)
					),
					title=_("Invalid Rate"),
				)

	def validate_discount_range(self):
		if not 0 <= flt(self.discount_percentage) <= 100:
			frappe.throw(_("Discount % must be between 0 and 100."), title=_("Invalid Discount"))

	def validate_discount_authority(self):
		threshold, approver_role = get_discount_rules()
		if (
			threshold
			and flt(self.discount_percentage) > threshold
			and approver_role not in frappe.get_roles()
		):
			frappe.throw(
				_(
					"A discount of {0}% is above the approval threshold of {1}%. "
					"Only a user with the {2} role can confirm this order."
				).format(flt(self.discount_percentage), threshold, frappe.bold(approver_role)),
				exc=DiscountApprovalRequiredError,
				title=_("Discount Needs Approval"),
			)

	# ------------------------------------------------------------------ calculations

	def calculate_totals(self):
		total = 0.0
		for row in self.items:
			row.amount = flt(flt(row.qty) * flt(row.rate), row.precision("amount"))
			total += row.amount

		self.total_amount = flt(total, self.precision("total_amount"))
		self.discount_amount = flt(
			self.total_amount * flt(self.discount_percentage) / 100, self.precision("discount_amount")
		)
		self.grand_total = flt(self.total_amount - self.discount_amount, self.precision("grand_total"))


# Covering index for the "Monthly Reno Order Value" report (Part 10): equality on company, range on
# transaction_date, then status and grand_total so the query never has to read the table rows.
REPORT_INDEX = ("company", "transaction_date", "status", "grand_total")
REPORT_INDEX_NAME = "company_date_status_total_index"


def on_doctype_update():
	"""Called by Frappe whenever the DocType is synced (bench migrate). ADD INDEX IF NOT EXISTS: safe
	to repeat; InnoDB builds a secondary index online, so reads and writes continue meanwhile."""
	frappe.db.add_index("Reno Order", list(REPORT_INDEX), index_name=REPORT_INDEX_NAME)


def _rows(rows) -> list[dict]:
	"""Comparable view of a child table: the values that matter, ignoring row metadata."""
	keys = ("item_code", "description", "qty", "uom", "rate", "amount", "warehouse")
	return [{k: row.get(k) for k in keys} for row in rows or []]


def get_discount_rules() -> tuple[float, str]:
	"""(threshold %, approver role) from Reno Settings. A threshold of 0 means no limit."""
	settings = frappe.get_cached_doc("Reno Settings")
	return flt(settings.discount_approval_threshold), settings.discount_approver_role or "Sales Manager"


@frappe.whitelist(methods=["POST"])
def create_sales_order(reno_order: str) -> str:
	"""Create a draft Sales Order from a confirmed Reno Order and return its name."""
	doc = frappe.get_doc("Reno Order", reno_order)
	doc.check_permission("read")
	return sales_order_flow.create_sales_order(doc).name

# Copyright (c) 2026, Patrick Anteh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from reno_order.erpnext_flow import sales_order as sales_order_flow
from reno_order.exceptions import DiscountApprovalRequiredError


class RenoOrder(Document):
	def validate(self):
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

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	# ------------------------------------------------------------------ validations

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

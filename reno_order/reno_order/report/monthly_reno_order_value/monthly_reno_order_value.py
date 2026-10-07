# Copyright (c) 2026, Patrick Anteh and contributors
# For license information, please see license.txt

"""Monthly Reno Order value, by status, for the last 12 months (Part 10).

One aggregate query. The composite index (company, transaction_date, status, grand_total), created in
reno_order.on_doctype_update, covers it: MariaDB range-scans the index and never reads the table rows.
EXPLAIN before and after, with timings, is in docs/performance.md.

The report respects the same row-level permissions as the list view (reno_order.permissions), so a
Sales Manager sees totals for their team's orders only.
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, get_first_day, getdate, nowdate

from reno_order.permissions import get_query_conditions

STATUSES = (
	"Draft",
	"Confirmed",
	"In Production",
	"Ready for Installation",
	"Installed",
	"Closed",
	"Cancelled",
)

MONTHLY_QUERY = """
	SELECT DATE_FORMAT(transaction_date, '%%Y-%%m') AS month,
		status,
		COUNT(*) AS orders,
		SUM(grand_total) AS value
	FROM `tabReno Order`
	WHERE company = %(company)s
		AND transaction_date BETWEEN %(from_date)s AND %(to_date)s
		{permission_condition}
	GROUP BY month, status
	ORDER BY month
"""


def execute(filters=None):
	filters = frappe._dict(filters or {})
	filters.company = filters.company or frappe.defaults.get_user_default("Company")
	filters.to_date = getdate(filters.to_date or nowdate())
	filters.from_date = getdate(filters.from_date or get_first_day(add_months(filters.to_date, -11)))
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be before To Date."))

	months = month_range(filters.from_date, filters.to_date)
	totals = get_monthly_totals(filters)

	data = []
	for month in months:
		row = {"month": month, "orders": 0, "total": 0.0}
		for status in STATUSES:
			value = totals.get((month, status), {})
			row[frappe.scrub(status)] = flt(value.get("value"))
			row["orders"] += value.get("orders", 0)
			row["total"] += flt(value.get("value"))
		data.append(row)

	return get_columns(), data, None, get_chart(months, data)


def get_monthly_totals(filters) -> dict:
	condition = get_query_conditions(frappe.session.user)
	# Only the server-generated permission condition is formatted in; user filters are bound as parameters.
	permission_condition = f"AND {condition}" if condition else ""
	query = MONTHLY_QUERY.format(permission_condition=permission_condition)  # nosemgrep
	return {(r.month, r.status): r for r in frappe.db.sql(query, filters, as_dict=True)}


def month_range(from_date, to_date) -> list[str]:
	months, current = [], get_first_day(from_date)
	while current <= to_date:
		months.append(current.strftime("%Y-%m"))
		current = add_months(current, 1)
	return months


def get_columns():
	columns = [{"fieldname": "month", "label": _("Month"), "fieldtype": "Data", "width": 100}]
	columns += [
		{"fieldname": frappe.scrub(status), "label": _(status), "fieldtype": "Currency", "width": 130}
		for status in STATUSES
	]
	columns += [
		{"fieldname": "total", "label": _("Total Value"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "orders", "label": _("Orders"), "fieldtype": "Int", "width": 80},
	]
	return columns


def get_chart(months, data):
	return {
		"data": {
			"labels": months,
			"datasets": [
				{"name": _(status), "values": [row[frappe.scrub(status)] for row in data]}
				for status in STATUSES
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"fieldtype": "Currency",
	}

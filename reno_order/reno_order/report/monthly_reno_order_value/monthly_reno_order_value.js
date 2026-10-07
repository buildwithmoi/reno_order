// Copyright (c) 2026, Patrick Anteh and contributors
// For license information, please see license.txt

frappe.query_reports["Monthly Reno Order Value"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			// first day of the month, 11 months back: the last 12 months including this one
			default: frappe.datetime.add_months(frappe.datetime.month_start(), -11),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
	],
};

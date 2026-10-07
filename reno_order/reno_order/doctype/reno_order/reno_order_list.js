// List view: one colour per status, and overdue installations stand out in red.
frappe.listview_settings["Reno Order"] = {
	add_fields: ["status", "is_overdue", "delivery_status"],
	has_indicator_for_draft: 1,
	get_indicator(doc) {
		if (doc.is_overdue) return [__("Overdue"), "red", "is_overdue,=,1"];
		const colours = {
			Draft: "gray",
			Confirmed: "blue",
			"In Production": "orange",
			"Ready for Installation": "purple",
			Installed: "green",
			Closed: "darkgrey",
			Cancelled: "red",
		};
		return [__(doc.status), colours[doc.status] || "gray", `status,=,${doc.status}`];
	},
};

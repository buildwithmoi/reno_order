// Copyright (c) 2026, Patrick Anteh and contributors
// For license information, please see license.txt

// Form behaviour for Reno Order (Part 12). Everything here is a convenience: each rule it mirrors
// (totals, discount approval, dates, who may mark an order Installed) is enforced again on the server.

const INSTALLATION_STATUSES = ["Ready for Installation", "Installed", "Closed"];
const SALES_ORDER_STATUSES = ["Confirmed", "In Production", "Ready for Installation", "Installed"];
const LINKED_DOCUMENTS = {
	sales_order: "Sales Order",
	delivery_note: "Delivery Note",
	sales_invoice: "Sales Invoice",
};

frappe.ui.form.on("Reno Order", {
	setup(frm) {
		// Dynamic filters
		const for_customer = (query) => () => ({
			query,
			filters: { link_doctype: "Customer", link_name: frm.doc.customer },
		});
		frm.set_query(
			"customer_address",
			for_customer("frappe.contacts.doctype.address.address.address_query")
		);
		frm.set_query(
			"contact_person",
			for_customer("frappe.contacts.doctype.contact.contact.contact_query")
		);
		frm.set_query("assigned_supervisor", () => ({
			query: "reno_order.api.queries.site_supervisor_query",
		}));
		frm.set_query("item_code", "items", () => ({
			filters: { is_sales_item: 1, disabled: 0, has_variants: 0 },
		}));
		frm.set_query("warehouse", "items", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));
	},

	refresh(frm) {
		frm.toggle_display("installation_section", INSTALLATION_STATUSES.includes(frm.doc.status));
		show_status_headline(frm);
		add_buttons(frm);
		show_discount_hint(frm);
	},

	async customer(frm) {
		// Fetch the customer's default address and contact.
		if (!frm.doc.customer) {
			frm.set_value({ customer_address: null, contact_person: null });
			return;
		}
		const args = { doctype: "Customer", name: frm.doc.customer };
		const [address, contact] = await Promise.all([
			frappe.xcall("frappe.contacts.doctype.address.address.get_default_address", args),
			frappe.xcall("frappe.contacts.doctype.contact.contact.get_default_contact", args),
		]);
		frm.set_value({ customer_address: address || null, contact_person: contact || null });
	},

	async customer_address(frm) {
		const display = frm.doc.customer_address
			? await frappe.xcall("frappe.contacts.doctype.address.address.get_address_display", {
					address_dict: frm.doc.customer_address,
			  })
			: "";
		frm.set_value("address_display", display);
	},

	discount_percentage(frm) {
		recalculate(frm);
		show_discount_hint(frm);
	},

	validate(frm) {
		// Friendly, early messages; the server repeats these checks.
		const { transaction_date, expected_installation_date } = frm.doc;
		if (
			transaction_date &&
			expected_installation_date &&
			expected_installation_date < transaction_date
		) {
			frappe.msgprint({
				title: __("Check the dates"),
				indicator: "orange",
				message: __("Expected Installation Date can't be before the Transaction Date."),
			});
			frappe.validated = false;
		}
		const bad_row = (frm.doc.items || []).find(
			(row) => flt(row.qty) <= 0 || flt(row.rate) < 0
		);
		if (bad_row) {
			frappe.msgprint({
				title: __("Check the items"),
				indicator: "orange",
				message: __("Row {0}: quantity must be more than 0 and rate can't be negative.", [
					bad_row.idx,
				]),
			});
			frappe.validated = false;
		}
	},
});

frappe.ui.form.on("Reno Order Item", {
	async item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;
		const defaults = await frappe.xcall("reno_order.api.queries.get_item_defaults", {
			item_code: row.item_code,
			company: frm.doc.company,
		});
		if (defaults.rate && !row.rate)
			await frappe.model.set_value(cdt, cdn, "rate", defaults.rate);
		if (defaults.warehouse && !row.warehouse)
			await frappe.model.set_value(cdt, cdn, "warehouse", defaults.warehouse);
	},
	qty: recalculate,
	rate: recalculate,
	items_remove: recalculate,
});

// Live preview of the totals. The server recalculates on save, so these numbers are never trusted.
function recalculate(frm) {
	let total = 0;
	for (const row of frm.doc.items || []) {
		row.amount = flt(row.qty) * flt(row.rate);
		total += row.amount;
	}
	frm.doc.total_amount = total;
	frm.doc.discount_amount = (total * flt(frm.doc.discount_percentage)) / 100;
	frm.doc.grand_total = total - frm.doc.discount_amount;
	frm.refresh_fields(["items", "total_amount", "discount_amount", "grand_total"]);
}

async function show_discount_hint(frm) {
	if (frm.doc.docstatus !== 0 || !flt(frm.doc.discount_percentage)) {
		frm.set_intro("");
		return;
	}
	frm.__discount_policy ??= await frappe.xcall("reno_order.api.queries.get_discount_policy");
	const { threshold, approver_role, can_approve } = frm.__discount_policy;
	frm.set_intro(
		threshold && flt(frm.doc.discount_percentage) > threshold && !can_approve
			? __(
					"A {0}% discount is above the {1}% limit. Someone with the {2} role must confirm this order.",
					[frm.doc.discount_percentage, threshold, approver_role]
			  )
			: "",
		"orange"
	);
}

function show_status_headline(frm) {
	const { is_overdue, delivery_status, delivery_note, delivery_error } = frm.doc;
	if (is_overdue) {
		frm.dashboard.set_headline_alert(
			__("Installation is overdue: the expected installation date has passed."),
			"red"
		);
	} else if (frm.doc.logistics_status === "Failed") {
		frm.dashboard.set_headline_alert(
			__("Delivery booking with the logistics provider failed: {0}", [
				frappe.utils.escape_html(frm.doc.logistics_error || ""),
			]),
			"red"
		);
	} else if (delivery_status === "Failed") {
		frm.dashboard.set_headline_alert(
			__("Delivery Note automation failed: {0}", [
				frappe.utils.escape_html(delivery_error || ""),
			]),
			"red"
		);
	} else if (delivery_status === "Queued") {
		frm.dashboard.set_headline_alert(
			__("Delivery Note is being prepared in the background…"),
			"blue"
		);
	} else if (delivery_status === "Prepared" && delivery_note) {
		frm.dashboard.set_headline_alert(
			__("Delivery Note {0} is ready for the warehouse.", [delivery_note]),
			"green"
		);
	}
}

function add_buttons(frm) {
	const doc = frm.doc;
	if (frm.is_new()) return;

	for (const [fieldname, doctype] of Object.entries(LINKED_DOCUMENTS)) {
		if (doc[fieldname]) {
			frm.add_custom_button(
				__(doctype),
				() => frappe.set_route("Form", doctype, doc[fieldname]),
				__("View")
			);
		}
	}

	// Status-based actions: only shown when they make sense. The server checks permission regardless.
	if (
		doc.docstatus === 1 &&
		!doc.sales_order &&
		SALES_ORDER_STATUSES.includes(doc.status) &&
		frappe.model.can_create("Sales Order")
	) {
		frm.add_custom_button(
			__("Sales Order"),
			async () => {
				const name = await frappe.xcall(
					"reno_order.reno_order.doctype.reno_order.reno_order.create_sales_order",
					{
						reno_order: doc.name,
					}
				);
				frappe.show_alert({
					message: __("Sales Order {0} created as a draft", [name]),
					indicator: "green",
				});
				frappe.set_route("Form", "Sales Order", name);
			},
			__("Create")
		);
	}

	if (
		doc.status === "Ready for Installation" &&
		frappe.user.has_role("Site Supervisor") &&
		frm.perm[0]?.write
	) {
		frm.add_custom_button(__("Mark as Installed"), () => mark_as_installed(frm)).addClass(
			"btn-primary"
		);
	}

	if (doc.logistics_status === "Failed" && frm.perm[0]?.write) {
		frm.add_custom_button(__("Retry Delivery Booking"), async () => {
			await frappe.xcall(
				"reno_order.integrations.logistics.booking.retry_shipment_booking",
				{ reno_order: doc.name }
			);
			frappe.show_alert({ message: __("Booking queued again"), indicator: "blue" });
			frm.reload_doc();
		});
	}

	if (doc.delivery_status === "Failed" && frm.perm[0]?.write) {
		frm.add_custom_button(__("Retry Delivery Note"), async () => {
			await frappe.xcall("reno_order.erpnext_flow.delivery.retry_delivery_note", {
				reno_order: doc.name,
			});
			frappe.show_alert({ message: __("Queued again"), indicator: "blue" });
			frm.reload_doc();
		});
	}
}

function mark_as_installed(frm) {
	frappe.prompt(
		[
			{
				fieldname: "remarks",
				fieldtype: "Small Text",
				label: __("Installation Remarks (optional)"),
			},
		],
		async ({ remarks }) => {
			// The same API the mobile app uses, so the server applies the same rules.
			if (remarks) {
				await frappe.xcall("reno_order.api.supervisor.add_installation_remarks", {
					reno_order: frm.doc.name,
					remarks,
				});
			}
			await frappe.xcall("reno_order.api.supervisor.update_installation_status", {
				reno_order: frm.doc.name,
				status: "Installed",
			});
			frappe.show_alert({
				message: __("Marked as Installed. The Delivery Note is being prepared."),
				indicator: "green",
			});
			frm.reload_doc();
		},
		__("Mark as Installed"),
		__("Confirm")
	);
}

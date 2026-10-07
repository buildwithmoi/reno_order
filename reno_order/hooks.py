app_name = "reno_order"
app_title = "Reno Order"
app_publisher = "Patrick Anteh"
app_description = "Custom app for the kitchen renovation order workflow on ERPNext"
app_email = "patrickanteh1@gmail.com"
app_license = "mit"

required_apps = ["erpnext"]

# Installation / migration
# ------------------------
before_install = "reno_order.setup.install.before_install"
after_install = "reno_order.setup.install.after_install"
before_migrate = "reno_order.setup.install.before_migrate"
after_migrate = "reno_order.setup.install.after_migrate"

# Standard ERPNext documents
# --------------------------
_reno_links = {
	"after_insert": "reno_order.erpnext_flow.links.link_reno_order",
	"on_cancel": "reno_order.erpnext_flow.links.unlink_reno_order",
	"on_trash": "reno_order.erpnext_flow.links.unlink_reno_order",
}

doc_events = {
	"Sales Order": {
		"validate": "reno_order.erpnext_flow.sales_order.validate_sales_order",
		**_reno_links,
	},
	"Delivery Note": _reno_links,
	"Sales Invoice": _reno_links,
}

# Row-level permissions (Part 11)
# -------------------------------
permission_query_conditions = {"Reno Order": "reno_order.permissions.get_query_conditions"}
has_permission = {"Reno Order": "reno_order.permissions.has_permission"}

# Scheduled jobs
# --------------
scheduler_events = {
	"hourly": [
		"reno_order.erpnext_flow.delivery.retry_failed_delivery_notes",
		"reno_order.integrations.logistics.booking.retry_failed_bookings",
	],
	"cron": {
		# 06:00 every day, before the installation teams start.
		"0 6 * * *": ["reno_order.tasks.flag_overdue_installations"],
	},
}

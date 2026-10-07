"""Optional demo data for trying the app and recording the walkthrough (not loaded on install).

	bench --site <site> execute reno_order.setup.demo.setup_demo_data

Idempotent: safe to run more than once.
"""

import frappe

CUSTOMERS = [
	{"customer_name": "Ama Owusu", "customer_type": "Individual"},
	{"customer_name": "Golden Tulip Apartments", "customer_type": "Company"},
]

# item_code: (item_name, is_stock_item, standard selling rate)
ITEMS = {
	"KC-CAB-01": ("Kitchen Cabinet", 1, 4500),  # manufactured in-house (Part 4)
	"KC-TOP-GRN": ("Granite Countertop", 1, 3200),
	"HW-HINGE-SET": ("Soft-close Hinge Set", 1, 180),  # bought in when out of stock (Part 5)
	"SVC-INSTALL": ("Installation Service", 0, 1500),
	# Raw materials for the cabinet's BOM (Part 4)
	"RM-PLYWOOD": ("Plywood Sheet 18mm", 1, 0),
	"RM-LAMINATE": ("Laminate Sheet", 1, 0),
	"RM-ADHESIVE": ("Wood Adhesive (1L)", 1, 0),
	"RM-HARDWARE": ("Cabinet Hardware Kit (handles, screws)", 1, 0),
}

# Part 4: one Kitchen Cabinet = these raw materials (qty per cabinet, valuation rate, opening stock).
CABINET_BOM = {
	"RM-PLYWOOD": (3, 250, 30),
	"RM-LAMINATE": (2, 120, 20),
	"RM-ADHESIVE": (1, 60, 10),
	"RM-HARDWARE": (1, 40, 10),
}
# Bought-in finished goods in stock. The hinge set is deliberately absent: it's out of stock (Part 5).
OPENING_STOCK = {"KC-TOP-GRN": (5, 2000)}

# Operation: (workstation, minutes per cabinet, hourly costs by component)
OPERATIONS = {
	"Cutting": ("Cutting Station", 60, {"Wages": 30, "Electricity": 20}),
	"Assembly": ("Assembly Bench", 90, {"Wages": 35, "Rent": 5}),
	"Finishing": ("Finishing Booth", 45, {"Wages": 30, "Consumables": 15}),
}

SUPPLIERS = ["Accra Hardware Supplies", "Tema Fittings Ltd"]  # two quotes to compare (Part 5)


# One user per role for the walkthrough. No passwords are stored here: set them in the UI
# (User → Change Password) or give each user API keys (User → Settings → API Access).
DEMO_USERS = {
	"sales@reno.local": ("Sam Sales", ["Sales User"]),
	"manager@reno.local": ("Mona Manager", ["Sales Manager", "Sales User"]),
	"production@reno.local": ("Paul Production", ["Production User"]),
	"supervisor@reno.local": ("Sara Supervisor", ["Site Supervisor"]),
	"accounts@reno.local": ("Ade Accounts", ["Accounts User"]),
	# Service account the Delivery Note automation runs as (Reno Settings → Automation User).
	"automation@reno.local": ("Reno Automation", ["Stock User", "Sales User"]),
}


def setup_demo_data():
	for email, (full_name, roles) in DEMO_USERS.items():
		if not frappe.db.exists("User", email):
			first_name, last_name = full_name.split(" ", 1)
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"last_name": last_name,
					"send_welcome_email": 0,
					"user_type": "System User",
				}
			).insert()
		frappe.get_doc("User", email).add_roles(*roles)
	settings = frappe.get_single("Reno Settings")
	if not settings.automation_user:
		settings.automation_user = "automation@reno.local"
		settings.save()

	for customer in CUSTOMERS:
		if not frappe.db.exists("Customer", {"customer_name": customer["customer_name"]}):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_group": _leaf("Customer Group"),
					"territory": _leaf("Territory"),
					**customer,
				}
			).insert()

	for item_code, (item_name, is_stock_item, rate) in ITEMS.items():
		if not frappe.db.exists("Item", item_code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": item_code,
					"item_name": item_name,
					"description": item_name,
					"item_group": _leaf("Item Group"),
					"stock_uom": "Nos",
					"is_stock_item": is_stock_item,
					"is_sales_item": int(not item_code.startswith("RM-")),
				}
			).insert()
		if rate and not frappe.db.exists(
			"Item Price", {"item_code": item_code, "price_list": "Standard Selling"}
		):
			frappe.get_doc(
				{
					"doctype": "Item Price",
					"item_code": item_code,
					"price_list": "Standard Selling",
					"price_list_rate": rate,
				}
			).insert()

	setup_manufacturing_and_buying()  # bench execute commits when the function returns
	print("Demo users, customers, items, BOM, opening stock and suppliers are ready.")


def setup_manufacturing_and_buying():
	"""Parts 4 and 5: standard ERPNext masters only. Nothing here customises manufacturing or buying."""
	company = frappe.defaults.get_global_default("company")
	warehouse = {
		name: frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": name})
		for name in ("Stores", "Work In Progress", "Finished Goods")
	}

	# v16 keeps the default work-in-progress and finished-goods warehouses on the Company.
	defaults = frappe.db.get_value(
		"Company", company, ["default_wip_warehouse", "default_fg_warehouse"], as_dict=True
	)
	frappe.db.set_value(
		"Company",
		company,
		{
			"default_wip_warehouse": defaults.default_wip_warehouse or warehouse["Work In Progress"],
			"default_fg_warehouse": defaults.default_fg_warehouse or warehouse["Stores"],
			"default_operating_cost_account": _operating_cost_account(company),
		},
	)

	for operation, (workstation, _minutes, costs) in OPERATIONS.items():
		if not frappe.db.exists("Workstation", workstation):
			frappe.get_doc(
				{
					"doctype": "Workstation",
					"workstation_name": workstation,
					"workstation_costs": [
						{"operating_component": component, "operating_cost": cost}
						for component, cost in costs.items()
					],
				}
			).insert()
		if not frappe.db.exists("Operation", operation):
			frappe.get_doc({"doctype": "Operation", "name": operation, "workstation": workstation}).insert()
		else:
			frappe.db.set_value("Operation", operation, "workstation", workstation)

	for item_code, (_qty, rate, _opening) in CABINET_BOM.items():
		frappe.db.set_value("Item", item_code, "valuation_rate", rate)

	if not frappe.db.exists("BOM", {"item": "KC-CAB-01", "is_active": 1, "docstatus": 1}):
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": "KC-CAB-01",
				"company": company,
				"quantity": 1,
				"with_operations": 1,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": code, "qty": qty} for code, (qty, _r, _o) in CABINET_BOM.items()],
				"operations": [
					{"operation": op, "workstation": ws, "time_in_mins": minutes}
					for op, (ws, minutes, _c) in OPERATIONS.items()
				],
			}
		)
		bom.insert()
		bom.submit()  # becomes the item's default BOM

	opening = {code: (qty, rate) for code, (_q, rate, qty) in CABINET_BOM.items()} | OPENING_STOCK
	missing = {
		code: v
		for code, v in opening.items()
		if not frappe.db.exists("Bin", {"item_code": code, "actual_qty": (">", 0)})
	}
	if missing:
		reco = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"company": company,
				"purpose": "Opening Stock",
				# Opening balances go against Temporary Opening (a balance-sheet account), not P&L.
				"expense_account": frappe.db.get_value(
					"Account", {"company": company, "account_type": "Temporary", "is_group": 0}
				),
				"items": [
					{"item_code": code, "warehouse": warehouse["Stores"], "qty": qty, "valuation_rate": rate}
					for code, (qty, rate) in missing.items()
				],
			}
		)
		reco.insert()
		reco.submit()

	for supplier in SUPPLIERS:
		if not frappe.db.exists("Supplier", supplier):
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": supplier,
					"supplier_group": frappe.db.exists("Supplier Group", "Hardware")
					or _leaf("Supplier Group"),
				}
			).insert()


def _operating_cost_account(company: str) -> str:
	"""Workstation costs (labour, power) absorbed into the cabinet's valuation are credited here when a
	Work Order finishes. Without it the Manufacture stock entry can't post its GL entries."""
	if account := frappe.db.get_value("Company", company, "default_operating_cost_account"):
		return account
	if account := frappe.db.get_value(
		"Account", {"company": company, "account_name": "Manufacturing Overheads"}
	):
		return account
	return (
		frappe.get_doc(
			{
				"doctype": "Account",
				"company": company,
				"account_name": "Manufacturing Overheads",
				"parent_account": frappe.db.get_value(
					"Account", {"company": company, "account_name": "Direct Expenses", "is_group": 1}
				),
				"root_type": "Expense",
				"report_type": "Profit and Loss",
			}
		)
		.insert()
		.name
	)


def _leaf(doctype: str) -> str:
	return frappe.db.get_value(doctype, {"is_group": 0}, "name")

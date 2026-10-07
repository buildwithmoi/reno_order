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
}


def setup_demo_data():
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

	frappe.db.commit()
	print("Demo customers and items are ready.")


def _leaf(doctype: str) -> str:
	return frappe.db.get_value(doctype, {"is_group": 0}, "name")

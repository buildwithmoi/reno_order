"""Backfill the new mandatory Reno Order field `order_type` with "Standard" (Part 9).

Runs as a **post_model_sync** patch, so the `order_type` column already exists when it starts.

Production safety:
- **Never overwrites a real value.** Only rows where order_type is NULL or '' are changed, and that
  condition is repeated in the UPDATE itself, so a value set by a user between our SELECT and our
  UPDATE is left alone.
- **Idempotent.** A second run finds nothing to change. Frappe's Patch Log also stops it running
  twice on the same site.
- **Small, short transactions.** The table is walked in primary-key order, 1,000 rows at a time,
  committing after each batch:
  - each UPDATE locks at most 1,000 rows for a few milliseconds, so users keep working
  - there's no single huge transaction to roll back or to delay replicas
  - if the patch is interrupted, rerunning it simply carries on
- **Plain SQL, not doc.save().** Saving 50,000 orders would run every validation and hook and change
  `modified` on each one. This is a data correction, not a business change.
"""

import frappe

BATCH_SIZE = 1000


def execute():
	if not frappe.db.has_column("Reno Order", "order_type"):
		return

	updated = 0
	last_name = ""
	while True:
		# Keyset pagination on the primary key: each batch is an index range scan, not an OFFSET.
		names = frappe.db.sql_list(
			"SELECT name FROM `tabReno Order` WHERE name > %s ORDER BY name LIMIT %s",
			(last_name, BATCH_SIZE),
		)
		if not names:
			break

		frappe.db.sql(
			"""
			UPDATE `tabReno Order` SET order_type = 'Standard'
			WHERE name IN %(names)s AND IFNULL(order_type, '') = ''
			""",
			{"names": names},
		)
		updated += frappe.db._cursor.rowcount
		frappe.db.commit()
		last_name = names[-1]

	print(f"Reno Order: order_type set to 'Standard' on {updated} record(s)")
	return updated

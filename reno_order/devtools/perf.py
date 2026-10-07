"""Reproduce the Part 9 / Part 10 measurements on a development site. Never run on production.

	bench --site <site> execute reno_order.devtools.perf.seed --kwargs "{'count': 100000}"
	bench --site <site> execute reno_order.devtools.perf.drop_report_index
	bench --site <site> execute reno_order.devtools.perf.measure        # "before"
	bench --site <site> execute reno_order.devtools.perf.add_report_index
	bench --site <site> execute reno_order.devtools.perf.measure        # "after"
	bench --site <site> execute reno_order.devtools.perf.measure_patch --kwargs "{'blank': 50000}"
	bench --site <site> execute reno_order.devtools.perf.clear

Synthetic rows are named PERF-…, so they're easy to remove. They are inserted with bulk SQL: no
child rows or hooks; enough for the report and the patch.
"""

import random
import statistics
import time
from datetime import date, timedelta

import frappe

from reno_order.reno_order.doctype.reno_order.reno_order import REPORT_INDEX, REPORT_INDEX_NAME
from reno_order.reno_order.report.monthly_reno_order_value.monthly_reno_order_value import MONTHLY_QUERY

PREFIX = "PERF-"
STATUS_WEIGHTS = {
	"Draft": 5,
	"Confirmed": 10,
	"In Production": 10,
	"Ready for Installation": 5,
	"Installed": 15,
	"Closed": 45,
	"Cancelled": 10,
}
DOCSTATUS = {"Draft": 0, "Cancelled": 2}


def _guard():
	if not frappe.conf.developer_mode:
		frappe.throw("Performance tooling only runs on a developer-mode site.")


def seed(count: int = 100_000, months: int = 24, batch: int = 2_000):
	"""Insert `count` orders spread over the last `months` months (so a 12-month report reads ~half)."""
	_guard()
	company = frappe.defaults.get_global_default("company")
	currency = frappe.get_cached_value("Company", company, "default_currency")
	statuses, weights = zip(*STATUS_WEIGHTS.items(), strict=True)
	start = date.today() - timedelta(days=30 * months)
	existing = frappe.db.count("Reno Order", {"name": ("like", f"{PREFIX}%")})

	rows = []
	for i in range(existing + 1, existing + count + 1):
		status = random.choices(statuses, weights)[0]
		transaction_date = start + timedelta(days=random.randint(0, 30 * months))
		total = round(random.uniform(2_000, 60_000), 2)
		discount = random.choice((0, 0, 0, 5, 10))
		rows.append(
			(
				f"{PREFIX}{i:07d}",
				DOCSTATUS.get(status, 1),
				status,
				"Perf Test Customer",
				company,
				currency,
				transaction_date,
				transaction_date + timedelta(days=random.randint(7, 45)),
				"Standard",
				total,
				discount,
				round(total * discount / 100, 2),
				round(total * (100 - discount) / 100, 2),
			)
		)
		if len(rows) == batch:
			_insert(rows)
			rows = []
	if rows:
		_insert(rows)
	frappe.db.commit()
	print(f"Seeded {count} orders; table now has {frappe.db.count('Reno Order')} rows")


def _insert(rows):
	frappe.db.sql(
		"""
		INSERT INTO `tabReno Order` (name, creation, modified, modified_by, owner, docstatus, status,
			customer_name, company, currency, transaction_date, expected_installation_date, order_type,
			total_amount, discount_percentage, discount_amount, grand_total)
		VALUES {}
		""".format(
			", ".join(
				[
					"(%s, NOW(6), NOW(6), 'Administrator', 'Administrator', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
				]
				* len(rows)
			)
		),
		[value for row in rows for value in row],
	)


def clear():
	_guard()
	frappe.db.sql("DELETE FROM `tabReno Order` WHERE name LIKE %s", f"{PREFIX}%")
	frappe.db.commit()
	print("Removed synthetic orders")


def drop_report_index():
	_guard()
	frappe.db.sql(f"ALTER TABLE `tabReno Order` DROP INDEX IF EXISTS `{REPORT_INDEX_NAME}`")
	print(f"Dropped {REPORT_INDEX_NAME}")


def add_report_index():
	frappe.db.add_index("Reno Order", list(REPORT_INDEX), index_name=REPORT_INDEX_NAME)
	print(f"Added {REPORT_INDEX_NAME} {REPORT_INDEX}")


def measure(runs: int = 7):
	"""Print EXPLAIN, rows read (InnoDB handler counters) and the median time of the report query."""
	_guard()
	params = {
		"company": frappe.defaults.get_global_default("company"),
		"from_date": date.today().replace(day=1) - timedelta(days=335),
		"to_date": date.today(),
	}
	query = MONTHLY_QUERY.format(permission_condition="")
	print(f"Rows in table: {frappe.db.count('Reno Order')}")
	print(f"Indexes: {sorted({r[2] for r in frappe.db.sql('SHOW INDEX FROM `tabReno Order`')})}\n")

	explain = frappe.db.sql(f"EXPLAIN {query}", params, as_dict=True)
	for row in explain:
		print(
			" | ".join(f"{k}={row[k]}" for k in ("type", "possible_keys", "key", "key_len", "rows", "Extra"))
		)

	before = _handler_reads()
	frappe.db.sql(query, params)
	after = _handler_reads()
	print("\nInnoDB reads for one run:", {k: after[k] - before[k] for k in after if after[k] - before[k]})

	timings = []
	for _ in range(runs):
		started = time.perf_counter()
		result = frappe.db.sql(query, params)
		timings.append((time.perf_counter() - started) * 1000)
	print(f"Median of {runs} runs: {statistics.median(timings):.1f} ms ({len(result)} result rows)")


def _handler_reads() -> dict:
	return {
		name: int(value)
		for name, value in frappe.db.sql("SHOW SESSION STATUS LIKE 'Handler_read%%'")
		if name in ("Handler_read_rnd_next", "Handler_read_next", "Handler_read_key")
	}


def measure_patch(blank: int = 50_000):
	"""Blank `blank` synthetic rows' order_type, then time the backfill patch (twice: idempotency)."""
	_guard()
	from reno_order.patches.v1_0 import backfill_order_type

	frappe.db.sql(
		"UPDATE `tabReno Order` SET order_type = NULL WHERE name LIKE %s ORDER BY name LIMIT %s",
		(f"{PREFIX}%", blank),
	)
	frappe.db.sql(
		"UPDATE `tabReno Order` SET order_type = 'Premium' WHERE name LIKE %s ORDER BY name DESC LIMIT 100",
		(f"{PREFIX}%",),
	)
	frappe.db.commit()
	print(
		"Blank before:",
		frappe.db.sql("SELECT COUNT(*) FROM `tabReno Order` WHERE IFNULL(order_type,'')=''")[0][0],
	)

	for run in (1, 2):
		started = time.perf_counter()
		updated = backfill_order_type.execute()
		print(f"Run {run}: updated {updated} rows in {time.perf_counter() - started:.2f}s")

	print(
		"Blank after:",
		frappe.db.sql("SELECT COUNT(*) FROM `tabReno Order` WHERE IFNULL(order_type,'')=''")[0][0],
	)
	print(
		"Premium kept:",
		frappe.db.count("Reno Order", {"order_type": "Premium", "name": ("like", f"{PREFIX}%")}),
	)

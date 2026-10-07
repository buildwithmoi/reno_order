# Database and performance: Monthly Reno Order Value (Part 10)

**Report:** *Monthly Reno Order Value* (Script Report, `reno_order/reno_order/report/monthly_reno_order_value/`). It shows one row per month for the last 12 months, a column of value per status, a total and an order count, and a stacked bar chart. The report applies the same row-level permissions as the list view, so a Sales Manager sees their team's totals only.

## The query
```sql
SELECT DATE_FORMAT(transaction_date, '%Y-%m') AS month,
       status,
       COUNT(*)         AS orders,
       SUM(grand_total) AS value
FROM `tabReno Order`
WHERE company = %(company)s
  AND transaction_date BETWEEN %(from_date)s AND %(to_date)s
  {permission_condition}          -- empty for unrestricted roles
GROUP BY month, status
ORDER BY month;
```
One aggregate query, with no per-row Python and no N+1 lookups. The database returns at most 12 × 7 groups, and Python pivots them into months × statuses.

## Test data
`reno_order/devtools/perf.py` seeds **100,000** orders spread over **24 months** with realistic status weights, so a 12-month report reads about half the table. Reproduce with:
```bash
bench --site <site> execute reno_order.devtools.perf.seed --kwargs "{'count': 100000}"   # ~11 s
bench --site <site> execute reno_order.devtools.perf.drop_report_index
bench --site <site> execute reno_order.devtools.perf.measure
bench --site <site> execute reno_order.devtools.perf.add_report_index
bench --site <site> execute reno_order.devtools.perf.measure
bench --site <site> execute reno_order.devtools.perf.clear
```

## EXPLAIN before optimisation
Indexes present: `PRIMARY`, `creation`, `customer`, `status`, `transaction_date` (single column), …
```
type=ALL | possible_keys=transaction_date | key=None | rows=98855
Extra=Using where; Using temporary; Using filesort

InnoDB reads for one run: Handler_read_rnd_next = 100,096   (every row of the table)
Median of 7 runs: 100.4 ms
```
**Note:** a single-column index on `transaction_date` **already existed, and MariaDB chose not to use it.** The range covers about half the table. Using that index would mean ~50,000 index lookups, *each followed by a random read of the full row* to get `status` and `grand_total`. The optimiser rightly judged a sequential full scan cheaper.

## Optimisation
```sql
ALTER TABLE `tabReno Order`
  ADD INDEX IF NOT EXISTS company_date_status_total_index (company, transaction_date, status, grand_total);
```
The index is created in code: `on_doctype_update()` in `reno_order.py`, also called from `after_migrate`, because Frappe only runs `on_doctype_update` when the DocType definition itself changes.

## EXPLAIN after optimisation
```
type=range | possible_keys=transaction_date,company_date_status_total_index
key=company_date_status_total_index | key_len=567 | rows=49427
Extra=Using where; Using index; Using temporary; Using filesort

InnoDB reads for one run: Handler_read_next = 47,267 (index entries), Handler_read_rnd_next = 92
Median of 7 runs: 56.9 ms
```
- **`type` ALL → range:** only the 12-month slice of the index is read.
- **`Using index` (covering):** every column the query needs is *in* the index, so **no table rows are read at all**. Table row reads went from 100,096 to 92 (the 92 are the small temporary table used for grouping).
- **Time 100 ms → 57 ms here.** On this dev machine the whole table is in memory. On a busy production server, where table pages compete for the buffer pool, the gap is much larger, because the work shrinks from "read every row" to "read a narrow index slice".
- *Using temporary; Using filesort* remain because we group by `DATE_FORMAT(...)`, an expression. With at most 84 groups that's trivial. To remove it at much larger scale, add a stored generated column `month` and index `(company, month, status, grand_total)`.

## Why this index (column order matters)
1. **`company` first:** an equality filter. Equality columns go before range columns, so the index narrows to one company immediately.
2. **`transaction_date` second:** the range filter. Once a range column is used, the columns after it can't narrow the search further…
3. **…so `status` and `grand_total` are there only to *cover* the query.** They make it answerable from the index alone.

## When an index helps
- **Selective filters:** finding a small fraction of rows, such as a name, a link or a short date range.
- **Covering:** all needed columns are in the index, so table rows are skipped, even for a large fraction of rows (our case).
- **`ORDER BY` / `GROUP BY` on indexed columns** can avoid sorting.
- **Joins** on indexed foreign keys.

## When an index hurts
- **Every write pays for it.** Each INSERT, and each UPDATE of an indexed column, must also update the index. Here `status` changes about 6 times in an order's life, which is acceptable for a reporting index. Indexing frequently-updated columns in a high-write table is not.
- **Low selectivity without covering**, as with the existing `transaction_date` index above. The optimiser ignores it, so it's pure write overhead.
- **Storage and memory:** indexes compete with data for the InnoDB buffer pool.
- **Too many overlapping indexes** slow writes and can mislead the optimiser. Prefer one well-ordered composite index over several single-column ones, and drop indexes that `sys.schema_unused_indexes` / `performance_schema` show are never used.

## Introducing the index safely in production
1. **Test on a staging copy** of production data (same size). Measure the build time and check EXPLAIN uses the index.
2. **Online DDL:** InnoDB builds a secondary index with `ALGORITHM=INPLACE, LOCK=NONE`, so reads and writes continue during the build. You can force it explicitly so it fails instead of locking: `ALTER TABLE … ADD INDEX …, ALGORITHM=INPLACE, LOCK=NONE;`
3. **Run it at a quiet time.** The build still uses I/O and briefly takes a metadata lock at the start and end. Long-running transactions can block that lock, so check `SHOW PROCESSLIST` first.
4. **Very large or very busy tables:** use `pt-online-schema-change` or `gh-ost` to build in a shadow table and swap.
5. **Ship it with the app** (`on_doctype_update` / `after_migrate`, `ADD INDEX IF NOT EXISTS`), so every site gets it on `bench migrate`, with no manual SQL.
6. **Watch afterwards:** the slow query log, replication lag, and write latency on the table.

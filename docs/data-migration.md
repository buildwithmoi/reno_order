# Data migration: backfilling Order Type (Part 9)

**Situation:** the mandatory field **Order Type** is added after ~50,000 Reno Orders already exist. The old rows have no value.

**Patch:** `reno_order/patches/v1_0/backfill_order_type.py`, registered under `[post_model_sync]` in `patches.txt`, so the column exists before it runs.

## What makes it production-safe
| Requirement | How |
|---|---|
| **Safe on production** | Short transactions: the table is walked in **primary-key order, 1,000 rows per batch, committed per batch**. Each `UPDATE` locks at most 1,000 rows for milliseconds, so users keep working, there's no giant transaction to roll back, and replicas don't fall behind |
| **Never overwrites valid values** | Only rows where `IFNULL(order_type,'') = ''` change. That condition is **in the UPDATE itself**, so a value a user sets between our SELECT and our UPDATE is respected |
| **Idempotent** | A second run changes 0 rows (tested). Frappe's **Patch Log** also stops it running twice on a site. If it's interrupted, rerunning simply continues |
| **Locking / performance** | **Keyset pagination** (`WHERE name > last ORDER BY name LIMIT 1000`) is an index range scan on the primary key, which stays fast however deep into the table it is. `OFFSET` gets slower with every batch, and a single `UPDATE … WHERE order_type IS NULL` would lock every matching row in one long transaction |
| **No side effects** | Plain SQL rather than `doc.save()`. Saving 50,000 orders would run every validation and hook (e.g. queue jobs) and change `modified` on each one. This is a data correction, not a business change |

**Measured** on 100,000 synthetic orders with 50,000 blank (`reno_order.devtools.perf.measure_patch`):
```
Blank before: 50000
Run 1: updated 50000 rows in 3.06s      (whole table walked: ~101 batches of 1,000, each committed)
Run 2: updated 0 rows in 1.48s          (idempotent: nothing left to change)
Blank after: 0
Premium kept: 100                        (existing values untouched)
```

## Deploying and validating on a live ERPNext instance
1. **Before:**
   - **Back up:** `bench --site <site> backup --with-files`
   - **Rehearse on staging** restored from that backup; time the patch and check the counts.
   - **Record a baseline:**
     ```sql
     SELECT IFNULL(NULLIF(order_type,''),'(blank)') AS type, COUNT(*) FROM `tabReno Order` GROUP BY 1;
     ```
2. **Deploy:**
   - `git pull` (or deploy the new app version), then `bench --site <site> migrate`. The new column is added during model sync. On MariaDB 10.3+, adding a nullable column at the end is an *instant* operation. Then the patch runs once and is logged in **Patch Log**.
   - Choose a quiet time. The batches keep locks short, but the patch still generates writes.
3. **Validate:**
   - **Patch Log** contains `reno_order.patches.v1_0.backfill_order_type`.
   - The baseline query shows **0 blank**. *Standard* grew by exactly the previous blank count, and every other type is unchanged.
   - **Spot-check** a few old orders in the UI. Open and save one, to confirm the mandatory check passes.
   - Check the **Error Log** and the migrate output for exceptions.
4. **Rollback:** restore the backup if something unexpected happens. If you need to undo *only* this change (rarely needed), the patch can first copy the affected names into a log table, so you can set them back exactly. Here that isn't necessary, because the old value was "nothing".

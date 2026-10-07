# Debugging: "Installed" hangs, errors, yet the downstream document appears (Part 14)

> *"Sometimes when we mark an order as Installed, the page keeps loading and eventually shows an error. However, sometimes the downstream transaction is still created."*

## 1. How I would investigate
1. **Pin down the facts.** Which orders and users, what time, how often, and roughly how long until the error? Is it tied to large orders, busy hours, or the external provider being slow? Get the exact error text, and the HTTP status from the browser's Network tab:
   - **502/504** → a proxy or worker timeout
   - **417** → a validation error
   - **500** → an unhandled exception
2. **Follow one failing request end to end**, using its time window and the order name:

| Where | What to look for | Command / place |
|---|---|---|
| nginx | 504 *upstream timed out* / 502 *upstream prematurely closed*; request duration (`$request_time`) | `/var/log/nginx/error.log`, `access.log` |
| Gunicorn (web) | `WORKER TIMEOUT` (bench default `http_timeout` 120 s), worker restarts | `logs/web.error.log` |
| Frappe | Tracebacks for the request; Error Log entries for the order | `logs/frappe.log`, **Error Log** list (filter by reference) |
| Request profile | Which SQL ran, how long each query took, and where the time went | **Recorder** (`/app/recorder`): start it, reproduce, stop, inspect |
| Monitoring | Request and job durations over time | `monitor: 1` in site config → `logs/monitor.json.log` |
| MariaDB | Lock waits, long transactions, deadlocks | `SHOW FULL PROCESSLIST`, `SHOW ENGINE INNODB STATUS`, `information_schema.innodb_trx`, slow query log |
| Background jobs | Whether jobs are queued, running or failing; whether a worker is online | `bench doctor` (→ "Workers online: 1"), `bench --site <site> show-pending-jobs`, **RQ Job** list, `logs/worker.error.log` |
| External calls | Each provider call with its duration and result | **Integration Request** list (filtered by the order) |
| The data | How many Delivery Notes exist for the order, and when each was created relative to the error | `frappe.get_all("Delivery Note", {"reno_order": ...}, ["name", "creation", "docstatus"])` |

3. **Reproduce on staging** with a slow dependency (e.g. the mock provider at `--latency 10-20`) and the Recorder on. Check whether the time is spent waiting on a lock, on a network call, or on the database.

## 2. Suspected root cause
The symptom ("long wait → error → but the document exists") is the classic sign of **slow work done inside the user's request, with a commit happening before the request dies**:

1. A naive "Installed" handler creates and submits the Delivery Note, which means stock postings and possibly reposting, and/or calls an external API, **synchronously inside the save**.
2. On a large order or a slow provider, the request outlives **nginx's proxy timeout** (often 60–120 s) or **Gunicorn's worker timeout**. The browser gets 502/504 and shows an error.
3. But the work is **already committed**, either:
   - because nginx gave up while the Gunicorn worker carried on and finished, or
   - because the code called `frappe.db.commit()` (or created the document in a separate transaction, e.g. `enqueue(now=True)`) partway through.
4. The user sees an error and **clicks again** → a **second** Delivery Note, because nothing made the operation idempotent.

A related cause is **lock waits**. If a background job holds the order's row lock (`SELECT … FOR UPDATE`) while doing slow work, a user saving the same order waits up to `innodb_lock_wait_timeout` (50 s), then gets *"Lock wait timeout exceeded"*. Meanwhile the job finishes and creates the document.

**In this implementation** those causes are designed out:
- **Nothing slow runs in the request.** Marking Installed only changes the status and queues `prepare_delivery_note` with `enqueue_after_commit=True`. The save returns in well under a second: measured **0.07 s** for the equivalent *Ready for Installation* step, while the external API took ~34 s in the background.
- **No commits partway through a request.** The job is queued *after* the commit, so if the save fails, nothing is queued.
- **Duplicates are impossible, not just unlikely:**
  - `job_id` + `deduplicate` in RQ
  - the job **locks the order and reuses an existing Delivery Note** (tested by running it twice)
  - ERPNext's over-delivery check against the Sales Order quantity
- **Locks are short.** The Delivery Note job holds the row lock only for local, quick work. The slow logistics call uses a **claim-and-commit** pattern and holds **no lock** during the network call.
- **Failures are visible and retried:** `delivery_status = Failed`, the error message, an Error Log entry, the hourly retry and a manual Retry.
- **Redis restarted and lost the job?** The order stays `Queued`. The hourly job re-queues orders stuck in `Queued`, and deduplication makes that a no-op when the job is still genuinely waiting.

## 3. Residual risks I'd still watch for
- **Auto-submitting very large Delivery Notes** (setting on) holds the order's row lock for longer while stock posts. Keep it off (the default), or submit in a second job without the order lock.
- **A worker that's down** means "Queued" stays queued. Alert on `bench doctor` / queue length, not only on errors.

## 4. Fix checklist for a codebase that has this bug
1. Move downstream document creation and external calls into `frappe.enqueue(..., enqueue_after_commit=True, job_id=..., deduplicate=True)` on the right queue.
2. Make the job **idempotent**: lock, check for an existing document, reuse it. Add a DB-level or validate-hook guard against a second active document.
3. Remove `frappe.db.commit()` from request code paths.
4. Never hold a row lock across a network call. Claim, commit, call, then record.
5. Show the processing state on the document, so users don't click twice. Make the action itself idempotent, so a repeated request does nothing harmful.
6. Add a regression test: run the job twice and assert one document (`test_running_the_job_twice_creates_one_delivery_note`).

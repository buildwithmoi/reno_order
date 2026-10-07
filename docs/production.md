# Production and server knowledge (Part 18)

Short answers, from running Frappe/ERPNext benches in production on AWS, Hetzner and Contabo.

## Frappe Cloud
| Topic | How it works |
|---|---|
| **Application deployment** | Apps (this one from its GitHub repo, with access granted through the Frappe Cloud GitHub app) are added to a **bench group** at a chosen branch. *Deploy* builds a new bench image with every app at the selected commit. Each site then moves to the new bench, and **`migrate` runs per site**. A site whose update fails is recovered onto the previous bench, with the backup taken just before the update. A staging site on the same bench group gets the release first. |
| **Backups** | **Automatic scheduled backups** (database, plus public and private files), kept **offsite** with a retention that depends on the plan. You can take one on demand before risky work. Download or **restore** from the dashboard: to the same site, or into a new site to test a restore. |
| **Logs** | Per site and bench in the dashboard (web, worker, scheduler). In the site itself: **Error Log**, **Scheduled Job Log**, **RQ Job** and **Integration Request**. The site analytics show request load, slow requests and slow queries, and background job time. |
| **Scheduler / workers** | Managed processes. You don't run supervisor. Worker capacity comes with the plan or server, and **dedicated servers** let you size workers. Check *Scheduled Job Log* and *RQ Job*. On Frappe Cloud a site with no user activity for *dormant days* runs scheduled jobs only once a day. |
| **Site configuration** | The site's **Config** tab edits `site_config.json` keys (e.g. `maintenance_mode`, `pause_scheduler`, custom keys an app reads with `frappe.conf`). Bench-group config and environment variables cover settings shared by every site. Secrets are entered there or in Password fields, never in Git. |

## Self-hosted ERPNext: the role of each part
```
browser ──HTTPS──► Nginx ─┬─ /assets, public /files  (served from disk)
                          ├─ /socket.io ──► Node socketio (realtime)
                          └─ everything else ──► Gunicorn (Frappe WSGI)
                                                  │        ▲
          Scheduler ──enqueues due jobs──► Redis queue ──► RQ Workers
                                                  │
                                    Redis cache ◄─┴─► MariaDB (one database per site)
          Supervisor (or systemd) keeps every process above running
```
| Component | Role |
|---|---|
| **Nginx** | Reverse proxy. TLS termination, **serves static assets and public files directly**, proxies the app to Gunicorn and websockets to socketio, picks the site by host name (multi-tenant), and sets upload size limits and timeouts |
| **Gunicorn** | Runs the Frappe Python app (`frappe.app:application`). A pool of workers handles HTTP requests; size it to CPU and RAM. A worker timeout kills requests that run too long |
| **Supervisor / process manager** | Starts, monitors and **restarts** web, workers, scheduler, socketio and Redis. `bench setup production` generates the config. `supervisorctl status` / `bench restart` |
| **Redis** | **redis-cache:** document, metadata and permission caches and session data. **redis-queue:** RQ background job queues (`short`, `default`, `long`) and realtime pub/sub for socketio |
| **MariaDB** | Stores everything: one database per site, InnoDB. Needs `utf8mb4` and enough `innodb_buffer_pool_size` (most of the free RAM on a dedicated DB box). Back up with `bench --site <site> backup` (plus binlogs for point-in-time recovery) |
| **Workers** | `bench worker` processes that **run** background jobs from the queues: emails, `frappe.enqueue` jobs (this app's Delivery Note and courier booking), report exports, and scheduled jobs |
| **Scheduler** | One `bench schedule` process. Every minute it checks each site's scheduled jobs (`scheduler_events`, *Scheduled Job Type*) and **enqueues** the due ones. It doesn't run them: **workers do**. If the workers are down, scheduled work stops too |

## Troubleshooting
| Symptom | Likely causes | Check | Fix |
|---|---|---|---|
| **502 Bad Gateway** | Nginx can't reach Gunicorn: process down or crash-looping (often an import error after a deploy), wrong upstream port, or killed by the **OOM killer** (A *504* is a timeout instead: requests slower than Gunicorn's or Nginx's limit.) | `supervisorctl status`, `logs/web.error.log`, `/var/log/nginx/error.log`, `ss -ltnp \| grep 8000`, `dmesg \| grep -i oom` | Fix the crash (roll back the bad release), `bench restart`. Add RAM or reduce Gunicorn workers if OOM. Move slow work to background jobs |
| **Worker queue backlog** | Workers down, too few for the load, one long job blocking a queue, jobs hanging on an external API with no timeout, or a burst of enqueues (e.g. in a loop) | `bench doctor`, `bench --site <site> show-pending-jobs`, *RQ Job* list, `logs/worker.error.log` | Restart workers. Add workers or a dedicated worker for `long`. Put slow jobs on `long` with **timeouts**. **Deduplicate** jobs (`job_id`). Purge junk with `bench --site <site> purge-jobs` only after reading what it is |
| **Scheduler not running** | Disabled in System Settings, `pause_scheduler` / `maintenance_mode` left on in site config (e.g. after a failed migrate), the scheduler process down, or **workers down** (jobs enqueued but never run) | `bench --site <site> scheduler status`, `bench doctor`, *Scheduled Job Log*, `logs/scheduler.error.log` | `bench --site <site> scheduler resume` / `enable`, `set-maintenance-mode off`, restart the scheduler and workers. Run one job by hand with `bench --site <site> trigger-scheduler-event <event>` |
| **High CPU** | Gunicorn: heavy reports or endpoints, bots or API loops. Workers: a runaway job. MariaDB: unindexed queries (often the real cause) | `htop` (which process?), `SHOW FULL PROCESSLIST`, slow query log, *RQ Job*, Nginx access log for hot URLs | Index or rewrite the query (see [performance.md](performance.md)), cache, paginate, move it to a background job, rate-limit abusive clients, then scale |
| **Slow MariaDB queries** | Missing or wrong indexes, `LIKE '%x%'`, huge `OFFSET`, N+1 loops in Python, a buffer pool too small, lock waits from long transactions | Slow query log (`long_query_time`), **`EXPLAIN`**, `SHOW ENGINE INNODB STATUS` for locks, Frappe's **Recorder** to see a request's queries | Composite or covering indexes (added in code with `on_doctype_update`), keyset pagination, set-based SQL, shorter transactions, tune `innodb_buffer_pool_size` |
| **Disk full** | Old backups in `sites/<site>/private/backups`, logs without rotation, MariaDB binlogs, large tables (`tabError Log`, `tabVersion`, `tabRoute History`), attachments | `df -h`, `du -sh` on bench, backups and logs, `/var/lib/mysql` | Move backups offsite and keep N locally, **logrotate**, `expire_logs_days` for binlogs, Log Settings auto-clear, `bench --site <site> trim-database`. Alert at 80%: when the disk is full MariaDB stops writing and backups fail |
| **Failed migration** | A patch error, a schema change failing on bad existing data (e.g. duplicates when adding a unique index), a timeout on a huge table, or app versions out of step | The traceback in the console, `logs/frappe.log`, `bench --site <site> ready-for-migration` | The site may still be in maintenance mode. Fix the data or the patch and **re-run `bench migrate`** (patches are tracked in *Patch Log*, so finished ones don't re-run). If needed, **restore the pre-deploy backup** and the previous code (see [ci-cd.md](ci-cd.md)). Make patches idempotent and batched (see [data-migration.md](data-migration.md)) |

**Habits that prevent most of these:**
- A backup before every deploy.
- Staging gets each release first.
- Monitoring of disk, RAM, queue length and error rate, with alerts.
- Timeouts on every external call.
- No heavy work in a web request.

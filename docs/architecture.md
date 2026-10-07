# Architecture note: major decisions and why

## 1. A custom document beside ERPNext, not a modified ERPNext
**Reno Order** is its own submittable DocType. It owns the renovation lifecycle: installation date, site supervisor, production and installation states. The money and the stock still go through **standard** Sales Order → Delivery Note → Sales Invoice, plus Work Orders and Material Requests / Purchase Orders.
- *Why:*
  - Renovation states don't fit the Sales Order's own status model.
  - Changing ERPNext core would break on every upgrade.
  - Accountants and storekeepers keep using screens and reports they already know.
- *How they connect:*
  - A **`reno_order` link field** is added (in code) to SO, DN, SI, Work Order, Material Request and Purchase Order.
  - It isn't `no_copy`, so **ERPNext's own mappers carry it forward**.
  - doc_events keep the reverse links on the Reno Order.
  - Where ERPNext builds a document **without** its mapper (Work Order from a Sales Order, Purchase Order from a Supplier Quotation), a `validate` hook takes the reference from the Sales Order or Material Request its rows point at.
  - The Reno Order's **Connections** tab lists all of them.
  - There is no copy of ERPNext's mapping logic.

## 2. The server is the source of truth
Totals are recalculated in `validate()`. Discount approval runs in `before_submit()`. Addresses and contacts are checked against the customer. System fields are restored on every save, and operational roles have a field guard. **JavaScript only previews and guides:** everything it mirrors is enforced again in Python, because the REST API, imports and scripts never run the form's JS.

## 3. Configuration and structure as code
The workflow, the roles, the custom fields and the report index are created by idempotent install / migrate hooks:
- `setup/workflow.py`
- `setup/install.py`
- `on_doctype_update` + `after_migrate`

There are no UI-built fixtures to export. Every site gets the same thing on `bench migrate`, and the code is versioned and reviewable. One trade-off: UI edits to the workflow are overwritten on the next migrate. That's intentional, so the process is changed in code.

## 4. Permissions in layers, all on the server
| Layer | Mechanism |
|---|---|
| Which documents a role may touch at all | DocPerm (role permissions) |
| Which *rows* | `permission_query_conditions` (lists, reports, API queries) **and** `has_permission` (single documents), sharing one set of rules: own / assigned / team via the Sales Person tree / assigned supervisor / confirmed orders for production |
| Which *fields* | Permission level 1 for installation fields, plus a server guard: operational roles may change only their own fields |
| Which *state changes* | The Frappe workflow, enforced in `validate_workflow` for form and API alike |

Two v16 behaviours shaped this. `has_permission` must return `True` (`None` means *deny*). And `save()` checks **write** even on submitted documents, which is why the field guard, not DocPerm, limits production and supervisor users.

## 5. Nothing slow or fragile in the user's request
Delivery Note preparation (on *Installed*) and courier booking (on *Ready for Installation*) are **background jobs**:
- **Queued after commit**, so a job never sees uncommitted data, and nothing is queued if the save fails.
- **Deduplicated by `job_id`**, and **idempotent inside**: the Delivery Note job locks the order and reuses an existing note, and courier booking uses an atomic claim plus an Idempotency-Key.
- **On the right queue:** `default` for quick local work, `long` for the 10–20 s external API.
- **No lock held across a network call**, using claim → commit → call → record.
- **Failures are recorded on the order and retried:** in-job backoff, then an hourly retry, then a manual Retry button. Interrupted claims are recovered.
- **Run as a configured service account** (Automation User), with **permission checks left on**. The Site Supervisor who clicked has no stock rights, and the brief forbids disabling checks.

This is also the answer to Part 14 (see `debugging.md`).

## 6. Integration security
- Credentials are **Password fields**: encrypted at rest, read with `get_password()` only when needed, **masked in Integration Request logs**.
- Outbound calls have connect/read timeouts and classified errors (transient vs permanent).
- The inbound webhook is **HMAC-SHA256 signed with a replay window**. It's open to guests, but inert unless the signature matches, and it can only change logistics fields.
- The mobile API uses **per-user tokens** (no shared super-user), so every permission rule applies. OAuth2 + PKCE is the production path.

## 7. Data and performance
- **Reporting:** one aggregate query backed by a **covering composite index** `(company, transaction_date, status, grand_total)`. Equality column first, then the range, then covering columns. Measured on 100k rows: full scan of 100,096 rows (100 ms) → index range scan reading 92 table rows (57 ms).
- **Data fixes:** **keyset-paginated batches** with a commit per batch, touching only blank values, idempotent, using plain SQL so no hooks fire. Measured: 50k rows in 3.06 s.

## 8. Testing approach
- **80 integration tests** run as real users with real roles: totals, approval, workflow, row and field permissions, Sales Order and Delivery Note idempotency, API status codes, courier retries and webhook security, the patch and the report, manufacturing and buying references, and HRMS leave allocation.
- **External HTTP is mocked:** fake responses and an injected `sleep`, so the tests are fast and deterministic. A standalone mock provider (`mock_services/`) exists for end-to-end runs.
- Tests live in `reno_order/tests/` (outside the DocType folders), so Frappe doesn't generate ERPNext's large test dataset. Each test builds what it needs, and everything is rolled back.
- **CI** builds a fresh Frappe v16 + ERPNext + HRMS site on every push. A `before_tests` hook completes ERPNext setup.

## Alternatives considered
| Option | Why not |
|---|---|
| Server Scripts / Client Scripts in the UI | Not versioned or testable, and limited Python (sandbox). Money and state logic belongs in an app with tests |
| Adding the renovation states to Sales Order | Fights ERPNext's own status logic and breaks on upgrades |
| `ignore_permissions=True` in automation | Forbidden by the brief and hides bugs. A least-privilege service account instead (only log records use it) |
| Row lock during the courier call | Blocks users for 10–20 s and causes lock-wait timeouts, the exact Part 14 symptom |
| A synchronous Delivery Note on *Installed* | Slow, and fails the supervisor's action because of a stock or accounting problem. The duplicate-on-retry risk is Part 14 |
| Fixtures for custom fields / workflow | Exported from the UI and easy to drift. Code is explicit and idempotent |

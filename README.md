# Reno Order: kitchen renovation workflow on ERPNext

A Frappe v16 app for a company that sells, manufactures and installs custom kitchens. **Reno Order** manages the renovation-specific workflow, from enquiry to installation and close-out, while every financial and stock transaction stays a **standard ERPNext document** (Sales Order → Delivery Note → Sales Invoice, Work Order, Material Request → Purchase Order).

> Technical assignment for Savyant Systems (Senior ERPNext / Frappe Developer). The design is explained in **[docs/architecture.md](docs/architecture.md)**.

## What's inside
| Area (assignment part) | Where | Docs |
|---|---|---|
| Reno Order DocType, server-side totals, validations, discount approval, Sales Order creation with duplicate protection (1) | `reno_order/reno_order/doctype/reno_order/`, `erpnext_flow/sales_order.py` | [architecture](docs/architecture.md) |
| Workflow, roles, overdue-installation scheduler (2) | `setup/workflow.py`, `tasks.py` | [architecture §3–4](docs/architecture.md) |
| SO → DN → SI with references; Delivery Note automation on *Installed* (2, 3) | `erpnext_flow/links.py`, `erpnext_flow/delivery.py` | [erpnext-integration](docs/erpnext-integration.md) |
| Manufacturing and buying scenarios (4, 5) | ERPNext configuration, linked through `reno_order` | [manufacturing-and-buying](docs/manufacturing-and-buying.md) |
| Site Supervisor REST API (6) | `api/supervisor.py` | [api](docs/api.md) |
| Logistics provider integration + webhook (7); background processing (8) | `integrations/logistics/`, `mock_services/logistics_api.py` | [integrations](docs/integrations.md) |
| Backfill patch for ~50k orders (9) | `patches/v1_0/backfill_order_type.py` | [data-migration](docs/data-migration.md) |
| Monthly value report on 100k+ rows, EXPLAIN before/after (10) | `reno_order/report/monthly_reno_order_value/` | [performance](docs/performance.md) |
| Row- and field-level permissions (11) | `permissions.py`, permission levels, field guard in `reno_order.py` | [architecture §4](docs/architecture.md) |
| Client-side behaviour (12) | `reno_order.js`, `reno_order_list.js` | [api §design](docs/api.md) |
| HRMS leave debugging (13) | Analysis + fix | [hrms-leave-allocation](docs/hrms-leave-allocation.md) |
| Debugging the "Installed" timeout (14) | Design + investigation | [debugging](docs/debugging.md) |
| Tests (15), Git (16), CI/CD (17) | `reno_order/tests/`, `.github/workflows/` | [ci-cd](docs/ci-cd.md) |
| Production and server knowledge (18) | Q&A | [production](docs/production.md) |

## Requirements
Frappe and ERPNext **v16** · Python 3.14 · Node 24 · MariaDB 10.6+ (tested on 11.x) · Redis

## Installation
```bash
cd ~/frappe-bench
bench get-app --branch version-16 erpnext          # if not already installed
bench get-app <this repository URL> --branch main
bench --site <site> install-app reno_order
bench --site <site> migrate                          # also syncs the workflow, custom fields and index
```
Installing creates:
- the **Production User** and **Site Supervisor** roles
- the **Reno Order Workflow**
- `reno_order` link fields on ERPNext doctypes
- the report index

**Optional demo data:** customers, kitchen items and prices, one user per role, and an automation service account:
```bash
bench --site <site> execute reno_order.setup.demo.setup_demo_data
```
Demo users have no passwords. Set them in *User → Change Password*, or generate API keys for the mobile API.

## Configuration
| Where | Setting |
|---|---|
| **Reno Settings** | Discount approval threshold (%) and approver role · **Automation User** (service account for background jobs; give it Stock User + Sales User) · submit Delivery Notes automatically (off = prepared as drafts) |
| **Reno Logistics Settings** | Enabled · API base URL · API key and webhook secret (**encrypted Password fields**) · connect/read timeouts · max booking attempts |
| **Users** | Sales User / Sales Manager / Production User / Site Supervisor / Accounts User. Sales Managers' teams come from the **Sales Person** tree, linked to users through Employee |
| **Each order** | *Assigned Site Supervisor*. Sales Users also see orders assigned to them through the standard *Assign To* |

To try the logistics integration locally, see [docs/integrations.md](docs/integrations.md#run-it-yourself) (mock provider with configurable latency and failures).

## Testing
```bash
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app reno_order                    # 70 tests
bench --site <site> run-tests --app reno_order --module reno_order.tests.test_api
```
On an empty site, the app's `before_tests` hook completes ERPNext's setup wizard first.

| Required by Part 15 | Test(s) |
|---|---|
| Total calculation | `test_reno_order.TestRenoOrderTotals` (incl. `test_client_supplied_totals_are_ignored`) |
| Discount authorisation | `test_reno_order.TestDiscountAuthorisation` |
| Invalid installation date | `test_installation_date_before_order_date_is_rejected` |
| Unauthorised API request | `test_api.test_guest_cannot_call_the_api`, `test_supervisor_cannot_update_someone_elses_order` |
| Permission restrictions | `test_permissions` (row visibility per role, supervisor field restrictions) |
| Sales Order creation | `test_sales_order_is_created_and_linked` |
| Duplicate Sales Order prevention | `test_duplicate_sales_order_is_prevented` |
| Installed status processing | `test_erpnext_flow` (queued, idempotent, failure + retry, through to Sales Invoice) |
| Patch behaviour | `test_patch_and_report.TestBackfillOrderTypePatch` |

Additional tests cover the workflow, the overdue job, courier retries and idempotency, credential masking, webhook signatures and replay, the report and its index.

**CI:** `.github/workflows/ci.yml` builds a fresh Frappe v16 + ERPNext site and runs the suite on every push and pull request. `linter.yml` runs pre-commit (Ruff, Prettier, ESLint), the Frappe Semgrep rules and pip-audit. See [docs/ci-cd.md](docs/ci-cd.md) for staging, production and rollback.

## Assumptions
- One delivery per order. The Delivery Note is prepared when the order is **Installed**, because installation *is* the hand-over. Courier delivery of components is booked at **Ready for Installation**.
- "Assigned to" a Sales User means the standard *Assign To*. A Sales Manager's **team** is their node in the Sales Person tree and everything below it.
- One Site Supervisor per order (*Assigned Site Supervisor*).
- Order Type values: Standard, Premium, Custom. Existing orders are backfilled to Standard.
- Discount approval: above **10%** needs the **Sales Manager** role by default (configurable).
- The logistics provider is mocked. The payload and webhook format are illustrative.

## Known limitations
- No partial deliveries per installation phase. The automation delivers everything pending on the Sales Order. Item-level links (`reno_order_item`) would be the next step.
- Cancelling an order after its delivery is booked doesn't cancel the shipment with the provider (a cancel endpoint call is needed).
- Stock reservation is ERPNext's *soft* reservation, unless *Stock Settings → Enable Stock Reservation* is turned on.
- `add_installation_remarks` appends again if a phone repeats the same request. Client-side idempotency keys would fix that.
- The workflow is synced from code on every migrate, so edits made in the UI are overwritten (intentional).

## Project layout
```
reno_order/
  reno_order/doctype/   reno_order, reno_order_item, reno_settings, reno_logistics_settings
  reno_order/report/    monthly_reno_order_value
  api/                  supervisor REST API, form helper queries
  erpnext_flow/         Sales Order creation, Delivery Note automation, reference links
  integrations/         logistics client, booking jobs, webhook
  patches/v1_0/         order_type backfill
  setup/                install/migrate hooks, workflow definition, demo data
  devtools/perf.py      100k-row seeding and EXPLAIN/timing measurements
  permissions.py        row-level rules; tasks.py: scheduled jobs
  tests/                70 integration tests
mock_services/          standalone mock logistics provider
docs/                   design notes and the written answers
```

## License
MIT

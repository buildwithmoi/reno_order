# Changelog

All notable changes to this app. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-08

### Added
- **Reno Order** DocType with items, server-side totals (client and API values are always recalculated), validations, and discount approval above a configurable threshold (**Reno Settings**).
- **Create Sales Order** from a confirmed order, with duplicate protection: a row lock plus a Sales Order validate hook.
- **Workflow** Draft → Confirmed → In Production → Ready for Installation → Installed → Closed, plus Cancelled, defined in code and synced on migrate. Roles: Production User and Site Supervisor.
- **Row-level permissions:** own / assigned / Sales Person team / assigned supervisor / confirmed orders for production. **Field-level protection** for operational roles and system-managed fields.
- **Daily job** that flags overdue installations and notifies Sales Managers.
- **Order-to-cash references:** `reno_order` on SO, DN, SI, Work Order, Material Request and Purchase Order, with reverse links kept by doc_events.
- **Delivery Note automation** on *Installed*: after commit, deduplicated, idempotent, run as a configured automation user, failures recorded and retried.
- **Site Supervisor REST API:** status, remarks and site photos, with token authentication and meaningful HTTP errors (401/403/404/409/422).
- **Form behaviour:** customer-scoped filters, default rates, live totals, discount hint, status headline, status-based buttons. **List view** status colours.
- **Logistics provider integration:** encrypted credentials, timeouts, Idempotency-Key, transient vs permanent errors, backoff and scheduled retries, Integration Request logging, an HMAC-signed status webhook, and a mock provider.
- **Patch** backfilling `order_type` for existing orders: keyset batches, idempotent.
- **Monthly Reno Order Value** report with a covering index. Developer tooling to reproduce the 100k-row measurements.
- **70 integration tests**, a CI workflow on a fresh Frappe v16 + ERPNext site, and linting (pre-commit, Semgrep, pip-audit).
- **Documentation:** architecture, ERPNext integration, API, integrations, debugging, performance, data migration, CI/CD, manufacturing and buying, HRMS leave analysis, production operations.

# CI/CD (Part 17)

## The pipeline today: `.github/workflows/ci.yml`
```
push to main / pull request
   └─► Build:  Python 3.14 + Node 24, `bench init --frappe-branch version-16`, get ERPNext (version-16) + this app
   └─► Setup:  throwaway MariaDB 11.8 + Redis services, fresh `test_site` with ERPNext + reno_order
               (the app's `before_tests` hook completes ERPNext's setup wizard on the empty site)
   └─► Test:   bench --site test_site run-tests --app reno_order   (70 tests)
   └─► Result: green/red check on the commit or PR
```
A second workflow, `linter.yml`, runs **pre-commit**, the **Frappe Semgrep rules** and **pip-audit** (known-vulnerable dependencies):
- pre-commit runs Ruff lint + format, Prettier, ESLint, and JSON / YAML / TOML checks
- the Semgrep rules catch common Frappe security and correctness mistakes

Both workflows pass `actionlint`, and pre-commit passes locally on every file.

Hygiene:
- The workflow token can only read the repo (`permissions: contents: read`).
- A newer push cancels the older run (`concurrency`).
- Every credential is a throwaway value for a container that lives a few minutes.

## Extending it to Development → Staging → Production
```
feature branch ──PR──► main ──(auto)──► Staging ──(release tag + approval)──► Production
      │                 │                  │                                    │
   CI + lint         CI + lint        deploy + migrate                     backup → deploy → migrate
   must pass         must pass        + smoke tests                        → smoke tests → monitor
```
1. **Development:** feature branches. A pull request must pass CI and lint, and get a code review, before merging (**branch protection** on `main`).
2. **Staging:** every merge to `main` deploys automatically to a staging site that holds a recent, **anonymised copy of production data**. Then smoke tests run:
   - `/api/method/ping`
   - create → confirm → install a demo order through the REST API
   - open the report

   Migrations get a real rehearsal here on production-sized data, with timings.
3. **Production:** deploy by **release tag** (`v1.2.0`), through a GitHub **Environment with required reviewers**, so a person approves each release. Steps:
   1. **Back up first:** `bench --site <site> backup --with-files`.
   2. Deploy: on **Frappe Cloud**, push to the bench group's branch and deploy (it builds a new bench image and runs migrate). **Self-hosted**: SSH to the server, then `git fetch && git checkout v1.2.0` in `apps/reno_order`, `bench --site <site> migrate`, `bench build --app reno_order`, `bench restart`.
   3. Post-deploy smoke tests, then watch error rates (Error Log), the background queues (`bench doctor`) and response times.

**Secrets** for deploys (SSH key, Frappe Cloud token) live in GitHub **Environment secrets**, scoped per environment, and are never in the repo.

## Rollback if a production deployment fails
- **Code:** go back to the previous tag (`git checkout v1.1.0`), `bench build`, `bench restart`. On Frappe Cloud, redeploy the previous bench image. It's fast, because the previous version is a known-good artifact.
- **Database:** Frappe patches run forward only. So:
  - **Write backward-compatible migrations** ("expand, then contract"): add columns and backfill first, and remove old ones only in a later release. Old code then still runs against the new schema, and a code rollback **doesn't need** a database rollback. The Order Type patch is like this: it only fills blanks.
  - If a release did corrupt data, **restore the backup taken just before the deploy** (Frappe Cloud also has point-in-time backups), then replay any business transactions entered since.
- **Turn a feature off instead of rolling back.** Integrations are behind switches. For example, unticking *Reno Logistics Settings → Enabled* stops new bookings immediately, without a deploy.
- **Afterwards:** add a test that reproduces the failure, so it can't ship again.

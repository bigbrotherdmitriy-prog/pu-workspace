# Organizer sessions at scheduler startup

`organizer_sessions` is the current table, not a renamed or retired one.
`OrganizerSession.__tablename__` points to it, the initial Alembic revision
`4d7fb326d458` creates it, later upgrades add columns, and both the live
organizer scan endpoint and scheduler recovery still use it. The `drop_table`
reference in the initial revision is its *downgrade*, not a current upgrade.

The legacy root `docker-compose.yml` started the scheduler as soon as
PostgreSQL accepted connections. The backend performs `alembic upgrade head`
and preflight in its own startup command. Therefore, on a fresh or partially
migrated database, scheduler recovery could query the table before Alembic
created it. `infra/primary/docker-compose.yml` already waits for a healthy
backend; the root stack now does the same. Its `/health` probe can answer only
after backend migrations and startup preflight complete. The startup preflight
checks configuration, database access, and exact schema revision, but does not
require heartbeats from workers or scheduler that have not started yet. The
normal `/api/readiness` and default preflight still require those live services.
Both worker and scheduler wait for the healthy backend. No SQL table name,
organizer business logic, or Alembic migration was changed.

The first root-Compose test on a disposable GitHub-hosted runner exposed a
second ordering defect in the original fix: the backend's full preflight
required two worker heartbeats and one scheduler heartbeat before starting
Uvicorn, while the scheduler waited for backend health. On an empty database
the migration reached `d29a6c4f1e83`, but preflight reported zero workers and
zero schedulers; backend restarted and Compose failed with `container
pu-workspace-backend is unhealthy`. This was reproduced in GitHub Actions run
`35320619930` on diagnostic commit `c0abfade9c27619d2b4b5d4243840152e3577067`.
The disposable stack and its volume were removed by that run. Startup-only
preflight and the worker dependency above break the cycle without weakening
the normal runtime readiness contract.

This is a proven ordering defect in the Compose configuration, **not proof**
that ordering caused every observed production `UndefinedTable`. A database
stamped at head but missing this table, or differing `search_path`/connection
targets between services, would need separate operational repair. Before
altering production data, compare the scheduler and backend database targets
without exposing credentials, then run read-only checks on that same target:

```sql
SELECT current_database(), current_schema(), current_setting('search_path');
SELECT version_num FROM alembic_version;
SELECT to_regclass('organizer_sessions');
```

If the relation remains absent *after* migrations completed, stop the
scheduler and diagnose that schema drift. Do not `stamp head`, silently skip
recovery, or create an empty replacement table without reviewing dependent
foreign keys and existing data.

The opt-in PostgreSQL test `test_organizer_recovery_postgres.py` migrates a
unique disposable schema, inserts queued/scanning/analyzing/ready sessions,
calls the real `recover_incomplete_scans()`, and asserts status reset and
single durable enqueue per session across two restarts. It never uses a
production database.

## PostgreSQL runtime verification — PASS (2026-09-18)

On source commit `28c4ad4d45e4d896f53306ff674bc0a4180531a9`, the test was
run in the separate `puw-mvp2-live-test` Docker Compose project on
`72.56.108.162`: PostgreSQL 16, isolated database
`puw_organizer_test_mvp2`, `PU_TEST_POSTGRES=1`, with
`PUW_ORGANIZER_TEST_DATABASE_URL` pointing only to that database. The test
itself created and dropped its unique temporary schema. It did not connect to
production or staging databases.

`alembic -c alembic.ini heads` and `current` both returned the single
revision `d29a6c4f1e83` after the full migration chain. The backend was
healthy. A fresh rerun of
`tests/test_organizer_recovery_postgres.py::test_recover_incomplete_scans_after_real_postgres_migration`
finished **1 passed, 0 failed, 0 skipped in 5.81 s** (exit code 0). The one
warning is Alembic's existing `path_separator` deprecation in `alembic.ini`;
it is not a test failure.

Verdict for this narrowly scoped recovery test: **PostgreSQL PASS**, upgraded
from offline-only evidence. This does not claim that the broader MVP-2 live
Gmail → Google Tasks/Calendar acceptance has run; test Google credentials are
not configured on the isolated stand yet. No DSN passwords or OAuth secrets
are recorded in this report.

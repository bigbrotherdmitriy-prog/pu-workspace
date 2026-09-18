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
after backend migrations and preflight complete. No SQL table name, organizer
business logic, or Alembic migration was changed.

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

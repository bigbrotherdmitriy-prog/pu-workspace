# MVP PostgreSQL ownership and mandatory runtime coverage

Date: 2026-09-05. Base: `3ff8b767383598226abe7fb3d6710e2de3bf734c`.

This change extends the existing `v54-pilot-runtime.yml` Python runner. It adds
no workflow, queue, provider capability, deployment, or production access.
Local contract tests are not PostgreSQL execution evidence.

## Audit findings and correction

The runner previously supplied neither `TEST_POSTGRES_DSN` nor the MVP1 storage
PostgreSQL target. Its full backend phase therefore skipped both storage proofs.
MVP2 Gmail and MVP3 had database environment variables and dedicated phases, but
exit code zero with skipped tests was accepted as success. MVP4 had SQLite and
offline migration tests, with no finance PostgreSQL concurrency target.

Two new regressions were run before the runner fix: both failed, showing the
missing storage environment and missing storage target. After the fix they pass.

The runner now pins the exact two test node IDs per MVP. Removing/renaming a
mandatory test fails collection. It checks the final pytest summary: all pinned
proofs must pass, with zero skipped, failed, errored, deselected, xfailed or xpassed
tests. Missing/incomplete summaries fail closed. The existing A/B/C PostgreSQL
phase also rejects skips or an absent passing summary. Warnings remain allowed.

| Phase | Owned database | Exact environment | Scope |
| --- | --- | --- | --- |
| MVP1 storage | `puw_v54_test_storage` | `TEST_POSTGRES_DSN` | Transaction lock; synthetic effect crash/replay simulation |
| MVP2 Gmail | `puw_mvp2_test_gmail_history` | `PUW_MVP2_GMAIL_HISTORY_DATABASE_URL` | Cursor claim CAS; migrated schema constraints |
| MVP3 management | `puw_mvp3_test_runtime` | `PUW_MVP3_TEST_DATABASE_URL` | Obligation CAS; digest scheduler race and engine restart/replay |
| MVP4 finance | `puw_mvp4_test_runtime` | `PUW_MVP4_TEST_DATABASE_URL` | Concurrent manual payment confirm; competing correction CAS |

Each database is created by this invocation, migrated to `a54f001c0a18` from
`backend/`, and checked using its actual `alembic_version` before its tests run.
The original migration database remains independent. The foundation database
remains initially empty because its upgrade/downgrade fixture requires that.
Storage and MVP3 fixtures still create ORM metadata in their owned UUID schemas;
those fixture schemas do not constitute migration-backed runtime proof. Their
database's public-schema migration is a separate check.

The MVP4 backend file and the MVP1 `postgres` hostname guard are supplied by the
parent integration change. The pinned MVP4 names are
`test_postgres_concurrent_payment_confirmation_creates_one_fact` and
`test_postgres_competing_payment_corrections_are_cas_serialized`, in
`backend/tests/test_mvp4_finance_postgres_runtime.py`. This runner commit must be
integrated with those backend changes; missing targets fail rather than skip.

## Isolation, cleanup, and reporting

The runner accepts localhost/127.0.0.1/db or the `postgres` service hostname only
under `GITHUB_ACTIONS=true`. Database names must belong to the fixed allowlist.
Passwords are URL encoded. A preexisting database aborts before ownership is
claimed. Cleanup attempts every name actually created by this invocation,
including after test failure or timeout; one failed drop does not prevent attempts
on the other owned databases. Undropped names remain in `CREATED`, so cleanup
cannot report success. No preexisting database is dropped.

Inherited PostgreSQL fixture gates and pytest filters/plugins are cleared before
setting the owned environment. The separate full backend phase disables every
known PostgreSQL gate and keeps SQLite as its application database. Its expected
offline skips do not count as mandatory PostgreSQL proof.

The existing JSON artifact schema gains `mandatory_postgres` statuses:
`PASS`, `FAIL`, `SKIPPED`, `INCOMPLETE`, `ERROR`, or `NOT_RUN`. A failed early phase
leaves subsequent mandatory phases `NOT_RUN`; overall PASS requires all mandatory
phases to pass. Only fixed phase names/statuses/counts and the existing sanitized
failure node IDs/locations are reported. Captured stdout/stderr are never written
or uploaded, and no JUnit/raw-log artifact is created. A valid completed child
runtime record remains reportable if later database cleanup fails.

## Boundaries still open

- MVP1 uses a synthetic adapter and simulated crash, not live Google/Яндекс effects
  or killing/restarting two storage worker processes.
- MVP2 covers Gmail cursor/schema concurrency, not live mailbox execution,
  attachment worker recovery, or full resync beyond the bounded listing.
- MVP3 has no live notification-channel evidence.
- MVP4 coverage is manual finance concurrency, not supply concurrency,
  backup/restore, financial/legal approval, or automated financial actions.
- The separate v5.4 authority, materialization/local-upload and generic
  `PU_TEST_POSTGRES` integration fixtures are not enabled by this bounded runner
  change. They remain separate gaps; their offline skips are not PASS.

## Validation

- Before fix: the two new missing-environment/target regressions failed (expected).
- Final targeted CI contracts: 49 passed, including all MVP runner gates and v5.4
  workflow/protocol contracts. Tests use mocks; no PostgreSQL was contacted.
- Full CI Python contracts excluding the shell smoke module: 159 passed in
  115.98 seconds. Two additional protocol regressions were then added and included
  in the final 49-test targeted pass; production runner code was unchanged.
- Real PostgreSQL/Docker, actionlint, live-provider, browser, backup/restore and
  full backend suites: NOT_RUN in this subtask. No packages were installed and
  no workflow was dispatched, pushed, merged or deployed.

Commands use the existing workspace virtualenv Python with `-X utf8`,
`-p no:cacheprovider`, and a unique `--basetemp`. The full contract invocation is
`python -X utf8 -m pytest scripts/ci -q
--ignore=scripts/ci/tests/test_smoke_workflow.py --tb=short -p no:cacheprovider
--basetemp=.pytest-mvp-runtime-ci-full-20260905`.

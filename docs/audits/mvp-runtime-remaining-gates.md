# Remaining PostgreSQL gates in the existing runtime runner

Date: 2026-09-05. Base commit: `098df263df3b41f40005cdd82b55b90f5e87614d`.
Branch: `codex/mvp-runtime-remaining-gates` in a new isolated worktree.

This change completes runner wiring for the previously omitted authority,
materialization, local-upload and generic schema fixtures. It also adds the
integration's new supply concurrency selectors. It does not claim PostgreSQL
execution success: the local checks below use mocks and source contracts.

## Explicit database and fixture contracts

All databases are in the runner's fixed ownership list. Existing databases abort
creation; cleanup only drops names created by this invocation, and attempts the
other owned databases even if one drop fails. No new workflow, runner, queue,
provider capability or artifact was introduced.

| Mandatory phase | Database | Environment | Initial schema |
| --- | --- | --- | --- |
| `postgres_authority_migration` | `puw_v54_test_authority_migration` | `PUW_V54_AUTHORITY_MIGRATION_DATABASE_URL` | Empty; fixture upgrades, downgrades to a01 and upgrades again |
| `postgres_authority_runtime` | `puw_v54_test_authority` | `PUW_V54_AUTHORITY_DATABASE_URL` | Fixture creates/drops its own UUID schema |
| `postgres_materialization_migration` | `puw_v54_test_materialization_migration` | Phase-specific `PUW_V54_MATERIALIZATION_DATABASE_URL` | Empty; fixture upgrades to current head |
| `postgres_materialization_runtime` | `puw_v54_test_materialization` | `PUW_V54_MATERIALIZATION_DATABASE_URL` | Fixture creates/drops its own UUID schema |
| `postgres_local_upload_runtime` | `puw_v54_test_local_upload` | `PUW_V54_LOCAL_UPLOAD_DATABASE_URL` | Fixture creates/drops its own UUID schema |
| `postgres_schema_fixture` | `puw_v54_test_schema_test` | Phase-specific `DATABASE_URL` and `PU_TEST_POSTGRES=1` | Runner migrates to a18 and checks `alembic_version` first |
| `postgres_mvp4_supply` | Existing `puw_mvp4_test_runtime` | `PUW_MVP4_TEST_DATABASE_URL` | Existing MVP4 migration to a18; follows finance phase |

The two migration fixtures must not be pre-migrated by the runner: both explicitly
reject a nonempty database. After each fixture passes, the runner independently
reads `alembic_version` and requires `a54f001c0a18`. Runtime authority,
materialization and local-upload checks use ORM-created UUID schemas in separate
databases, so their runtime results are not evidence that those schemas were
created by Alembic. The migration phases prove the separate migration path.

Generic fixtures require a database name ending in `_test`. Their opt-in and
application DSN are local to one subprocess. Default `DATABASE_URL` remains
in-memory SQLite and `PU_TEST_POSTGRES=0`; the full offline backend phase clears
all PostgreSQL fixture DSNs, including the new ones. Local-upload receives its
own explicit URL and does not rely on its materialization fallback.

## Mandatory selections and reporting

Each of the six remaining gates pins one existing test function. The supply phase
pins nine individual parametrized node IDs in
`backend/tests/test_mvp4_supply_postgres_runtime.py`:

- `test_postgres_duplicate_supply_commands_create_one_effect`: `request`,
  `request_approval`, `order_approval`, `act`, `dds`;
- `test_postgres_stale_supply_updates_preserve_one_effect`: `order`, `delivery`,
  `act`, `dds`.

The mandatory count is nine, not the two Python function names. Removed or renamed
nodes fail collection. All phases use the existing strict summary contract:
exact passing count, no skips/xfails/xpasses/deselections, and no absent summary.
The safe protocol distinguishes FAIL, SKIPPED, INCOMPLETE, ERROR and NOT_RUN from
PASS. Early failure leaves later gates NOT_RUN. New scope descriptions identify
synthetic authority, materialization, local upload and schema checks. Captured
stdout/stderr remain in memory only; no raw diagnostics or JUnit artifacts are
published. Existing cleanup and sanitized failure reporting remain in force.

## Integration dependencies

- The integration owns the backend change to
  `backend/tests/test_v54_authority_postgres.py`: accept `postgres` only when
  `GITHUB_ACTIONS=true`, preserving the database prefix/query guards. Its previous
  host allowlist rejected the existing workflow's service hostname. That request
  was communicated before wiring; this branch does not edit backend files.
- The parallel supply change owns the two parametrized test functions above.
  It uses the existing MVP4 database environment, unique fixture projects/UUIDs
  and per-case assertions, so it can run after finance without removing or
  resetting finance data. The runner intentionally fails if that file or any
  pinned variant has not been integrated.

## Validation and limits

- Before the fix, 10 regression cases failed: four absent fixture environment
  mappings and six absent mandatory phases. A separate supply regression also
  failed before its phase was added.
- Intermediate targeted runner/environment/protocol tests: 60 passed.
- Final full CI Python contracts, excluding shell smoke: **181 passed in 77.19
  seconds**, after all runner and test changes. `git diff --check` also passed.
- Real PostgreSQL, Docker/actionlint, live providers, browser, backup/restore and
  full backend execution: NOT_RUN in this subtask. No packages were installed,
  and no workflow was dispatched, pushed, merged or deployed.

The full local command uses the existing shared workspace virtualenv Python:
`python -X utf8 -m pytest scripts/ci -q
--ignore=scripts/ci/tests/test_smoke_workflow.py --tb=short -p no:cacheprovider
--basetemp=.pytest-remaining-pg-ci-full-20260905`.

Actual PostgreSQL gates and backend integration must still execute successfully
before their runtime coverage can be reported PASS. Supply concurrency does not
prove backup/restore, live provider effects, automated payments or legal approval.

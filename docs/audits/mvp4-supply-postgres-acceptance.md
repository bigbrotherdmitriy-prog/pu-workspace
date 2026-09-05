# MVP4 supply PostgreSQL acceptance — 2026-09-05

Branch: `codex/mvp4-supply-postgres-acceptance`.
Base: `098df263df3b41f40005cdd82b55b90f5e87614d`.
Required migrated revision: `a54f001c0a18`.

## Result

Prepared nine PostgreSQL concurrency cases through the real `SupplyService` and
its existing role, evidence, row-lock, CAS and receipt checks. No service method,
permission guard or repository is replaced with a test double. Source documents,
users, memberships, approved baseline/budget and exact evidence links are synthetic
database fixtures; no provider, signature, supplier or payment API is called.

The first regression demonstrated a stale ORM identity-map defect in
`SupplyService._locked`: a session that loaded a supply case before another session
committed a delivery could retain the old `record_version` even after its locking
SELECT. The stale command was accepted instead of raising
`SupplyConflict("record_version_conflict")`. The minimal fix adds
`populate_existing=True` to that existing SELECT, so CAS uses the row obtained
under lock. No new schema, command semantics, finance assumptions or monetary
conversions were added.

## Runtime fixture and concurrency evidence

`PUW_MVP4_TEST_DATABASE_URL` is the only database configuration input. The fixture
accepts PostgreSQL on the same explicit local/CI host allowlist as the existing
finance suite, requires a nonempty `puw_mvp4_test_*` database name, and rejects URL
query overrides. It verifies `current_database()`, the single migrated revision
and READ COMMITTED isolation before inserting fixture data. A configured but
unreachable/wrong database fails; only an absent environment variable causes the
explicit CONDITIONAL skip.

The runtime orchestrator owns creation, migration and removal of the disposable
database. This fixture does not create/reset tables, change migrations, truncate
or drop schemas, or clean up other suites' rows. Every scenario creates unique
users/projects and UUID evidence/source/identity rows; assertions are scoped to
its own project/case and can share the owned database with finance acceptance.

Each race uses two independent sessions plus a controller transaction holding the
case row (project row for request creation). Both worker sessions begin together;
the controller observes **two PostgreSQL Lock waits** in `pg_stat_activity` under
a unique transaction-local application name before releasing the lock. This
prevents a merely sequential execution from counting as a concurrency pass.
Queries, barriers and result waits are bounded. Worker failures expose exception
types, not SQL, DSNs, credentials or fixture contents.

Duplicate commands must return one applied result and one identical receipt
replay. Competing commands with the same expected version must yield exactly one
success and one `record_version_conflict`, including when both sessions preloaded
the old ORM object before the race. Assertions require one version increment,
one new immutable history entry/receipt/audit, preservation of earlier snapshots,
one supply case, and no duplicate accepted/delivered quantity or DDS entry.
Created DDS entries remain proposals with zero actual amount, no actual date,
pending human confirmation and no payment fact history. Approval and act commands
create no DDS or external action.

## Exact mandatory PostgreSQL nodes

The runtime integration owner was sent these collected node IDs; this branch
does not edit `scripts/ci`:

```text
tests/test_mvp4_supply_postgres_runtime.py::test_postgres_duplicate_supply_commands_create_one_effect[request]
tests/test_mvp4_supply_postgres_runtime.py::test_postgres_duplicate_supply_commands_create_one_effect[request_approval]
tests/test_mvp4_supply_postgres_runtime.py::test_postgres_duplicate_supply_commands_create_one_effect[order_approval]
tests/test_mvp4_supply_postgres_runtime.py::test_postgres_duplicate_supply_commands_create_one_effect[act]
tests/test_mvp4_supply_postgres_runtime.py::test_postgres_duplicate_supply_commands_create_one_effect[dds]
tests/test_mvp4_supply_postgres_runtime.py::test_postgres_stale_supply_updates_preserve_one_effect[order]
tests/test_mvp4_supply_postgres_runtime.py::test_postgres_stale_supply_updates_preserve_one_effect[delivery]
tests/test_mvp4_supply_postgres_runtime.py::test_postgres_stale_supply_updates_preserve_one_effect[act]
tests/test_mvp4_supply_postgres_runtime.py::test_postgres_stale_supply_updates_preserve_one_effect[dds]
```

## Local verification and remaining gate

Regression-first command selected local checks and reproduced the stale-session
defect: **1 failed, 4 passed, 11 deselected**. After the one-query fix, the initial
new/existing supply profile passed **29 tests**, with the **9 PostgreSQL nodes
explicitly skipped** because no isolated PostgreSQL URL was configured.

The final local fixture uses a file-backed SQLite database so its sessions also
have separate connections. It validates every scenario's real-service setup and
the stale ORM/CAS failure mode across order, delivery, act and DDS commands. It
does not prove PostgreSQL locking, migrated constraints or runtime acceptance.

Final targeted command (from `backend`, using the shared test virtualenv Python):

```powershell
python -X utf8 -m pytest tests/test_mvp4_supply_postgres_runtime.py tests/test_mvp4_supply_acts.py -q --basetemp=.pytest-supply-pg-acceptance-final-20260905
python -X utf8 -m pytest tests/test_mvp4_financial_acceptance.py -q -k supply --basetemp=.pytest-supply-dds-regression-20260905
```

Results: **38 passed, 9 skipped** (24.19 seconds) for new/local and existing
supply coverage; **6 passed, 9 deselected** (1.06 seconds) for existing supply/DDS
financial acceptance. All nine skips are the explicitly unavailable PostgreSQL
runtime nodes. `git diff --check` passed.

PostgreSQL runtime acceptance remains **CONDITIONAL** until all nine explicit
nodes pass on the owned, migrated PostgreSQL database without skips. No full
backend run, production data, push, merge or deployment belongs to this branch.

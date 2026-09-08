# v7 D14 retention correction

Base: `521a2d75762db42a0978da3e891f2133c1586124` (D14 bridge, schema a18).
Branch: `codex/v7-xlsx-retention-recovery`. Date: 2026-09-08.
Only `staging/local_upload.py`, `source_evidence/xlsx_ingestion.py`, new
`test_v7_xlsx_retention_recovery.py` and this report change. The separately owned
factory, existing D14 test/report, frontend, queue, models, migrations and CI are
untouched. No live data, provider requests, production operations or deployment.

## Regression-first findings

1. The child cleanup selected the first `4 * limit` expired children globally,
   then rejected unsupported sources/scopes/extractors/jobs. Rejected rows remained
   the same ordered prefix forever; later eligible ciphertext was never visited.
   Even after SQL eligibility filtering, malformed exact bindings or repeatable
   deletion failures could similarly pin that prefix.
2. The original A05 cleanup also selected D14 children. Its `_retention_binding`
   locked Materialization before Project, opposite to the D14 cleanup/reader
   Project-before-Materialization order. Two transactions could wait on each
   other's locks; the original hook only rejected the child later at job binding.

Four red regressions reproduced unsupported-prefix starvation, denied-prefix
starvation, original cleanup visiting child rows and the inverted lock order.
The first fixture attempt to mutate an immutable handle via ORM was correctly
rejected. The final corruption test uses explicit test-only SQL to exercise the
existing fail-closed exact-identity validator; no schema guard was weakened.

## Repair and authority boundaries

- The original candidate query is original-only (`parent_id IS NULL`) and filters
  configured retention scopes and failed/dead-letter local-upload jobs before
  LIMIT. `_retention_binding` reads candidate scope without a row lock, obtains
  the existing Project retention guard, then locks/reloads the exact original
  with matching tenant/project/owner and rechecks its capability. Pending changes
  to that Materialization fail closed before refresh.
- D14 SQL prefilters configured tenant/project pairs, local-upload source and
  identity, exact extractor name/version/configuration, terminal original job
  and matching staging payload before LIMIT. This is candidate selection only:
  every candidate still undergoes the existing locked capability, lineage,
  deterministic child identity, job state, retention and CAS checks before delete.
- A bounded keyset traversal orders by `(retention_until, id)`. Cursor advancement
  records each **attempted** candidate, including denial/failure, not merely the
  last fetched row. Stopping after the purge limit cannot skip unattempted rows.
  At end of the range, one wrap starts another sweep so repaired or newly eligible
  earlier rows are revisited.
- One invocation fetches at most `4 * limit` nonempty candidate rows and purges
  at most `limit`; an empty tail allows one extra bounded query from the start.
  There is no unbounded application scan. SQL execution cost/large-volume query
  plans have not been measured here.
- A nonblocking same-instance scan lock avoids duplicate prefix work inside one
  process. It is not distributed exclusion. Database locks/CAS and the verified
  existing storage delete capability remain the only effect-safety mechanism.

## Cursor restart limitation (not a closed durable-fairness gate)

The in-memory cursor is explicitly **non-authoritative and non-durable**. It
contains only a traversal position, never permission, a lease, a deletion receipt
or a claim that an object is gone. A new process/ingestion instance resets it to
the beginning and safely rechecks every visited candidate against current DB
state. Independently running instances may repeat work; tombstones and exact
checks protect effects. Changing capability scopes cannot be overridden by an
old cursor.

With a finite eligible set and continued calls on a surviving instance, a denied
prefix no longer starves later candidates; wrap retries repaired candidates.
Repeated restarts before reaching those candidates can still delay progress
indefinitely. This increment therefore does **not** claim persistent bounded
retention latency under perpetual restarts. A durable scheduling/fairness SLA
would require separately designed authoritative checkpoint ownership and tests,
not relabelling this memory hint as persisted state or adding a hidden queue.

## Tests and prepared PostgreSQL gate

New offline tests use the actual A05 DB authority and encrypted local storage:
unsupported provider/extractor/nonterminal prefix, malformed-binding prefix,
bounded attempted-row count, runtime recreation, repaired-prefix wrap, changed
capability, original-only selection and observed SQLAlchemy lock order.
Existing D14 and A05 lifecycle regressions remain positive controls.

Scoped verification command, from `backend` using the shared test venv:

```text
python -X utf8 -m pytest tests/test_v7_xlsx_retention_recovery.py tests/test_mvp1_xlsx_durable_evidence.py tests/test_v54_local_upload_a05_wiring.py tests/test_v54_local_upload_a05_postgres.py tests/test_v54_local_upload_staging.py tests/test_v54_materialization_lifecycle.py tests/test_v54_staging_safety_hardening.py -q --basetemp=.pytest-tmp-v7-retention-final
```

Result: **92 passed, 3 conditional skips** in 30.62s; existing Alembic
`path_separator` warnings only. A final added pending-binding preservation
regression was then included in the new-file-only run: **9 passed, 1 conditional
PostgreSQL skip**. `git diff --check` passed. These are scoped offline results,
not a full backend or PostgreSQL PASS.

The opt-in real PostgreSQL node is:

```text
backend/tests/test_v7_xlsx_retention_recovery.py::test_pg_original_retention_waits_on_project_without_locking_materialization
```

Environment: `PUW_V54_LOCAL_UPLOAD_DATABASE_URL`, falling back to the existing
`PUW_V54_MATERIALIZATION_DATABASE_URL`. The reused fixture URL validator allows
PostgreSQL only, hosts `localhost`, `127.0.0.1`, `::1`, `db`, `postgres`, database
prefix `puw_v54_test_`, and no caller connection query options. It never reads
`DATABASE_URL` for its target. The test creates/removes only a UUID-owned schema
and runs real migrations through `CURRENT_SCHEMA_REVISION` (a18 on this base;
the integration head supplies its own current revision).

A transaction holds the real Project lock; concurrent `_retention_binding`
must block there, observed with `pg_blocking_pids`. The holder then acquires the
original Materialization lock. Old order deadlocks/times out; corrected order
leaves that row free until Project is acquired. The fixture seeds actual DB
mandates with an empty synthetic grant set and real encrypted staging. No
SQL mock stands in for this concurrency assertion.

Neither owned PostgreSQL environment variable was configured locally, so this
node is **NOT RUN / conditional skip**, not PASS. Observing generated lock order
on SQLite is not PostgreSQL concurrency proof. Full-backend, exact-SHA Linux
and live retention-policy acceptance remain parent-owned gates.

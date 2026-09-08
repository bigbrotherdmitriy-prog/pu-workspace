# v7 B1 / M2-02: confirmed-context deferred analysis recovery

Base: `5226782908dff519a2bf2c6b9d320b59f2972c10`.
Branch: `codex/v7-context-confirm-recovery`. Date: 2026-09-08.
Scope: `api/ai_secretary.py`, independent tests, this report. No changes to
automation/monthly rules, models, schema, migrations, queue, shared authority,
CI, frontend, provider configuration, or production. No live calls or deployment.

## Reproduced defect (tests first)

The API committed context before analysis. Task, response and governance engines
each committed their Session independently; the API attached `message_id` and
the proposed external-action status only after all engines returned. A failure
after the task engine therefore persisted Tasks/Obligations with no message
attachment. Replay skipped their existing source digest and did not attach them.
Later failure points similarly persisted drafts and governance rows without a
completed message checkpoint. Plain concurrent requests also lacked a Message
row lock across effect creation.

Initial regression result: **3 failed, 1 passed**. Actual remaining database
counts `(Task, Obligation, Draft, Risk, Decision)` after rollback were `(2,2,0,0,0)`,
`(2,2,1,0,0)` and `(2,2,1,1,1)` instead of all zero. The tests use the real local
extraction/materialization engines; only the payload's provider descriptor is
synthetic, with no dispatch.

## Bounded repair and semantics

- Single confirmation locks and refreshes the exact Message before permissions,
  context changes or effects. Bulk confirmation obtains the same locks in ID
  order. Refresh runs under `no_autoflush`; dirty/deleted Message state gets an
  explicit 409 and is not silently overwritten.
- Context changes, all local proposals, their message links, materialization
  audit and `analysis_required=false` commit together in the caller transaction.
  **A failed analysis now rolls back the context confirmation too**; the user
  retries the same confirmation. There is no new asynchronous queue or job type.
- Legacy engines run in a SQLAlchemy Session bound to the caller's existing
  Connection with `join_transaction_mode="rollback_only"`. Their `commit()`
  flushes without committing the outer transaction or releasing its row lock.
  Closing the child Session does not end the caller transaction. The parent
  Message is refreshed only after the child flushes its checkpoint.
- Existing engines and their non-message callers remain unchanged. No provider
  effect is part of these engines; external Task/Calendar actions remain proposals.
  This is not an exactly-once external-effect guarantee.
- Existing already-confirmed/pending messages remain retryable. Stale clean
  identity-map state is reloaded under the lock and cannot re-run a completed
  analysis. Concurrent distinct context edits serialize; this endpoint still has
  its existing DTO/last-writer semantics, not a newly invented user CAS contract.

## Verification

`backend/tests/test_v7_context_confirm_recovery.py` covers:

- failure after task creation, after draft creation and before checkpoint;
- outer commit failure, full rollback and successful full-chain replay;
- stale loaded message replay, one materialization audit and exact message links;
- pending Message edits/deletes survive explicit denial;
- child commits do not commit or discard unrelated pending caller audit writes;
- bulk second-message failure rolls back the first message's effects too;
- complete bulk replay creates no second effect set.

Final scoped command (shared Python test venv, from `backend`):

```text
python -X utf8 -m pytest tests/test_v7_context_confirm_recovery.py tests/test_v7_context_confirm_postgres.py tests/test_mvp2_message_workflow.py tests/test_ai_secretary_api.py tests/test_response_drafts_api.py tests/test_outgoing_email_completion.py tests/test_v54_mailbox_identity.py tests/test_task_engine.py tests/test_response_engine.py tests/test_governance_engine.py -q --basetemp=.pytest-tmp-v7-context-final
```

Result: **79 passed, 3 conditional PostgreSQL skips**, 6.08s; one existing
Alembic `path_separator` deprecation warning. No full-backend run claimed.

## Prepared PostgreSQL gate (NOT RUN here)

`PUW_MVP2_TEST_DATABASE_URL` was not configured. The fixture refuses nonlocal,
non-PostgreSQL, non-owned database names and caller-supplied connection options.
It accepts `puw_mvp2_test_*` on local/container test hosts, creates a UUID-owned
schema, migrates that schema through `a54f001c0a19`, checks READ COMMITTED,
and removes only that invocation's schema. Unsafe URL validation tests are
offline tests, not PostgreSQL runtime proof.

Exact leaf node IDs for future mandatory runtime inclusion:

```text
backend/tests/test_v7_context_confirm_postgres.py::test_pg_context_confirmation_serializes_real_engines[duplicate_single]
backend/tests/test_v7_context_confirm_postgres.py::test_pg_context_confirmation_serializes_real_engines[bulk_vs_single]
backend/tests/test_v7_context_confirm_postgres.py::test_pg_context_confirmation_serializes_real_engines[failure_then_waiter]
```

The first request pauses **after the real task engine's internal commit**.
A second connection preloads stale Message state, then calls the actual single
or bulk API. The observer must see `pg_blocking_pids` contention and zero visible
Tasks/unconfirmed context. After release, or first-request rollback, assertions
require one complete effect set, attached task/draft message IDs, one
materialization audit, and zero ProviderAction rows. No SQL mock substitutes
for these assertions, and no SQLite concurrency PASS is claimed.

## Remaining acceptance boundaries

This increment prevents new partial commits; it does not backfill historical
orphaned Task/Draft rows left by older code or inspect a production database.
Such existing-data reconciliation needs a separate exact-source audit rather
than silent reattribution. It also does not prove OS process kill/restart,
PostgreSQL runtime until those opt-in tests execute, source-provider ACL changes,
live Gmail/Tasks/Calendar effects, or the final clean-SHA Linux acceptance run.

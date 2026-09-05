# MVP2 durable Gmail history cursor

Date: 2026-09-05

Base: `ad6b37c6ec2805d897b2b3b21461f1b57e562cc6`

Branch: `codex/mvp2-gmail-history-cursor`

## Result

Implemented a durable, mailbox-scoped `users.history` cursor on the existing
`BackgroundJob` queue. No second queue, real Gmail call, outgoing message,
production credential or production data was used.

The cursor is pinned to the exact organization, verified connection identity,
mail connection, credential generation and binding epoch. Every provider retry
rechecks the current project role, mailbox authority, rollout generation and
CAS ownership before reading.

## Lifecycle

```text
missing -> resync_required -> syncing(resync) -> active
active  -> syncing(incremental) -> active
syncing(incremental) -- provider 404 --> syncing(resync) -> active
syncing -- incomplete/fault --> active or resync_required -> retry
credential generation change -> resync_required -> bounded resync
```

- `checkpoint_epoch` is the monotonic CAS fence.
- `active_job_id` permits only the winning durable job and its lease-recovery
  replay to continue.
- a second job sees `history_checkpoint_busy`.
- terminal replay of the same job returns the previously stored safe counters
  without another provider or document effect.
- the job payload is exactly `{"checkpoint_id": "<opaque UUID>"}`.

## Expired cursor and replay

Gmail 404 for an expired `startHistoryId` does not reset the mailbox identity
or silently switch projects. The same claimed job records `cursor_expired`,
runs one bounded inbox resync (maximum 100 messages, last 30 days). It obtains
the profile history ID **before** listing, so messages arriving during the scan
remain available in the next incremental delta. It advances by CAS only after
the listing is complete and all message ingestion succeeds.

Existing mailbox-scoped message identity and ingestion deduplication ensure a
replayed page or resync does not create a second Message, draft or attachment
job. A partial result (`failed > 0`) never advances the cursor; successful rows
are deduplicated when the durable job retries.

A continuation token after the 100-message budget is exhausted rejects the
resync without ingestion or cursor advance. This is an explicit bounded-scope
limitation: an inbox backlog above that limit requires a future paged recovery
workflow; repeatedly retrying the same oversized backlog does not resolve it.
No claim of complete historical mailbox import is made.

Final review added four regression cases: pre-scan profile pin, rejection of
an incomplete bounded listing, worker attempt/lease expiry, and a superseded
checkpoint before ingestion. The production worker checks the exact job ID,
worker ID, attempt, lock timestamp and unexpired lease on every read and before
checkpoint completion. Failure cleanup cannot release a newer checkpoint epoch.
These guards do not claim exactly-once external effects.

## Storage and audit

Migration `a54f001c0a18` adds:

- `v54_gmail_history_checkpoints` — mutable current cursor and CAS fence;
- `v54_gmail_history_checkpoint_events` — append-only transition evidence.

The event table contains only opaque checkpoint/job IDs, from/to epochs, an
allowlisted outcome code and a timestamp. It has no provider message/thread or
history ID, address, subject, body, attachment metadata, token, DSN, exception
text or provider response. The raw history ID is stored only in the scoped
checkpoint because Gmail requires it for the next request; it is never placed
in the queue payload, result, audit or logs.

Credential rotation is monotonic. After the new generation has separately
received the explicit rollout flags and mailbox authority remains valid, the
same mailbox checkpoint drops its old cursor and requires a bounded resync.
An old or concurrently running generation cannot advance it.

## Schema and pins

- previous revision: `a54f001c0a17`;
- only head: `a54f001c0a18`;
- `CURRENT_SCHEMA_REVISION`, Docker readiness, durable harness, v5.4 runtime
  runner and their contract tests were updated from `a17` to `a18`;
- prior applied migrations were not edited.

## Verification

From `backend`:

```text
history/Gmail/mailbox targeted: 72 passed, 2 skipped (42.66 s)
migration/schema targeted: 129 passed, 5 skipped (38.42 s)
existing Gmail/provider pagination/retry regression: 33 passed (15.89 s)
```

From repository root:

```text
CI pin contracts: 22 passed
```

Final verification used
`C:/Users/dpush/OneDrive/Документы/ChatGPT/Workspace/.venv-pu-workspace-tests/Scripts/python.exe`.
Exact pytest file sets (each with `-q`):

```text
backend: tests/test_mvp2_gmail_history_cursor.py tests/test_mvp2_gmail_history_cursor_postgres.py tests/test_mvp2_gmail_history_migration.py tests/test_gmail_project_validation.py tests/test_v54_mailbox_identity.py --basetemp=.pytest-finish-cursor-20260905
backend: tests/test_schema_revision.py tests/test_mvp3_contact_resolution_migration.py tests/test_mvp3_contract_versions_migration.py tests/test_mvp3_digest_migration.py tests/test_mvp3_foundation_migration.py tests/test_mvp3_search_migration.py tests/test_mvp4_budget_dds.py tests/test_mvp4_supply_migration.py tests/test_v54_autonomy_authorization.py tests/test_v54_deadline_precision.py tests/test_v54_materialization_postgres.py tests/test_v54_pilot_foundation.py tests/test_v54_provider_action_migration.py --basetemp=.pytest-finish-cursor-schema-20260905
root: scripts/ci/test_mvp3_runtime_gate.py scripts/ci/test_v54_pilot_workflow.py scripts/ci/test_v54_wave3_ci_gate.py --basetemp=.pytest-finish-cursor-ci-20260905
backend: tests/test_mvp2_gmail_history_acceptance.py tests/test_mvp2_gmail_provider_fault_acceptance.py tests/test_gmail_adapter.py tests/test_gmail_automation.py --basetemp=.pytest-finish-cursor-provider-20260905
backend: python -m alembic -c alembic.ini heads => a54f001c0a18 (head)
root: git diff --check => PASS
```

An earlier agent reported a full run of 1413 passed / 24 skipped. That result
predates the final guard/resync corrections and is not presented as the final
full-suite result. Final verification above is targeted; the integration stream
must run the full backend suite after porting this commit. Frontend is unchanged.

The two new skips are explicit PostgreSQL gates: migration inspection and a
two-session CAS race. They run only when
`PUW_MVP2_GMAIL_HISTORY_DATABASE_URL` points to a clearly named isolated
`puw_mvp2_test_*` PostgreSQL database. No skip was introduced to hide a failure.

## Remaining gates

Status: **OFFLINE/SQLITE PASS; POSTGRESQL AND LIVE GMAIL CONDITIONAL**.

- Alembic upgrade and constraint inspection on isolated PostgreSQL;
- real two-session `FOR UPDATE`/CAS claim race and lease recovery;
- Gmail `users.history.list` quota behavior and real 404 expiration;
- live profile checkpoint before a bounded resync;
- long-running mailbox behavior under credential revocation/rotation;
- provider-side reconciliation using a dedicated non-production mailbox.

These need isolated PostgreSQL and a dedicated test mailbox. They are not
replaced by the synthetic tests in this branch.

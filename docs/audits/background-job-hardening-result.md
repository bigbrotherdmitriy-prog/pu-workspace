# BackgroundJob production hardening

## Baseline and inventory

Branch `codex/commercial-p0-job-hardening` starts at `ee2166e`. The existing
PostgreSQL queue is retained. Durable handlers cover organizer scan/safe copy,
workspace snapshots and analysis, OCR batches, Gmail synchronization and AI
Secretary rules. Telegram polling remains a separate integration process by
design and was not changed.

Remaining synchronous document work is the local-upload endpoint and manual
Gmail attachment indexing. Moving uploaded or
downloaded bytes safely requires an encrypted staging object:
document bytes must never be stored in `background_jobs.payload`. This is a
known follow-up, not silently moved into a second queue.

## Failure protocol

Jobs use `queued → running → completed`; retryable errors use `retrying`, then
`dead_letter` after exhaustion. A classified non-retryable error uses `failed`.
Only queued/retrying jobs can be cancelled. Administrators retry `failed` jobs
and explicitly redrive dead-letter jobs. Claim and lease recovery are atomic in
PostgreSQL. Completion, heartbeat and progress require the same `worker_id`, so
a worker that lost its lease cannot commit a result.

Workers stop claiming on SIGTERM/SIGINT and drain the current job while its
lease heartbeat remains active. Scheduler shutdown is interruptible. Compose
grants workers a grace period longer than the default job lease.

Errors persist only a sanitized class/short message. URLs and common credential
labels are redacted; worker logs do not print exception text or payloads.

## Operations and metrics

Admin endpoints: `GET /admin/jobs`, `GET /admin/jobs/metrics`, and POST actions
`cancel`, `retry`, `redrive`. Metrics expose queue length, oldest queued age,
failed/dead-letter count, active worker count, statuses and service heartbeats.

Queue-only backup and guarded restore are provided by
`scripts/backup-job-queue.sh` and `scripts/restore-job-queue.sh`. Stop scheduler
and workers before restore; verify the Alembic revision before restarting.

## Fault-injection acceptance protocol

1. Start PostgreSQL 16, two Uvicorn workers, two job workers and scheduler.
2. Enqueue a deterministic test job twice with one idempotency key; assert one row.
3. Let both workers claim concurrently; assert one owner and one execution.
4. Kill the owning worker, wait past the lease, and assert `retrying` then claim by another worker.
5. Restart API and then the complete Compose project; assert queued/running state survives.
6. Force failures through max attempts; assert `dead_letter`, then redrive.
7. Cancel a queued job and assert workers never claim it.
8. Inspect service heartbeats/metrics and scan logs for payload text and credential values.
9. Create queue backup, restore into an empty test database and compare row counts/statuses.

Never run destructive restore over production. Production deploy is outside this fork.

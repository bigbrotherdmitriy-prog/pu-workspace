# MVP1 managed-copy cleanup worker fencing — 2026-09-05

Branch: `codex/mvp1-cleanup-worker-fencing`. Base: `3ff8b767383598226abe7fb3d6710e2de3bf734c`.

## Result and scope

The managed-copy cleanup handler now requires the existing queue's exact claim-time
`(job_id, worker_id, attempt, locked_at)` context. A missing/incomplete context,
different job payload/kind, changed owner or attempt, expired lease, cancellation
request, cancellation timestamp, completion timestamp, waiting state or terminal
state fails closed. No cleanup effect or receipt is authorized by a stale attempt.

The worker reads the requester from the existing durable enqueue idempotency key
`workspace.safe_copy_cleanup:{project_id}:{user_id}:{command_key}` and checks the
current user and owner membership (or current administrator permission). This uses
the existing three-field job payload without introducing a schema or queue change.
Ownership is not reconstructed from whichever project owner remains today.

Every item repeats fresh project, role, connection, snapshot/session/source and
protected-original checks. Source provider must match the snapshot binding. The
worker rechecks the exact claim immediately before the external effect and checks
the current state again after it. ORM identity-map refreshes prevent stale reads
between effects. A binding, role, source or project change after an effect prevents
the item receipt and subsequent effects; recovery then requires the explicitly
capable provider's reconciliation behavior.

The project row is locked for each effect/receipt transaction. This serializes
cooperating cleanup workers and the HTTP command's existing project lock in
PostgreSQL. After a receipt commit the next item reacquires the lock and recomputes
the inventory. Older active commands win deterministically; later commands fail
closed and must revalidate against the receipts/version after the earlier command.
Before receipt commit the handler locks and checks the exact BackgroundJob row,
then performs a conditional ownership/lease/cancellation update and appends the
audit receipt in that transaction. The job row is not held locked during the
external effect, allowing heartbeat/cancellation/recovery to proceed.

New item and final receipts pin job ID, cleanup version and attempt in addition to
command key and managed identity. Identical command text from another actor/job
cannot borrow a receipt. Legacy receipts without job ID are accepted only when
the durable command resolves to this one job unambiguously. Final receipt replay
requires matching version/count, all item receipts, and the explicit boolean
`originals_affected=false`. A completed worker job cannot invoke the handler again;
HTTP replay continues through the existing stored job result. Handler receipt
replay is for an exact live attempt recovering completion before queue success.

## Provider boundary

Real Google/Yandex adapters remain disabled. A static adapter-class capability
check now rejects cleanup **before** `storage_for_project` can construct a client
or refresh credentials. Instance capability is checked as well. Tests open the
static gate only alongside an explicitly injected synthetic adapter. No adapter
flags were enabled and no network operations were implemented or performed.

The exact database checks do not make the remote effect atomic with a lease,
cancellation, permission change or receipt. An operation already in flight may
finish after authority changes; its stale attempt cannot append a receipt, and the
next attempt may replay the provider call. A synthetic adapter models idempotent
reconciliation, not a proof of exactly-once provider behavior. Original protection
covers exact registered folder identifiers; provider-side descendant/original
proof remains a separate required gate.

## Validation

Regression first: the no-claim test failed against the base because cleanup ran
without an execution owner. Initial fencing profile: **66 passed**. Expanded
fencing/identity/cleanup selection before final proof checks: **105 passed,
52 deselected**. All data and provider effects were synthetic.

Final targeted commands, run in `backend` with
`C:/Users/dpush/OneDrive/Документы/ChatGPT/Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8`:

```powershell
python -X utf8 -m pytest tests/test_mvp1_cleanup_worker_fencing.py tests/test_managed_copy_identity.py -q --basetemp=.pytest-cleanup-fencing-proof-20260905
python -X utf8 -m pytest tests/test_storage_binding_validation.py -q -k 'cleanup or copy_that_is_another_projects_original' --basetemp=.pytest-cleanup-binding-proof-20260905
```

Final results: **97 passed** (30.25 seconds) for fencing/identity, and **16 passed,
52 deselected** (37.24 seconds) for the existing cleanup/original-protection
selection. No test was skipped. The new tests cover both
synthetic Google and Yandex providers: stale/missing claims and reused worker IDs,
expired leases (including expiry during the final inventory read), waiting and
terminal jobs, running cancellation, payload/command/version mismatch, changes
during an effect and after its committed receipt, cross-actor command collision,
competing commands, final-proof validation, provider capability denial before
client construction, crash before receipt, and recovery after committed receipt.

SQLite tests exercise deterministic interleavings through separate sessions.
They do not establish PostgreSQL row-lock concurrency, external provider fencing,
or provider reconciliation acceptance. Those remain isolated runtime/provider
gates; the live capability deny remains necessary. No DB-enforced immutable ledger,
new job queue, schema migration, push, merge, deployment or production operation
is included. Full application suites belong to the parent integration worktree.

# v5.4 Gmail attachment encrypted staging

## Result

Gmail attachment import is cut over from synchronous API decoding/extraction to
the existing durable `BackgroundJob` and the encrypted staging lifecycle seam.
The change is default-deny until server composition installs the concrete
`A05GmailAttachmentLifecycle` from `app.staging.gmail_a05`. It adds no queue,
materialization model, migration, provider credential,
OAuth scope, frontend behavior, OCR implementation or AUTO path.

The API now returns an opaque `staging_id` and the existing job identity. The
job payload is exactly:

```json
{"staging_id":"<32 lower-case hex characters>"}
```

Provider message/attachment IDs remain only in the ephemeral Gmail adapter.
Attachment bytes/base64/text, filenames, tokens, checksums and filesystem paths
do not cross the job, audit or logging boundaries. The audit records only the
opaque staging ID and a safe state.

## Enforcement points

Before the provider body read and again at every worker authorization callback,
`validate_gmail_attachment_binding` verifies:

- exact organization, requesting owner and current editor-or-higher project access;
- current message project and tenant;
- exact connection identity/account, `MailConnection`, credential generation,
  binding epoch, current mailbox origin binding and exact mailbox authority
  version;
- exact message `SourceReference` and current immutable `SourceVersion` resolved
  by the mailbox cutover runtime;
- current `primary_read` and `actions` rollout flags plus their record version;
- `CONFIRM` mode only;
- the attachment slot's exact normalized MIME type and declared size, bounded by
  `GMAIL_ATTACHMENT_MAX_BYTES`.

The guarded provider adapter can open once. It uses the provider-observed message
origin from the mailbox runtime, rejects invalid base64, bounds encoded input
before decoding, and requires Gmail's reported/decoded size to equal the pinned
declaration. Any rotation, revoke, mailbox/project move, attachment metadata
change or rollout disable after enqueue denies before the next read.

The worker supplies the a05 adapter with the existing queue's immutable
claim-time tuple `(job_id, worker_id, attempt, locked_at)`. Expired leases are
recovered by the existing queue. The new attempt reuses the same staging ID and
does not download or create a second encrypted object.

## Lifecycle and cleanup

`jobs.handlers` owns a narrow kind dispatch for
`gmail.attachment.materialize`; generic queue code remains unchanged. Worker
outcome routing invokes the lifecycle hook after `retrying`, `failed`,
`dead_letter`, `completed` and worker-produced `cancelled`. The admin queued-job
cancel path invokes the same hook. Hook failures are logged only by exception
class. The scheduler calls the lifecycle recovery seam, which both performs its
retention/purge maintenance and returns bounded opaque IDs requiring a missing
dispatch. Existing enqueue idempotency collapses duplicate/restart delivery.

The a05 implementation must make cleanup idempotent and fenced from a live
writer. `completed` and `cancelled` must transition all declared input/temp/
derived artifacts to purge and record `PURGED` only after verified deletion.
`retrying` may retain ciphertext only to its policy deadline. `failed` and
`dead_letter` must invoke the policy-bounded failed-retention hook; expiry,
revoke, integrity failure or missing key must become purge/quarantine, never an
unbounded retry or plaintext re-stage.

## Exact a05 interface requirements

The integration owner installs one process-local implementation with
`install_gmail_attachment_lifecycle(port)`. It must implement the following
methods from `app.staging.gmail.GmailAttachmentLifecyclePort`:

```python
admit_and_stage(db, binding, provider) -> GmailAttachmentStageResult
describe(db, staging_id) -> GmailAttachmentBinding
process(db, staging_id, claim, authorize_read) -> GmailAttachmentProcessResult
on_job_outcome(db, staging_id, status) -> None
recover_pending(db, limit) -> Collection[str]
```

Required semantics:

1. `admit_and_stage` derives/loads one shared materialization request for the
   complete `GmailAttachmentBinding`; duplicate delivery returns the same
   `staging_id` with `duplicate=True`. It persists no provider raw ID. It commits
   admission/binding before returning so crash-before-enqueue is recoverable.
2. It must authorize access/copy, MIME/size, residency, backup, retention and the
   requested non-OCR derive classes before invoking `provider.open()`. Unknown or
   absent policy denies with zero provider reads. `provider.open()` is invoked at
   most once and immediately streams into the existing authenticated encrypted
   staging store; no plaintext filesystem fallback is permitted.
3. The shared row stores every field of `GmailAttachmentBinding` unchanged, plus
   the a05 policy/version pins, retention deadline, representation descriptor and
   checksum/integrity evidence. `staging_id` is the only cross-boundary identity.
   a05 remains the sole owner of these shared models and of migration `a05`.
4. `describe` returns the exact immutable binding for the staging ID with a
   uniform unavailable response for missing or cross-scope IDs. It must never
   return provider locators, content, secrets, checksums or storage paths.
5. `process` verifies the claim against the live `BackgroundJob` lease/fence,
   enters `with authorize_read():` around **every** encrypted/plaintext read and
   around the Document/task/draft/risk commit, and
   performs CAS/idempotency on the stable materialization request. A stale worker
   cannot read, commit or delete artifacts. It returns only the typed safe counts
   and internal document ID represented by `GmailAttachmentProcessResult`.
6. Processing must use the existing Gmail document/task/draft/risk behavior only
   for already-authorized derive classes. This change does not authorize or alter
   OCR and never upgrades `CONFIRM` to AUTO. Evidence must identify the actual
   materialized representation, not the provider original.
7. `on_job_outcome` is idempotent for repeated/out-of-order delivery and drives
   completed/cancelled cleanup and failed retention. Integrity/checksum mismatch
   publishes no document/evidence and schedules fenced purge/quarantine.
8. `recover_pending` performs bounded dispatch and cleanup maintenance. It returns
   unique valid 32-hex staging IDs only for admitted durable requests lacking the
   reusable job binding. It must not redrive a terminal request implicitly or
   reacquire provider content after purge/revoke.

## Synthetic verification

Fixtures contain no network client, real mailbox, OAuth token or production
data. Fake Gmail provides only in-memory base64 and fake lifecycle state.

- full relevant Gmail/mailbox/queue/pilot/encrypted-staging regression:
  `196 passed, 2 skipped`; both skips are existing Windows capability checks for
  unavailable symlink/hardlink creation;
- cases cover duplicate delivery, exact ID-only payload, audit redaction,
  pre-download default deny, post-enqueue rollout/generation/epoch/project/
  MIME/size changes, lease recovery, failed/terminal hooks, restart dispatch
  recovery, and provider size/integrity tamper denial.

No push, merge, deploy, production access or real Gmail call was performed.

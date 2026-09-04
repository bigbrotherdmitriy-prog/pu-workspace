# v5.4 Gmail attachment → a05 wiring

## Result

`A05GmailAttachmentLifecycle` is the concrete implementation of the existing
`GmailAttachmentLifecyclePort`. It reuses `v54_materializations`,
`MaterializationLifecycle`, `FilesystemStagingStorage`, and the existing
`BackgroundJob`; it adds no table, migration, queue, OAuth scope, provider
client, frontend path, OCR path, or AUTO behavior.

The implementation remains explicit server composition. If an installation
does not supply a policy factory, KEK resolver/storage, retention, residency,
and derive allow-list, the pre-existing Gmail seam remains unavailable and
denies before provider download.

## Admission and binding

Before `provider.open()` the API and guarded provider boundary require the exact
mailbox identity, connection, generation, binding epoch, source/version,
origin binding, rollout record, current `primary_read + actions`, requesting
owner/project membership, and the exact mailbox authority version. The a05
adapter additionally requires explicit materialization write/observe/audit/read
authority and an exact server-side policy decision for MIME, size, copy,
no-backup, residency, KEK version, retention, failed retention, and non-OCR
derive classes.

One deterministic child attachment `SourceReference`/immutable `SourceVersion`
and `Evidence` record carry the complete internal binding and policy pins. They
contain no Gmail message/attachment locator and no filename. The source version
records authenticated-encryption integrity; the plaintext digest remains
inside the encrypted a05 footer. Admission enters `WRITING` before the one
provider open. The representation is sealed, derived, and committed before the
API enqueues the existing job. A duplicate complete binding resolves to the
same materialization and 32-hex `staging_id`, without a second provider read.

The queue payload and idempotency boundary are exactly:

```json
{"staging_id":"<32 lowercase hex>"}
```

Provider IDs, attachment bytes/base64, filenames, storage paths, checksums, and
key references do not enter job payload/result metadata, logs, or audits.

## Worker, derivation, and cleanup

Every encrypted iterator read is enclosed by the Gmail seam's live
authorization callback. The adapter also verifies the durable
`(job_id, worker_id, attempt, locked_at)` claim, unexpired lease, immutable
binding, materialization state, and exact policy again. Legacy document/task/
draft/risk/decision processors run behind a transaction proxy: their internal
commit calls become guarded flushes, and one final authorization precedes the
single real business commit. The external document identity uses only the
opaque staging ID. Extraction is UTF-8 native text/CSV/Markdown/JSON only;
no OCR function is called.

Completed and cancelled jobs delete ciphertext and leave a `PURGED` tombstone.
Integrity/key/tamper failures publish no document/evidence output and purge
immediately. Other failures retain ciphertext only until the lesser of the
materialization deadline and failed-retention deadline. Outcome hooks are
idempotent and ignore stale out-of-order notifications. Recovery purges missed
terminal cleanup and expired/failed materializations, promotes a safely sealed
request only after current reauthorization, and dispatches only a unique
derived staging ID with no existing job. It never reacquires provider content.

## Verification and integration note

Synthetic tests use fake Gmail metadata/body only, SQLite, and the real a05
AES-GCM filesystem store. They cover duplicate ingress, stable opaque payload,
authority-version rotation, per-read worker fences, atomic business output,
completed/cancelled purge, bounded failed retention, ciphertext tamper, sink
redaction, and crash-before-enqueue recovery.

Verification on 2026-09-04:

- concrete Gmail→a05 suite: `7 passed`;
- relevant Gmail/mailbox/materialization/authority/source/job suite:
  `327 passed, 8 skipped`;
- full backend: `1067 passed, 13 skipped`, plus one unrelated timing-only
  performance threshold miss (`10.31s` against `<10s`); its isolated rerun
  passed in `5.22s`.

The parallel local-upload→a05 branch extends shared
`backend/app/staging/lifecycle.py` with explicit IDs, `source_object`,
`authorize_read`, and early-retirement helpers. This Gmail commit intentionally
does not edit that shared file. Integration should take the local lifecycle
extension first and then keep this adapter isolated; its current use of existing
private pin/descriptor helpers can be replaced mechanically with the new public
helpers without changing the Gmail port contract.

No push, merge, deployment, production access, or real Gmail call was made.

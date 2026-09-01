# Encrypted staging for queued uploads and Gmail attachments

Status: architecture decision only. This integration deliberately does not enqueue local-upload or Gmail attachment bytes until an implementation is reviewed.

## Boundary

`BackgroundJob.payload` may contain only opaque identifiers and non-sensitive routing metadata: `staging_object_id`, `project_id`, `requested_by`, media type, byte length and SHA-256. It must never contain file bytes, extracted text, email bodies, credentials or document names.

## Storage and encryption

- Store each temporary object outside the API container filesystem in a private staging store that is shared by workers.
- Generate a unique data-encryption key (DEK) per object, encrypt with an authenticated cipher, and wrap the DEK with a versioned master key (KEK) supplied by the deployment secret manager.
- Persist only the wrapped DEK, nonce, authentication metadata, checksum, owner/workspace scope and lifecycle timestamps. Never log a DEK, plaintext path or content.
- Authorize every read against both the job identity and the workspace/project scope. A worker receives a short-lived read capability, not a permanent storage credential.

## Lifecycle

1. API streams the upload/attachment into encrypted staging while calculating size and SHA-256.
2. After authenticated storage and checksum verification, metadata changes from `uploading` to `ready`.
3. The durable job is enqueued using an idempotency key derived from workspace, source identity and content hash.
4. A worker atomically claims the staging object (`ready` → `processing`) and verifies the checksum after decryption.
5. On completion or cancellation the plaintext buffer is released immediately and the encrypted object is deleted; metadata records only deletion outcome and audit identifiers.
6. On retry the encrypted object remains until the next attempt. On terminal failure/dead-letter it is retained for a short, configurable recovery window and then deleted by a lease-aware sweeper.
7. The sweeper deletes abandoned `uploading`, expired `ready`, terminal and orphaned objects. Delete failures move metadata to `deletion_failed`, emit an operator alert and retry with backoff.

## Failure and recovery rules

- A crashed worker cannot delete an object owned by an active lease; after lease expiry another worker may reclaim it.
- Redrive is allowed only while the encrypted object exists and its checksum matches. Otherwise the operator must request a new upload.
- Idempotent repeated HTTP requests reuse the same ready object/job; partial uploads are never exposed to workers.
- Backups include encrypted objects and wrapped keys only for the documented recovery window. Expired staging data must also expire from backups according to the retention policy.
- Metrics contain counts, bytes, age, state and failures only. Audit events identify actor/job/object but contain no document content.

## Required schema (future migration)

Suggested `staging_objects`: opaque UUID, workspace/project/user IDs, source kind/source ID hash, state, storage key, wrapped DEK and key version, cipher metadata, SHA-256, size, media type, created/ready/claimed/expires/deleted timestamps, lease owner/expiry, delete attempts and sanitized error. Add unique scope + idempotency hash and indexes on state/expiry and lease expiry.

The integration flow that owns BackgroundJob must create and review the migration. Gmail and Google integration code remains unchanged here.

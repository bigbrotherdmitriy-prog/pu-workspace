# MVP2 Gmail provider fault acceptance

Date: 2026-09-05

Base: `583510782e6ab4eb3bdbd1d7e7c0eab6c5ef04eb`

Branch: `codex/mvp2-gmail-provider-fault-acceptance`

## Scope and safety

This acceptance is synthetic and offline. It does not connect to Gmail, send
mail, use real OAuth credentials, call external AI, read production data or
change production. The existing mailbox identity, encrypted attachment staging,
BackgroundJob and provider-action outbox are reused. No queue, model or
migration was created.

## Audit: reuse, reproduced gap and result

| Area | Existing behavior | Gap reproduced | Result |
|---|---|---|---|
| Gmail idempotent reads | direct `execute()` for list, message and attachment | 429 or transient 5xx aborted immediately | one shared bounded read wrapper retries 429/5xx/network faults at most three times with exponential delay and bounded `Retry-After` |
| Safe errors | item loop exposed only exception class | provider exception could leave raw response context at outer pagination/attachment boundary | provider error text is replaced by `provider_read_rejected` or `provider_read_unavailable`; sync endpoint returns a fixed safe 503 |
| Token/cursor errors | OAuth credentials refresh before service creation; cursor shape is locally checked | 400/401/403 retry policy was implicit | invalid/expired cursor and expired/revoked authorization are not retried blindly |
| Credential generation | mailbox runtime pins exact generation | rotation during a retry could permit another read with the stale service before ingest was rejected | exact current identity, connection, generation, epoch, token and pilot flag are checked before every list/message attempt |
| Attachment ingress | authorization before download and before worker reads | retry inside provider download would otherwise reuse one earlier authorization | each attachment retry re-runs the independent live binding/authority guard before provider read |
| Durable staging | opaque `staging_id`, lease fence, recovery and cleanup | no new defect | existing API restart, expired lease, second worker, cancel and integrity tests remain green |
| Provider send | CONFIRM outbox, UNKNOWN reconciliation and receipt | no new defect | existing timeout-before/after-effect tests prove no blind resend and one external effect/draft receipt |

The first regression failed because the initial synthetic 429 escaped and no
second request occurred. The minimal implementation was added only after that
failure was recorded.

## Fault scenarios accepted

- 429 followed by success: one bounded backoff and one mailbox-scoped Message;
- consecutive 503 responses: delays `0.25s`, `0.5s`, then success;
- bounded numeric `Retry-After` is respected; oversized/unparseable values fall
  back to the bounded local policy;
- 400 expired pagination token, 401 expired/revoked token and 403 permission
  rejection perform no automatic retry and expose no provider response body;
- failure on page two can be replayed: page-one Message is deduplicated and no
  ResponseDraft is duplicated or created before context confirmation;
- access-token expiry with a refresh token performs one existing OAuth refresh
  and persists the new encrypted access token;
- credential generation rotated during 429 backoff prevents the second provider
  read and leaves no partial Message;
- attachment 429 retries once, while attachment 401 is denied without retry;
- attachment authorization/generation change between attempts prevents the
  second provider read;
- existing staging tests verify restart recovery, expired lease, claim by a
  second worker, stale-owner denial and opaque job payload;
- existing provider-action tests verify timeout before effect as NOT_APPLIED,
  timeout after effect as UNKNOWN, lookup reconciliation, one Gmail effect and
  one persisted draft receipt.

## Verification

From `backend`:

```text
# Gmail/mailbox/OAuth slice plus attachment lifecycle
130 passed

# Provider outbox / UNKNOWN / reconciliation slice
33 passed

# New provider-fault contract
13 passed

# Complete backend regression
1392 passed, 21 skipped
```

The full-suite skips are existing explicitly gated PostgreSQL/runtime tests. No
test was converted to a mock or skip. Python compilation, one Alembic head and
`git diff --check` are verified before the final commit.

## Durable Gmail history cursor: schema required, not implemented

`messages.list` pagination and immutable `historyId` observations are now
covered, but a durable `users.history.list` checkpoint cannot be implemented
safely without state. A future sequential migration should add:

### `gmail_history_checkpoints`

- `id UUID PRIMARY KEY` — only this opaque ID is allowed in BackgroundJob
  payload;
- `organization_id`, `mail_connection_id`, `credential_generation`,
  `binding_epoch` — exact mailbox scope and generation pins;
- encrypted or provider-ID-classified `history_id VARCHAR(200)` — never logged;
- `state` constrained to `active`, `resync_required`, `blocked`;
- `record_version INTEGER > 0` for CAS;
- `last_success_at`, `created_at`, `updated_at`;
- unique `(organization_id, mail_connection_id)` and scoped FK to the existing
  mail connection; generation/identity consistency is rechecked transactionally.

### `gmail_history_checkpoint_events`

- append-only event ID, checkpoint ID, from/to record versions, safe outcome
  code, job ID and timestamp;
- no subject, body, address, provider response, page token or attachment data.

The flow must be one existing BackgroundJob kind with payload
`{"checkpoint_id": "<opaque>"}`. A provider page token remains process-local;
only a successfully completed page advances the CAS checkpoint. Gmail history
404/expiry changes state to `resync_required`; it must not silently reset to the
current project or infer a mailbox. A human-authorized bounded resync then
rebuilds observations under the same persisted mailbox identity.

No version of this table or job was created in this branch.

## Remaining live/PostgreSQL gates

Status: **OFFLINE FAULT CONTRACT PASS / LIVE AND POSTGRESQL CONDITIONAL**.

- real Gmail quota headers, network timing and OAuth refresh/revocation behavior;
- real `users.history.list` checkpoint, 404 expiry and webhook/poll lifecycle;
- current-generation rotation concurrent with reads under PostgreSQL isolation;
- actual API and worker process termination during encrypted staging;
- dedicated test-mailbox timeout-after-send lookup with a real provider receipt;
- PostgreSQL claim/lease concurrency for this combined scenario.

These gates need a dedicated non-production mailbox and isolated PostgreSQL.
They cannot be honestly replaced by the synthetic acceptance in this branch.

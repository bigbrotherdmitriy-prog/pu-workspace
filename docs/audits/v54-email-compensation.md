# V5.4 acceptance E: irreversible email compensation

Date: 2026-09-04

Base: `e947a17`

Branch: `codex/v54-email-compensation`

## Result

The inbox now states **«Отменить отправку нельзя»** for sent drafts. A corrective
follow-up can be proposed only when the server can bind the protected sent draft
to one exact `APPLIED`, `IRREVERSIBLE`, synthetic send in the existing provider
Action Ledger. Missing, stale, conflicting, or multi-revision sources disable the
control.

Proposal is atomic and draft-only:

- a new protected `ResponseDraft(status=draft)` holds subject/body/recipient;
- a new `synthetic.effect.corrective` action is sealed as `FROZEN` with new
  action, command, idempotency, envelope, and payload hashes;
- source project/mailbox/evidence/context/authority/capability/credential pins
  are preserved and the exact source outcome snapshot is included in the sealed
  payload hash and PII-free audit event;
- no approval, outbox row, background job, adapter call, credential access,
  provider call, network call, live send, or `AUTO` path is created;
- legacy draft approval and Gmail-send routes reject corrective follow-ups, and
  their normal approve/send controls are not rendered;
- the original action and append-only `APPLIED` observation remain unchanged.

The read/propose endpoints require manager authority. A proposal carries
`approval_mode=CONFIRM`; a stale server ETag is rejected. Editing a sent draft is
also rejected so the protected source record cannot be rewritten after send.

## Schema boundary

No migration was added. The explicit relational pins needed after parallel
`a07` are documented in
[`email-compensation-a08-handoff.md`](../architecture/v54/email-compensation-a08-handoff.md).

## Verification

- Targeted backend regression (`test_v54_email_compensation`, provider runtime,
  response API, Gmail adapter, and inbox source checks): **41 passed**.
- Full backend suite: **1087 passed, 14 skipped**. The skips are the existing
  environment-gated PostgreSQL checks.
- Frontend component regression: **3 passed**.
- Full frontend suite, run with one worker to avoid resource contention:
  **12 files passed, 94 tests passed**. The existing full-App storage picker
  test now declares a local five-second DOM wait and fifteen-second test budget;
  assertions and production behavior are unchanged.
- TypeScript `tsc --noEmit`: passed.
- Production frontend build (`tsc -b` plus Vite): passed; the tracked
  `backend/app/react_dist` bundle was refreshed.
- Repository CI-script tests: **110 passed**.
- `git diff --check`: passed.
- Changed-file inspection: no Alembic revision or other schema migration.
- Capability/PII audit: the proposal module imports no provider adapter,
  credential accessor, network client, worker, approval, or outbox primitive.
  Recipient, subject, and body are written only to the protected
  `ResponseDraft`; ledger and audit records contain opaque IDs, exact pins,
  counts, and hashes only.

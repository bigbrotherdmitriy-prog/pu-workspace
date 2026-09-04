# PU Workspace v5.4 — focused final review of late Wave 3 changes

Date: 2026-09-04

Base: `842215fb1b977a0e50e08a025cf6f8a69d8a6b69`

Reviewed changes: `218d48f` (email compensation), `7758bdd` (staging
safety), `842215f` (a08 CI/readiness pins).

## Decision

**CODE REVIEW PASS after two minimal fixes; PostgreSQL runtime remains
CONDITIONAL.** No production, provider, OAuth, mailbox credentials or customer
data were accessed. No new migration was created; the only head remains
`a54f001c0a08`.

## Confirmed findings and fixes

### P1 — corrective proposal TOCTOU and concurrent duplication

`propose_email_compensation` first read an eligible APPLIED observation and
later read it again without a lock. A late receipt could advance the exact
source between those reads, while two concurrent POSTs could both observe no
proposal and create distinct corrective actions because their command and
idempotency keys are intentionally random.

The fix locks the immutable source `ProviderAction` row with `FOR UPDATE`, then
rechecks the client `source_etag` and existing proposal under the same lock
before creating anything. A regression injects a late observation precisely
between the optimistic read and locked recheck. A second regression verifies
that proposal creation emits a row-locking select.

### P1 — a08 offline downgrade safety guard was not renderable

`alembic downgrade a54f001c0a08:a54f001c0a07 --sql` failed before producing a
rollback script because Alembic's offline `MockConnection` has no `scalar`
method. The release process therefore could not generate a guarded rollback.

The online path retains its existing preflight query. The offline path now
emits a PostgreSQL `DO` block that aborts execution if any service-retention
audit rows exist, before dropping `service_principal`. A regression verifies
both the guard and its ordering before destructive DDL.

## Reviewed boundaries with no remaining P0/P1 finding

- Cross-tenant/project/mailbox: compensation resolves the source using the
  draft's project and that project's organization, and binds the corrective
  action to the exact mailbox, project, evidence pins and source action.
- Legacy send bypass: corrective drafts remain `draft`; ordinary approval is
  rejected and the only Gmail send endpoint rejects the protected marker.
- Stale authority/pins: the corrective action copies the exact source context,
  authority, capability, credential and evidence versions; execution still
  requires a separate CONFIRM and live runtime checks.
- Retention service impersonation: purge uses an explicit service principal,
  allowlisted tenant/project scopes, residency and KEK, and records no user
  actor.
- Purge ordering: failed/dead-letter recovery commits `EXPIRED` and its audit
  before deleting ciphertext, then commits the `PURGED` tombstone; retry after
  a delete/commit crash is idempotent.
- Lease race: business commits through the local upload processor are fenced
  by job id, worker id, attempt, lock timestamp and unexpired lease while
  holding the job row lock.
- Document identity: the unique index is partial to `source='local_upload'`, so
  matching IDs from Google Drive, Yandex Disk or other providers do not collide.
- Secrets/PII: request actor/correlation values are hashed; tests inspect both
  compensation audit events and the ledger action. No body, recipient,
  provider raw ID, path, DSN or secret is retained there.
- CI pins: schema, Compose, Docker smoke, v5.4 runtime and durable queue all
  expect `a54f001c0a08`; Alembic reports one head.

## Tests

- Focused email/staging regression: `15 passed, 1 skipped`.
- Broader provider/local-upload/migration regression after the fixes:
  `41 passed, 3 skipped`.
- Wave 3 CI contract: `14 passed`.
- Full backend regression: `1115 passed, 17 skipped`.
- PostgreSQL-only tests are skipped because no isolated test DSN is available
  locally. The new `FOR UPDATE` behavior and a08 online upgrade/downgrade still
  require the isolated Linux/PostgreSQL workflow before runtime PASS.

## Residual limitations

- The corrective provider remains synthetic-only and CONFIRM-only; this review
  does not authorize a live email send.
- Editing a protected corrective draft after freeze invalidates its derived
  payload binding and therefore fails closed. A future product flow may add an
  explicit new revision, but must not mutate the frozen action.
- The generic materialization API is broader than this review; the verified
  failed/dead-letter recovery path is specifically the local-upload a08 path.
- Production enable, merge and deploy remain blocked pending green PostgreSQL
  fault/runtime artifacts on the exact integrated SHA.
